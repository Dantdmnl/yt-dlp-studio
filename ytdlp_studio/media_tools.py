"""Discover and install a complete FFmpeg/ffprobe pair in the project venv."""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

BUILD_URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"


def find_installed_tools() -> tuple[Path, Path] | None:
    suffix = ".exe" if os.name == "nt" else ""
    local = Path(sys.prefix) / "tools" / "ffmpeg"
    candidates = [local / f"ffmpeg{suffix}"]
    on_path = shutil.which("ffmpeg")
    if on_path:
        candidates.append(Path(on_path))
    for ffmpeg in candidates:
        ffprobe = ffmpeg.with_name(f"ffprobe{suffix}")
        if ffmpeg.is_file() and ffprobe.is_file():
            return ffmpeg, ffprobe
    return None


def verify_tools(tools: tuple[Path, Path]) -> None:
    for executable in tools:
        result = subprocess.run([str(executable), "-version"], capture_output=True, timeout=10,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        if result.returncode:
            raise RuntimeError(f"{executable.name} could not run.")


def install_tools() -> tuple[Path, Path]:
    existing = find_installed_tools()
    if existing:
        try:
            verify_tools(existing)
            print("FFmpeg and ffprobe are ready.", flush=True)
            return existing
        except (OSError, RuntimeError, subprocess.TimeoutExpired):
            print("Existing media tools failed validation; installing a project-local pair.", flush=True)
    if os.name != "nt" or sys.maxsize <= 2**32:
        raise RuntimeError("Install FFmpeg and ffprobe for your platform and put both in the same PATH folder.")
    if sys.prefix == sys.base_prefix:
        raise RuntimeError("Run this installer with the project's .venv Python.")
    destination = Path(sys.prefix) / "tools" / "ffmpeg"
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="studio-media-") as temporary:
        staging = Path(temporary)
        archive = staging / "ffmpeg.zip"
        print("Downloading FFmpeg + ffprobe from gyan.dev (about 110 MB)...", flush=True)
        with urllib.request.urlopen(BUILD_URL + ".sha256", timeout=60) as response:
            checksum = response.read(4096).decode("ascii").split()[0]
        if not re.fullmatch(r"[a-fA-F0-9]{64}", checksum):
            raise RuntimeError("The download server returned an invalid checksum.")
        digest = hashlib.sha256()
        downloaded = 0
        reported = 0
        with urllib.request.urlopen(BUILD_URL, timeout=60) as response, archive.open("wb") as stream:
            while chunk := response.read(1024 * 1024):
                stream.write(chunk)
                digest.update(chunk)
                downloaded += len(chunk)
                if downloaded - reported >= 10 * 1024 * 1024:
                    print(f"  Downloaded {downloaded // (1024 * 1024)} MB", flush=True)
                    reported = downloaded
        if digest.hexdigest().casefold() != checksum.casefold():
            raise RuntimeError("FFmpeg checksum verification failed. Run setup again.")
        print("Checksum verified. Extracting media tools...", flush=True)
        names = ("ffmpeg.exe", "ffprobe.exe")
        with zipfile.ZipFile(archive) as package:
            for name in names:
                entries = [entry for entry in package.infolist()
                           if PurePosixPath(entry.filename).parts[-2:] == ("bin", name)]
                if len(entries) != 1:
                    raise RuntimeError(f"Expected exactly one {name} in the download.")
                with package.open(entries[0]) as source, (staging / name).open("wb") as target:
                    shutil.copyfileobj(source, target)
        verify_tools((staging / names[0], staging / names[1]))
        for name in names:
            partial = destination / (name + ".installing")
            try:
                shutil.copy2(staging / name, partial)
                partial.replace(destination / name)
            finally:
                partial.unlink(missing_ok=True)
    print(f"FFmpeg and ffprobe installed at {destination}", flush=True)
    return destination / "ffmpeg.exe", destination / "ffprobe.exe"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install", action="store_true")
    args = parser.parse_args()
    try:
        tools = install_tools() if args.install else find_installed_tools()
        if not tools:
            raise RuntimeError("FFmpeg + ffprobe are missing. Run .\\install.ps1.")
        verify_tools(tools)
        return 0
    except Exception as exc:
        print(f"Media tools setup failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
