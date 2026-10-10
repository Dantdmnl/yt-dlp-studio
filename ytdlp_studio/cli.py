"""Command-line entry points; GUI imports stay lazy."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import traceback

from . import backend as core


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Advanced yt-dlp GUI and CLI wrapper.")
    parser.add_argument("--version", action="version", version=f"{core.APP_NAME} {core.APP_VERSION}")
    parser.add_argument("--provider", choices=["Auto", "yt-dlp", "spotDL"], default=None)
    parser.add_argument("--spotdl-extra-args", default=None)
    parser.add_argument("--sync-only", action="store_true", help="Mirror the existing local library without downloading.")
    parser.add_argument("--discography", metavar="SPOTIFY_ARTIST_URL", help="Download the artist's complete paged release catalogue.")
    parser.add_argument("--all-editions", action="store_true", help="Include alternate editions in artist downloads.")
    parser.add_argument("--flat-discography", action="store_true", help="Keep artist downloads in one folder instead of album folders.")
    parser.add_argument("urls", nargs="*", help="URLs to download. Omit to open the GUI.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--audio", action="store_true", help="Download as audio.")
    mode.add_argument("--video", action="store_true", help="Download as video.")
    parser.add_argument("--audio-preset", choices=list(core.AUDIO_PRESETS), default=None)
    parser.add_argument("--video-preset", choices=list(core.VIDEO_PRESETS), default=None)
    parser.add_argument("-o", "--output-dir", default=None)
    parser.add_argument("--sync-dir", default=None, help="Optional second folder to mirror completed output into.")
    parser.add_argument(
        "--sync-flat",
        action="store_true",
        help="Copy directly into --sync-dir instead of creating a matched output subfolder.",
    )
    parser.add_argument("--no-sync", action="store_true", help="Disable sync even if it is enabled in saved settings.")
    archive = parser.add_mutually_exclusive_group()
    archive.add_argument("--archive", action="store_true", help="Enable download-archive.txt skipping.")
    archive.add_argument("--no-archive", action="store_true", help="Disable download-archive.txt creation/use.")
    parser.add_argument("--cookies", default=None)
    parser.add_argument("--no-playlist", action="store_true")
    sponsor_group = parser.add_mutually_exclusive_group()
    sponsor_group.add_argument("--sponsorblock", action="store_true", help="Remove tagged sponsors, outros, and non-music sections (default on).")
    sponsor_group.add_argument("--no-sponsorblock", action="store_true", help="Keep all segments in downloaded media.")
    parser.add_argument("--extra-args", default=None)
    parser.add_argument("--print-command", action="store_true")
    parser.add_argument("--dedupe", action="store_true", help="Scan the output folder for duplicate media candidates.")
    parser.add_argument("--dedupe-apply", action="store_true", help="Move duplicate candidates into _duplicates_review.")
    parser.add_argument("--dedupe-exact", action="store_true", help="Only deduplicate byte-identical media files.")
    parser.add_argument("--dedupe-sync", action="store_true", help="When applying dedupe, move matching sync copies too.")
    parser.add_argument("--dedupe-after-download", action="store_true", help="Move byte-identical duplicates after a successful download.")
    return parser.parse_args(argv)


def run_cli(args: argparse.Namespace) -> int:
    settings = core.load_settings()
    if args.discography and (args.sync_only or args.dedupe or args.dedupe_apply):
        core.cli_output("Error: Artist downloads cannot be combined with sync-only or duplicate review.\n")
        return 2
    if args.discography:
        try:
            core.spotify_id(args.discography, "artist")
            if args.video or args.provider == "yt-dlp":
                raise ValueError("Artist discographies use spotDL in Audio mode.")
        except ValueError as exc:
            core.cli_output(f"Error: {exc}\n")
            return 2
        settings.provider = "Auto"
        settings.mode = "audio"
    if args.all_editions:
        settings.spotify_all_editions = True
    if args.flat_discography:
        settings.spotify_album_folders = False
    if args.provider:
        settings.provider = args.provider
    if args.spotdl_extra_args is not None:
        settings.spotdl_extra_args = args.spotdl_extra_args
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
        settings.sync_library_root = ""
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
    if args.no_sponsorblock:
        settings.sponsorblock = False
    if args.extra_args is not None:
        settings.extra_args = args.extra_args
    if args.dedupe_exact:
        settings.dedupe_exact_only = True
    if args.dedupe_after_download:
        settings.auto_dedupe_after_download = True

    if args.dedupe or args.dedupe_apply:
        try:
            if not settings.output_dir.strip():
                raise ValueError("Choose a local library folder first.")
            sync_dir = str(core.resolve_sync_target(settings)) if args.dedupe_sync and settings.sync_enabled and settings.sync_dir else None
            core.run_deduplicator(settings.output_dir, core.cli_output, apply=args.dedupe_apply,
                                  exact_only=settings.dedupe_exact_only, sync_dir=sync_dir)
            return 0
        except (ValueError, OSError) as exc:
            core.cli_output(f"Error: {exc}\n")
            return 2

    if args.sync_only:
        try:
            core.validate_download(settings)
            if not settings.sync_enabled:
                raise ValueError("Use --sync-dir to choose a mirror folder.")
            return core.sync_output_dir(settings, core.cli_output)
        except (ValueError, OSError) as exc:
            core.cli_output(f"Error: {exc}\n")
            return 2
    try:
        jobs = core.build_jobs(args.urls, settings) if args.urls else []
        if args.discography:
            if not settings.playlist:
                raise ValueError("Enable playlists to download an artist's albums and singles.")
            if not core.spotdl_available():
                raise ValueError("Install Spotify support with .\\install.ps1 -Spotify first.")
            catalogue = core.request_catalogue("artist", args.discography, all_editions=settings.spotify_all_editions)
            jobs.extend(core.build_discography_jobs(catalogue, settings))
            core.cli_output(f"{catalogue['artist']}: {len(catalogue['items'])} releases; {catalogue['groups']} release groups scanned.\n")
    except (ValueError, OSError, TimeoutError) as exc:
        core.cli_output(f"Error: {exc}\n")
        return 2
    if args.print_command:
        for job in jobs:
            print(subprocess.list2cmdline(job.command))
        if settings.sync_enabled and settings.sync_dir:
            print(f"Sync target: {core.resolve_sync_target(settings)}")
        return 0
    result = 0
    for job in jobs:
        try:
            if job.label:
                core.cli_output(f"Starting {job.label}\n")
            before = core.snapshot_output_files(job.settings)
            code = core.run_download_job(job, core.cli_output)
            if code:
                core.cleanup_orphan_thumbnails(job.settings, before, core.cli_output)
                result = code
                if code == 130:
                    break
                continue
            sync_code = core.finish_download_job(job, core.cli_output)
            if sync_code:
                result = sync_code
        except (ValueError, OSError, TimeoutError) as exc:
            core.cli_output(f"Job failed: {exc}\n")
            result = 1
    return result


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.urls or args.dedupe or args.dedupe_apply or args.sync_only or args.discography:
        return run_cli(args)
    try:
        from .gui import StudioApp
        app = StudioApp()
    except Exception:
        details = traceback.format_exc()
        try:
            core.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            log_file = core.CONFIG_DIR / "startup-error.log"
            log_file.write_text(details, encoding="utf-8")
            message = f"Could not start {core.APP_NAME}.\n\nDetails: {log_file}\n\nRun .\\install.ps1 to repair the environment."
        except OSError:
            message = details
        if os.name == "nt":
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, message, core.APP_NAME, 16)
        else:
            print(message, file=sys.stderr)
        return 1
    app.mainloop()
    return 0
