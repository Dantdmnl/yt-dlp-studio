"""Run unmodified downloader entry points with narrowly scoped compatibility hooks."""

from __future__ import annotations

import json
import runpy
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ytdlp_studio.processes import hide_child_windows


def configure_spotdl(report: Path | None = None) -> None:
    from ytmusicapi import YTMusic
    from spotdl.providers.audio.ytmusic import YouTubeMusic
    from spotdl.providers.audio.base import YTDLLogger

    # Filtered German searches currently return no songs; keep native matching in English.
    YouTubeMusic._create_client = staticmethod(lambda: YTMusic(language="en"))
    original_error = YTDLLogger.error
    original_warning = YTDLLogger.warning

    def error(self, message):
        print(f"yt-dlp: {message}", file=sys.stderr, flush=True)
        return original_error(self, message)

    def warning(self, message):
        print(f"yt-dlp warning: {message}", file=sys.stderr, flush=True)
        return original_warning(self, message)

    YTDLLogger.error = error
    YTDLLogger.warning = warning
    if report is None:
        return
    from spotdl.download.downloader import Downloader
    from mutagen import File, MutagenError
    original_download = Downloader.async_search_and_download

    async def download(self, song):
        result = await original_download(self, song)
        resolved_song, path = result
        valid = False
        candidates = ([path] if path is not None else []) + list(self.known_songs.get(resolved_song.url, []))
        for candidate in candidates:
            try:
                media = File(candidate)
                valid = media is not None and getattr(media.info, "length", 0) > 0
                if valid:
                    path = candidate
                    break
            except (OSError, ValueError, MutagenError):
                pass
        failed = any(resolved_song.url in message for message in self.errors) if resolved_song.url else False
        record = {"url": resolved_song.url, "path": str(Path(path).resolve()) if valid else None,
                  "status": "completed" if valid else "failed" if failed or path is not None else "skipped"}
        with report.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        return result

    Downloader.async_search_and_download = download


def main(argv=None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments or arguments[0] not in {"yt_dlp", "spotdl"}:
        print("Expected yt_dlp or spotdl.", file=sys.stderr)
        return 2
    provider = arguments.pop(0)
    report = None
    if "--studio-report" in arguments:
        index = arguments.index("--studio-report")
        if index + 1 >= len(arguments):
            return 2
        report = Path(arguments[index + 1])
        del arguments[index:index + 2]
    hide_child_windows()
    if provider == "spotdl":
        configure_spotdl(report)
    sys.argv = [provider, *arguments]
    runpy.run_module(provider, run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
