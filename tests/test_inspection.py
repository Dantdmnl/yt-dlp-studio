import threading
import time
import unittest
from unittest.mock import Mock, patch

from ytdlp_studio import backend, catalogue, formats, inspection


class InspectionTests(unittest.TestCase):
    def wait(self, controller):
        deadline = time.monotonic() + 3
        while controller.busy and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertFalse(controller.busy)

    def test_stale_preview_cannot_replace_new_link(self):
        old_ready = threading.Event()
        release_old = threading.Event()

        def inspect(url, settings, cancel):
            if url == "old":
                old_ready.set()
                release_old.wait(2)
            return {"title": url}

        controller = inspection.PreviewController(inspect)
        controller.start("old", backend.Settings())
        self.assertTrue(old_ready.wait(1))
        controller.start("new", backend.Settings())
        release_old.set()
        self.wait(controller)
        self.assertEqual(controller.poll(), ({"title": "new"}, None))
        self.assertIsNone(controller.poll())

    def test_cancelled_preview_is_discarded_and_settings_are_snapshotted(self):
        ready = threading.Event()
        captured = []

        def inspect(url, settings, cancel):
            ready.set()
            cancel.wait(2)
            captured.append(settings.output_dir)
            return {"title": "old"}

        settings = backend.Settings(output_dir="original")
        controller = inspection.PreviewController(inspect)
        controller.start("link", settings)
        self.assertTrue(ready.wait(1))
        settings.output_dir = "changed"
        controller.invalidate()
        self.wait(controller)
        self.assertEqual(captured, ["original"])
        self.assertIsNone(controller.poll())

    def test_preview_failure_is_a_result_not_a_thread_crash(self):
        controller = inspection.PreviewController(Mock(side_effect=ValueError("Offline")))
        controller.start("link", backend.Settings())
        self.wait(controller)
        self.assertEqual(controller.poll(), (None, "Offline"))

    def test_youtube_preview_is_metadata_only_and_parses_quality(self):
        raw = {"title": "Song", "duration": 125, "uploader": "Artist", "extractor_key": "Youtube",
               "formats": [{"height": 1080}, {"height": None}, {"height": 720}, {"height": 1080}]}
        with patch.object(backend, "find_ytdlp_command", return_value=["yt-dlp"]), patch.object(backend, "find_js_runtime", return_value=None), patch.object(inspection, "run_json_command", return_value=raw) as run:
            result = inspection.inspect_link("https://youtube.com/watch?v=test", backend.Settings(), threading.Event())
        command = run.call_args.args[0]
        self.assertIn("--skip-download", command)
        self.assertIn("--flat-playlist", command)
        self.assertEqual(result["heights"], [720, 1080])
        self.assertIn("2:05", inspection.preview_summary(result))

    def test_spotify_preview_routes_without_youtube_extraction(self):
        with patch.object(inspection, "request_catalogue", return_value={"title": "Artist"}) as request:
            inspection.inspect_link("https://open.spotify.com/artist/" + "A" * 22, backend.Settings(), threading.Event())
        self.assertEqual(request.call_args.args[0], "inspect")

    def test_preview_rejects_batches_and_non_urls(self):
        for value in ("not a link", "https://example.com/a https://example.com/b", "-dangerous"):
            with self.assertRaises(ValueError):
                inspection.inspect_link(value, backend.Settings(), threading.Event())

    def test_spotify_track_album_playlist_and_artist_previews(self):
        client = Mock()
        client.get_track_info.return_value = {"data": {"trackUnion": {"name": "Song", "duration": {"totalMilliseconds": 90000}, "firstArtist": {"items": [{"profile": {"name": "Artist"}}]}}}}
        client.get_album_info.return_value = {"data": {"albumUnion": {"name": "Album", "tracksV2": {"totalCount": 30}}}}
        client.get_playlist_info.return_value = {"data": {"playlistV2": {"name": "Playlist", "content": {"totalCount": 80}}}}
        client.get_artist.return_value = {"data": {"artistUnion": {"profile": {"name": "Artist"}}}}
        results = {kind: catalogue.inspect_spotify(f"https://open.spotify.com/{kind}/" + "A" * 22, client)
                   for kind in ("track", "album", "playlist", "artist")}
        self.assertEqual(results["track"]["creator"], "Artist")
        self.assertEqual(results["track"]["duration"], 90)
        self.assertEqual(results["album"]["count"], 30)
        self.assertEqual(results["playlist"]["count"], 80)
        self.assertEqual(results["artist"]["title"], "Artist")
        client.get_album_info.assert_called_once_with(limit=1)
        client.get_playlist_info.assert_called_once_with(limit=1)

    def test_every_simple_choice_maps_to_a_valid_backend_preset(self):
        for mode, presets in (("audio", backend.AUDIO_PRESETS), ("video", backend.VIDEO_PRESETS)):
            for format_name, qualities in formats.choices_for(mode).items():
                for quality, preset in qualities.items():
                    self.assertIn(preset, presets)
                    self.assertEqual(formats.selection_for(mode, preset), (format_name, quality))

    def test_spotify_ids_reject_extra_uri_segments_and_unsupported_paths(self):
        for value in ("spotify:artist:" + "A" * 22 + ":" + "B" * 22,
                      "ftp://open.spotify.com/artist/" + "A" * 22,
                      "https://open.spotify.com/foo/artist/" + "A" * 22,
                      "https://open.spotify.com/artist/" + "A" * 22 + "/extra"):
            with self.assertRaises(ValueError):
                catalogue.spotify_id(value, "artist")


if __name__ == "__main__":
    unittest.main()
