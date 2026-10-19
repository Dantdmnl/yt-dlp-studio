"""Download jobs, persisted settings, library review, and mirroring."""

from __future__ import annotations

import hashlib
import importlib.util
import filecmp
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path, PureWindowsPath
from typing import Callable, Iterable
from urllib.parse import urlparse

from .media_tools import find_installed_tools
from .catalogue import request_catalogue, spotify_id
from .processes import own_process_tree, terminate_tree, window_kwargs

from .version import APP_NAME as APP_NAME, APP_VERSION as APP_VERSION
CONFIG_DIR = Path(os.environ.get("APPDATA", Path.home() / ".config")) / "ytdlp-studio"
CONFIG_FILE = CONFIG_DIR / "settings.json"
DEFAULT_FILENAME_TEMPLATE = "%(artist,creator,uploader|Unknown)s - %(title)s.%(ext)s"
DEFAULT_SPOTDL_TEMPLATE = "{artists} - {title}.{output-ext}"
SPONSORBLOCK_CATEGORIES = ("sponsor", "intro", "outro", "selfpromo", "preview", "filler", "interaction", "music_offtopic")


AUDIO_PRESETS = {
    "MP3 320 kbps": {"format": "bestaudio/best", "codec": "mp3", "quality": "320"},
    "MP3 192 kbps": {"format": "bestaudio/best", "codec": "mp3", "quality": "192"},
    "M4A best": {"format": "bestaudio[ext=m4a]/bestaudio/best", "codec": "m4a", "quality": "0"},
    "Opus best": {"format": "bestaudio[ext=opus]/bestaudio/best", "codec": "opus", "quality": "0"},
    "FLAC": {"format": "bestaudio/best", "codec": "flac", "quality": "0"},
}

