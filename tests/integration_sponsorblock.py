"""Verify real cutting with generated audio and deterministic SponsorBlock tags."""

import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ytdlp_studio import backend as app
from ytdlp_studio.media_tools import find_installed_tools
from mutagen.mp3 import MP3
from yt_dlp import YoutubeDL
from yt_dlp.postprocessor.modify_chapters import ModifyChaptersPP
from yt_dlp.postprocessor.sponsorblock import SponsorBlockPP


def main():
    tools = find_installed_tools()
    assert tools, "Run .venv/Scripts/python.exe media_tools.py --install first."
    with tempfile.TemporaryDirectory(prefix="studio-sponsor-test-") as folder:
        audio = Path(folder) / "sample.mp3"
        subprocess.run([str(tools[0]), "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                        "sine=frequency=440:duration=10", "-c:a", "libmp3lame", str(audio)], check=True,
                       **app.subprocess_window_kwargs())
        original_duration = MP3(audio).info.length
        info = {"id": "synthetic", "extractor_key": "Youtube", "filepath": str(audio), "ext": "mp3",
                "title": "Sample song", "duration": 10, "vcodec": "none", "acodec": "mp3", "__real_download": True}
        segments = [{"segment": [start, end], "category": category, "actionType": "skip", "videoDuration": 10}
                    for start, end, category in ((0, 1, "music_offtopic"), (3, 4, "sponsor"), (8, 10, "outro"))]
        with YoutubeDL({"quiet": True, "ffmpeg_location": str(tools[0])}) as downloader:
            sponsor = SponsorBlockPP(downloader, app.SPONSORBLOCK_CATEGORIES)
            with patch.object(sponsor, "_get_sponsor_segments", return_value=segments):
                _, info = sponsor.run(info)
            assert len(info["sponsorblock_chapters"]) == 3
            cutter = ModifyChaptersPP(downloader, remove_sponsor_segments=app.SPONSORBLOCK_CATEGORIES)
            originals, info = cutter.run(info)
        duration = MP3(audio).info.length
        assert 5.7 < duration < 6.3, (original_duration, duration)
        assert originals and all(Path(original).is_file() for original in originals)
        assert info["duration"] == 6
        print(f"PASS: real FFmpeg/ffprobe cut a {original_duration:.2f}s file to {duration:.2f}s, removing sponsor, outro and non-music tags")
        if app.spotdl_available():
            import shlex
            from spotdl.utils.arguments import create_parser
            from spotdl.utils.formatter import args_to_ytdlp_options
            from spotdl.download.downloader import SPONSOR_BLOCK_CATEGORIES
            settings = app.Settings(output_dir=folder)
            command = app.build_spotdl_command(["Artist - Song"], settings)
            parsed = create_parser().parse_args(command[4:])
            assert parsed.sponsor_block
            assert {"sponsor", "outro", "music_offtopic"} <= set(SPONSOR_BLOCK_CATEGORIES)
            options = args_to_ytdlp_options(shlex.split(parsed.yt_dlp_args))
            with YoutubeDL(options) as downloader:
                postprocessor = ModifyChaptersPP(downloader, remove_sponsor_segments=SPONSOR_BLOCK_CATEGORIES)
                assert postprocessor.available and postprocessor.probe_available
            print("PASS: spotDL's actual parser and native SponsorBlock processors can access both installed media tools")


if __name__ == "__main__":
    main()
