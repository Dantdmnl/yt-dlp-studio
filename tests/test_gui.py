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

    def test_artist_layout_is_shared_by_simple_advanced_and_browser_controls(self):
        window = self.window
        window.simple_one_folder.invoke()
        self.assertFalse(window._vars["spotify_album_folders"].get())
        browser = window.open_music_browser()
        self.assertFalse(browser.album_folders.get())
        window.library_album_folders.invoke()
        self.assertTrue(browser.album_folders.get())
        browser.flat_layout.invoke()
        self.assertFalse(window._settings_from_ui().spotify_album_folders)
        with patch.object(core, "save_settings") as save:
            window.save_from_ui()
        self.assertFalse(save.call_args.args[0].spotify_album_folders)
        browser.close()

    @patch.object(core, "spotdl_available", return_value=True)
    @patch.object(core, "build_spotdl_command", return_value=["spotdl"])
    def test_direct_flat_artist_queues_separate_jobs_with_one_local_and_nas_folder(self, *_):
        window = self.window
        collection = artist_discography(ARTIST, client=client_for([group(edition(n)) for n in range(3)]))
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def discover(*args, **kwargs):
            entered.set()
            release.wait(3)
            return collection
        mirror = Path(self.temp.name) / "NAS"
        window._vars["sync_dir"].set(str(mirror))
        window._vars["sync_enabled"].set(True)
        window.simple_one_folder.invoke()
        window.simple_link_var.set(ARTIST)
        with patch.object(core, "request_catalogue", side_effect=discover), patch.object(window, "_start_worker"):
            window.simple_download()
            self.assertTrue(entered.wait(1))
            window.simple_album_folders.invoke()
            release.set()
            self.wait_until(lambda: not window.preparing)
        jobs = list(window.job_history.values())
        self.assertEqual(len(jobs), 3)
        self.assertEqual({job.settings.output_dir for job in jobs}, {self.settings.output_dir})
        self.assertEqual({core.resolve_sync_target(job.settings) for job in jobs}, {mirror / "Artist"})
        self.assertTrue(all(not job.settings.spotify_album_folders and not job.release_id for job in jobs))
        self.assertEqual(window.jobs.qsize(), 3)

    @patch.object(core, "build_spotdl_command", return_value=["spotdl"])
    def test_browser_one_folder_queues_flat_releases_and_closes(self, _):
        window = self.window
        browser = window.open_music_browser()
        collection = artist_discography(ARTIST, client=client_for([group(edition(n)) for n in range(2)]))
        browser.catalogue = collection
        browser.items = {item["url"]: item for item in collection["items"]}
        browser.render()
        browser.flat_layout.invoke()
        with patch.object(window, "_start_worker"):
            browser.download_selected(all_releases=True)
            self.wait_until(lambda: not browser.winfo_exists())
        self.assertEqual(len(window.job_history), 2)
        self.assertTrue(all(job.settings.output_dir == self.settings.output_dir for job in window.job_history.values()))
        self.assertFalse(window._vars["spotify_album_folders"].get())

    def test_flat_release_retry_keeps_shared_folder_and_layout(self):
        window = self.window
        self.settings.spotify_album_folders = False
        failed = "https://open.spotify.com/track/" + "F" * 22
        job = core.DownloadJob(["album"], self.settings, status="Partial", failed_urls=[failed])
        window.job_history[job.id] = job
        window.job_table.insert("", "end", iid=job.id, values=(job.provider, "Album", job.status))
        window.job_table.selection_set(job.id)
        with patch.object(core, "build_jobs", side_effect=lambda urls, settings: [core.DownloadJob(urls, settings)]) as build, patch.object(window, "enqueue_jobs") as enqueue:
            window.retry_selected()
        self.assertEqual(build.call_args.args[0], [failed])
        retry = enqueue.call_args.args[0][0]
        self.assertFalse(retry.settings.spotify_album_folders)
        self.assertEqual(retry.settings.output_dir, self.settings.output_dir)

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
        self.assertNotEqual(window.queue_summary_label.grid_info(), {})
        self.assertNotEqual(window.log_toolbar.grid_info(), {})
        self.assertEqual(window.copy_log_button.grid_info(), {})
        window.details_var.set(True)
        window.apply_view_mode()
        self.assertNotEqual(window.log.grid_info(), {})

    def test_artist_preparation_keeps_ui_responsive_and_closes_after_queueing(self):
        window = self.window
        browser = window.open_music_browser()
        collection = artist_discography(ARTIST, client=client_for([group(edition(1))]))
        browser.catalogue = collection
        browser.items = {item["url"]: item for item in collection["items"]}
        browser.render()
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def slow(*args):
            entered.set()
            release.wait(3)
            return [core.DownloadJob([ARTIST], self.settings)]
        with patch.object(core, "build_discography_jobs", side_effect=slow), patch.object(window, "_start_worker"):
            browser.download_selected(all_releases=True)
            self.assertTrue(entered.wait(1))
            ticks = []
            window.after(10, lambda: ticks.append(True))
            self.wait_until(lambda: bool(ticks))
            self.assertTrue(browser.winfo_exists())
            release.set()
            self.wait_until(lambda: not browser.winfo_exists())
            self.assertEqual(window.jobs.qsize(), 1)

    def test_closing_browser_cancels_preparation_without_queueing(self):
        window = self.window
        browser = window.open_music_browser()
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def slow(*args):
            entered.set()
            release.wait(3)
            return [core.DownloadJob([ARTIST], self.settings)]
        with patch.object(core, "build_jobs", side_effect=slow), patch.object(window, "_start_worker"):
            window.queue_discovery_items([{"kind": "track", "url": ARTIST}], self.settings.output_dir,
                                         False, None, on_done=browser.queued, cancel=browser.cancel_event)
            self.assertTrue(entered.wait(1))
            browser.close()
            release.set()
            self.wait_until(lambda: not window.preparation_worker.is_alive() and window.ui_events.empty())
            self.assertTrue(window.jobs.empty())

    def test_stop_cancels_entire_queue_and_does_not_automatically_resume(self):
        window = self.window
        entered = threading.Event()
        calls = []
        def downloading(job, output, stop):
            calls.append(job)
            entered.set()
            stop.wait(3)
            return 130
        self.addCleanup(window.stop_event.set)
        jobs = [core.DownloadJob([ARTIST], self.settings) for _ in range(3)]
        with patch.object(core, "run_download_job", side_effect=downloading):
            window.enqueue_jobs(jobs)
            self.assertTrue(entered.wait(1))
            window.cancel_current()
            self.wait_until(lambda: not window.worker.is_alive())
            window._start_worker()
            self.assertEqual(calls, jobs[:1])
            self.assertTrue(window.stop_event.is_set())
            self.assertTrue(window.jobs.empty())
            self.assertTrue(all(job.status == "Cancelled" for job in jobs))

    def test_artist_retry_preserves_failed_release_destination(self):
        window = self.window
        album_settings = core.Settings(output_dir=str(Path(self.temp.name) / "Artist" / "2026 - Album"))
        failed = "https://open.spotify.com/track/" + "F" * 22
        child = core.DownloadJob(["album"], album_settings, status="Failed", provider="spotDL",
                                 failed_urls=[failed], release_id="A" * 22)
        parent = core.DownloadJob([ARTIST], self.settings, status="Partial", provider="spotDL", retry_jobs=[child])
        window.job_history[parent.id] = parent
        window.job_table.insert("", "end", iid=parent.id, values=(parent.provider, "Artist", parent.status))
        window.job_table.selection_set(parent.id)
        def build(urls, settings):
            return [core.DownloadJob(urls, settings, provider="spotDL")]
        with patch.object(core, "build_jobs", side_effect=build), patch.object(window, "enqueue_jobs") as enqueue:
            window.retry_selected()
        retry = enqueue.call_args.args[0][0]
        self.assertEqual(retry.urls, [failed])
        self.assertEqual(retry.settings.output_dir, album_settings.output_dir)
        self.assertEqual(retry.release_id, child.release_id)

    def test_cancelled_album_retry_includes_not_yet_attempted_tracks(self):
        window = self.window
        job = core.DownloadJob(["album"], self.settings, status="Cancelled", provider="spotDL", failed_urls=["earlier failure"])
        window.job_history[job.id] = job
        window.job_table.insert("", "end", iid=job.id, values=(job.provider, "Album", job.status))
        window.job_table.selection_set(job.id)
        with patch.object(core, "build_jobs", return_value=[]) as build, patch.object(window, "enqueue_jobs"):
            window.retry_selected()
        self.assertEqual(build.call_args.args[0], ["album"])

    @patch.object(core, "spotdl_available", return_value=True)
    @patch.object(core, "build_spotdl_command", return_value=["spotdl"])
    def test_pasted_artist_creates_all_visible_release_jobs_off_the_ui_thread(self, *_):
        window = self.window
        collection = artist_discography(ARTIST, client=client_for([group(edition(n, f"Release {n}")) for n in range(177)]))
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def discover(*args, **kwargs):
            self.assertIsNot(threading.current_thread(), threading.main_thread())
            entered.set()
            release.wait(3)
            return collection
        window.simple_link_var.set(ARTIST)
        window.preview_url = ARTIST
        window.preview_data = {"title": "Sample Artist"}
        original_destination = window._vars["output_dir"].get()
        with patch.object(core, "request_catalogue", side_effect=discover), patch.object(window, "_start_worker"), patch.object(core, "run_download_job") as download:
            window.simple_download()
            self.assertTrue(entered.wait(1))
            self.assertEqual(len(window.job_history), 1)
            self.assertEqual(next(iter(window.job_history.values())).status, "Discovering")
            window.remove_finished()
            self.assertEqual(len(window.job_history), 1)
            self.assertIn("Discovering", window.queue_var.get())
            self.assertEqual(str(window.simple_stop_button["state"]), "normal")
            ticks = []
            window.after(10, lambda: ticks.append(True))
            self.wait_until(lambda: bool(ticks))
            window._vars["output_dir"].set(str(Path(self.temp.name) / "changed"))
            release.set()
            self.wait_until(lambda: not window.preparing)
            self.assertEqual(len(window.job_table.get_children()), 177)
            self.assertEqual(len(window.job_history), 177)
            self.assertEqual(window.jobs.qsize(), 177)
            self.assertIn("Queued: 177", window.queue_var.get())
            self.assertTrue(all(job.label.startswith("Sample Artist - Release") for job in window.job_history.values()))
            self.assertTrue(all(Path(job.settings.output_dir).parent == Path(original_destination) for job in window.job_history.values()))
            self.assertFalse(any(ARTIST in job.urls for job in window.job_history.values()))
            self.assertEqual(window.simple_link_var.get(), "")
            download.assert_not_called()

    @patch.object(core, "spotdl_available", return_value=True)
    @patch.object(core, "build_spotdl_command", return_value=["spotdl"])
    def test_direct_artist_queue_counts_active_and_pending_releases(self, *_):
        window = self.window
        collection = artist_discography(ARTIST, client=client_for([group(edition(n)) for n in range(3)]))
        entered = threading.Event()
        self.addCleanup(window.stop_event.set)
        def download(job, output, cancel):
            entered.set()
            cancel.wait(3)
            return 130
        window.simple_link_var.set(ARTIST)
        with patch.object(core, "request_catalogue", return_value=collection), patch.object(core, "run_download_job", side_effect=download) as run:
            window.simple_download()
            self.wait_until(lambda: entered.is_set())
            window.update_queue_summary()
            self.assertEqual(window.queue_var.get(), "Queued: 2 | Active: 1")
            self.assertEqual(len(window.job_table.get_children()), 3)
            window.cancel_current()
            self.wait_until(lambda: not window.worker.is_alive())
            self.assertEqual(run.call_count, 1)
            self.assertTrue(all(job.status == "Cancelled" for job in window.job_history.values()))

    def test_stop_during_artist_discovery_preserves_link_and_adds_no_downloads(self):
        window = self.window
        entered = threading.Event()
        def prepare(urls, settings, cancel, progress):
            entered.set()
            cancel.wait(3)
            raise InterruptedError("Preparation cancelled.")
        window.simple_link_var.set(ARTIST)
        with patch.object(core, "prepare_jobs", side_effect=prepare), patch.object(core, "run_download_job") as download:
            window.simple_download()
            self.assertTrue(entered.wait(1))
            window.cancel_current()
            self.wait_until(lambda: not window.preparing)
            self.assertEqual(window.simple_link_var.get(), ARTIST)
            self.assertEqual(next(iter(window.job_history.values())).status, "Cancelled")
            self.assertTrue(window.jobs.empty())
            download.assert_not_called()

    @patch.object(core, "spotdl_available", return_value=True)
    @patch.object(core, "build_spotdl_command", return_value=["spotdl"])
    def test_failed_artist_discovery_is_retryable_without_a_hidden_artist_job(self, *_):
        window = self.window
        collection = artist_discography(ARTIST, client=client_for([group(edition(1))]))
        window.simple_link_var.set(ARTIST)
        with patch.object(core, "request_catalogue", side_effect=[ValueError("incomplete catalogue"), collection]), patch.object(window, "_start_worker"):
            window.simple_download()
            self.wait_until(lambda: not window.preparing)
            failed = next(iter(window.job_history.values()))
            self.assertEqual(failed.status, "Failed")
            self.assertEqual(window.simple_link_var.get(), ARTIST)
            self.assertTrue(window.jobs.empty())
            window.job_table.selection_set(failed.id)
            window.retry_selected()
            self.wait_until(lambda: not window.preparing)
            releases = [job for job in window.job_history.values() if job is not failed]
            self.assertEqual(len(releases), 1)
            self.assertEqual(releases[0].urls, [collection["items"][0]["url"]])
            self.assertEqual(window.jobs.qsize(), 1)

    def test_preparation_thread_start_failure_does_not_leave_a_busy_row(self):
        window = self.window
        placeholder = core.DownloadJob([ARTIST], self.settings, status="Discovering")
        with patch.object(threading.Thread, "start", side_effect=RuntimeError("thread unavailable")):
            with self.assertRaisesRegex(ValueError, "Could not start"):
                window.prepare_batch(lambda: [], lambda *args: None, threading.Event(), placeholder)
        self.assertFalse(window.preparing)
        self.assertEqual(placeholder.status, "Failed")
        self.assertTrue(window.jobs.empty())

    @patch.object(core, "spotdl_available", return_value=True)
    @patch.object(core, "build_spotdl_command", return_value=["spotdl"])
    def test_advanced_artist_preparation_preserves_newly_added_links(self, *_):
        window = self.window
        collection = artist_discography(ARTIST, client=client_for([group(edition(1))]))
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def discover(*args, **kwargs):
            entered.set()
            release.wait(3)
            return collection
        window.add_urls([ARTIST])
        with patch.object(core, "request_catalogue", side_effect=discover), patch.object(window, "_start_worker"):
            window.add_job()
            self.assertTrue(entered.wait(1))
            other = "https://youtube.com/watch?v=new-link"
            window.add_urls([other])
            release.set()
            self.wait_until(lambda: not window.preparing)
        self.assertEqual(window.detected_urls, [other])
        self.assertEqual(len(window.job_history), 1)
        self.assertNotEqual(next(iter(window.job_history.values())).urls, [ARTIST])

    def test_minimum_size_keeps_primary_controls_inside_the_window(self):
        window = self.window
        window._vars["sync_enabled"].set(True)
        window._vars["sync_dir"].set(str(Path(self.temp.name) / "NAS"))
        window.geometry("900x700")
        window.deiconify()
        window.update()
        for widget in (window.simple_format, window.simple_quality, window.preview_button,
                       window.job_table, window.simple_mirror_row, window.simple_stop_button, window.queue_summary_label,
                       window.simple_one_folder, window.simple_album_folders):
            x = widget.winfo_rootx() - window.winfo_rootx()
            y = widget.winfo_rooty() - window.winfo_rooty()
            self.assertGreaterEqual(x, 0)
            self.assertGreaterEqual(y, 0)
            self.assertLessEqual(x + widget.winfo_width(), window.winfo_width())
            self.assertLessEqual(y + widget.winfo_height(), window.winfo_height())


if __name__ == "__main__":
    unittest.main()
