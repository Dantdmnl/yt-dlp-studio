import json
import tempfile
import threading
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from ytdlp_studio import backend as app


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        reports = patch.object(app, "CONFIG_DIR", self.root / "config")
        reports.start()
        self.addCleanup(reports.stop)
        self.settings = app.Settings(output_dir=str(self.root / "local"), sync_enabled=True,
                                     sync_dir=str(self.root / "nas"), sync_match_output_folder=False,
                                     sponsorblock=False, auto_dedupe_after_download=False)

    def test_partial_album_retains_failed_urls_and_mirrors_only_valid_audio(self):
        failed = "https://open.spotify.com/track/" + "A" * 22
        job = app.DownloadJob(["https://open.spotify.com/album/" + "B" * 22], self.settings, provider="spotDL", command=["fixture"])
        def download(command, output, cancel, directory):
            folder = Path(directory)
            folder.mkdir()
            with wave.open(str(folder / "good.wav"), "wb") as audio:
                audio.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
                audio.writeframes(b"\x00\x00" * 8000)
            (folder / "bad.mp3").write_bytes(b"not audio")
            (folder / "thumbnail.jpg").write_bytes(b"not music")
            (folder / "spotdl-archive.txt").write_text("private archive")
            Path(command[-1]).write_text(f"Lookup failed: {failed}", encoding="utf-8")
            report = Path(command[command.index("--studio-report") + 1])
            report.write_text(json.dumps({"url": "completed", "path": str(folder / "good.wav"), "status": "completed"}) + "\n" +
                              json.dumps({"url": failed, "status": "failed"}) + "\n", encoding="utf-8")
            return 0
        with patch.object(app, "run_command", side_effect=download):
            self.assertEqual(app.run_download_job(job, lambda _: None), 1)
        self.assertEqual(job.failed_urls, [failed])
        self.assertEqual(len(job.completed_files), 1)
        self.assertTrue(Path(job.report_path).is_file())
        self.assertEqual(app.finish_download_job(job, lambda _: None), 0)
        self.assertEqual({p.name for p in (self.root / "nas").iterdir()}, {"good.wav"})
        self.assertEqual((self.root / "nas" / "good.wav").read_bytes(), (self.root / "local" / "good.wav").read_bytes())

    def test_cancellation_does_not_start_a_mirror(self):
        job = app.DownloadJob([], self.settings, provider="spotDL")
        cancel = threading.Event()
        cancel.set()
        with patch.object(app, "sync_output_dir") as sync:
            self.assertEqual(app.finish_download_job(job, lambda _: None, cancel), 130)
        sync.assert_not_called()
