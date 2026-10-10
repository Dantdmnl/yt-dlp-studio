"""Isolated Tk smoke check; captures sample views into ignored build/ files."""

import argparse
import sys
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ytdlp_studio import backend as app
from ytdlp_studio import browser as music_browser
from tests.fixtures import ARTIST, client_for, edition, group
from ytdlp_studio.catalogue import artist_discography
from PIL import ImageGrab
from ytdlp_studio.gui import StudioApp


def main(screenshots=True):
    sample = app.Settings(output_dir="D:/Music/ArtistName", watch_clipboard=False, auto_preview=False)
    with patch.object(app, "load_settings", return_value=sample), patch.object(app, "check_environment", return_value=["Test environment ready"]):
        window = StudioApp()
    failures = []
    window.report_callback_exception = lambda *_args: failures.append(_args)
    destination = Path(__file__).resolve().parents[1] / "build" / "ui-check"
    destination.mkdir(parents=True, exist_ok=True)
    def capture(name, target=window):
        if screenshots:
            ImageGrab.grab(window=target.winfo_id()).save(destination / name)
    try:
        window.add_urls(["https://www.youtube.com/watch?v=sample", "https://open.spotify.com/playlist/sample"])
        window.job_table.insert("", "end", values=("yt-dlp", "3 tracks", "Completed"))
        window.job_table.insert("", "end", values=("spotDL", "Sample playlist", "Mirror failed"))
        window.geometry("900x640+40+40")
        window.update()
        capture("simple.png")
        assert not window.log.winfo_ismapped()
        assert window.queue_summary_label.winfo_ismapped()
        assert not window.copy_log_button.winfo_ismapped()
        window.simple_one_folder.invoke()
        assert not window._settings_from_ui().spotify_album_folders
        window.update()
        capture("simple-one-folder.png")
        window.simple_album_folders.invoke()
        assert str(window.settings_panel) not in window.main_pane.panes()
        window._vars["sync_dir"].set("G:/Multimedia/Music")
        window._vars["sync_enabled"].set(True)
        for name in ("A", "Music", "ArtistName"):
            window._vars["output_dir"].set("D:/Music/" + name)
            assert Path(window._vars["sync_dir"].get()) == Path("G:/Multimedia/Music") / name
            assert Path(window._vars["sync_library_root"].get()) == Path("G:/Multimedia/Music")
        window.update()
        capture("simple-mirror.png")
        window.preview_title.set("Sample Song")
        window.preview_info.set("Youtube | Video | Sample Artist | 3:12 | Up to 1080p")
        window.update()
        capture("simple-preview.png")
        window._vars["sync_enabled"].set(False)
        window.details_var.set(True)
        window.apply_view_mode()
        window.update()
        assert window.log.winfo_ismapped()
        window.details_var.set(False)
        window.apply_view_mode()
        window.simple_link_var.set("")
        with patch.object(window, "clipboard_get", return_value="https://youtube.com/watch?v=clipboard"):
            window.scan_clipboard(auto=False)
        assert window.simple_link_var.get() == "https://youtube.com/watch?v=clipboard"
        with patch.object(app, "build_jobs", return_value=[]) as build, patch.object(app, "save_settings"), patch.object(window, "enqueue_jobs"):
            window.simple_link_var.set("https://youtube.com/watch?v=test")
            window.provider_var.set("spotDL")
            window.simple_download()
            assert build.call_args.args[1].provider == "Auto"
            assert window.provider_var.get() == "spotDL"
            assert window.simple_link_var.get() == ""
        window.provider_var.set("Auto")
        window.simple_mode_var.set(False)
        window.apply_view_mode()
        for name, settings_index, search in (("links", 0, False), ("library", 1, False), ("advanced", 4, False), ("search", 0, True), ("sync", 2, False), ("cleanup", 3, False)):
            window.settings_tabs.select(settings_index)
            intake = window.url_listbox.master.master
            intake.select(1 if search else 0)
            window.geometry("1100x760+40+40")
            for _ in range(4):
                window.update()
                time.sleep(0.05)
            capture(f"{name}.png")
        assert not failures, failures
        assert window._settings_from_ui().provider == "Auto"
        with patch.object(app, "snapshot_output_files", return_value={}), patch.object(app, "run_download_job", side_effect=[OSError("test failure"), 0]), patch.object(app, "sync_output_dir", return_value=0):
            jobs = [app.DownloadJob(["first"], sample), app.DownloadJob(["second"], sample)]
            window.enqueue_jobs(jobs)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                window.update()
                if not window.worker.is_alive() and window.ui_events.empty():
                    break
                time.sleep(0.02)
            assert [job.status for job in jobs] == ["Failed", "Completed"]
            assert window.job_table.set(jobs[1].id, "status") == "Completed"
        def wait_browser(browser):
            deadline = time.monotonic() + 5
            while browser.busy and time.monotonic() < deadline:
                window.update()
                time.sleep(0.02)
            assert not browser.busy
            window.update()

        browser = window.open_music_browser("Sample Artist")
        assert browser is window.open_music_browser("Sample Artist")
        pages = [{"items": [{"url": f"https://open.spotify.com/track/{n:022d}", "kind": "track", "title": f"Song {n}", "artist": "Sample Artist", "info": "3:12"} for n in range(start, start + 25)],
                  "offset": start + 25, "more": start == 0, "total": 50} for start in (0, 25)]
        with patch.object(music_browser, "request_catalogue", side_effect=pages) as request:
            browser.search()
            wait_browser(browser)
            assert len(browser.items) == 25
            browser.search(more=True)
            wait_browser(browser)
            assert len(browser.items) == 50
            assert request.call_args.kwargs["offset"] == 25
            assert str(browser.more_button["state"]) == "disabled"
        browser.geometry("900x660+40+40")
        window.update()
        capture("music-search.png", browser)
        browser.query.set("Other artist")
        with patch.object(music_browser, "request_catalogue", side_effect=ValueError("Offline")):
            browser.search()
            wait_browser(browser)
        assert browser.search_context[0] == "Sample Artist"
        assert len(browser.items) == 50
        collection = artist_discography(ARTIST, client=client_for([group(edition(n, title=f"Release {n}", kind=kind))
                                        for n, kind in enumerate(("ALBUM", "ALBUM", "SINGLE", "EP", "COMPILATION"), 1)]))
        with patch.object(music_browser, "request_catalogue", return_value=collection):
            browser.load_artist(ARTIST)
            wait_browser(browser)
        assert len(browser.table.get_children()) == 5
        browser.filters["album"].set(False)
        browser.render()
        assert len(browser.table.get_children()) == 3
        assert str(browser.all_button["text"]) == "Download Filtered Releases"
        browser.filters["album"].set(True)
        browser.render()
        window.update()
        capture("discography.png", browser)
        window._vars["sync_enabled"].set(True)
        with patch.object(window, "enqueue_jobs") as enqueue, patch.object(app, "build_spotdl_command", return_value=["spotdl"]):
            browser.download_selected(all_releases=True)
            deadline = time.monotonic() + 5
            while not enqueue.called and time.monotonic() < deadline:
                window.update()
                time.sleep(0.02)
            assert enqueue.called, "Artist preparation did not reach the queue"
            queued = enqueue.call_args.args[0]
            assert len(queued) == 5
            assert Path(queued[0].settings.output_dir).parent == Path(sample.output_dir)
            assert app.resolve_sync_target(queued[0].settings).parent == Path("G:/Multimedia/Music/ArtistName")
        assert not browser.winfo_exists(), "Browser must close after queueing"
        browser = window.open_music_browser()
        browser.engine.set("YouTube")
        browser.query.set("Sample Song")
        with patch.object(music_browser, "run_json_command", return_value={"entries": [{"url": "https://youtube.com/watch?v=test", "title": "Sample Song", "duration": 125}]}), patch.object(window, "youtube_search_command", return_value=["yt-dlp"]):
            browser.search()
            wait_browser(browser)
        assert len(browser.items) == 1 and browser.catalogue is None
        assert str(browser.kind_box["state"]) == "disabled"
        assert str(browser.all_button["state"]) == "disabled"
        browser.close()
        window.update()
        assert not failures, failures
        print(f"PASS: Tk initialization, all tabs, minimum-size screenshots at {destination}")
        print("PASS: queue recovery, search pagination, error recovery, discography/EP filters and matching album mirrors")
        print("PASS: simple view, clipboard intake, details toggle, automatic routing and view switching")
    finally:
        window.destroy()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-screenshots", action="store_true")
    main(screenshots=not parser.parse_args().no_screenshots)
