from __future__ import annotations

import argparse
import hashlib
import filecmp
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Iterable

try:
    import tkinter as tk
    from tkinter import filedialog, font as tkfont, messagebox, ttk
except ImportError:  # pragma: no cover - only relevant on stripped Python builds
    tk = None
    tkfont = None
    ttk = None


APP_NAME = "yt-dlp Studio"
APP_VERSION = "0.1.0"
CONFIG_DIR = Path(os.environ.get("APPDATA", Path.home() / ".config")) / "ytdlp-studio"
CONFIG_FILE = CONFIG_DIR / "settings.json"
DEFAULT_FILENAME_TEMPLATE = "%(artist,creator,uploader|Unknown)s - %(title)s.%(ext)s"


AUDIO_PRESETS = {
    "MP3 320 kbps": {"format": "bestaudio/best", "codec": "mp3", "quality": "320"},
    "MP3 192 kbps": {"format": "bestaudio/best", "codec": "mp3", "quality": "192"},
    "M4A best": {"format": "bestaudio[ext=m4a]/bestaudio/best", "codec": "m4a", "quality": "0"},
    "Opus best": {"format": "bestaudio[ext=opus]/bestaudio/best", "codec": "opus", "quality": "0"},
    "FLAC": {"format": "bestaudio/best", "codec": "flac", "quality": "0"},
}

VIDEO_PRESETS = {
    "Best video + audio": "bv*+ba/best",
    "1080p MP4": "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080][ext=mp4]/best",
    "720p MP4": "bv*[height<=720][ext=mp4]+ba[ext=m4a]/b[height<=720][ext=mp4]/best",
    "Small compatible MP4": "bv*[height<=480][ext=mp4]+ba[ext=m4a]/b[height<=480][ext=mp4]/best",
}

MEDIA_SUFFIXES = {
    ".aac",
    ".flac",
    ".m4a",
    ".mka",
    ".mkv",
    ".mov",
    ".mp3",
    ".mp4",
    ".ogg",
    ".opus",
    ".wav",
    ".webm",
}
THUMBNAIL_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
URL_PATTERN = re.compile(r"https?://[^\s<>'\")\]]+", re.IGNORECASE)
DUPLICATE_REVIEW_DIR = "_duplicates_review"
DUPLICATE_COPY_RE = re.compile(r"(?i)(?:\s*[-_ ]\s*)?(?:copy|\(\d+\)|\[\d+\]|-\d+)$")
DUPLICATE_TITLE_DROP_RE = re.compile(
    r"(?i)\b(?:official|music|video|audio|lyrics?|visualizer|remaster(?:ed)?|hd|4k)\b"
)
DUPLICATE_COVER_TAIL_RE = re.compile(
    r"(?i)\b(?:x\s+)?(?:metal|rock|pop punk|punk rock|cinematic|acoustic|piano|full band)?\s*cover\b.*$"
)
DUPLICATE_VERSION_WORD_RE = re.compile(
    r"(?i)\b(?:metal|rock|cinematic|acoustic|piano|orchestral|live|version|remix|remastered|full band)\b"
)
DUPLICATE_WEAK_TITLES = {
    "cover",
    "covers",
    "metal cover",
    "rock cover",
    "official video",
    "official music video",
    "music video",
    "audio",
    "lyrics",
}


@dataclass
class Settings:
    output_dir: str = str(Path.home() / "Music" / "yt-dlp")
    mode: str = "audio"
    audio_preset: str = "MP3 320 kbps"
    video_preset: str = "1080p MP4"
    filename_template: str = DEFAULT_FILENAME_TEMPLATE
    embed_thumbnail: bool = True
    embed_metadata: bool = True
    download_subtitles: bool = False
    playlist: bool = True
    sponsorblock: bool = False
    keep_original: bool = False
    write_archive: bool = True
    restrict_filenames: bool = False
    sync_enabled: bool = False
    sync_dir: str = ""
    sync_match_output_folder: bool = True
    auto_dedupe_after_download: bool = False
    dedupe_exact_only: bool = False
    cookies_file: str = ""
    extra_args: str = ""


SETTINGS_FIELDS = set(Settings.__dataclass_fields__)


@dataclass
class DownloadJob:
    urls: list[str]
    settings: Settings
    status: str = "Queued"
    command: list[str] = field(default_factory=list)


@dataclass
class DuplicatePlan:
    groups: list[tuple[Path, list[Path]]]
    scanned: int


def subprocess_window_kwargs() -> dict[str, int]:
    if os.name != "nt":
        return {}
    return {"creationflags": subprocess.CREATE_NO_WINDOW}


def normalize_settings(settings: Settings) -> Settings:
    if settings.mode not in {"audio", "video"}:
        settings.mode = "audio"
    if settings.audio_preset not in AUDIO_PRESETS:
        settings.audio_preset = "MP3 320 kbps"
    if settings.video_preset not in VIDEO_PRESETS:
        settings.video_preset = "1080p MP4"
    if not settings.filename_template.strip():
        settings.filename_template = DEFAULT_FILENAME_TEMPLATE
    return settings


def load_settings() -> Settings:
    try:
        if not CONFIG_FILE.exists():
            return Settings()
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        known = {field.name for field in Settings.__dataclass_fields__.values()}
        return normalize_settings(Settings(**{key: value for key, value in data.items() if key in known}))
    except (OSError, TypeError, json.JSONDecodeError):
        return Settings()


def save_settings(settings: Settings) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(asdict(settings), indent=2), encoding="utf-8")


