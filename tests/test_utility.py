import ctypes
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from ytdlp_studio import backend as app
from ytdlp_studio import cli


class UtilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        reports = patch.object(app, "CONFIG_DIR", self.root / "config")
        reports.start()
        self.addCleanup(reports.stop)
        self.settings = app.Settings(output_dir=str(self.root / "local"), sponsorblock=False)

    def test_auto_routes_only_real_spotify_hosts(self):
        self.assertEqual(app.provider_for("https://open.spotify.com/track/id", self.settings), "spotDL")
        self.assertEqual(app.provider_for("https://spotify.link/id", self.settings), "spotDL")
        self.assertEqual(app.provider_for("https://example.com/open.spotify.com", self.settings), "yt-dlp")
        self.assertEqual(app.provider_for("https://open.spotify.com.example.com/track/id", self.settings), "yt-dlp")

    def test_legacy_polluted_mirror_path_is_recovered(self):
        settings = app.Settings(output_dir="D:/Music/Artist", sync_dir="G:/Multimedia/Music/A/Music/Artist")
        settings = app.normalize_settings(settings)
        self.assertEqual(Path(settings.sync_library_root), Path("G:/Multimedia/Music"))
        self.assertEqual(app.resolve_sync_target(settings), Path("G:/Multimedia/Music/Artist"))
        for name in ("A", "Music", "Other Artist", "Artist"):
            settings.output_dir = "D:/Music/" + name
            self.assertEqual(app.resolve_sync_target(settings), Path("G:/Multimedia/Music") / name)

    def test_explicit_nested_root_is_preserved(self):
        settings = app.Settings(output_dir="D:/Music/Artist", sync_dir="G:/Music/Curated",
                               sync_library_root="G:/Music/Curated")
        self.assertEqual(app.resolve_sync_target(settings), Path("G:/Music/Curated/Artist"))

    @patch.object(app, "find_ytdlp_command", return_value=["python", "-m", "yt_dlp"])
    @patch.object(app, "find_js_runtime", return_value=None)
    @patch.object(app, "find_ffmpeg_location", return_value="ffmpeg.exe")
    @patch.object(app, "spotdl_available", return_value=True)
    def test_mixed_batch_and_snapshot(self, *_):
        inputs = ["https://youtube.com/watch?v=1", "https://open.spotify.com/track/id"]
        jobs = app.build_jobs(inputs + inputs, self.settings)
        self.assertEqual([job.provider for job in jobs], ["yt-dlp", "spotDL"])
        self.assertEqual([len(job.urls) for job in jobs], [1, 1])
        self.settings.output_dir = "changed"
        self.assertEqual(jobs[0].settings.output_dir, str(self.root / "local"))
        self.assertIn("--ffmpeg", jobs[1].command)
        self.assertIn("--scan-for-songs", jobs[1].command)
        self.assertNotIn("--download-archive", jobs[1].command)

    @patch.object(app, "spotdl_available", return_value=False)
    def test_missing_optional_dependency_has_actionable_error(self, _):
        with self.assertRaisesRegex(ValueError, "install.ps1 -Spotify"):
            app.build_jobs(["https://open.spotify.com/track/id"], self.settings)

    @patch.object(app, "find_ffmpeg_location", return_value="ffmpeg.exe")
    @patch.object(app, "spotdl_available", return_value=True)
    def test_spotify_archive_can_be_disabled(self, *_):
        self.settings.write_archive = False
        command = app.build_spotdl_command(["Artist - Song"], self.settings)
        self.assertEqual(command[command.index("--archive") + 1], "")

    @patch.object(app, "spotdl_available", return_value=True)
    def test_spotify_rejects_video_mode(self, _):
        self.settings.mode = "video"
        with self.assertRaisesRegex(ValueError, "Audio"):
            app.build_spotdl_command(["https://open.spotify.com/track/id"], self.settings)

    @patch.object(app, "find_installed_tools", return_value=(Path("ffmpeg.exe"), Path("ffprobe.exe")))
    @patch.object(app, "find_ytdlp_command", return_value=["yt-dlp"])
    @patch.object(app, "find_js_runtime", return_value=None)
    @patch.object(app, "find_ffmpeg_location", return_value="ffmpeg.exe")
    @patch.object(app, "spotdl_available", return_value=True)
    def test_sponsorblock_enabled_for_both_providers(self, *_):
        import shlex
        self.settings.sponsorblock = True
        jobs = app.build_jobs(["https://youtube.com/watch?v=id", "https://open.spotify.com/track/id"], self.settings)
        categories = jobs[0].command[jobs[0].command.index("--sponsorblock-remove") + 1].split(",")
        self.assertIn("sponsor", categories)
        self.assertIn("outro", categories)
        self.assertIn("music_offtopic", categories)
        self.assertIn("--sponsor-block", jobs[1].command)
        backend = shlex.split(jobs[1].command[jobs[1].command.index("--yt-dlp-args") + 1])
        self.assertEqual(backend[backend.index("--ffmpeg-location") + 1], "ffmpeg.exe")

    @patch.object(app, "find_installed_tools", return_value=None)
    @patch.object(app, "find_ytdlp_command", return_value=["yt-dlp"])
    @patch.object(app, "find_js_runtime", return_value=None)
    @patch.object(app, "find_ffmpeg_location", return_value="ffmpeg.exe")
    def test_sponsorblock_missing_probe_has_repair_message(self, *_):
        self.settings.sponsorblock = True
        with self.assertRaisesRegex(ValueError, "ffprobe.*install.ps1"):
            app.build_command(["https://youtube.com/watch?v=id"], self.settings)

    @patch.object(app, "find_ytdlp_command", return_value=["yt-dlp"])
    @patch.object(app, "find_js_runtime", return_value=None)
    @patch.object(app, "find_ffmpeg_location", return_value="ffmpeg.exe")
    @patch.object(app, "spotdl_available", return_value=True)
    def test_sponsorblock_can_be_disabled_for_both_providers(self, *_):
        self.assertNotIn("--sponsorblock-remove", app.build_command(["https://youtube.com/watch?v=id"], self.settings))
        self.assertNotIn("--sponsor-block", app.build_spotdl_command(["Artist - Song"], self.settings))

    def test_sponsorblock_default_and_cli_override(self):
        self.assertTrue(app.Settings().sponsorblock)
        args = cli.parse_args(["https://youtube.com/watch?v=id", "--no-sponsorblock"])
        self.assertTrue(args.no_sponsorblock)
        with patch.object(app, "load_settings", return_value=app.Settings(output_dir=str(self.root))), patch.object(app, "build_jobs", return_value=[]) as build:
            args.print_command = True
            self.assertEqual(cli.run_cli(args), 0)
            self.assertFalse(build.call_args.args[1].sponsorblock)

    def test_sync_copies_media_but_not_archives_or_review(self):
        source = Path(self.settings.output_dir)
        source.mkdir()
        (source / "track.mp3").write_bytes(b"music")
        (source / "spotdl-archive.txt").write_text("archive")
        (source / "unfinished.mp3.sync-temp").write_bytes(b"partial")
        (source / app.DUPLICATE_REVIEW_DIR).mkdir()
        (source / app.DUPLICATE_REVIEW_DIR / "track.mp3").write_bytes(b"review")
        self.settings.sync_enabled = True
        self.settings.sync_dir = str(self.root / "mirror")
        self.settings.sync_match_output_folder = False
        self.assertEqual(app.sync_output_dir(self.settings, lambda _: None), 0)
        self.assertEqual(list((self.root / "mirror").iterdir()), [self.root / "mirror" / "track.mp3"])

    def test_failed_sync_preserves_existing_mirror_file(self):
        source = Path(self.settings.output_dir)
        source.mkdir()
        (source / "track.mp3").write_bytes(b"new music")
        target = self.root / "mirror"
        target.mkdir()
        (target / "track.mp3").write_bytes(b"old music")
        self.settings.sync_enabled = True
        self.settings.sync_dir = str(target)
        self.settings.sync_match_output_folder = False
        with patch.object(app.shutil, "copy2", side_effect=OSError("NAS offline")):
            self.assertEqual(app.sync_output_dir(self.settings, lambda _: None), 1)
        self.assertEqual((target / "track.mp3").read_bytes(), b"old music")

    def test_explicit_sync_files_resolve_aliases_and_preserve_names(self):
        source = Path(self.settings.output_dir)
        source.mkdir()
        prefix = self.root / "path-alias"
        prefix.mkdir()
        (source / "track.mp3").write_bytes(b"music")
        (source / ".studio-release.json").write_text('{"release_id": "fixture"}')
        self.settings.sync_enabled = True
        self.settings.sync_dir = str(self.root / "mirror")
        self.settings.sync_match_output_folder = False
        files = [prefix / ".." / "local" / name for name in ("track.mp3", ".studio-release.json")]
        original_settings = self.settings.output_dir
        self.assertEqual(app.sync_output_dir(self.settings, lambda _: None, files=files), 0)
        self.assertEqual((self.root / "mirror" / "track.mp3").read_bytes(), b"music")
        self.assertEqual((self.root / "mirror" / ".studio-release.json").read_text(), '{"release_id": "fixture"}')
        self.assertEqual(self.settings.output_dir, original_settings)

    def test_explicit_sync_rejects_files_resolving_outside_library(self):
        source = Path(self.settings.output_dir)
        source.mkdir()
        valid = source / "track.mp3"
        valid.write_bytes(b"music")
        outside = self.root / "outside.mp3"
        outside.write_bytes(b"private")
        self.settings.sync_enabled = True
        self.settings.sync_dir = str(self.root / "mirror")
        self.settings.sync_match_output_folder = False
        output = []
        files = [source / ".." / outside.name, valid]
        self.assertEqual(app.sync_output_dir(self.settings, output.append, files=files), 1)
        self.assertEqual(outside.read_bytes(), b"private")
        self.assertEqual([file.name for file in (self.root / "mirror").rglob("*")], [valid.name])
        self.assertIn("could not validate library file", "".join(output))

    @unittest.skipUnless(sys.platform == "win32", "Windows path aliases")
    def test_explicit_sync_accepts_windows_short_path_alias(self):
        source = Path(self.settings.output_dir)
        source.mkdir()
        media = source / "long track filename.mp3"
        media.write_bytes(b"music")
        get_short_path = ctypes.windll.kernel32.GetShortPathNameW
        get_short_path.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint]
        get_short_path.restype = ctypes.c_uint
        buffer = ctypes.create_unicode_buffer(32768)
        length = get_short_path(str(media), buffer, len(buffer))
        if not length or length >= len(buffer) or buffer.value == str(media):
            self.skipTest("8.3 path aliases are unavailable on this volume")
        self.settings.sync_enabled = True
        self.settings.sync_dir = str(self.root / "mirror")
        self.settings.sync_match_output_folder = False
        self.assertEqual(app.sync_output_dir(self.settings, lambda _: None, files=[Path(buffer.value)]), 0)
        self.assertEqual((self.root / "mirror" / media.name).read_bytes(), b"music")

    def test_nested_mirror_is_rejected(self):
        source = Path(self.settings.output_dir)
        source.mkdir()
        self.settings.sync_enabled = True
        self.settings.sync_dir = str(source / "nested")
        self.settings.sync_match_output_folder = False
        with self.assertRaises(ValueError):
            app.validate_download(self.settings)
        self.assertEqual(app.sync_output_dir(self.settings, lambda _: None), 1)
        self.assertFalse((source / "nested").exists())

    def test_silent_process_cancellation(self):
        stop = threading.Event()
        timer = threading.Timer(0.3, stop.set)
        timer.start()
        started = time.monotonic()
        try:
            code = app.run_command([sys.executable, "-c", "import time; time.sleep(20)"],
                                   lambda _: None, stop, self.settings.output_dir)
        finally:
            timer.join()
        self.assertEqual(code, 130)
        self.assertLess(time.monotonic() - started, 6)

    def test_missing_executable_returns_error(self):
        self.assertEqual(app.run_command([str(self.root / "missing.exe")], lambda _: None), 127)

    def test_spotdl_song_errors_are_not_reported_as_success(self):
        job = app.DownloadJob(["spotify link"], self.settings, provider="spotDL", command=["spotdl"])
        def failed_track(command, *_):
            Path(command[-1]).write_text("Some tracks failed", encoding="utf-8")
            return 0
        with patch.object(app, "run_command", side_effect=failed_track):
            self.assertEqual(app.run_download_job(job, lambda _: None), 1)

    def test_exact_review_keeps_different_recordings(self):
        source = Path(self.settings.output_dir)
        source.mkdir()
        (source / "Artist - Song.mp3").write_bytes(b"studio")
        (source / "Artist - Song Live.mp3").write_bytes(b"live")
        (source / "Artist - Song Copy.mp3").write_bytes(b"studio")
        plan = app.build_duplicate_plan(str(source), exact_only=True)
        self.assertEqual(len(plan.groups), 1)
        self.assertEqual(len(plan.groups[0][1]), 1)

    def test_different_nas_recording_is_not_moved_by_name(self):
        source = self.root / "local"
        source.mkdir()
        duplicate = source / "song.mp3"
        duplicate.write_bytes(b"local recording")
        mirror = self.root / "mirror"
        mirror.mkdir()
        copy = mirror / "song.mp3"
        copy.write_bytes(b"different NAS recording")
        app.move_matching_sync_duplicate(source, duplicate, mirror, lambda _: None, app.file_hash(duplicate))
        self.assertTrue(copy.exists())
        copy.write_bytes(b"local recording")
        app.move_matching_sync_duplicate(source, duplicate, mirror, lambda _: None, app.file_hash(duplicate))
        self.assertFalse(copy.exists())
        self.assertTrue((mirror / app.DUPLICATE_REVIEW_DIR / "song.mp3").exists())

    def test_saved_settings_are_atomic_and_round_trip(self):
        with patch.object(app, "CONFIG_DIR", self.root), patch.object(app, "CONFIG_FILE", self.root / "settings.json"):
            self.settings.provider = "spotDL"
            app.save_settings(self.settings)
            self.assertEqual(app.load_settings().provider, "spotDL")
            self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_invalid_settings_do_not_crash_startup(self):
        config = self.root / "settings.json"
        with patch.object(app, "CONFIG_FILE", config):
            config.write_text('["not a settings object"]')
            self.assertEqual(app.load_settings().provider, "Auto")
            config.write_text('{"mode": [], "output_dir": 123, "provider": "spotDL"}')
            self.assertEqual(app.load_settings().mode, "audio")
            self.assertEqual(app.load_settings().provider, "spotDL")

    def test_quoted_extra_arguments_are_passed_without_literal_quotes(self):
        self.assertEqual(app.split_extra_args('--match-filter "duration < 600"'),
                         ["--match-filter", "duration < 600"])
        if sys.platform == "win32":
            self.assertEqual(app.split_extra_args('--cookies "C:\\Music Library\\cookies.txt"'),
                             ["--cookies", "C:\\Music Library\\cookies.txt"])

    @patch.object(app, "find_ytdlp_command", return_value=["yt-dlp"])
    @patch.object(app, "find_js_runtime", return_value=None)
    @patch.object(app, "find_ffmpeg_location", return_value="ffmpeg.exe")
    @patch.object(app, "spotdl_available", return_value=True)
    def test_cli_mixed_batch_and_no_archive(self, *_):
        args = cli.parse_args(["https://youtube.com/watch?v=id", "https://open.spotify.com/track/id", "--no-archive"])
        with patch.object(app, "load_settings", return_value=self.settings), patch.object(app, "run_download_job", return_value=0) as run:
            self.assertEqual(cli.run_cli(args), 0)
        self.assertEqual(run.call_count, 2)
        jobs = [call.args[0] for call in run.call_args_list]
        self.assertNotIn("--download-archive", jobs[0].command)
        self.assertEqual(jobs[1].command[jobs[1].command.index("--archive") + 1], "")

    def test_cli_sync_only(self):
        source = Path(self.settings.output_dir)
        source.mkdir()
        (source / "song.mp3").write_bytes(b"music")
        args = cli.parse_args(["--sync-only", "--sync-dir", str(self.root / "mirror"), "--sync-flat"])
        with patch.object(app, "load_settings", return_value=self.settings), patch.object(app, "cli_output"):
            self.assertEqual(cli.run_cli(args), 0)
        self.assertEqual((self.root / "mirror" / "song.mp3").read_bytes(), b"music")


if __name__ == "__main__":
    unittest.main()
