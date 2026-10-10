import importlib.util
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from tests.fixtures import ARTIST, client_for, edition, group
from ytdlp_studio import backend as core, cli
from ytdlp_studio.catalogue import artist_discography


class ArtistLayoutTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.settings = core.Settings(output_dir=str(self.root / "Artist"), sponsorblock=False,
                                      spotify_album_folders=False, sync_enabled=True,
                                      sync_dir=str(self.root / "NAS"), sync_library_root=str(self.root / "NAS"))
        self.catalogue = artist_discography(ARTIST, True, client_for([group(edition(1)), group(edition(2))]))
        for mock in (patch.object(core, "spotdl_available", return_value=True),
                     patch.object(core, "find_ffmpeg_location", return_value="ffmpeg.exe"),
                     patch.object(core, "find_js_runtime", return_value=None)):
            mock.start()
            self.addCleanup(mock.stop)

    def test_flat_jobs_share_destinations_archive_and_safe_skip_policy(self):
        jobs = core.build_discography_jobs(self.catalogue, self.settings)
        self.assertEqual(len(jobs), 2)
        self.assertNotEqual(jobs[0].id, jobs[1].id)
        for job in jobs:
            self.assertEqual(job.settings.output_dir, self.settings.output_dir)
            self.assertEqual(core.resolve_sync_target(job.settings), self.root / "NAS" / "Artist")
            self.assertEqual(Path(job.command[job.command.index("--archive") + 1]), self.root / "Artist" / "spotdl-archive.txt")
            self.assertEqual(job.command[job.command.index("--overwrite") + 1], "skip")
            self.assertIn("--scan-for-songs", job.command)
            self.assertEqual(job.release_id, "")
            self.assertNotIn("{track-number}", job.settings.spotdl_template)
        self.assertFalse((self.root / "Artist").exists())

    def test_flat_templates_never_create_subfolders_or_escape_the_selected_root(self):
        for template in ("{album}/{title}.{output-ext}", r"{album}\{title}.{output-ext}",
                         r"C:\Elsewhere\{title}.{output-ext}", "../{title}.{output-ext}",
                         "fixed-name", "..", "", "{track-number}"):
            with self.subTest(template=template):
                self.settings.spotdl_template = template
                jobs = core.build_discography_jobs(self.catalogue, self.settings)
                for job in jobs:
                    output = Path(job.command[job.command.index("--output") + 1])
                    self.assertEqual(output.parent, self.root / "Artist")
                    self.assertIn("{title}", output.name)
                self.assertEqual(self.settings.spotdl_template, template)

    def test_album_layout_and_existing_job_snapshots_survive_switching_modes(self):
        flat = core.build_discography_jobs(self.catalogue, self.settings)
        self.settings.spotify_album_folders = True
        album = core.build_discography_jobs(self.catalogue, self.settings)
        self.assertTrue(all(Path(job.settings.output_dir).parent == self.root / "Artist" for job in album))
        self.assertTrue(all(job.release_id for job in album))
        self.assertTrue(all(job.settings.output_dir == str(self.root / "Artist") for job in flat))
        self.assertTrue(all(not job.settings.spotify_album_folders for job in flat))

    def test_archive_can_be_disabled_without_removing_existing_history(self):
        folder = self.root / "Artist"
        folder.mkdir()
        archive = folder / "spotdl-archive.txt"
        archive.write_text("existing history", encoding="utf-8")
        self.settings.write_archive = False
        for job in core.build_discography_jobs(self.catalogue, self.settings):
            self.assertEqual(job.command[job.command.index("--archive") + 1], "")
        self.assertEqual(archive.read_text(encoding="utf-8"), "existing history")

    def test_actual_flat_mirror_is_idempotent_and_does_not_copy_archives(self):
        jobs = core.build_discography_jobs(self.catalogue, self.settings)
        folder = self.root / "Artist"
        folder.mkdir()
        audio = folder / "Artist - Song.wav"
        with wave.open(str(audio), "wb") as stream:
            stream.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
            stream.writeframes(b"\x00\x00" * 8000)
        (folder / "spotdl-archive.txt").write_text("private history", encoding="utf-8")
        target = self.root / "NAS" / "Artist" / audio.name
        self.assertEqual(core.finish_download_job(jobs[0], lambda _: None), 0)
        original = target.stat().st_mtime_ns
        self.assertEqual(core.finish_download_job(jobs[1], lambda _: None), 0)
        self.assertEqual(target.stat().st_mtime_ns, original)
        self.assertEqual(target.read_bytes(), audio.read_bytes())
        self.assertEqual({p.name for p in target.parent.iterdir()}, {audio.name})
        self.assertFalse((folder / ".studio-release.json").exists())

    def test_layout_roundtrips_saved_settings(self):
        with patch.object(core, "CONFIG_DIR", self.root), patch.object(core, "CONFIG_FILE", self.root / "settings.json"):
            for value in (False, True):
                self.settings.spotify_album_folders = value
                core.save_settings(self.settings)
                self.assertEqual(core.load_settings().spotify_album_folders, value)

    def test_cli_flat_discography_previews_shared_folder_without_downloading(self):
        self.settings.spotify_album_folders = True
        with patch.object(core, "load_settings", return_value=self.settings), patch.object(core, "request_catalogue", return_value=self.catalogue), patch.object(core, "run_download_job") as run, patch("builtins.print") as output:
            result = cli.run_cli(cli.parse_args(["--discography", ARTIST, "--flat-discography", "--print-command"]))
        self.assertEqual(result, 0)
        self.assertFalse(self.settings.spotify_album_folders)
        commands = [call.args[0] for call in output.call_args_list if call.args and "--output" in str(call.args[0])]
        self.assertEqual(len(commands), 2)
        self.assertTrue(all("{artists} - {title}.{output-ext}" in str(command) for command in commands))
        run.assert_not_called()

    @unittest.skipUnless(importlib.util.find_spec("spotdl"), "Optional Spotify dependency")
    def test_native_formatter_creates_real_flat_filenames(self):
        from spotdl.types.song import Song
        from spotdl.utils.formatter import create_file_name
        song = Song(name="Test Song", artists=["Artist"], artist="Artist", genres=[], disc_number=1,
                    disc_count=1, album_name="Release", album_artist="Artist", duration=180, year=2026,
                    date="2026-10-10", track_number=1, tracks_count=1, song_id="A" * 22,
                    explicit=False, publisher="", url="https://open.spotify.com/track/" + "A" * 22,
                    isrc=None, cover_url=None, copyright_text=None)
        for template in ("{album}/{title}.{output-ext}", "fixed-name", ".."):
            self.settings.spotdl_template = template
            job = core.build_discography_jobs(self.catalogue, self.settings)[0]
            output = job.command[job.command.index("--output") + 1]
            actual = create_file_name(song, output, "mp3")
            self.assertEqual(actual.parent, self.root / "Artist")
            self.assertIn("Test Song", actual.name)
