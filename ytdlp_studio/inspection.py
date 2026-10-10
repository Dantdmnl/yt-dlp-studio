"""Cancellable link inspection; preview responses never start downloads."""

from __future__ import annotations

import queue
import threading
from dataclasses import asdict
from pathlib import Path

from . import backend as core
from .catalogue import request_catalogue, run_json_command


def inspect_link(url: str, settings: core.Settings, cancel: threading.Event) -> dict:
    if core.extract_urls(url) != [url]:
        raise ValueError("Enter one complete media link.")
    if core.provider_for(url, core.Settings()) == "spotDL":
        return request_catalogue("inspect", url, cancel=cancel)
    command = core.find_ytdlp_command() + ["--ignore-config", "--dump-single-json", "--skip-download",
                                         "--flat-playlist", "--playlist-end", "1", "--no-warnings"]
    runtime = core.find_js_runtime()
    if runtime:
        command += ["--js-runtimes", f"{runtime[0]}:{runtime[1]}"]
    if settings.cookies_file:
        command += ["--cookies", str(Path(settings.cookies_file).expanduser().resolve())]
    command += ["--", url]
    data = run_json_command(command, cancel, timeout=90)
    return {"url": url, "source": data.get("extractor_key") or data.get("extractor") or "yt-dlp",
            "kind": "playlist" if "entries" in data else "video", "title": data.get("title") or url,
            "creator": data.get("artist") or data.get("uploader") or data.get("channel") or "",
            "duration": data.get("duration"), "count": data.get("playlist_count"),
            "heights": sorted({int(f["height"]) for f in data.get("formats", []) if f.get("height")})}


def preview_summary(data: dict) -> str:
    parts = [data["source"], data["kind"].title()]
    if data.get("creator"):
        parts.append(data["creator"])
    if data.get("duration"):
        seconds = int(data["duration"])
        parts.append(f"{seconds // 60}:{seconds % 60:02d}")
    if data.get("count") is not None:
        parts.append(f"{data['count']} items")
    if data.get("heights"):
        parts.append(f"Up to {max(data['heights'])}p")
    return " | ".join(parts)


class PreviewController:
    def __init__(self, inspect=inspect_link):
        self.inspect = inspect
        self.generation = 0
        self.cancel = threading.Event()
        self.results = queue.Queue()
        self.workers = []

    @property
    def busy(self):
        self.workers = [worker for worker in self.workers if worker.is_alive()]
        return bool(self.workers)

    def invalidate(self):
        self.cancel.set()
        self.generation += 1

    def start(self, url: str, settings: core.Settings):
        self.invalidate()
        generation = self.generation
        cancel = self.cancel = threading.Event()
        snapshot = core.Settings(**asdict(settings))

        def worker():
            try:
                data = self.inspect(url, snapshot, cancel)
                self.results.put((generation, data, None))
            except Exception as exc:
                self.results.put((generation, None, str(exc)))

        thread = threading.Thread(target=worker, daemon=True)
        self.workers.append(thread)
        thread.start()

    def poll(self):
        current = None
        while True:
            try:
                generation, data, error = self.results.get_nowait()
            except queue.Empty:
                return current
            if generation == self.generation:
                current = (data, error)