def find_ytdlp_command() -> list[str]:
    probe = subprocess.run(
        [sys.executable, "-m", "yt_dlp", "--version"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
        **subprocess_window_kwargs(),
    )
    if probe.returncode == 0:
        return [sys.executable, "-m", "yt_dlp"]

    exe = shutil.which("yt-dlp")
    return [exe] if exe else [sys.executable, "-m", "yt_dlp"]


def find_ffmpeg_location() -> str | None:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe

    try:
        import imageio_ffmpeg

        bundled = imageio_ffmpeg.get_ffmpeg_exe()
        if bundled and Path(bundled).exists():
            return bundled
    except (ImportError, RuntimeError, OSError):
        pass

    return None


def find_js_runtime() -> tuple[str, str] | None:
    for name in ("deno", "node", "qjs"):
        exe = shutil.which(name)
        if exe:
            key = "quickjs" if name == "qjs" else name
            return key, exe

    codex_node = Path.home() / ".cache" / "codex-runtimes" / "codex-primary-runtime" / "dependencies" / "node" / "bin" / "node.exe"
    if codex_node.exists():
        return "node", str(codex_node)

    return None


def get_ytdlp_version() -> str | None:
    command = find_ytdlp_command()
    probe = subprocess.run(
        command + ["--version"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
        **subprocess_window_kwargs(),
    )
    return probe.stdout.strip() if probe.returncode == 0 else None


def check_environment() -> list[str]:
    messages: list[str] = []
    ytdlp_command = find_ytdlp_command()
    ytdlp_version = get_ytdlp_version()
    ffmpeg_location = find_ffmpeg_location()
    js_runtime = find_js_runtime()

    if ytdlp_version:
        messages.append(f"yt-dlp: {subprocess.list2cmdline(ytdlp_command)} ({ytdlp_version})")
    else:
        messages.append("yt-dlp: missing. Run .\\install.ps1")

    if ffmpeg_location:
        messages.append(f"ffmpeg: {ffmpeg_location}")
    else:
        messages.append("ffmpeg: missing. Run .\\install.ps1 to install the bundled project-local FFmpeg.")

    if js_runtime:
        messages.append(f"js runtime: {js_runtime[0]} at {js_runtime[1]}")
    else:
        messages.append("js runtime: missing. YouTube may return 403. Install Deno or Node 22+.")

    return messages


def split_extra_args(raw: str) -> list[str]:
    if not raw.strip():
        return []
    try:
        import shlex

        return shlex.split(raw, posix=False)
    except ValueError:
        return raw.split()


def cli_output(text: str) -> None:
    try:
        print(text, end="")
    except UnicodeEncodeError:
        sys.stdout.flush()
        encoded = text.encode(sys.stdout.encoding or "utf-8", errors="replace")
        sys.stdout.buffer.write(encoded)
        sys.stdout.flush()


def extract_urls(text: str) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for match in URL_PATTERN.findall(text):
        url = match.rstrip(".,;")
        if url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def normalize_duplicate_key(path: Path) -> str:
    metadata_key = metadata_duplicate_key(path)
    if metadata_key:
        return metadata_key
    return normalize_duplicate_text(path.stem)


def duplicate_keys(path: Path) -> list[tuple[str, bool]]:
    keys: dict[str, bool] = {}

    metadata_key = metadata_duplicate_key(path)
    if metadata_key:
        keys[metadata_key] = True

    full_key = normalize_duplicate_text(path.stem)
    if full_key:
        keys.setdefault(full_key, True)

    primary_titles = set()
    metadata_title = metadata_title_key(path)
    if metadata_title:
        primary_titles.add(metadata_title)

    title_candidates = filename_title_candidates(path)
    if title_candidates:
        primary_titles.add(title_candidates[0])

    for key in primary_titles:
        if is_useful_duplicate_title(key):
            keys[key] = True

    for key in title_candidates[1:]:
        if is_useful_duplicate_title(key):
            keys.setdefault(key, False)

    return list(keys.items())


def normalize_duplicate_text(value: str) -> str:
    text = value.casefold()
    text = re.sub(r"\[[^\]]*]", "", text)
    text = re.sub(r"\([^)]*(?:official|lyrics?|audio|video|visualizer|hd|4k|remaster|cover)[^)]*\)", "", text)
    text = DUPLICATE_COPY_RE.sub("", text)
    text = re.sub(r"\b(?:feat|ft|featuring)\b\.?", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def normalize_track_title_text(value: str) -> str:
    text = value
    text = re.sub(r"@[A-Za-z0-9_.-]+", " ", text)
    text = re.sub(r"\[[^\]]*]", " ", text)
    text = re.sub(r"\([^)]*(?:official|lyrics?|audio|video|visualizer|hd|4k|remaster)[^)]*\)", " ", text)
    text = DUPLICATE_COVER_TAIL_RE.sub(" ", text)
    text = re.sub(r"(?i)\b(?:feat|ft|featuring)\b\.?.*$", " ", text)
    text = re.sub(r"(?i)\bby\b.*$", " ", text)
    text = re.sub(r"(?i)\bx\s+(?:metal|rock|cinematic|acoustic|piano|cover).*$", " ", text)
    text = re.sub(r"(?i)\b(?:metal|rock|cinematic|acoustic|piano)\s+version\b", " ", text)
    return normalize_duplicate_text(text)


def is_useful_duplicate_title(value: str) -> bool:
    if not value or value in DUPLICATE_WEAK_TITLES:
        return False
    words = value.split()
    if len(words) > 8:
        return False
    if len(value) < 4 and not any(char.isdigit() for char in value):
        return False
    if len(words) <= 2 and DUPLICATE_VERSION_WORD_RE.fullmatch(value):
        return False
    return True


def filename_title_candidates(path: Path) -> list[str]:
    raw_parts = [part.strip() for part in re.split(r"\s+-\s+", path.stem) if part.strip()]
    if not raw_parts:
        return []

    parts = raw_parts[1:] if len(raw_parts) > 1 else raw_parts
    candidates: list[tuple[str, int]] = []
    for index, part in enumerate(parts):
        normalized = normalize_track_title_text(part)
        if is_useful_duplicate_title(normalized) and normalized not in {candidate for candidate, _index in candidates}:
            candidates.append((normalized, index))

    if not candidates:
        fallback = normalize_track_title_text(path.stem)
        if is_useful_duplicate_title(fallback):
            candidates.append((fallback, 0))

    return [candidate for candidate, _index in sorted(candidates, key=title_candidate_score, reverse=True)]


def title_candidate_score(candidate: tuple[str, int]) -> tuple[int, int, int, int]:
    value, index = candidate
    version_penalty = 1 if DUPLICATE_VERSION_WORD_RE.search(value) else 0
    return (-version_penalty, index, -abs(len(value.split()) - 3), len(value))


def metadata_title_key(path: Path) -> str:
    try:
        from mutagen import File as MutagenFile
    except ImportError:
        return ""

    try:
        audio = MutagenFile(path, easy=True)
    except Exception:
        return ""
    if not audio:
        return ""

    title = first_tag(audio, "title")
    return normalize_track_title_text(title) if title else ""


def metadata_duplicate_key(path: Path) -> str:
    try:
        from mutagen import File as MutagenFile
    except ImportError:
        return ""

    try:
        audio = MutagenFile(path, easy=True)
    except Exception:
        return ""
    if not audio:
        return ""

    title = first_tag(audio, "title")
    artist = first_tag(audio, "artist") or first_tag(audio, "albumartist")
    if not title:
        return ""

    normalized_title = normalize_duplicate_text(title)
    normalized_artist = normalize_duplicate_text(artist)
    if not normalized_title:
        return ""
    return f"{normalized_artist} {normalized_title}".strip()


def first_tag(audio: object, key: str) -> str:
    try:
        values = audio.get(key, [])
    except AttributeError:
        return ""
    if not values:
        return ""
    return str(values[0])


def file_hash(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def duplicate_score(path: Path) -> tuple[int, int, int, int, int, float, str]:
    suffix_rank = {
        ".flac": 7,
        ".wav": 6,
        ".m4a": 5,
        ".mp3": 4,
        ".opus": 3,
        ".ogg": 2,
        ".webm": 1,
        ".mp4": 1,
    }.get(path.suffix.lower(), 0)
    copy_penalty = 1 if DUPLICATE_COPY_RE.search(path.stem) else 0
    metadata_rank = 1 if metadata_title_key(path) else 0
    noisy_name_penalty = len(
        re.findall(r"(?i)(?:\bofficial\b|\bcover\b|\bvideo\b|\blyrics?\b|@[A-Za-z0-9_.-]+)", path.stem)
    )
    stat = path.stat()
    return (
        suffix_rank,
        metadata_rank,
        -noisy_name_penalty,
        -len(path.stem),
        -copy_penalty,
        stat.st_size,
        path.name,
    )


def build_duplicate_plan(output_dir: str, exact_only: bool = False) -> DuplicatePlan:
    root = Path(output_dir).expanduser()
    if not root.exists():
        return DuplicatePlan(groups=[], scanned=0)

    candidates = [
        path
        for path in root.rglob("*")
        if path.is_file()
        and path.suffix.lower() in MEDIA_SUFFIXES
        and DUPLICATE_REVIEW_DIR not in path.parts
    ]
    groups: list[tuple[Path, list[Path]]] = []

    if exact_only:
        buckets: dict[tuple[int, str], list[Path]] = {}
        for path in candidates:
            try:
                buckets.setdefault((path.stat().st_size, file_hash(path)), []).append(path)
            except OSError:
                continue
        for bucket in buckets.values():
            if len(bucket) < 2:
                continue
            ordered = sorted(bucket, key=duplicate_score, reverse=True)
            groups.append((ordered[0], ordered[1:]))
    else:
        buckets: dict[tuple[int, str], list[tuple[Path, bool]]] = {}
        for path in candidates:
            for key, primary in duplicate_keys(path):
                buckets.setdefault((0, key), []).append((path, primary))

        seen_group_keys: set[frozenset[Path]] = set()
        for bucket_items in buckets.values():
            bucket = list(dict.fromkeys(path for path, _primary in bucket_items))
            if len(bucket) < 2:
                continue
            primary_count = sum(1 for _path, primary in bucket_items if primary)
            if primary_count == 0:
                continue
            if len(bucket) > 4 and len(bucket) != primary_count:
                continue
            group_key = frozenset(bucket)
            if group_key in seen_group_keys:
                continue
            seen_group_keys.add(group_key)
            ordered = sorted(bucket, key=duplicate_score, reverse=True)
            groups.append((ordered[0], ordered[1:]))

    return DuplicatePlan(groups=groups, scanned=len(candidates))


def unique_review_path(review_root: Path, relative: Path) -> Path:
    target = review_root / relative
    if not target.exists():
        return target
    stem = target.stem
    suffix = target.suffix
    for index in range(2, 10_000):
        candidate = target.with_name(f"{stem} ({index}){suffix}")
        if not candidate.exists():
            return candidate
    raise FileExistsError(f"Could not find a unique review path for {relative}")


def move_matching_sync_duplicate(root: Path, duplicate: Path, sync_root: Path, output: Callable[[str], None]) -> None:
    try:
        relative = duplicate.relative_to(root)
    except ValueError:
        return
    sync_duplicate = sync_root / relative
    if not sync_duplicate.exists() or not sync_duplicate.is_file():
        return
    review_root = sync_root / DUPLICATE_REVIEW_DIR
    destination = unique_review_path(review_root, relative)
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(sync_duplicate), str(destination))
        output(f"  Sync duplicate moved to: {destination.relative_to(sync_root)}\n")
    except OSError as exc:
        output(f"  Sync duplicate warning for {relative}: {exc}\n")


def run_deduplicator(
    output_dir: str,
    output: Callable[[str], None],
    apply: bool = False,
    exact_only: bool = False,
    sync_dir: str | None = None,
) -> int:
    root = Path(output_dir).expanduser()
    plan = build_duplicate_plan(output_dir, exact_only=exact_only)
    output(f"Deduplicator scanned {plan.scanned} media file(s).\n")
    if not plan.groups:
        output("No duplicates found.\n")
        return 0

    review_root = root / DUPLICATE_REVIEW_DIR
    total_duplicates = sum(len(duplicates) for _keeper, duplicates in plan.groups)
    output(f"Found {total_duplicates} duplicate candidate(s) in {len(plan.groups)} group(s).\n")

    for keeper, duplicates in plan.groups:
        output(f"Keep: {keeper.relative_to(root)}\n")
        for duplicate in duplicates:
            output(f"  Duplicate: {duplicate.relative_to(root)}\n")
            if apply:
                destination = unique_review_path(review_root, duplicate.relative_to(root))
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(duplicate), str(destination))
                output(f"  Moved to: {destination.relative_to(root)}\n")
                if sync_dir:
                    move_matching_sync_duplicate(root, duplicate, Path(sync_dir).expanduser(), output)

    if not apply:
        output("Dry run only. Use Apply Deduplication or --dedupe-apply to move duplicates.\n")
    return total_duplicates


def build_command(urls: Iterable[str], settings: Settings) -> list[str]:
    output_path = Path(settings.output_dir).expanduser()
    archive_path = output_path / "download-archive.txt"
    command = find_ytdlp_command()

    command += [
        "--newline",
        "--ignore-errors",
        "--no-overwrites",
        "-P",
        str(output_path),
        "-o",
        settings.filename_template,
    ]

    ffmpeg_location = find_ffmpeg_location()
    if ffmpeg_location:
        command += ["--ffmpeg-location", ffmpeg_location]

    js_runtime = find_js_runtime()
    if js_runtime:
        command += ["--js-runtimes", f"{js_runtime[0]}:{js_runtime[1]}"]

    if settings.mode == "audio":
        preset = AUDIO_PRESETS[settings.audio_preset]
        command += [
            "-f",
            preset["format"],
            "-x",
            "--audio-format",
            preset["codec"],
            "--audio-quality",
            preset["quality"],
        ]
    else:
        command += ["-f", VIDEO_PRESETS[settings.video_preset], "--merge-output-format", "mp4"]

    if settings.embed_metadata:
        command.append("--embed-metadata")
    if settings.embed_thumbnail and settings.mode == "audio":
        command += ["--embed-thumbnail", "--convert-thumbnails", "jpg"]
    if settings.download_subtitles:
        command += ["--write-subs", "--write-auto-subs", "--sub-langs", "en.*,nl.*,de.*"]
    if not settings.playlist:
        command.append("--no-playlist")
    if settings.sponsorblock:
        command += ["--sponsorblock-remove", "sponsor,intro,outro,selfpromo,preview,filler"]
    if settings.keep_original:
        command.append("--keep-video")
    if settings.write_archive:
        command += ["--download-archive", str(archive_path)]
    if settings.restrict_filenames:
        command.append("--restrict-filenames")
    if settings.cookies_file:
        command += ["--cookies", settings.cookies_file]

    command += split_extra_args(settings.extra_args)
    command += list(urls)
    return command


def snapshot_output_files(settings: Settings) -> dict[Path, float]:
    output_path = Path(settings.output_dir).expanduser()
    if not output_path.exists():
        return {}
    return {path: path.stat().st_mtime for path in output_path.rglob("*") if path.is_file()}


def cleanup_orphan_thumbnails(settings: Settings, before: dict[Path, float], output: Callable[[str], None]) -> None:
    output_path = Path(settings.output_dir).expanduser()
    if not output_path.exists():
        return

    media_stems = {
        path.with_suffix("")
        for path in output_path.rglob("*")
        if path.is_file() and path.suffix.lower() in MEDIA_SUFFIXES
    }
    removed = 0
    run_started_at = time.time() - 60 * 60

    for path in output_path.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in THUMBNAIL_SUFFIXES:
            continue
        if path in before and path.stat().st_mtime <= before[path]:
            continue
        if path.with_suffix("") in media_stems:
            continue
        if path.stat().st_mtime < run_started_at:
            continue
        try:
            path.unlink()
            removed += 1
            output(f"Removed orphan thumbnail after failed download: {path.name}\n")
        except OSError as exc:
            output(f"Could not remove orphan thumbnail {path.name}: {exc}\n")

    if removed:
        output(f"Cleaned {removed} orphan thumbnail file(s).\n")


def should_sync_file(path: Path) -> bool:
    skipped_suffixes = {".part", ".ytdl", ".tmp", ".temp"}
    skipped_names = {"download-archive.txt"}
    return (
        path.name.lower() not in skipped_names
        and not any(part.endswith(".part") for part in path.parts)
        and path.suffix.lower() not in skipped_suffixes
    )


def resolve_sync_target(settings: Settings) -> Path:
    source = Path(settings.output_dir).expanduser()
    target_root = Path(settings.sync_dir).expanduser()
    if not settings.sync_match_output_folder:
        return target_root
    if target_root.name.casefold() == source.name.casefold():
        return target_root
    if target_root.parent.name.casefold() == source.parent.name.casefold():
        return target_root.parent / source.name
    return target_root / source.name


def sync_output_dir(settings: Settings, output: Callable[[str], None]) -> int:
    if not settings.sync_enabled or not settings.sync_dir.strip():
        return 0

    source = Path(settings.output_dir).expanduser()
    target = resolve_sync_target(settings)
    if source == target:
        output("Sync skipped: local and sync folders are the same.\n")
        return 0
    if not source.exists():
        output(f"Sync skipped: local output folder does not exist: {source}\n")
        return 1

    copied = 0
    skipped = 0
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        output(f"Sync failed: could not create {target}: {exc}\n")
        return 1
    output(f"Syncing completed files from {source} to {target}\n")

    for source_file in source.rglob("*"):
        if not source_file.is_file() or not should_sync_file(source_file):
            continue
        relative = source_file.relative_to(source)
        target_file = target / relative
        try:
            target_file.parent.mkdir(parents=True, exist_ok=True)
            if target_file.exists() and filecmp.cmp(source_file, target_file, shallow=False):
                skipped += 1
                continue
            shutil.copy2(source_file, target_file)
            copied += 1
            output(f"Synced: {relative}\n")
        except OSError as exc:
            output(f"Sync warning for {relative}: {exc}\n")

    output(f"Sync complete: {copied} copied, {skipped} already current.\n")
    return 0


def run_command(command: list[str], output: Callable[[str], None], stop_event: threading.Event | None = None) -> int:
    try:
        Path(command[command.index("-P") + 1]).mkdir(parents=True, exist_ok=True)
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            **subprocess_window_kwargs(),
        )
    except (OSError, ValueError) as exc:
        output(f"Could not start yt-dlp: {exc}\n")
        return 127

    assert process.stdout is not None
    for line in process.stdout:
        if stop_event and stop_event.is_set():
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
            output("Cancelled by user.\n")
            return 130
        output(line)

    return process.wait()


class StudioApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"{APP_NAME} {APP_VERSION}")
        self.minsize(1060, 720)
        self.geometry("1160x760")
        self.settings = load_settings()
        self.jobs: queue.Queue[DownloadJob] = queue.Queue()
        self.log_queue: queue.Queue[str] = queue.Queue()
        self.worker: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.current_job: DownloadJob | None = None
        self.detected_urls: list[str] = []
        self.last_clipboard_text = ""
        self._sync_field_updating = False

        self._configure_style()
        self._vars: dict[str, tk.Variable] = {}
        self._build_ui()
        self._load_vars()
        self._wire_setting_traces()
        self.apply_matched_sync_field()
        environment = check_environment()
        self.environment_var.set(self._environment_summary(environment))
        self._append_log("Environment:\n" + "\n".join(environment) + "\n\n")
        self.scan_clipboard(auto=True)
        self._poll_log_queue()
        self._poll_clipboard()
        self._refresh_status()
        self.bind("<Control-v>", lambda _event: self.scan_clipboard(auto=False))

    def _configure_style(self) -> None:
        self.configure(background="#f3f4f6")
        default_font = tkfont.Font(family="Segoe UI", size=10)
        title_font = tkfont.Font(family="Segoe UI", size=15, weight="bold")
        section_font = tkfont.Font(family="Segoe UI", size=10, weight="bold")
        self.option_add("*Font", default_font)
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TFrame", background="#f3f4f6")
        style.configure("Surface.TFrame", background="#ffffff")
        style.configure("TLabel", background="#f3f4f6", foreground="#111827")
        style.configure("Surface.TLabel", background="#ffffff", foreground="#111827")
        style.configure("Muted.TLabel", background="#ffffff", foreground="#6b7280")
        style.configure("Title.TLabel", background="#ffffff", foreground="#111827", font=title_font)
        style.configure("Badge.TLabel", background="#e0f2fe", foreground="#075985", padding=(8, 3))
        style.configure("TLabelframe", background="#f3f4f6", bordercolor="#d1d5db", relief="solid")
        style.configure("TLabelframe.Label", background="#f3f4f6", foreground="#374151", font=section_font)
        style.configure("TButton", padding=(10, 6))
        style.configure("Accent.TButton", padding=(12, 8), foreground="#ffffff", background="#2563eb")
        style.map("Accent.TButton", background=[("active", "#1d4ed8"), ("pressed", "#1e40af")])

    def _environment_summary(self, messages: list[str]) -> str:
        if any("missing" in message.casefold() for message in messages):
            return "Environment needs attention"
        return "Environment ready"

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        header = ttk.Frame(self, style="Surface.TFrame", padding=(14, 12))
        header.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 8))
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text=APP_NAME, style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            header,
            text="Download, sync, and review your music library without babysitting the terminal.",
            style="Muted.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(2, 0))
        self.environment_var = tk.StringVar(value="Checking environment...")
        ttk.Label(header, textvariable=self.environment_var, style="Badge.TLabel").grid(row=0, column=1, rowspan=2, sticky="e")

        toolbar = ttk.Frame(self, padding=(12, 10))
        toolbar.grid(row=1, column=0, sticky="ew")
        toolbar.columnconfigure(0, weight=1)

        intake = ttk.LabelFrame(toolbar, text="1. Links", padding=10)
        intake.grid(row=0, column=0, sticky="ew")
        intake.columnconfigure(0, weight=1)
        self.clipboard_var = tk.StringVar(value="Copy a YouTube, playlist, or music link and it will appear here.")
        ttk.Label(intake, textvariable=self.clipboard_var).grid(row=0, column=0, columnspan=2, sticky="w")
        self.url_listbox = tk.Listbox(intake, height=4, activestyle="none")
        self.url_listbox.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        list_actions = ttk.Frame(intake)
        list_actions.grid(row=1, column=1, sticky="ns", padx=(8, 0), pady=(6, 0))
        ttk.Button(list_actions, text="Add Clipboard", command=lambda: self.scan_clipboard(auto=False)).grid(row=0, column=0, sticky="ew")
        ttk.Button(list_actions, text="Remove", command=self.remove_selected_urls).grid(row=1, column=0, sticky="ew", pady=5)
        ttk.Button(list_actions, text="Clear", command=self.clear_urls).grid(row=2, column=0, sticky="ew")

        manual = ttk.Frame(intake)
        manual.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        manual.columnconfigure(0, weight=1)
        self.url_text = tk.Text(manual, height=2, wrap="word")
        self.url_text.grid(row=0, column=0, sticky="ew")
        ttk.Button(manual, text="Add Typed Links", command=self.add_typed_urls).grid(row=0, column=1, sticky="ns", padx=(8, 0))

        buttons = ttk.Frame(toolbar)
        buttons.grid(row=0, column=1, sticky="n", padx=(10, 0))
        ttk.Button(buttons, text="Start Download", style="Accent.TButton", command=self.add_job).grid(row=0, column=0, sticky="ew")
        ttk.Button(buttons, text="Preview Command", command=self.show_command).grid(row=1, column=0, sticky="ew", pady=6)
        ttk.Button(buttons, text="Stop Current Job", command=self.cancel_current).grid(row=2, column=0, sticky="ew")

        main = ttk.PanedWindow(self, orient="horizontal")
        main.grid(row=2, column=0, sticky="nsew", padx=12, pady=(0, 12))

        left = ttk.Frame(main, padding=8)
        right = ttk.Frame(main, padding=8)
        main.add(left, weight=1)
        main.add(right, weight=2)

        self._build_settings(left)
        self._build_log(right)

        status = ttk.Frame(self, padding=(12, 0, 12, 10))
        status.grid(row=3, column=0, sticky="ew")
        status.columnconfigure(0, weight=1)
        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(status, textvariable=self.status_var).grid(row=0, column=0, sticky="w")
        ttk.Button(status, text="Save Settings", command=self.save_from_ui).grid(row=0, column=1, sticky="e")

    def _build_settings(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(1, weight=1)

        self._vars = {
            "output_dir": tk.StringVar(),
            "output_subfolder": tk.StringVar(),
            "mode": tk.StringVar(),
            "audio_preset": tk.StringVar(),
            "video_preset": tk.StringVar(),
            "filename_template": tk.StringVar(),
            "embed_thumbnail": tk.BooleanVar(),
            "embed_metadata": tk.BooleanVar(),
            "download_subtitles": tk.BooleanVar(),
            "playlist": tk.BooleanVar(),
            "sponsorblock": tk.BooleanVar(),
            "keep_original": tk.BooleanVar(),
            "write_archive": tk.BooleanVar(),
            "restrict_filenames": tk.BooleanVar(),
            "sync_enabled": tk.BooleanVar(),
            "sync_dir": tk.StringVar(),
            "sync_match_output_folder": tk.BooleanVar(),
            "auto_dedupe_after_download": tk.BooleanVar(),
            "dedupe_exact_only": tk.BooleanVar(),
            "cookies_file": tk.StringVar(),
            "extra_args": tk.StringVar(),
        }

        row = 0
        ttk.Label(parent, text="Mode").grid(row=row, column=0, sticky="w")
        mode = ttk.Frame(parent)
        mode.grid(row=row, column=1, sticky="ew")
        ttk.Radiobutton(mode, text="Audio", value="audio", variable=self._vars["mode"]).grid(row=0, column=0)
        ttk.Radiobutton(mode, text="Video", value="video", variable=self._vars["mode"]).grid(row=0, column=1, padx=10)

        row += 1
        ttk.Label(parent, text="Audio preset").grid(row=row, column=0, sticky="w", pady=6)
        ttk.Combobox(parent, textvariable=self._vars["audio_preset"], values=list(AUDIO_PRESETS), state="readonly").grid(
            row=row, column=1, sticky="ew", pady=6
        )

        row += 1
        ttk.Label(parent, text="Video preset").grid(row=row, column=0, sticky="w", pady=6)
        ttk.Combobox(parent, textvariable=self._vars["video_preset"], values=list(VIDEO_PRESETS), state="readonly").grid(
            row=row, column=1, sticky="ew", pady=6
        )

        row += 1
        ttk.Label(parent, text="Local library").grid(row=row, column=0, sticky="w", pady=6)
        output = ttk.Frame(parent)
        output.grid(row=row, column=1, sticky="ew", pady=6)
        output.columnconfigure(0, weight=1)
        ttk.Entry(output, textvariable=self._vars["output_dir"]).grid(row=0, column=0, sticky="ew")
        ttk.Button(output, text="Browse", command=self.browse_output).grid(row=0, column=1, padx=(6, 0))
        row += 1
        ttk.Label(parent, text="Artist/project folder").grid(row=row, column=0, sticky="w", pady=(0, 6))
        output_folder = ttk.Frame(parent)
        output_folder.grid(row=row, column=1, sticky="ew", pady=(0, 6))
        output_folder.columnconfigure(0, weight=1)
        ttk.Entry(output_folder, textvariable=self._vars["output_subfolder"]).grid(row=0, column=0, sticky="ew")
        ttk.Button(output_folder, text="Create & Use", command=self.apply_output_subfolder).grid(row=0, column=1, padx=(6, 0))

        row += 1
        ttk.Label(parent, text="Filename").grid(row=row, column=0, sticky="w", pady=6)
        ttk.Entry(parent, textvariable=self._vars["filename_template"]).grid(row=row, column=1, sticky="ew", pady=6)

        row += 1
        ttk.Label(parent, text="Mirror to NAS/share").grid(row=row, column=0, sticky="w", pady=6)
        sync = ttk.Frame(parent)
        sync.grid(row=row, column=1, sticky="ew", pady=6)
        sync.columnconfigure(1, weight=1)
        ttk.Checkbutton(sync, text="On", variable=self._vars["sync_enabled"]).grid(row=0, column=0, sticky="w")
        ttk.Entry(sync, textvariable=self._vars["sync_dir"]).grid(row=0, column=1, sticky="ew", padx=(6, 0))
        ttk.Button(sync, text="Browse", command=self.browse_sync).grid(row=0, column=2, padx=(6, 0))
        ttk.Checkbutton(sync, text="Follow the local artist/project folder", variable=self._vars["sync_match_output_folder"]).grid(
            row=1, column=1, sticky="w", padx=(6, 0), pady=(5, 0)
        )

        row += 1
        ttk.Label(parent, text="Cookies").grid(row=row, column=0, sticky="w", pady=6)
        cookies = ttk.Frame(parent)
        cookies.grid(row=row, column=1, sticky="ew", pady=6)
        cookies.columnconfigure(0, weight=1)
        ttk.Entry(cookies, textvariable=self._vars["cookies_file"]).grid(row=0, column=0, sticky="ew")
        ttk.Button(cookies, text="File", command=self.browse_cookies).grid(row=0, column=1, padx=(6, 0))

        checks = ttk.LabelFrame(parent, text="Options", padding=8)
        checks.grid(row=row + 1, column=0, columnspan=2, sticky="ew", pady=12)
        checks.columnconfigure(0, weight=1)
        options = [
            ("embed_thumbnail", "Embed thumbnail"),
            ("embed_metadata", "Embed metadata"),
            ("download_subtitles", "Download subtitles"),
            ("playlist", "Allow playlists"),
            ("sponsorblock", "Remove sponsor segments"),
            ("keep_original", "Keep source files"),
            ("write_archive", "Download archive file"),
            ("restrict_filenames", "Filesystem-safe names"),
        ]
        for index, (name, label) in enumerate(options):
            ttk.Checkbutton(checks, text=label, variable=self._vars[name]).grid(
                row=index // 2, column=index % 2, sticky="w", padx=4, pady=3
            )

        row += 2
        ttk.Label(parent, text="Extra args").grid(row=row, column=0, sticky="w", pady=6)
        ttk.Entry(parent, textvariable=self._vars["extra_args"]).grid(row=row, column=1, sticky="ew", pady=6)

        row += 1
        dedupe = ttk.LabelFrame(parent, text="Library cleanup", padding=8)
        dedupe.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        dedupe.columnconfigure((0, 1), weight=1)
        ttk.Checkbutton(dedupe, text="Exact files only", variable=self._vars["dedupe_exact_only"]).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 6)
        )
        ttk.Checkbutton(dedupe, text="Review duplicates after successful downloads", variable=self._vars["auto_dedupe_after_download"]).grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(0, 6)
        )
        ttk.Button(dedupe, text="Scan Only", command=lambda: self.start_dedupe(apply=False)).grid(
            row=2, column=0, sticky="ew", padx=(0, 5)
        )
        ttk.Button(dedupe, text="Move to Review Folder", command=lambda: self.start_dedupe(apply=True)).grid(
            row=2, column=1, sticky="ew", padx=(5, 0)
        )

        row += 1
        actions = ttk.Frame(parent)
        actions.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        actions.columnconfigure((0, 1), weight=1)
        ttk.Button(actions, text="Scan Clipboard", command=lambda: self.scan_clipboard(auto=False)).grid(
            row=0, column=0, sticky="ew", padx=(0, 5)
        )
        ttk.Button(actions, text="Open Local Folder", command=self.open_output_dir).grid(row=0, column=1, sticky="ew", padx=(5, 0))

    def _build_log(self, parent: ttk.Frame) -> None:
        parent.rowconfigure(1, weight=1)
        parent.columnconfigure(0, weight=1)
        self.queue_var = tk.StringVar(value="Queue is empty")
        top = ttk.Frame(parent)
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(0, weight=1)
        ttk.Label(top, textvariable=self.queue_var).grid(row=0, column=0, sticky="w")
        ttk.Button(top, text="Copy Log", command=self.copy_log).grid(row=0, column=1, sticky="e", padx=(8, 0))
        ttk.Button(top, text="Clear Log", command=self.clear_log).grid(row=0, column=2, sticky="e", padx=(6, 0))
        self.log = tk.Text(parent, wrap="word", state="disabled", background="#111827", foreground="#e5e7eb")
        self.log.grid(row=1, column=0, sticky="nsew", pady=(8, 0))

    def _load_vars(self) -> None:
        for key, var in self._vars.items():
            if key in SETTINGS_FIELDS:
                var.set(getattr(self.settings, key))

    def _settings_from_ui(self) -> Settings:
        values = {key: var.get() for key, var in self._vars.items() if key in SETTINGS_FIELDS}
        return Settings(**values)

    def _wire_setting_traces(self) -> None:
        self._vars["output_dir"].trace_add("write", lambda *_args: self.apply_matched_sync_field())
        self._vars["sync_enabled"].trace_add("write", lambda *_args: self.apply_matched_sync_field())
        self._vars["sync_match_output_folder"].trace_add("write", lambda *_args: self.apply_matched_sync_field())

    def apply_matched_sync_field(self) -> None:
        if self._sync_field_updating:
            return
        try:
            settings = self._settings_from_ui()
            if settings.sync_enabled and settings.sync_dir.strip() and settings.sync_match_output_folder:
                resolved = str(resolve_sync_target(settings))
                if resolved != settings.sync_dir:
                    self._sync_field_updating = True
                    self._vars["sync_dir"].set(resolved)
                    self._sync_field_updating = False
        except (OSError, RuntimeError, tk.TclError):
            self._sync_field_updating = False

    def save_from_ui(self) -> None:
        self.settings = self._settings_from_ui()
        save_settings(self.settings)
        self.status_var.set(f"Settings saved to {CONFIG_FILE}")

    def start_dedupe(self, apply: bool) -> None:
        settings = self._settings_from_ui()
        output_dir = settings.output_dir
        exact_only = settings.dedupe_exact_only
        sync_dir = str(resolve_sync_target(settings)) if settings.sync_enabled and settings.sync_dir else None
        if apply:
            confirmed = messagebox.askyesno(
                APP_NAME,
                f"Move duplicate candidates from {output_dir} to {DUPLICATE_REVIEW_DIR}?",
            )
            if not confirmed:
                return
        threading.Thread(target=self._run_dedupe_worker, args=(output_dir, apply, exact_only, sync_dir), daemon=True).start()
        self.status_var.set("Deduplicator running...")

    def _run_dedupe_worker(self, output_dir: str, apply: bool, exact_only: bool, sync_dir: str | None) -> None:
        code = run_deduplicator(output_dir, self.log_queue.put, apply=apply, exact_only=exact_only, sync_dir=sync_dir)
        action = "moved" if apply else "found"
        self.log_queue.put(f"Deduplicator finished: {code} duplicate candidate(s) {action}.\n")

    def paste_urls(self) -> None:
        try:
            self.add_urls(extract_urls(self.clipboard_get()))
        except tk.TclError:
            pass

    def add_urls(self, urls: list[str]) -> int:
        added = 0
        for url in urls:
            if url in self.detected_urls:
                continue
            self.detected_urls.append(url)
            self.url_listbox.insert("end", url)
            added += 1
        self._update_clipboard_status(added)
        return added

    def add_typed_urls(self) -> None:
        urls = extract_urls(self.url_text.get("1.0", "end"))
        if not urls:
            messagebox.showwarning(APP_NAME, "No links found in the typed text.")
            return
        added = self.add_urls(urls)
        self.url_text.delete("1.0", "end")
        self.status_var.set(f"Added {added} typed link(s).")

    def scan_clipboard(self, auto: bool) -> None:
        try:
            text = self.clipboard_get()
        except tk.TclError:
            return
        if auto and text == self.last_clipboard_text:
            return
        self.last_clipboard_text = text
        urls = extract_urls(text)
        if not urls:
            if not auto:
                self.status_var.set("No links found on the clipboard.")
            return
        added = self.add_urls(urls)
        if added:
            self.status_var.set(f"Detected {added} new clipboard link(s).")
        elif not auto:
            self.status_var.set("Clipboard links are already in the list.")

    def remove_selected_urls(self) -> None:
        selected = list(self.url_listbox.curselection())
        if not selected:
            return
        for index in reversed(selected):
            del self.detected_urls[index]
            self.url_listbox.delete(index)
        self._update_clipboard_status(0)

    def clear_urls(self) -> None:
        self.detected_urls.clear()
        self.url_listbox.delete(0, "end")
        self._update_clipboard_status(0)

    def _update_clipboard_status(self, _added: int) -> None:
        count = len(self.detected_urls)
        if count:
            self.clipboard_var.set(f"{count} link(s) ready. Choose settings, then Download Now.")
        else:
            self.clipboard_var.set("Copy a YouTube, playlist, or music link and it will appear here.")

    def browse_output(self) -> None:
        selected = filedialog.askdirectory(initialdir=self._vars["output_dir"].get() or str(Path.home()))
        if selected:
            self._vars["output_dir"].set(selected)

    def apply_output_subfolder(self) -> None:
        name = self._vars["output_subfolder"].get().strip().strip("\\/")
        if not name:
            return
        base = Path(self._vars["output_dir"].get()).expanduser()
        output_dir = base / name
        output_dir.mkdir(parents=True, exist_ok=True)
        self._vars["output_dir"].set(str(output_dir))
        self._vars["output_subfolder"].set("")
        self.status_var.set(f"Output folder ready: {output_dir}")

    def browse_cookies(self) -> None:
        selected = filedialog.askopenfilename(filetypes=[("Cookies text", "*.txt"), ("All files", "*.*")])
        if selected:
            self._vars["cookies_file"].set(selected)

    def browse_sync(self) -> None:
        selected = filedialog.askdirectory(initialdir=self._vars["sync_dir"].get() or str(Path.home()))
        if selected:
            self._vars["sync_dir"].set(selected)
            self._vars["sync_enabled"].set(True)

    def add_job(self) -> None:
        typed_urls = extract_urls(self.url_text.get("1.0", "end"))
        if typed_urls:
            self.add_urls(typed_urls)
            self.url_text.delete("1.0", "end")
        urls = list(self.detected_urls)
        if not urls:
            self.scan_clipboard(auto=False)
            urls = list(self.detected_urls)
        if not urls:
            messagebox.showwarning(APP_NAME, "Copy or add one or more links first.")
            return
        self.settings = self._settings_from_ui()
        save_settings(self.settings)
        job = DownloadJob(urls=urls, settings=self.settings)
        job.command = build_command(job.urls, job.settings)
        self.jobs.put(job)
        self.clear_urls()
        self._append_log(f"Queued {len(urls)} URL(s).\n")
        self._refresh_status()
        self._start_worker()

    def show_command(self) -> None:
        typed_urls = extract_urls(self.url_text.get("1.0", "end"))
        urls = list(self.detected_urls) + [url for url in typed_urls if url not in self.detected_urls]
        if not urls:
            try:
                urls = extract_urls(self.clipboard_get())
            except tk.TclError:
                urls = []
        settings = self._settings_from_ui()
        command = build_command(urls or ["<URL>"], settings)
        preview = "\nCommand preview:\n" + subprocess.list2cmdline(command) + "\n"
        if settings.sync_enabled and settings.sync_dir:
            preview += f"Sync target preview:\n{resolve_sync_target(settings)}\n"
        self._append_log(preview + "\n")

    def open_output_dir(self) -> None:
        output_dir = Path(self._vars["output_dir"].get()).expanduser()
        output_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(output_dir)
        except AttributeError:
            subprocess.Popen(["xdg-open", str(output_dir)])

    def copy_log(self) -> None:
        text = self.log.get("1.0", "end-1c")
        self.clipboard_clear()
        self.clipboard_append(text)
        self.status_var.set("Log copied to clipboard.")

    def clear_log(self) -> None:
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")
        self.status_var.set("Log cleared.")

    def cancel_current(self) -> None:
        self.stop_event.set()
        self.status_var.set("Cancelling current job...")

    def _start_worker(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        self.stop_event.clear()
        self.worker = threading.Thread(target=self._worker_loop, daemon=True)
        self.worker.start()

    def _worker_loop(self) -> None:
        while not self.jobs.empty():
            self.current_job = self.jobs.get()
            self.log_queue.put(f"\nStarting: {len(self.current_job.urls)} URL(s)\n")
            self.log_queue.put(subprocess.list2cmdline(self.current_job.command) + "\n")
            before = snapshot_output_files(self.current_job.settings)
            code = run_command(self.current_job.command, self.log_queue.put, self.stop_event)
            self.log_queue.put(f"Finished with exit code {code}.\n")
            if code == 0 and not self.stop_event.is_set():
                sync_code = sync_output_dir(self.current_job.settings, self.log_queue.put)
                if sync_code:
                    self.log_queue.put(f"Sync finished with warnings: {sync_code}.\n")
                if self.current_job.settings.auto_dedupe_after_download:
                    sync_dir = (
                        str(resolve_sync_target(self.current_job.settings))
                        if self.current_job.settings.sync_enabled and self.current_job.settings.sync_dir
                        else None
                    )
                    dedupe_count = run_deduplicator(
                        self.current_job.settings.output_dir,
                        self.log_queue.put,
                        apply=True,
                        exact_only=self.current_job.settings.dedupe_exact_only,
                        sync_dir=sync_dir,
                    )
                    self.log_queue.put(f"Post-download dedupe moved {dedupe_count} duplicate candidate(s).\n")
            elif code != 0:
                cleanup_orphan_thumbnails(self.current_job.settings, before, self.log_queue.put)
            if self.stop_event.is_set():
                break
        self.current_job = None
        self.stop_event.clear()

    def _append_log(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text)
        self.log.see("end")
        self.log.configure(state="disabled")

    def _poll_log_queue(self) -> None:
        try:
            while True:
                self._append_log(self.log_queue.get_nowait())
        except queue.Empty:
            pass
        self.after(120, self._poll_log_queue)

    def _poll_clipboard(self) -> None:
        self.scan_clipboard(auto=True)
        self.after(1800, self._poll_clipboard)

    def _refresh_status(self) -> None:
        queued = self.jobs.qsize()
        if self.current_job:
            self.status_var.set(f"Downloading, {queued} queued")
        else:
            self.status_var.set(f"{queued} queued" if queued else "Ready")
        self.queue_var.set(f"Queued jobs: {queued}")
        self.after(750, self._refresh_status)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Advanced yt-dlp GUI and CLI wrapper.")
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {APP_VERSION}")
    parser.add_argument("urls", nargs="*", help="URLs to download. Omit to open the GUI.")
    parser.add_argument("--audio", action="store_true", help="Download as audio.")
    parser.add_argument("--video", action="store_true", help="Download as video.")
    parser.add_argument("--audio-preset", choices=list(AUDIO_PRESETS), default=None)
    parser.add_argument("--video-preset", choices=list(VIDEO_PRESETS), default=None)
    parser.add_argument("-o", "--output-dir", default=None)
    parser.add_argument("--sync-dir", default=None, help="Optional second folder to mirror completed output into.")
    parser.add_argument(
        "--sync-flat",
        action="store_true",
        help="Copy directly into --sync-dir instead of creating a matched output subfolder.",
    )
    parser.add_argument("--no-sync", action="store_true", help="Disable sync even if it is enabled in saved settings.")
    parser.add_argument("--archive", action="store_true", help="Enable download-archive.txt skipping.")
    parser.add_argument("--no-archive", action="store_true", help="Disable download-archive.txt creation/use.")
    parser.add_argument("--cookies", default=None)
    parser.add_argument("--no-playlist", action="store_true")
    parser.add_argument("--sponsorblock", action="store_true")
    parser.add_argument("--extra-args", default=None)
    parser.add_argument("--print-command", action="store_true")
    parser.add_argument("--dedupe", action="store_true", help="Scan the output folder for duplicate media candidates.")
    parser.add_argument("--dedupe-apply", action="store_true", help="Move duplicate candidates into _duplicates_review.")
    parser.add_argument("--dedupe-exact", action="store_true", help="Only deduplicate byte-identical media files.")
    parser.add_argument("--dedupe-sync", action="store_true", help="When applying dedupe, move matching sync copies too.")
    parser.add_argument("--dedupe-after-download", action="store_true", help="Apply dedupe after a successful download.")
    return parser.parse_args(argv)


def run_cli(args: argparse.Namespace) -> int:
    settings = load_settings()
    if args.audio:
        settings.mode = "audio"
    if args.video:
        settings.mode = "video"
    if args.audio_preset:
        settings.audio_preset = args.audio_preset
    if args.video_preset:
        settings.video_preset = args.video_preset
    if args.output_dir:
        settings.output_dir = args.output_dir
    if args.sync_dir is not None:
        settings.sync_dir = args.sync_dir
        settings.sync_enabled = bool(args.sync_dir)
    if args.sync_flat:
        settings.sync_match_output_folder = False
    if args.no_sync:
        settings.sync_enabled = False
    if args.archive:
        settings.write_archive = True
    if args.no_archive:
        settings.write_archive = False
    if args.cookies is not None:
        settings.cookies_file = args.cookies
    if args.no_playlist:
        settings.playlist = False
    if args.sponsorblock:
        settings.sponsorblock = True
    if args.extra_args is not None:
        settings.extra_args = args.extra_args
    if args.dedupe_exact:
        settings.dedupe_exact_only = True
    if args.dedupe_after_download:
        settings.auto_dedupe_after_download = True

    if args.dedupe or args.dedupe_apply:
        sync_dir = str(resolve_sync_target(settings)) if args.dedupe_sync and settings.sync_enabled and settings.sync_dir else None
        run_deduplicator(
            settings.output_dir,
            cli_output,
            apply=args.dedupe_apply,
            exact_only=settings.dedupe_exact_only,
            sync_dir=sync_dir,
        )
        return 0

    command = build_command(args.urls, settings)
    if args.print_command:
        print(subprocess.list2cmdline(command))
        if settings.sync_enabled and settings.sync_dir:
            print(f"Sync target: {resolve_sync_target(settings)}")
        return 0
    before = snapshot_output_files(settings)
    code = run_command(command, cli_output)
    if code == 0:
        sync_code = sync_output_dir(settings, cli_output)
        if settings.auto_dedupe_after_download:
            sync_dir = str(resolve_sync_target(settings)) if settings.sync_enabled and settings.sync_dir else None
            run_deduplicator(
                settings.output_dir,
                cli_output,
                apply=True,
                exact_only=settings.dedupe_exact_only,
                sync_dir=sync_dir,
            )
        return sync_code
    cleanup_orphan_thumbnails(settings, before, cli_output)
    return code


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    if args.urls or args.dedupe or args.dedupe_apply:
        return run_cli(args)
    if tk is None:
        print("Tkinter is not available. Pass one or more URLs to use CLI mode.", file=sys.stderr)
        return 2
    app = StudioApp()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
