import asyncio
import importlib.util
import json
import tempfile
import types
import unittest
import wave
from pathlib import Path
from unittest.mock import AsyncMock, patch

from ytdlp_studio import download_host


@unittest.skipUnless(importlib.util.find_spec("spotdl"), "Optional Spotify dependency")
class DownloadHostTests(unittest.TestCase):
    def setUp(self):
        from spotdl.providers.audio.ytmusic import YouTubeMusic
        from spotdl.providers.audio.base import YTDLLogger
        from spotdl.download.downloader import Downloader
        self.provider, self.logger, self.downloader = YouTubeMusic, YTDLLogger, Downloader
        for target, name in ((YouTubeMusic, "_create_client"), (YTDLLogger, "error"),
                             (YTDLLogger, "warning"), (Downloader, "async_search_and_download")):
            restore = patch.object(target, name, target.__dict__[name])
            restore.start()
            self.addCleanup(restore.stop)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.report = self.root / "tracks.jsonl"
        self.song = types.SimpleNamespace(url="https://open.spotify.com/track/" + "A" * 22)

    def test_client_is_english_without_disabling_native_recording_filter(self):
        with patch("ytmusicapi.YTMusic") as client:
            download_host.configure_spotdl()
            self.provider._create_client()
        client.assert_called_once_with(language="en")

    def test_underlying_extraction_error_is_visible(self):
        from spotdl.providers.audio.base import AudioProviderError
        download_host.configure_spotdl()
        with patch("builtins.print") as output:
            with self.assertRaises(AudioProviderError):
                self.logger().error("Unavailable source recording")
        self.assertIn("Unavailable source recording", output.call_args.args[0])

    def record_result(self, path, known=None, errors=None):
        self.downloader.async_search_and_download = AsyncMock(return_value=(self.song, path))
        download_host.configure_spotdl(self.report)
        fake = types.SimpleNamespace(known_songs=known or {}, errors=errors or [])
        asyncio.run(self.downloader.async_search_and_download(fake, self.song))
        return json.loads(self.report.read_text(encoding="utf-8"))

    def test_report_preserves_failed_track_identity(self):
        record = self.record_result(None, errors=["Lookup failed " + self.song.url])
        self.assertEqual(record, {"url": self.song.url, "path": None, "status": "failed"})

    def test_known_existing_song_is_complete_not_false_failure(self):
        audio = self.root / "existing.wav"
        with wave.open(str(audio), "wb") as stream:
            stream.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
            stream.writeframes(b"\x00\x00" * 8000)
        record = self.record_result(self.root / "nonexistent.mp3", known={self.song.url: [audio]})
        self.assertEqual(record["status"], "completed")
        self.assertEqual(Path(record["path"]), audio.resolve())

    def test_unreadable_output_is_not_reported_as_complete(self):
        audio = self.root / "bad.mp3"
        audio.write_bytes(b"not audio")
        self.assertEqual(self.record_result(audio)["status"], "failed")

    def test_intentionally_skipped_song_is_not_failed(self):
        self.assertEqual(self.record_result(None)["status"], "skipped")
