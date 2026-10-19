"""Build a source release from an explicit runtime/documentation allowlist."""

from __future__ import annotations

import argparse
import hashlib
from datetime import date
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ytdlp_studio.version import APP_VERSION  # noqa: E402

RUNTIME_FILES = ("ytdlp_wrapper.py", "media_tools.py", "Setup.cmd", "install.ps1", "run-gui.ps1",
                 "yt-dlp-studio.vbs", "requirements.txt", "requirements-spotify.txt",
                 "README.md", "CHANGELOG.md", "LICENSE")


def release_notes(root: Path = ROOT) -> str:
    lines = (root / "CHANGELOG.md").read_text(encoding="utf-8").splitlines()
    prefix = f"## {APP_VERSION} - "
    headings = [index for index, line in enumerate(lines) if line.startswith(prefix)]
    if len(headings) != 1:
        raise ValueError(f"Expected one changelog section for {APP_VERSION}.")
    start = headings[0]
    try:
        date.fromisoformat(lines[start][len(prefix):].strip())
    except ValueError as exc:
        raise ValueError("Date the changelog section before publishing a release.") from exc
    end = next((index for index in range(start + 1, len(lines)) if lines[index].startswith("## ")), len(lines))
    changes = "\n".join(lines[start + 1:end]).strip()
    if not changes:
        raise ValueError("The release changelog is empty.")
    return (
        "## Get Started\n\n"
        "This is a Windows source distribution, not a standalone executable.\n\n"
        "Prerelease for validation. Artist downloads remain subject to upstream matching and extraction availability. "
        "Do not assume every recording in a discovered catalogue can be downloaded.\n\n"
        "1. Install Python 3.10+ for Windows (64-bit).\n"
        f"2. Download `yt-dlp-studio-v{APP_VERSION}.zip` below and extract the whole zip.\n"
        "3. Double-click **Setup.cmd**; choose optional Spotify support. Setup installs dependencies, "
        "FFmpeg/ffprobe and shortcuts.\n"
        "4. Launch from a shortcut or **yt-dlp-studio.vbs**, without a lingering terminal.\n\n"
        "For YouTube, also have Deno or Node.js 22+ on PATH; interactive setup can optionally install Deno with WinGet. "
        "Internet access is required for setup. "
        "The README and user guides are included in the zip.\n\n"
        f"## Changes In {APP_VERSION}\n\n{changes}\n\n"
        "## Integrity\n\n"
        "The matching `.zip.sha256` asset contains the ZIP's SHA-256 digest. "
        "Verify it with `Get-FileHash <zip-path> -Algorithm SHA256`. "
        "The checksum detects corruption; the source distribution is not code-signed.\n"
    )


def release_files(root: Path) -> list[Path]:
    files = [root / name for name in RUNTIME_FILES]
    files += sorted((root / "ytdlp_studio").glob("*.py"))
    files += sorted((root / "docs").glob("*.md"))
    files += sorted((root / "docs").glob("*.png"))
    required = {root / "ytdlp_studio" / name for name in
                ("__init__.py", "__main__.py", "version.py", "cli.py", "gui.py", "backend.py",
                 "catalogue.py", "browser.py", "inspection.py", "formats.py", "media_tools.py", "processes.py", "download_host.py", "library.py")}
    required.update(root / "docs" / name for name in ("user-guide.md", "cli.md", "troubleshooting.md"))
    if not required.issubset(files):
        raise ValueError("The runtime package or user documentation is incomplete.")
    for path in files:
        if not path.is_file() or path.is_symlink() or root.resolve() not in path.resolve().parents:
            raise ValueError(f"Missing or unsafe release file: {path}")
    return files


def build_release(output: Path, tag: str | None = None, root: Path = ROOT) -> Path:
    if tag is not None and tag != f"v{APP_VERSION}":
        raise ValueError(f"Tag {tag} does not match application version v{APP_VERSION}.")
    files = release_files(root)
    output.mkdir(parents=True, exist_ok=True)
    archive = output / f"yt-dlp-studio-v{APP_VERSION}.zip"
    temporary = archive.with_suffix(".zip.tmp")
    checksum = archive.with_suffix(".zip.sha256")
    temporary_checksum = checksum.with_suffix(".sha256.tmp")
    try:
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as package:
            for path in files:
                package.write(path, arcname=path.relative_to(root).as_posix())
        digest = hashlib.sha256(temporary.read_bytes()).hexdigest()
        temporary_checksum.write_text(f"{digest}  {archive.name}\n", encoding="ascii")
        temporary.replace(archive)
        temporary_checksum.replace(checksum)
    finally:
        temporary.unlink(missing_ok=True)
        temporary_checksum.unlink(missing_ok=True)
    return archive


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    parser.add_argument("--tag")
    parser.add_argument("--notes", type=Path, help="Write dated, version-specific GitHub release notes.")
    args = parser.parse_args()
    try:
        notes = release_notes() if args.notes else None
        print(build_release(args.output, args.tag))
        if args.notes:
            args.notes.parent.mkdir(parents=True, exist_ok=True)
            args.notes.write_text(notes, encoding="utf-8")
        return 0
    except (OSError, ValueError) as exc:
        print(f"Release packaging failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