VIDEO_PRESETS = {
    "Best video + audio": "bv*+ba/best",
    "1080p MP4": "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080][ext=mp4]/bv*[height<=1080]+ba/b[height<=1080]",
    "720p MP4": "bv*[height<=720][ext=mp4]+ba[ext=m4a]/b[height<=720][ext=mp4]/bv*[height<=720]+ba/b[height<=720]",
    "Small compatible MP4": "bv*[height<=480][ext=mp4]+ba[ext=m4a]/b[height<=480][ext=mp4]/bv*[height<=480]+ba/b[height<=480]",
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
    sponsorblock: bool = True
    keep_original: bool = False
    write_archive: bool = True
    restrict_filenames: bool = False
    sync_enabled: bool = False
    sync_dir: str = ""
    sync_library_root: str = ""
    sync_match_output_folder: bool = True
    auto_dedupe_after_download: bool = False
    dedupe_exact_only: bool = False
    cookies_file: str = ""
    extra_args: str = ""
    provider: str = "Auto"
    spotdl_template: str = DEFAULT_SPOTDL_TEMPLATE
    spotdl_extra_args: str = ""
    watch_clipboard: bool = True
    simple_mode: bool = True
    spotify_album_folders: bool = True
    spotify_all_editions: bool = False
    auto_preview: bool = True


SETTINGS_FIELDS = set(Settings.__dataclass_fields__)


@dataclass
class DownloadJob:
    urls: list[str]
    settings: Settings
    status: str = "Queued"
    command: list[str] = field(default_factory=list)
    provider: str = "yt-dlp"
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    label: str = ""
    failed_urls: list[str] = field(default_factory=list)
    completed_files: list[Path] = field(default_factory=list)
    report_path: str = ""
    release_id: str = ""
    retry_jobs: list[DownloadJob] = field(default_factory=list)


@dataclass
class DuplicatePlan:
    groups: list[tuple[Path, list[Path]]]
    scanned: int


def subprocess_window_kwargs() -> dict[str, int]:
    return window_kwargs()


def normalize_settings(settings: Settings) -> Settings:
    if settings.sync_dir and settings.sync_match_output_folder and not settings.sync_library_root:
        settings.sync_library_root = str(infer_sync_library_root(settings))
    if settings.provider not in {"Auto", "yt-dlp", "spotDL"}:
        settings.provider = "Auto"
    if settings.mode not in {"audio", "video"}:
        settings.mode = "audio"
    if settings.audio_preset not in AUDIO_PRESETS:
        settings.audio_preset = "MP3 320 kbps"
    if settings.video_preset not in VIDEO_PRESETS:
        settings.video_preset = "1080p MP4"
    if not settings.filename_template.strip():
        settings.filename_template = DEFAULT_FILENAME_TEMPLATE
    if not settings.spotdl_template.strip():
        settings.spotdl_template = DEFAULT_SPOTDL_TEMPLATE
    return settings


def load_settings() -> Settings:
    try:
        if not CONFIG_FILE.exists():
            return Settings()
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return Settings()
        known = {field.name for field in Settings.__dataclass_fields__.values()}
        defaults = Settings()
        return normalize_settings(Settings(**{key: value for key, value in data.items()
                                               if key in known and type(value) is type(getattr(defaults, key))}))
    except (OSError, TypeError, json.JSONDecodeError):
        return Settings()


def save_settings(settings: Settings) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    temporary = CONFIG_FILE.with_name(f"{CONFIG_FILE.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(asdict(settings), indent=2), encoding="utf-8")
        temporary.replace(CONFIG_FILE)
    finally:
        temporary.unlink(missing_ok=True)


def spotdl_available() -> bool:
    return importlib.util.find_spec("spotdl") is not None


def provider_for(query: str, settings: Settings) -> str:
    if settings.provider != "Auto":
        return settings.provider
    host = (urlparse(query).hostname or "").casefold()
    return "spotDL" if host in {"open.spotify.com", "spotify.link"} else "yt-dlp"


def validate_download(settings: Settings) -> None:
    if not settings.output_dir.strip():
        raise ValueError("Choose a local library folder first.")
    if settings.sync_enabled:
        if not settings.sync_dir.strip():
            raise ValueError("Choose a mirror folder or turn mirroring off.")
        source = Path(settings.output_dir).expanduser().resolve()
        target = resolve_sync_target(settings).resolve()
        if source == target or source in target.parents or target in source.parents:
            raise ValueError("Choose separate local and mirror folders that do not contain one another.")
    if settings.cookies_file and not Path(settings.cookies_file).expanduser().is_file():
        raise ValueError("The selected cookies file does not exist.")


def build_spotdl_command(queries: list[str], settings: Settings) -> list[str]:
    if not spotdl_available():
        raise ValueError("Spotify support is not installed. Run .\\install.ps1 -Spotify, then reopen the app.")
    if settings.mode != "audio":
        raise ValueError("spotDL downloads audio. Select Audio mode for Spotify links.")
    if any(query.startswith("-") for query in queries):
        raise ValueError("A search or link cannot start with a dash.")
    if not settings.playlist and any(
        re.search(r"/(album|playlist|artist)/", query) for query in queries
    ):
        raise ValueError("Enable playlists to download a Spotify album, playlist, or artist.")
    preset = AUDIO_PRESETS[settings.audio_preset]
    command = downloader_command("spotdl") + ["download", *queries,
               "--output", str(Path(settings.output_dir).expanduser() / settings.spotdl_template),
               "--format", preset["codec"], "--bitrate", "auto",
               "--overwrite", "skip", "--scan-for-songs", "--threads", "1",
               "--simple-tui", "--print-errors"]
    ffmpeg = find_ffmpeg_location()
    if not ffmpeg:
        raise ValueError("FFmpeg is missing. Run .\\install.ps1 to repair the environment.")
    command += ["--ffmpeg", ffmpeg]
    import shlex
    backend_args = ["--ffmpeg-location", ffmpeg]
    runtime = find_js_runtime()
    if runtime:
        backend_args += ["--js-runtimes", f"{runtime[0]}:{runtime[1]}"]
    command += ["--yt-dlp-args", shlex.join(backend_args)]
    if settings.write_archive:
        command += ["--archive", str(Path(settings.output_dir).expanduser() / "spotdl-archive.txt")]
    else:
        command += ["--archive", ""]
    if settings.cookies_file:
        command += ["--cookie-file", settings.cookies_file]
    if settings.sponsorblock:
        if not find_installed_tools():
            raise ValueError("SponsorBlock cutting needs FFmpeg and ffprobe. Run .\\install.ps1 to install both.")
        command.append("--sponsor-block")
    if settings.restrict_filenames:
        command += ["--restrict", "strict"]
    command += split_extra_args(settings.spotdl_extra_args)
    return command


def build_jobs(queries: Iterable[str], settings: Settings) -> list[DownloadJob]:
    validate_download(settings)
    groups: dict[str, list[str]] = {}
    for query in dict.fromkeys(queries):
        if query.startswith("-"):
            raise ValueError("A search or link cannot start with a dash.")
        provider = provider_for(query, settings)
        if provider == "yt-dlp" and (urlparse(query).hostname or "").casefold() in {"open.spotify.com", "spotify.link"}:
            raise ValueError("Spotify links require the Auto or spotDL source.")
        groups.setdefault(provider, []).append(query)
    jobs = []
    for provider, inputs in groups.items():
        job = DownloadJob(inputs, Settings(**asdict(settings)), provider=provider)
        job.command = build_spotdl_command(inputs, settings) if provider == "spotDL" else build_command(inputs, settings)
        jobs.append(job)
    return jobs


def prepare_jobs(queries: Iterable[str], settings: Settings, cancel=None, progress=None) -> list[DownloadJob]:
    """Expand artist links before queue insertion; ordinary sources keep their routing."""
    settings = Settings(**asdict(settings))
    validate_download(settings)
    jobs, pending, artists = [], [], set()
    for query in dict.fromkeys(queries):
        if cancel and cancel.is_set():
            raise InterruptedError("Preparation cancelled.")
        if not is_spotify_artist(query) or provider_for(query, settings) != "spotDL":
            pending.append(query)
            continue
        identity = spotify_id(query, "artist")
        if identity in artists:
            continue
        artists.add(identity)
        if not settings.playlist:
            raise ValueError("Enable playlists to download an artist's albums and singles.")
        if settings.mode != "audio":
            raise ValueError("spotDL downloads audio. Select Audio mode for Spotify links.")
        if not spotdl_available():
            raise ValueError("Spotify support is not installed. Run .\\install.ps1 -Spotify, then reopen the app.")
        if pending:
            jobs.extend(build_jobs(pending, settings))
            pending.clear()
        if progress:
            progress("Discovering artist releases...")
        catalogue = request_catalogue("artist", query, all_editions=settings.spotify_all_editions, cancel=cancel)
        if cancel and cancel.is_set():
            raise InterruptedError("Preparation cancelled.")
        if progress:
            progress(f"{catalogue['artist']}: preparing {len(catalogue['items'])} release jobs...")
        jobs.extend(build_discography_jobs(catalogue, settings))
    if pending:
        jobs.extend(build_jobs(pending, settings))
    if cancel and cancel.is_set():
        raise InterruptedError("Preparation cancelled.")
    return jobs


def run_download_job(job: DownloadJob, output: Callable[[str], None], stop_event: threading.Event | None = None) -> int:
    if stop_event and stop_event.is_set():
        return 130
    job.failed_urls.clear()
    job.completed_files.clear()
    job.retry_jobs.clear()
    job.report_path = ""
    if job.release_id:
        write_release_marker(Path(job.settings.output_dir), job.release_id, job.label)
    if job.provider != "spotDL":
        return run_command(job.command, output, stop_event, job.settings.output_dir)
    artist_urls = [url for url in job.urls if is_spotify_artist(url)]
    if artist_urls:
        result = 0
        remaining = [url for url in job.urls if url not in artist_urls]
        children = []
        if remaining:
            children.extend(build_jobs(remaining, job.settings))
        for url in artist_urls:
            output("Fetching all Spotify artist releases...\n")
            try:
                catalogue = request_catalogue("artist", url, all_editions=job.settings.spotify_all_editions,
                                              cancel=stop_event)
            except InterruptedError:
                return 130
            children.extend(build_discography_jobs(catalogue, job.settings))
            output(f"{catalogue['artist']}: {len(catalogue['items'])} releases in {catalogue['groups']} release groups.\n")
        for index, child in enumerate(children, 1):
            if stop_event and stop_event.is_set():
                job.retry_jobs.extend(children[index - 1:])
                return 130
            output(f"Release {index}/{len(children)}: {child.label or child.urls[0]}\n")
            try:
                before = snapshot_output_files(child.settings)
                code = run_download_job(child, output, stop_event)
                if code and code != 130:
                    cleanup_orphan_thumbnails(child.settings, before, output)
                if code != 130 and (not code or child.completed_files):
                    sync_code = finish_download_job(child, output, stop_event)
                    code = code or sync_code
            except (ValueError, OSError, TimeoutError) as exc:
                output(f"Release failed: {exc}\n")
                code = 1
            if code:
                result = code
                child.status = "Cancelled" if code == 130 else "Failed"
                job.retry_jobs.append(child)
            job.failed_urls.extend(child.failed_urls)
            job.completed_files.extend(child.completed_files)
            if code == 130:
                job.retry_jobs.extend(children[index:])
                return code
        return result
    # spotDL may return zero with individual song failures; inspect its error report.
    with tempfile.TemporaryDirectory(prefix="studio-spotdl-") as temporary:
        errors = Path(temporary) / "errors.txt"
        tracks = Path(temporary) / "tracks.jsonl"
        command = job.command + ["--studio-report", str(tracks), "--save-errors", str(errors)]
        code = run_command(command, output, stop_event, job.settings.output_dir)
        details = errors.read_text(encoding="utf-8") if errors.exists() else ""
        job.failed_urls = list(dict.fromkeys(re.findall(r"https://open\.spotify\.com/track/[A-Za-z0-9]{22}", details)))
        root = Path(job.settings.output_dir).expanduser().resolve()
        if tracks.exists():
            for line in tracks.read_text(encoding="utf-8").splitlines():
                try:
                    record = json.loads(line)
                    if record.get("status") == "completed" and record.get("path"):
                        path = Path(record["path"]).resolve()
                        if root in path.parents and path.is_file():
                            job.completed_files.append(path)
                    elif record.get("status") == "failed" and record.get("url"):
                        spotify_id(record["url"], "track")
                        job.failed_urls.append(record["url"])
                except (ValueError, TypeError, AttributeError, OSError):
                    output("An incomplete track report was ignored; completed files remain local.\n")
        job.failed_urls = list(dict.fromkeys(job.failed_urls))
        if code == 0 and (details.strip() or job.failed_urls):
            code = 1
        if details.strip():
            output("spotDL reported failed tracks:\n" + details + "\n")
        if code:
            save_failure_report(job, code, details, output)
        return code


def save_failure_report(job: DownloadJob, code: int, details: str, output: Callable[[str], None]) -> None:
    try:
        folder = CONFIG_DIR / "reports"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{job.id}.json"
        temporary = path.with_suffix(".json.tmp")
        try:
            temporary.write_text(json.dumps({"provider": job.provider, "label": job.label, "urls": job.urls,
                                             "output_dir": job.settings.output_dir, "exit_code": code,
                                             "failed_urls": job.failed_urls,
                                             "completed_files": [str(file) for file in job.completed_files],
                                             "details": details}, indent=2), encoding="utf-8")
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
        job.report_path = str(path)
        output(f"Failure/recovery report saved to {path}\n")
    except OSError as exc:
        output(f"Could not save the recovery report: {exc}\n")


def is_spotify_artist(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.hostname == "open.spotify.com" and "artist" in parsed.path.strip("/").split("/")


def finish_download_job(job: DownloadJob, output: Callable[[str], None], cancel=None) -> int:
    if job.provider == "spotDL" and any(is_spotify_artist(url) for url in job.urls):
        # Expanded artist jobs finish each album independently.
        return 0
    settings = job.settings
    if cancel and cancel.is_set():
        return 130
    if settings.auto_dedupe_after_download:
        sync_dir = str(resolve_sync_target(settings)) if settings.sync_enabled else None
        run_deduplicator(settings.output_dir, output, apply=True, exact_only=True, sync_dir=sync_dir)
    files = valid_audio_files(settings.output_dir) if job.provider == "spotDL" else None
    marker = Path(settings.output_dir) / ".studio-release.json"
    if files and marker.is_file():
        files.append(marker)
    return sync_output_dir(settings, output, files=files, cancel=cancel)


def valid_audio_files(folder: str) -> list[Path]:
    from mutagen import File, MutagenError
    files = []
    for path in Path(folder).expanduser().rglob("*"):
        if not path.is_file() or path.suffix.casefold() not in MEDIA_SUFFIXES or DUPLICATE_REVIEW_DIR in path.parts:
            continue
        try:
            media = File(path)
            if media is not None and getattr(media.info, "length", 0) > 0:
                files.append(path)
        except (OSError, ValueError, MutagenError):
            continue
    return files


def album_folder_name(release: dict) -> str:
    title = re.sub(r'[<>:"/\\|?*{}\x00-\x1f]+', " - ", release["title"])
    title = re.sub(r"\s+", " ", title)[:90].strip(" .-") or "Untitled"
    return f"{release.get('date', '')[:4] or 'Undated'} - {title}"


def read_release_identity(marker: Path) -> str:
    try:
        info = marker.lstat()
        if marker.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("Linked identity file")
        data = json.loads(marker.read_text(encoding="utf-8"))
        identity = data.get("album_id") if isinstance(data, dict) else None
        if not isinstance(identity, str) or not re.fullmatch(r"[A-Za-z0-9]{22}", identity):
            raise ValueError("Invalid release ID")
        return identity
    except (OSError, ValueError) as error:
        raise ValueError(f"Invalid release identity file: {marker}. Restore it before downloading or migrating.") from error


def release_folder_names(catalogue: dict, root: Path) -> dict[str, str]:
    mapped, occupied = {}, set()
    if root.exists():
        for folder in root.iterdir():
            occupied.add(folder.name.casefold())
            if not folder.is_dir() or folder.is_symlink() or folder.resolve().parent != root.resolve():
                continue
            identity = None
            marker = folder / ".studio-release.json"
            if marker.is_file():
                identity = read_release_identity(marker)
            legacy = re.search(r" \[([A-Za-z0-9]{22})\]$", folder.name)
            identity = identity or (legacy.group(1) if legacy else None)
            if identity:
                if identity in mapped:
                    raise ValueError("A release is present in multiple folders; review the library before downloading.")
                mapped[identity] = folder.name
    for release in catalogue["items"]:
        if release["id"] in mapped:
            continue
        base = album_folder_name(release)
        candidate, edition = base, 2
        while candidate.casefold() in occupied:
            candidate = f"{base} (edition {edition})"
            edition += 1
        occupied.add(candidate.casefold())
        mapped[release["id"]] = candidate
    return mapped


def write_release_marker(folder: Path, identity: str, label: str) -> None:
    spotify_id(f"https://open.spotify.com/album/{identity}", "album")
    folder.mkdir(parents=True, exist_ok=True)
    marker = folder / ".studio-release.json"
    if marker.exists():
        if read_release_identity(marker) != identity:
            raise ValueError("This album folder belongs to another release; refusing to overwrite it.")
        return
    temporary = folder / f".studio-release.{uuid.uuid4().hex}.tmp"
    try:
        temporary.write_text(json.dumps({"album_id": identity, "label": label}, indent=2), encoding="utf-8")
        temporary.replace(marker)
    finally:
        temporary.unlink(missing_ok=True)


def build_discography_jobs(catalogue: dict, settings: Settings, releases: list[dict] | None = None) -> list[DownloadJob]:
    if not settings.playlist:
        raise ValueError("Enable playlists to download an artist's albums and singles.")
    validate_download(settings)
    releases = catalogue["items"] if releases is None else releases
    if not releases:
        raise ValueError("No releases selected.")
    jobs = []
    folders = release_folder_names(catalogue, Path(settings.output_dir).expanduser()) if settings.spotify_album_folders else {}
    for release in releases:
        spotify_id(release["url"], "album")
        copy = Settings(**asdict(settings))
        copy.mode = "audio"
        copy.provider = "spotDL"
        if settings.spotify_album_folders:
            folder = folders[release["id"]]
            copy.output_dir = str(Path(settings.output_dir).expanduser() / folder)
            copy.spotdl_template = "{disc-number}-{track-number} - {title}.{output-ext}"
            if settings.sync_enabled:
                copy.sync_dir = str(resolve_sync_target(settings) / folder)
                copy.sync_library_root = ""
                copy.sync_match_output_folder = False
        else:
            # Flat artist downloads keep filename customization, not template subfolders.
            filename = PureWindowsPath(settings.spotdl_template).name
            copy.spotdl_template = filename if "{title}" in filename else DEFAULT_SPOTDL_TEMPLATE
        job = DownloadJob([release["url"]], copy, provider="spotDL", label=f"{catalogue['artist']} - {release['title']}")
        job.release_id = release["id"] if settings.spotify_album_folders else ""
        job.command = build_spotdl_command(job.urls, copy)
        jobs.append(job)
    return jobs


def find_ytdlp_command() -> list[str]:
    if importlib.util.find_spec("yt_dlp") is not None:
        return downloader_command("yt_dlp")

    exe = shutil.which("yt-dlp")
    return [exe] if exe else [sys.executable, "-m", "yt_dlp"]


def downloader_command(provider: str) -> list[str]:
    executable = Path(sys.executable)
    if executable.name.casefold() == "pythonw.exe" and executable.with_name("python.exe").is_file():
        executable = executable.with_name("python.exe")
    return [str(executable), "-u", str(Path(__file__).with_name("download_host.py").resolve()), provider]


def youtube_search_command(query: str, offset: int, limit: int) -> list[str]:
    return find_ytdlp_command() + ["--ignore-config", "--flat-playlist", "--dump-single-json",
                                  "--no-warnings", "--playlist-start", str(offset + 1),
                                  "--playlist-end", str(offset + limit), f"ytsearch{offset + limit}:{query}"]


def find_ffmpeg_location() -> str | None:
    pair = find_installed_tools()
    if pair:
        return str(pair[0])
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

    user_deno = Path.home() / ".deno" / "bin" / "deno.exe"
    if user_deno.is_file():
        return "deno", str(user_deno)

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
    pair = find_installed_tools()
    messages.append(f"ffprobe: {pair[1]}" if pair else "ffprobe: missing. SponsorBlock cutting needs .\\install.ps1.")

    if js_runtime:
        messages.append(f"js runtime: {js_runtime[0]} at {js_runtime[1]}")
    else:
        messages.append("js runtime: missing. YouTube may return 403. Install Deno or Node 22+, then restart the app.")

    return messages


def split_extra_args(raw: str) -> list[str]:
    if not raw.strip():
        return []
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        parse = ctypes.windll.shell32.CommandLineToArgvW
        parse.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
        parse.restype = ctypes.POINTER(wintypes.LPWSTR)
        count = ctypes.c_int()
        arguments = parse("studio " + raw, ctypes.byref(count))
        if not arguments:
            raise ValueError("Could not parse extra arguments.")
        try:
            return [arguments[index] for index in range(1, count.value)]
        finally:
            ctypes.windll.kernel32.LocalFree.argtypes = [ctypes.c_void_p]
            ctypes.windll.kernel32.LocalFree(arguments)
    import shlex
    return shlex.split(raw)


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


def move_matching_sync_duplicate(root: Path, duplicate: Path, sync_root: Path, output: Callable[[str], None],
                                 expected_hash: str | None = None) -> None:
    try:
        relative = duplicate.relative_to(root)
    except ValueError:
        return
    sync_duplicate = sync_root / relative
    if not sync_duplicate.exists() or not sync_duplicate.is_file():
        return
    if expected_hash is not None:
        try:
            if file_hash(sync_duplicate) != expected_hash:
                output(f"  Sync copy differs; left untouched: {relative}\n")
                return
        except OSError as exc:
            output(f"  Could not verify sync copy {relative}: {exc}\n")
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
        if not keeper.exists():
            continue
        output(f"Keep: {keeper.relative_to(root)}\n")
        for duplicate in duplicates:
            output(f"  Duplicate: {duplicate.relative_to(root)}\n")
            if apply:
                if not duplicate.exists():
                    continue
                expected_hash = file_hash(duplicate) if sync_dir else None
                destination = unique_review_path(review_root, duplicate.relative_to(root))
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(duplicate), str(destination))
                output(f"  Moved to: {destination.relative_to(root)}\n")
                if sync_dir:
                    move_matching_sync_duplicate(root, duplicate, Path(sync_dir).expanduser(), output, expected_hash)

    if not apply:
        output("Dry run only. Use Apply Deduplication or --dedupe-apply to move duplicates.\n")
    return total_duplicates


def build_command(urls: Iterable[str], settings: Settings) -> list[str]:
    output_path = Path(settings.output_dir).expanduser()
    archive_path = output_path / "download-archive.txt"
    command = find_ytdlp_command()

    command += [
        "--ignore-config",
        "--newline",
        "--no-abort-on-error",
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
        if not find_installed_tools():
            raise ValueError("SponsorBlock cutting needs FFmpeg and ffprobe. Run .\\install.ps1 to install both.")
        command += ["--sponsorblock-remove", ",".join(SPONSORBLOCK_CATEGORIES)]
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
    skipped_suffixes = {".part", ".ytdl", ".tmp", ".temp", ".sync-temp"}
    skipped_names = {"download-archive.txt", "spotdl-archive.txt"}
    return (
        path.name.lower() not in skipped_names
        and not any(part.endswith(".part") for part in path.parts)
        and path.suffix.lower() not in skipped_suffixes
    )


def infer_sync_library_root(settings: Settings) -> Path:
    source = Path(settings.output_dir).expanduser()
    target_root = Path(settings.sync_dir).expanduser()
    if settings.sync_library_root:
        return Path(settings.sync_library_root).expanduser()
    # Migrate old matched paths, including paths accumulated during live typing.
    for index, part in enumerate(target_root.parts):
        if part.casefold() == source.parent.name.casefold():
            return Path(*target_root.parts[:index + 1])
    if target_root.name.casefold() == source.name.casefold():
        return target_root.parent
    return target_root


def resolve_sync_target(settings: Settings) -> Path:
    source = Path(settings.output_dir).expanduser()
    target_root = Path(settings.sync_dir).expanduser()
    if not settings.sync_match_output_folder:
        return target_root
    return infer_sync_library_root(settings) / source.name


def sync_output_dir(settings: Settings, output: Callable[[str], None], files=None, cancel=None) -> int:
    if not settings.sync_enabled or not settings.sync_dir.strip():
        return 0

    source = Path(settings.output_dir).expanduser().resolve()
    target = resolve_sync_target(settings).resolve()
    if source == target:
        output("Sync skipped: local and sync folders are the same.\n")
        return 0
    if source in target.parents or target in source.parents:
        output("Sync failed: local and mirror folders must not contain one another.\n")
        return 1
    if not source.exists():
        output(f"Sync skipped: local output folder does not exist: {source}\n")
        return 1

    copied = 0
    skipped = 0
    failed = 0
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        output(f"Sync failed: could not create {target}: {exc}\n")
        return 1
    output(f"Syncing completed files from {source} to {target}\n")

    for source_file in source.rglob("*") if files is None else files:
        if cancel and cancel.is_set():
            output("Mirroring stopped; completed local files are preserved.\n")
            return 130
        if not source_file.is_file() or not should_sync_file(source_file):
            continue
        try:
            source_file = source_file.resolve()
            relative = source_file.relative_to(source)
        except (OSError, ValueError) as exc:
            failed += 1
            output(f"Sync warning: could not validate library file {source_file}: {exc}\n")
            continue
        if DUPLICATE_REVIEW_DIR in relative.parts:
            continue
        target_file = target / relative
        try:
            target_file.parent.mkdir(parents=True, exist_ok=True)
            if target_file.exists() and filecmp.cmp(source_file, target_file, shallow=False):
                skipped += 1
                continue
            temporary = target_file.with_name(f"{target_file.name}.{uuid.uuid4().hex}.sync-temp")
            try:
                shutil.copy2(source_file, temporary)
                os.replace(temporary, target_file)
            finally:
                temporary.unlink(missing_ok=True)
            copied += 1
            output(f"Synced: {relative}\n")
        except OSError as exc:
            failed += 1
            output(f"Sync warning for {relative}: {exc}\n")

    output(f"Sync complete: {copied} copied, {skipped} already current, {failed} failed.\n")
    return 1 if failed else 0


def run_command(command: list[str], output: Callable[[str], None], stop_event: threading.Event | None = None,
                output_dir: str | None = None) -> int:
    if stop_event and stop_event.is_set():
        return 130
    try:
        if output_dir:
            Path(output_dir).expanduser().mkdir(parents=True, exist_ok=True)
        elif "-P" in command:
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
        output(f"Could not start downloader: {exc}\n")
        return 127

    assert process.stdout is not None
    tree = own_process_tree(process)
    monitor_done = threading.Event()

    def monitor_cancellation() -> None:
        while not monitor_done.wait(0.1):
            if not stop_event or not stop_event.is_set():
                continue
            terminate_tree(process, tree)
            return

    monitor = threading.Thread(target=monitor_cancellation, daemon=True)
    monitor.start()
    try:
        for line in process.stdout:
            output(line)
        code = process.wait()
    finally:
        monitor_done.set()
        monitor.join()
        process.stdout.close()
        if tree is not None:
            tree.close()
    if stop_event and stop_event.is_set():
        output("Cancelled by user.\n")
        return 130
    return code
