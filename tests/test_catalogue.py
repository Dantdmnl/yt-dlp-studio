import sys
import tempfile
import threading
import unittest
import wave
from pathlib import Path
from unittest.mock import Mock, patch

from ytdlp_studio import catalogue
from ytdlp_studio import backend as app
from ytdlp_studio import cli


from tests.fixtures import ARTIST, client_for, edition, group, page


class CatalogueTests(unittest.TestCase):
    def test_every_artist_page_and_same_title_releases_are_preserved(self):
        client = client_for([group(edition(n)) for n in range(121)])
        result = catalogue.artist_discography(ARTIST, client=client)
        self.assertEqual(len(result["items"]), 121)
        self.assertEqual([call.kwargs["offset"] for call in client.get_artist_discography.call_args_list], [0, 50, 100])
        self.assertEqual(len({item["id"] for item in result["items"]}), 121)

    def test_default_edition_is_playable_and_all_editions_are_distinct(self):
        groups = [group(edition(1, playable=False), edition(2)), group(edition(3, kind="EP"))]
        default = catalogue.artist_discography(ARTIST, client=client_for(groups))
        self.assertEqual([item["id"] for item in default["items"]], [f"{2:022d}", f"{3:022d}"])
        self.assertEqual(default["items"][1]["kind"], "ep")
        expanded = catalogue.artist_discography(ARTIST, True, client_for(groups))
        self.assertEqual(len(expanded["items"]), 3)

    def test_identical_release_ids_are_deduplicated_not_titles(self):
        result = catalogue.artist_discography(ARTIST, True, client_for([group(edition(1)), group(edition(1), edition(2))]))
        self.assertEqual(len(result["items"]), 2)

    def test_truncated_artist_page_is_an_error_not_partial_success(self):
        client = client_for([])
        client.get_artist_discography.side_effect = [page([group(edition(1))], 2), page([], 2)]
        with self.assertRaisesRegex(ValueError, "stopped at 1 of 2"):
            catalogue.artist_discography(ARTIST, client=client)

    def test_repeated_artist_page_is_an_error(self):
        client = client_for([])
        client.get_artist_discography.side_effect = [page([group(edition(1))], 2)] * 2
        with self.assertRaisesRegex(ValueError, "repeated"):
            catalogue.artist_discography(ARTIST, client=client)

    def test_partially_overlapping_artist_pages_are_rejected(self):
        client = client_for([])
        client.get_artist_discography.side_effect = [page([group(edition(1)), group(edition(2))], 3), page([group(edition(2))], 3)]
        with self.assertRaisesRegex(ValueError, "repeated release groups"):
            catalogue.artist_discography(ARTIST, client=client)

    def test_catalogue_change_during_paging_requires_retry(self):
        client = client_for([])
        client.get_artist_discography.side_effect = [page([group(edition(1))], 2), page([group(edition(2))], 3)]
        with self.assertRaisesRegex(ValueError, "changed while paging"):
            catalogue.artist_discography(ARTIST, client=client)

    def test_malformed_discography_response_is_an_error(self):
        client = client_for([])
        client.get_artist_discography.side_effect = None
        client.get_artist_discography.return_value = {"data": {}}
        with self.assertRaisesRegex(ValueError, "incomplete"):
            catalogue.artist_discography(ARTIST, client=client)

    def test_empty_catalogue_is_valid_discovery(self):
        result = catalogue.artist_discography(ARTIST, client=client_for([]))
        self.assertEqual(result["items"], [])

    def test_malformed_release_ids_and_impossible_counts_are_rejected(self):
        for response in (page([group({"id": None}, edition(1))], 1), page([], -1), page([group(edition(1)), group(edition(2))], 1)):
            client = client_for([])
            client.get_artist_discography.side_effect = None
            client.get_artist_discography.return_value = response
            with self.assertRaises(ValueError):
                catalogue.artist_discography(ARTIST, client=client)

    def test_artist_url_validation_supports_uri_and_localized_url(self):
        identity = "A" * 22
        self.assertEqual(catalogue.spotify_id(f"spotify:artist:{identity}", "artist"), identity)
        self.assertEqual(catalogue.spotify_id(f"https://open.spotify.com/intl-nl/artist/{identity}?si=abc", "artist"), identity)
        for value in ("https://open.spotify.com.example.org/artist/" + identity, "https://open.spotify.com/artist/short", ARTIST.replace("artist", "album")):
            with self.assertRaises(ValueError):
                catalogue.spotify_id(value, "artist")

    def test_search_uses_requested_page_and_parses_track_metadata(self):
        client = Mock()
        data = {"uri": "spotify:track:" + "T" * 22, "name": "Song",
                "duration": {"totalMilliseconds": 125000}, "artists": {"items": [{"profile": {"name": "Artist"}}]}}
        client.query_songs.return_value = {"data": {"searchV2": {"tracksV2": {"items": [{"item": {"data": data}}], "totalCount": 100, "pagingInfo": {"nextOffset": 50}}}}}
        result = catalogue.search_spotify("Song", "tracks", 25, 25, client)
        client.query_songs.assert_called_once_with("Song", limit=25, offset=25)
        self.assertEqual(result["offset"], 50)
        self.assertTrue(result["more"])
        self.assertEqual(result["items"][0]["artist"], "Artist")
        self.assertEqual(result["items"][0]["info"], "2:05")

    def test_artist_search_and_end_of_results(self):
        client = Mock()
        data = {"uri": "spotify:artist:" + "A" * 22, "profile": {"name": "Artist"}}
        client.query_artists.return_value = {"data": {"searchV2": {"artists": {"items": [{"data": data}], "totalCount": 1, "pagingInfo": {"nextOffset": None}}}}}
        result = catalogue.search_spotify("Artist", "artists", 0, 25, client)
        self.assertFalse(result["more"])
        self.assertEqual(result["items"][0]["kind"], "artist")

    def test_subprocess_errors_timeouts_cancellation_and_unicode(self):
        result = catalogue.run_json_command([sys.executable, "-c", "import json; print(json.dumps({'title':chr(0x65e5)},ensure_ascii=False))"])
        self.assertEqual(result["title"], chr(0x65e5))
        with self.assertRaisesRegex(ValueError, "failed"):
            catalogue.run_json_command([sys.executable, "-c", "raise SystemExit(3)"])
        with self.assertRaises(TimeoutError):
            catalogue.run_json_command([sys.executable, "-c", "import time; time.sleep(10)"], timeout=0)
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(InterruptedError):
            catalogue.run_json_command([sys.executable, "-c", "import time; time.sleep(10)"], cancel)


class DiscographyJobTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.settings = app.Settings(output_dir=str(self.root / "Artist"), sync_enabled=True,
                                     sync_dir=str(self.root / "NAS"), sync_library_root=str(self.root / "NAS"), sponsorblock=False)
        self.collection = catalogue.artist_discography(ARTIST, True, client_for([group(edition(1, "Same {title}:Album"), edition(2, "Same {title}:Album"))]))
        mock = patch.object(app, "build_spotdl_command", side_effect=lambda urls, settings: ["spotdl", *urls, settings.output_dir])
        mock.start()
        self.addCleanup(mock.stop)

    @patch.object(app, "spotdl_available", return_value=True)
    def test_prepare_jobs_expands_direct_artist_and_keeps_ordinary_sources(self, _):
        video = "https://youtube.com/watch?v=fixture"
        with patch.object(app, "request_catalogue", return_value=self.collection) as discovery, patch.object(app, "build_command", return_value=["yt-dlp"]):
            jobs = app.prepare_jobs([ARTIST, ARTIST + "?si=different", video], self.settings)
        self.assertEqual(len(jobs), 3)
        self.assertEqual([j.urls[0] for j in jobs[:2]], [r["url"] for r in self.collection["items"]])
        self.assertEqual(jobs[2].urls, [video])
        discovery.assert_called_once_with("artist", ARTIST, all_editions=False, cancel=None)
        self.assertTrue(all(j.release_id for j in jobs[:2]))

    @patch.object(app, "spotdl_available", return_value=True)
    def test_direct_artist_cancellation_and_bad_catalogue_never_return_partial_queue(self, _):
        cancel = threading.Event()
        def discover(*args, **kwargs):
            cancel.set()
            return self.collection
        with patch.object(app, "request_catalogue", side_effect=discover), patch.object(app, "build_discography_jobs") as build:
            with self.assertRaises(InterruptedError):
                app.prepare_jobs([ARTIST], self.settings, cancel)
            build.assert_not_called()
        cancel.clear()
        with patch.object(app, "request_catalogue", side_effect=ValueError("incomplete catalogue")):
            with self.assertRaisesRegex(ValueError, "incomplete"):
                app.prepare_jobs([ARTIST], self.settings, cancel)

    def test_disabled_playlists_or_missing_spotify_fail_before_discovery(self):
        with patch.object(app, "request_catalogue") as discovery:
            self.settings.playlist = False
            with self.assertRaisesRegex(ValueError, "playlists"):
                app.prepare_jobs([ARTIST], self.settings)
            self.settings.playlist = True
            self.settings.mode = "video"
            with self.assertRaisesRegex(ValueError, "Audio"):
                app.prepare_jobs([ARTIST], self.settings)
            self.settings.mode = "audio"
            with patch.object(app, "spotdl_available", return_value=False), self.assertRaisesRegex(ValueError, "not installed"):
                app.prepare_jobs([ARTIST], self.settings)
            discovery.assert_not_called()

    def test_album_folders_match_nas_hierarchy_and_do_not_collide(self):
        jobs = app.build_discography_jobs(self.collection, self.settings)
        self.assertEqual(len(jobs), 2)
        self.assertNotEqual(jobs[0].settings.output_dir, jobs[1].settings.output_dir)
        for job in jobs:
            local = Path(job.settings.output_dir)
            self.assertEqual(local.parent, Path(self.settings.output_dir))
            self.assertEqual(app.resolve_sync_target(job.settings), self.root / "NAS" / "Artist" / local.name)
            self.assertNotIn("{title}", local.name)
            self.assertIn("{track-number}", job.settings.spotdl_template)
        self.assertEqual(Path(self.settings.output_dir), self.root / "Artist")
        self.assertTrue(self.settings.sync_match_output_folder)

    def test_flat_mode_and_selected_subset_and_archive_setting(self):
        self.settings.spotify_album_folders = False
        self.settings.write_archive = False
        jobs = app.build_discography_jobs(self.collection, self.settings, self.collection["items"][:1])
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].settings.output_dir, self.settings.output_dir)
        self.assertFalse(jobs[0].settings.write_archive)

    def test_no_playlist_is_rejected(self):
        self.settings.playlist = False
        with self.assertRaisesRegex(ValueError, "playlists"):
            app.build_discography_jobs(self.collection, self.settings)

    def test_artist_job_expands_into_album_commands_and_continues_after_failure(self):
        job = app.DownloadJob([ARTIST], self.settings, provider="spotDL")
        child_jobs = [app.DownloadJob([release["url"]], self.settings, provider="yt-dlp") for release in self.collection["items"]]
        with patch.object(app, "request_catalogue", return_value=self.collection), patch.object(app, "build_discography_jobs", return_value=child_jobs), patch.object(app, "run_command", side_effect=[1, 0]) as run, patch.object(app, "finish_download_job", return_value=0) as finish:
            self.assertEqual(app.run_download_job(job, lambda _: None), 1)
            self.assertEqual(run.call_count, 2)
            finish.assert_called_once_with(child_jobs[1], unittest.mock.ANY, None)
        self.assertEqual(job.retry_jobs, child_jobs[:1])

    def test_artist_cancellation_does_not_start_album_downloads(self):
        job = app.DownloadJob([ARTIST], self.settings, provider="spotDL")
        cancel = threading.Event()
        cancel.set()
        with patch.object(app, "request_catalogue", side_effect=InterruptedError), patch.object(app, "run_command") as run:
            self.assertEqual(app.run_download_job(job, lambda _: None, cancel), 130)
            run.assert_not_called()

    def test_successful_artist_is_not_mirrored_or_deduped_twice(self):
        job = app.DownloadJob([ARTIST], self.settings, provider="spotDL")
        with patch.object(app, "sync_output_dir") as sync, patch.object(app, "run_deduplicator") as dedupe:
            self.assertEqual(app.finish_download_job(job, lambda _: None), 0)
            sync.assert_not_called()
            dedupe.assert_not_called()

    def test_expanded_artist_completes_real_per_album_mirrors(self):
        job = app.DownloadJob([ARTIST], self.settings, provider="spotDL")

        def download_fixture(_command, _output, _cancel, output_dir):
            folder = Path(output_dir)
            folder.mkdir(parents=True, exist_ok=True)
            with wave.open(str(folder / "track.wav"), "wb") as audio:
                audio.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
                audio.writeframes(b"\x00\x00" * 8000)
            (folder / "spotdl-archive.txt").write_text("test archive", encoding="utf-8")
            return 0

        with patch.object(app, "request_catalogue", return_value=self.collection), patch.object(app, "run_command", side_effect=download_fixture):
            self.assertEqual(app.run_download_job(job, lambda _: None), 0)
        mirrored = list((self.root / "NAS" / "Artist").rglob("track.wav"))
        self.assertEqual(len(mirrored), 2)
        self.assertEqual({file.parent.name for file in mirrored}, set(app.release_folder_names(self.collection, self.root / "Artist").values()))
        self.assertFalse(list((self.root / "NAS").rglob("spotdl-archive.txt")))

    def test_cli_bad_artist_and_video_combination_are_friendly_errors(self):
        for args in (["--discography", "invalid"], ["--discography", ARTIST, "--video"]):
            with patch.object(app, "load_settings", return_value=self.settings), patch.object(app, "cli_output"):
                self.assertEqual(cli.run_cli(cli.parse_args(args)), 2)

    def test_cli_print_command_resolves_each_album_without_downloading(self):
        with patch.object(app, "load_settings", return_value=self.settings), patch.object(app, "spotdl_available", return_value=True), patch.object(app, "request_catalogue", return_value=self.collection), patch.object(app, "run_download_job") as download, patch("builtins.print") as output:
            self.assertEqual(cli.run_cli(cli.parse_args(["--discography", ARTIST, "--print-command"])), 0)
            download.assert_not_called()
            commands = [str(call.args[0]) for call in output.call_args_list if call.args and str(call.args[0]).startswith("spotdl")]
            self.assertEqual(len(commands), 2)

    def test_youtube_search_command_paginates_instead_of_repeating(self):
        with patch.object(app, "find_ytdlp_command", return_value=["yt-dlp"]):
            command = app.youtube_search_command("Artist", 25, 25)
        self.assertIn("ytsearch50:Artist", command)
        self.assertEqual(command[command.index("--playlist-start") + 1], "26")
        self.assertEqual(command[command.index("--playlist-end") + 1], "50")


if __name__ == "__main__":
    unittest.main()
