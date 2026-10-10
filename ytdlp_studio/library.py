"""Validated, reversible renaming of legacy Spotify release folders."""

from __future__ import annotations

import hashlib
import re
import stat
from dataclasses import dataclass
from pathlib import Path

from .backend import album_folder_name, read_release_identity, write_release_marker


def reject_link(path: Path) -> None:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise ValueError(f"Linked/reparse paths cannot be migrated: {path}")


def library_root(path: Path) -> Path:
    path = path.absolute()
    for ancestor in (path, *path.parents):
        reject_link(ancestor)
    if not path.is_dir():
        raise ValueError(f"Library is unavailable: {path}")
    return path.resolve(strict=True)


def fingerprint(folder: Path) -> dict[str, tuple[int, str]]:
    """Hash every ordinary file; never traverse links or reparse points."""
    reject_link(folder)
    result = {}
    pending = [folder]
    while pending:
        for path in pending.pop().iterdir():
            reject_link(path)
            if path.is_dir():
                pending.append(path)
            elif path.is_file() and path != folder / ".studio-release.json":
                digest = hashlib.sha256()
                with path.open("rb") as stream:
                    for block in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(block)
                result[path.relative_to(folder).as_posix()] = (path.stat().st_size, digest.hexdigest())
            elif not path.is_file():
                raise ValueError(f"Unsupported library entry: {path}")
    return result


@dataclass
class FolderRename:
    source: Path
    destination: Path
    identity: str
    label: str
    files: dict[str, tuple[int, str]]


def validate_rename(change: FolderRename) -> None:
    root = library_root(change.source.parent)
    if change.source.parent != root or change.destination.parent != root:
        raise ValueError("Folder rename must stay directly inside its validated library root.")
    if change.destination.exists():
        raise ValueError(f"Destination already exists; refusing to merge: {change.destination}")
    if fingerprint(change.source) != change.files:
        raise ValueError(f"Library changed after validation: {change.source}")
    marker = change.source / ".studio-release.json"
    if marker.exists() and read_release_identity(marker) != change.identity:
        raise ValueError(f"Release marker does not match: {change.source}")


def plan_legacy_renames(catalogue: dict, local: Path, mirror: Path | None = None) -> list[FolderRename]:
    local = library_root(local)
    mirror = library_root(mirror) if mirror is not None else None
    if mirror is not None and (mirror == local or local in mirror.parents or mirror in local.parents):
        raise ValueError("The local and mirror libraries must be separate, not nested.")
    releases = {item["id"]: item for item in catalogue["items"]}
    legacy = {}
    occupied = set()
    mapped = {}
    for folder in local.iterdir():
        match = re.search(r" \[([A-Za-z0-9]{22})\]$", folder.name)
        if match and folder.is_dir():
            reject_link(folder)
            identity = match.group(1)
            if identity not in releases:
                raise ValueError(f"Release does not belong to the supplied artist: {folder}")
            if identity in legacy:
                raise ValueError(f"Release appears in multiple folders: {identity}")
            legacy[identity] = folder
        else:
            occupied.add(folder.name.casefold())
            if folder.is_dir():
                reject_link(folder)
                marker = folder / ".studio-release.json"
                if marker.is_file():
                    reject_link(marker)
                    identity = read_release_identity(marker)
                    if identity in mapped or identity in legacy:
                        raise ValueError("Release appears in multiple folders.")
                    mapped[identity] = folder.name
    if legacy.keys() & mapped.keys():
        raise ValueError("A release already has both a readable and a legacy folder.")
    changes = []
    for release in catalogue["items"]:
        identity = release["id"]
        if identity in mapped:
            continue
        base = album_folder_name(release)
        name, edition = base, 2
        while name.casefold() in occupied:
            name = f"{base} (edition {edition})"
            edition += 1
        occupied.add(name.casefold())
        if identity not in legacy:
            continue
        folder = legacy[identity]
        label = f"{catalogue['artist']} - {release['title']}"
        files = fingerprint(folder)
        changes.append(FolderRename(folder, local / name, identity, label, files))
        if mirror is not None and (mirror / folder.name).exists():
            source = mirror / folder.name
            mirrored = fingerprint(source)
            if any(files.get(path) != value for path, value in mirrored.items()):
                raise ValueError(f"NAS files do not match the local release: {source}")
            changes.append(FolderRename(source, mirror / name, identity, label, mirrored))
    destinations = [str(change.destination).casefold() for change in changes]
    if len(destinations) != len(set(destinations)):
        raise ValueError("Multiple releases would share a destination.")
    for change in changes:
        validate_rename(change)
    return changes


def apply_renames(changes: list[FolderRename]) -> None:
    """Validate the complete plan first, then roll back all renames on failure."""
    for change in changes:
        validate_rename(change)
    moved, markers = [], []
    try:
        for change in changes:
            validate_rename(change)
            change.source.rename(change.destination)
            moved.append(change)
        for change in moved:
            marker = change.destination / ".studio-release.json"
            existed = marker.exists()
            write_release_marker(change.destination, change.identity, change.label)
            if not existed:
                markers.append(marker)
            if fingerprint(change.destination) != change.files:
                raise ValueError(f"Files changed during rename: {change.destination}")
    except (OSError, ValueError):
        errors = []
        for marker in reversed(markers):
            try:
                marker.unlink()
            except OSError as error:
                errors.append(str(error))
        for change in reversed(moved):
            try:
                if change.source.exists():
                    raise ValueError(f"Original path was recreated: {change.source}")
                change.destination.rename(change.source)
            except (OSError, ValueError) as error:
                errors.append(str(error))
        if errors:
            raise RuntimeError("Migration rollback needs manual review: " + "; ".join(errors)) from None
        raise
