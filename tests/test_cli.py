import contextlib
import io
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from ytdlp_studio import backend, cli
from ytdlp_studio.version import APP_VERSION
from tests.fixtures import ARTIST


class CliTests(unittest.TestCase):
    def test_conflicting_format_or_archive_flags_are_rejected(self):
        for flags in (["--audio", "--video"], ["--archive", "--no-archive"]):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                cli.parse_args(flags)
            self.assertEqual(error.exception.code, 2)

    def test_discography_cannot_be_silently_ignored_by_maintenance(self):
        for flag in ("--sync-only", "--dedupe", "--dedupe-apply"):
            with patch.object(backend, "load_settings", return_value=backend.Settings()), patch.object(backend, "cli_output"):
                self.assertEqual(cli.run_cli(cli.parse_args(["--discography", ARTIST, flag])), 2)

    def test_dedupe_error_returns_error_code_without_traceback(self):
        with patch.object(backend, "run_deduplicator", side_effect=OSError("Unavailable")), patch.object(backend, "cli_output") as output:
            self.assertEqual(cli.run_cli(cli.parse_args(["--dedupe"])), 2)
            self.assertIn("Unavailable", output.call_args.args[0])

    def test_cli_is_headless_and_old_launcher_works_from_another_directory(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run([sys.executable, "-B", "-c", "import sys; import ytdlp_studio.cli; assert 'tkinter' not in sys.modules"], cwd=root, capture_output=True, **backend.subprocess_window_kwargs())
        self.assertEqual(result.returncode, 0, result.stderr)
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, "-B", str(root / "ytdlp_wrapper.py"), "--version"], cwd=directory,
                                    capture_output=True, text=True, **backend.subprocess_window_kwargs())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(APP_VERSION, result.stdout)

    def test_precancelled_job_never_starts_a_subprocess(self):
        cancelled = threading.Event()
        cancelled.set()
        with patch.object(backend.subprocess, "Popen") as popen:
            self.assertEqual(backend.run_command(["ignored"], lambda _: None, cancelled), 130)
            popen.assert_not_called()

    def test_download_does_not_mask_postprocessing_errors(self):
        with patch.object(backend, "find_ytdlp_command", return_value=["yt-dlp"]), patch.object(backend, "find_ffmpeg_location", return_value=None), patch.object(backend, "find_js_runtime", return_value=None):
            command = backend.build_command(["https://example.com"], backend.Settings(sponsorblock=False))
        self.assertNotIn("--ignore-errors", command)
        self.assertIn("--no-abort-on-error", command)

    def test_video_quality_caps_apply_to_every_fallback(self):
        for name, cap in (("1080p MP4", "1080"), ("720p MP4", "720"), ("Small compatible MP4", "480")):
            for alternative in backend.VIDEO_PRESETS[name].split("/"):
                self.assertIn(f"height<={cap}", alternative)


if __name__ == "__main__":
    unittest.main()
