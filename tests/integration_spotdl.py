"""Real native spotDL converters, with generated audio and no network/user media."""

import asyncio
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import wave
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ytdlp_studio.media_tools import find_installed_tools  # noqa: E402
from ytdlp_studio.processes import hide_child_windows  # noqa: E402


def main():
    if not importlib.util.find_spec("spotdl"):
        print("SKIP: optional spotDL is not installed")
        return
    from spotdl.utils.ffmpeg import convert, async_convert
    from mutagen.mp3 import MP3
    tools = find_installed_tools()
    assert tools, "Install the complete media tools first"
    hide_child_windows()
    launches = []
    if os.name == "nt":
        import _winapi
        import subprocess
        original = _winapi.CreateProcess
        def hidden_launch(*args, **kwargs):
            flags = args[5]
            assert flags & subprocess.CREATE_NO_WINDOW
            assert not flags & (subprocess.CREATE_NEW_CONSOLE | subprocess.DETACHED_PROCESS)
            launches.append(args[0] or args[1])
            return original(*args, **kwargs)
        context = patch.object(_winapi, "CreateProcess", hidden_launch)
    else:
        from contextlib import nullcontext
        context = nullcontext()
    with tempfile.TemporaryDirectory(prefix="studio-native-conversion-") as directory, context:
        root = Path(directory)
        source = root / "input.wav"
        with wave.open(str(source), "wb") as audio:
            audio.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
            audio.writeframes(b"\x00\x00" * 8000)
        ok, error = convert(source, root / "sync.mp3", ffmpeg=str(tools[0]))
        assert ok, error
        ok, error = asyncio.run(async_convert(source, root / "async.mp3", ffmpeg=str(tools[0])))
        assert ok, error
        assert all(MP3(root / filename).info.length > 0 for filename in ("sync.mp3", "async.mp3"))
    if os.name == "nt":
        assert len(launches) >= 2, launches
    print("PASS: native synchronous and asyncio spotDL conversion; playable MP3s and hidden FFmpeg launches")


if __name__ == "__main__":
    main()
