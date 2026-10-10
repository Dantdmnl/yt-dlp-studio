import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from ytdlp_studio import backend as core
from ytdlp_studio import browser as browser_module
from ytdlp_studio.catalogue import artist_discography
from ytdlp_studio.gui import StudioApp, tb
from ytdlp_studio.inspection import PreviewController
from tests.fixtures import ARTIST, client_for, edition, group


@unittest.skipIf(os.name != "nt" and not os.environ.get("DISPLAY"), "GUI tests need a display")
class GuiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.settings = core.Settings(output_dir=str(Path(self.temp.name) / "Music" / "Artist"),
                                      sponsorblock=False, watch_clipboard=False, auto_preview=False)
        if tb:
            tb.Style.instance = None
            from ttkbootstrap.publisher import Publisher
            Publisher.clear_subscribers()
        for patcher in (patch.object(core, "load_settings", return_value=self.settings),
                        patch.object(core, "check_environment", return_value=["Test environment ready"]),
                        patch.object(core, "save_settings")):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.window = StudioApp()
        self.window.withdraw()
        self.failures = []
        self.window.report_callback_exception = lambda *error: self.failures.append(error)

    def tearDown(self):
        self.window.preview.invalidate()
        self.wait_until(lambda: not self.window.preview.busy)
        self.window.destroy()
        self.assertEqual(self.failures, [])

    def wait_until(self, predicate, timeout=4):
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            self.window.update()
            time.sleep(0.01)
        self.window.update()
        self.assertTrue(predicate(), "GUI operation did not finish")

    def test_format_and_quality_controls_share_advanced_settings(self):
        window = self.window
        window.simple_format_var.set("MP3")
        window.simple_quality_var.set("192 kbps")
        window.choose_simple_format()
        self.assertEqual(window._vars["audio_preset"].get(), "MP3 192 kbps")
        window._vars["mode"].set("video")
        self.assertEqual(window.simple_format_var.get(), "MP4")
        window.simple_quality_var.set("720p")
        window.choose_simple_format()
        self.assertEqual(window._vars["video_preset"].get(), "720p MP4")
        window._vars["mode"].set("audio")
        self.assertEqual(window.simple_quality_var.get(), "192 kbps")
        window.simple_format_var.set("FLAC")
        window.choose_simple_format()
        self.assertEqual(str(window.simple_quality["state"]), "disabled")
        self.assertEqual(window._vars["audio_preset"].get(), "FLAC")

    def test_preview_updates_title_and_disabled_auto_mode_never_schedules_network(self):
        window = self.window
        url = "https://youtube.com/watch?v=test"
        window.simple_link_var.set(url)
        self.assertIsNone(window.preview_timer)
        data = {"url": url, "source": "Youtube", "kind": "video", "title": "Sample Song", "creator": "Artist", "duration": 125, "heights": [720]}
        window.preview = PreviewController(lambda *_: data)
        window.preview_link()
        self.wait_until(lambda: not window.preview_loading)
        self.assertEqual(window.preview_title.get(), "Sample Song")
        self.assertIn("2:05", window.preview_info.get())
        window.simple_link_var.set(url + "2")
        self.assertIsNone(window.preview_data)
        self.assertEqual(window.preview_title.get(), "Link ready")

    def test_cancel_preview_releases_request_and_cannot_restore_old_result(self):
        window = self.window
        ready = threading.Event()

        def slow(_url, _settings, cancel):
            ready.set()
            cancel.wait(2)
            return {"title": "stale"}

        window.preview = PreviewController(slow)
        window.simple_link_var.set("https://youtube.com/watch?v=test")
        window.preview_link()
        self.assertTrue(ready.wait(1))
        window.preview_link()
        self.wait_until(lambda: not window.preview.busy)
        self.assertEqual(window.preview_info.get(), "Preview cancelled")
        self.assertIsNone(window.preview_data)

    def test_terminal_queue_removal_ignores_delayed_worker_events(self):
        window = self.window
        job = core.DownloadJob(["https://example.com"], self.settings, status="Completed")
        window.job_history[job.id] = job
        window.job_table.insert("", "end", iid=job.id, values=(job.provider, "Song", "Completed"))
        window.ui_events.put(("job", job.id, "Completed"))
        window.remove_finished()
        window._poll_log_queue()
        self.assertNotIn(job.id, window.job_history)
        self.assertFalse(window.job_table.exists(job.id))

    def test_failed_maintenance_is_not_reported_as_finished(self):
        window = self.window
        window.run_maintenance(lambda: 1, "Sync")
        self.wait_until(lambda: not window.maintenance_worker.is_alive() and window.ui_events.empty())
        self.assertEqual(window.status_var.get(), "Sync failed (1)")

    def test_optional_installer_uses_project_requirements_and_recovers_button(self):
        window = self.window
        with patch.object(core, "run_command", return_value=0) as run:
            window.install_spotify()
            self.wait_until(lambda: not window.maintenance_worker.is_alive() and window.ui_events.empty())
        requirements = Path(run.call_args.args[0][-1])
        self.assertEqual(requirements, Path(__file__).resolve().parents[1] / "requirements-spotify.txt")
        self.assertEqual(str(window.install_spotify_button["state"]), "normal")
        self.assertEqual(window.spotify_status.get(), "Spotify support installed")

    def test_discography_filter_includes_eps_and_queues_only_visible_releases(self):
        window = self.window
        browser = window.open_music_browser()
        browser.withdraw()
        collection = artist_discography(ARTIST, client=client_for([group(edition(1)), group(edition(2, kind="EP")), group(edition(3, kind="SINGLE"))]))
        with patch.object(browser_module, "request_catalogue", return_value=collection):
            browser.load_artist(ARTIST)
            self.wait_until(lambda: not browser.busy)
        self.assertEqual(len(browser.table.get_children()), 3)
        browser.filters["album"].set(False)
        browser.render()
        with patch.object(window, "queue_discovery_items", return_value=2) as queue:
            browser.download_selected(all_releases=True)
        self.assertEqual({item["kind"] for item in queue.call_args.args[0]}, {"ep", "single"})
        browser.close()

    def test_simple_and_advanced_views_share_presets_and_restore_details(self):
        window = self.window
        window.simple_mode_var.set(False)
        window.apply_view_mode()
        window._vars["audio_preset"].set("Opus best")
        window.simple_mode_var.set(True)
        window.apply_view_mode()
        self.assertEqual(window.simple_format_var.get(), "Opus")
        self.assertEqual(window.log.grid_info(), {})
        window.details_var.set(True)
        window.apply_view_mode()
        self.assertNotEqual(window.log.grid_info(), {})

    def test_minimum_size_keeps_primary_controls_inside_the_window(self):
        window = self.window
        window._vars["sync_enabled"].set(True)
        window._vars["sync_dir"].set(str(Path(self.temp.name) / "NAS"))
        window.geometry("900x700")
        window.deiconify()
        window.update()
        for widget in (window.simple_format, window.simple_quality, window.preview_button,
                       window.job_table, window.simple_mirror_row, window.simple_stop_button):
            x = widget.winfo_rootx() - window.winfo_rootx()
            y = widget.winfo_rooty() - window.winfo_rooty()
            self.assertGreaterEqual(x, 0)
            self.assertGreaterEqual(y, 0)
            self.assertLessEqual(x + widget.winfo_width(), window.winfo_width())
            self.assertLessEqual(y + widget.winfo_height(), window.winfo_height())


if __name__ == "__main__":
    unittest.main()
