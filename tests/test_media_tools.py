import io
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ytdlp_studio import media_tools


class MediaToolsTests(unittest.TestCase):
    def test_a_converter_without_ffprobe_is_not_a_complete_pair(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            suffix = ".exe" if os.name == "nt" else ""
            local = root / "tools" / "ffmpeg"
            local.mkdir(parents=True)
            (local / f"ffmpeg{suffix}").write_bytes(b"fixture")
            with patch.object(media_tools.sys, "prefix", str(root)), patch.object(media_tools.shutil, "which", return_value=None):
                self.assertIsNone(media_tools.find_installed_tools())
                (local / f"ffprobe{suffix}").write_bytes(b"fixture")
                self.assertEqual(media_tools.find_installed_tools(), (local / f"ffmpeg{suffix}", local / f"ffprobe{suffix}"))

    def test_working_pair_does_not_trigger_network_installation(self):
        tools = (Path("ffmpeg"), Path("ffprobe"))
        with patch.object(media_tools, "find_installed_tools", return_value=tools), patch.object(media_tools, "verify_tools"), patch.object(media_tools.urllib.request, "urlopen") as download:
            self.assertEqual(media_tools.install_tools(), tools)
            download.assert_not_called()

    def test_bad_tool_is_rejected(self):
        with patch.object(media_tools.subprocess, "run", return_value=subprocess.CompletedProcess([], 1)):
            with self.assertRaisesRegex(RuntimeError, "could not run"):
                media_tools.verify_tools((Path("ffmpeg"), Path("ffprobe")))

    @unittest.skipUnless(os.name == "nt", "Automatic media installer is Windows-only")
    def test_bad_checksum_preserves_existing_tools(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "tools" / "ffmpeg"
            target.mkdir(parents=True)
            old = target / "ffmpeg.exe"
            old.write_bytes(b"old tool")
            with patch.object(media_tools, "find_installed_tools", return_value=None), patch.object(media_tools.sys, "prefix", str(root)), patch.object(media_tools.sys, "base_prefix", str(root / "base")), patch.object(media_tools.urllib.request, "urlopen", side_effect=[io.BytesIO(b"0" * 64), io.BytesIO(b"bad archive")]):
                with self.assertRaisesRegex(RuntimeError, "checksum verification failed"):
                    media_tools.install_tools()
            self.assertEqual(old.read_bytes(), b"old tool")
            self.assertFalse((target / "ffprobe.exe").exists())
