"""Download-first search and artist catalogue browser for the desktop app."""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import webbrowser

from .catalogue import request_catalogue, run_json_command, spotify_id


class MusicBrowser(tk.Toplevel):
    def __init__(self, owner, query="", engine="Spotify"):
        super().__init__(owner)
        self.owner = owner
        self.title("Find Music / Artist")
        self.geometry("980x690")
        self.minsize(900, 660)
        self.transient(owner)
        self.busy = False
        self.cancel_event = threading.Event()
        self.events = queue.Queue()
        self.items = {}
        self.catalogue = None
        self.next_offset = 0
        self.has_more = False
        self.search_context = None
        self.query = tk.StringVar(value=query)
        self.engine = tk.StringVar(value=engine)
        self.kind = tk.StringVar(value="Tracks")
        self.limit = tk.StringVar(value="25")
        settings = owner._settings_from_ui()
        self.destination = tk.StringVar(value=settings.output_dir)
        self.album_folders = owner._vars["spotify_album_folders"]
        self.all_editions = tk.BooleanVar(value=settings.spotify_all_editions)
        self.filters = {kind: tk.BooleanVar(value=True) for kind in ("album", "single", "compilation")}
        self.status = tk.StringVar(value="")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)
        bar = ttk.Frame(self, padding=12)
        bar.grid(row=0, column=0, sticky="ew")
        bar.columnconfigure(0, weight=1)
        self.entry = ttk.Entry(bar, textvariable=self.query)
        self.entry.grid(row=0, column=0, sticky="ew")
        self.entry.bind("<Return>", lambda _: self.search())
        self.engine_box = ttk.Combobox(bar, textvariable=self.engine, values=["Spotify", "YouTube"], width=10, state="readonly")
        self.engine_box.grid(row=0, column=1, padx=6)
        self.kind_box = ttk.Combobox(bar, textvariable=self.kind, values=["Tracks", "Artists"], width=9, state="readonly")
        self.kind_box.grid(row=0, column=2, padx=6)
        self.limit_box = ttk.Combobox(bar, textvariable=self.limit, values=["25", "50", "100"], width=5, state="readonly")
        self.limit_box.grid(row=0, column=3, padx=6)
        self.search_button = ttk.Button(bar, text="Search", command=self.search)
        self.search_button.grid(row=0, column=4, padx=6)
        self.cancel_button = ttk.Button(bar, text="Cancel", command=self.cancel_event.set, state="disabled")
        self.cancel_button.grid(row=0, column=5)
        self.engine.trace_add("write", lambda *_: self.update_kind())
        options = ttk.Frame(self, padding=(12, 0))
        options.grid(row=1, column=0, sticky="ew")
        self.discography_button = ttk.Button(options, text="Artist Discography", command=self.load_artist)
        self.discography_button.grid(row=0, column=0, padx=(0, 12))
        self.editions_check = ttk.Checkbutton(options, text="All editions", variable=self.all_editions,
                                             command=self.reload_editions)
        self.editions_check.grid(row=0, column=1)
        self.filter_bar = ttk.Frame(options)
        self.filter_bar.grid(row=0, column=2, padx=8)
        for column, (kind, label) in enumerate((("album", "Albums"), ("single", "Singles / EPs"), ("compilation", "Compilations"))):
            ttk.Checkbutton(self.filter_bar, text=label, variable=self.filters[kind], command=self.render).grid(row=0, column=column, padx=8)
        self.filter_bar.grid_remove()
        ttk.Label(self, textvariable=self.status, style="PanelMuted.TLabel", wraplength=850).grid(row=2, column=0, sticky="w", padx=12, pady=8)
        listing = ttk.Frame(self, padding=(12, 0))
        listing.grid(row=3, column=0, sticky="nsew")
        listing.columnconfigure(0, weight=1)
        listing.rowconfigure(0, weight=1)
        self.table = ttk.Treeview(listing, columns=("kind", "title", "artist", "info"), show="headings", selectmode="extended")
        for key, label, width in (("kind", "Type", 85), ("title", "Title", 340), ("artist", "Artist", 150), ("info", "Release / Duration", 200)):
            self.table.heading(key, text=label)
            self.table.column(key, width=width, minwidth=70, stretch=key != "kind")
        self.table.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(listing, orient="vertical", command=self.table.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.table.configure(yscrollcommand=scroll.set)
        self.table.bind("<Double-1>", self.activate_selected)
        self.table.bind("<<TreeviewSelect>>", lambda _: self.update_selection())
        controls = ttk.Frame(self, padding=12)
        controls.grid(row=4, column=0, sticky="ew")
        controls.columnconfigure(2, weight=1)
        self.more_button = ttk.Button(controls, text="Load More", command=lambda: self.search(more=True), state="disabled")
        self.more_button.grid(row=0, column=0)
        ttk.Button(controls, text="Select All", command=lambda: self.table.selection_set(self.table.get_children())).grid(row=0, column=1, padx=8)
        ttk.Button(controls, text="Open on Source", command=self.open_source).grid(row=0, column=3)
        folder = ttk.Frame(self, padding=(12, 0))
        folder.grid(row=5, column=0, sticky="ew")
        folder.columnconfigure(1, weight=1)
        ttk.Label(folder, text="Save to").grid(row=0, column=0, padx=(0, 12))
        ttk.Entry(folder, textvariable=self.destination).grid(row=0, column=1, sticky="ew")
        ttk.Button(folder, text="Browse", command=self.browse).grid(row=0, column=2, padx=8)
        ttk.Label(folder, text="Artist layout").grid(row=1, column=0, sticky="w", padx=(0, 12), pady=8)
        layout = ttk.Frame(folder)
        layout.grid(row=1, column=1, sticky="w", pady=8)
        self.album_layout = ttk.Radiobutton(layout, text="Album folders", variable=self.album_folders, value=True)
        self.album_layout.grid(row=0, column=0)
        self.flat_layout = ttk.Radiobutton(layout, text="One folder", variable=self.album_folders, value=False)
        self.flat_layout.grid(row=0, column=1, padx=(12, 0))
        actions = ttk.Frame(self, padding=12)
        actions.grid(row=6, column=0, sticky="ew")
        actions.columnconfigure(0, weight=1)
        self.selected_button = ttk.Button(actions, text="Download Selected", command=self.download_selected)
        self.selected_button.grid(row=0, column=1, padx=8)
        self.all_button = ttk.Button(actions, text="Download Discography", style=owner.primary_button_style,
                                     command=lambda: self.download_selected(all_releases=True), state="disabled")
        self.all_button.grid(row=0, column=2)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.update_kind()
        self.update_selection()
        self.query.trace_add("write", lambda *_: self.update_selection())
        self.poll_timer = self.after(100, self.poll)
        self.entry.focus_set()

    def update_kind(self):
        self.kind_box.configure(state="readonly" if self.engine.get() == "Spotify" and not self.busy else "disabled")

    def update_selection(self):
        selected = [self.items[row] for row in self.table.selection() if row in self.items]
        artist_selected = any(item["kind"] == "artist" for item in selected)
        self.selected_button.configure(text="Browse Discography" if artist_selected else "Download Selected",
                                       state="normal" if selected and not self.busy else "disabled")
        try:
            spotify_id(self.query.get().strip(), "artist")
            artist_link = True
        except ValueError:
            artist_link = False
        self.discography_button.configure(state="normal" if not self.busy and (artist_selected or artist_link) else "disabled")

    def start_request(self, operation, action, append=False):
        if self.busy:
            return
        self.busy = True
        self.cancel_event = threading.Event()
        self.search_button.configure(state="disabled")
        self.discography_button.configure(state="disabled")
        self.more_button.configure(state="disabled")
        self.all_button.configure(state="disabled")
        self.selected_button.configure(state="disabled")
        self.cancel_button.configure(state="normal")
        self.editions_check.configure(state="disabled")
        for widget in (self.entry, self.engine_box, self.kind_box, self.limit_box):
            widget.configure(state="disabled")
        self.status.set("Loading artist releases..." if operation == "artist" else "Searching...")
        def worker():
            try:
                self.events.put((operation, action(self.cancel_event), None, append))
            except Exception as exc:
                self.events.put((operation, None, str(exc), append))
        threading.Thread(target=worker, daemon=True).start()

    def search(self, more=False):
        query = self.query.get().strip()
        if not query or self.busy:
            return
        if query.startswith("-"):
            self.status.set("A search cannot start with a dash.")
            return
        if not more:
            try:
                spotify_id(query, "artist")
            except ValueError:
                pass
            else:
                self.load_artist(query)
                return
        engine, kind, limit = self.engine.get(), self.kind.get(), int(self.limit.get())
        context = (query, engine, kind, limit)
        append = more and context == self.search_context and self.catalogue is None
        offset = self.next_offset if append else 0
        def action(cancel):
            if engine == "Spotify":
                data = request_catalogue("search", query, kind=kind.casefold(), offset=offset, limit=limit, cancel=cancel)
                data["context"] = context
                return data
            command = self.owner.youtube_search_command(query, offset, limit)
            data = run_json_command(command, cancel, timeout=90)
            entries = data.get("entries", [])
            items = []
            for entry in entries:
                if not entry:
                    continue
                url = entry.get("webpage_url") or entry.get("url")
                if not url or not url.startswith("https://"):
                    continue
                seconds = int(entry.get("duration") or 0)
                items.append({"url": url, "kind": "video", "title": entry.get("title") or url,
                              "artist": entry.get("uploader") or entry.get("channel") or "",
                              "info": f"{seconds // 60}:{seconds % 60:02d}" if seconds else ""})
            return {"items": items, "offset": offset + len(entries), "more": len(entries) >= limit,
                    "total": None, "context": context}
        self.start_request("search", action, append)

    def load_artist(self, url=None):
        if self.busy:
            return
        if url is None:
            selected = self.table.selection()
            item = self.items.get(selected[0]) if selected else None
            url = item["url"] if item and item["kind"] == "artist" else self.query.get().strip()
        editions = self.all_editions.get()
        self.start_request("artist", lambda cancel: request_catalogue("artist", url, all_editions=editions, cancel=cancel))

    def activate_selected(self, _event=None):
        selected = self.table.selection()
        if selected and self.items[selected[0]]["kind"] == "artist":
            self.load_artist()

    def reload_editions(self):
        if self.catalogue is not None:
            self.load_artist(self.catalogue["artist_url"])

    def render(self):
        selected_urls = {self.items[row]["url"] for row in self.table.selection() if row in self.items}
        self.table.delete(*self.table.get_children())
        if self.catalogue is None:
            self.filter_bar.grid_remove()
        else:
            self.filter_bar.grid()
        visible = []
        for identity, item in self.items.items():
            filter_kind = "single" if item["kind"] == "ep" else item["kind"]
            if self.catalogue is not None and (filter_kind not in self.filters or not self.filters[filter_kind].get()):
                continue
            info = item["info"] + (" | unavailable" if not item.get("playable", True) else "")
            label = "EP" if item["kind"] == "ep" else item["kind"].title()
            self.table.insert("", "end", iid=identity, values=(label, item["title"], item["artist"], info))
            visible.append(item)
            if item["url"] in selected_urls:
                self.table.selection_add(identity)
        if self.catalogue is not None:
            self.status.set(f"{self.catalogue['artist']}: {len(visible)} releases, {sum(i['tracks'] for i in visible)} track entries; {self.catalogue['groups']} release groups scanned")
            self.all_button.configure(state="normal" if visible and not self.busy else "disabled",
                                      text="Download Discography" if all(v.get() for v in self.filters.values()) else "Download Filtered Releases")
        self.update_selection()

    def poll(self):
        try:
            while True:
                operation, data, error, append = self.events.get_nowait()
                self.busy = False
                for widget in (self.search_button, self.discography_button, self.selected_button, self.entry, self.editions_check):
                    widget.configure(state="normal")
                for widget in (self.engine_box, self.limit_box):
                    widget.configure(state="readonly")
                self.update_kind()
                self.cancel_button.configure(state="disabled")
                if error:
                    self.status.set(error)
                    self.owner._append_log(f"Discovery: {error}\n")
                    if operation == "artist" and self.catalogue is not None:
                        self.all_editions.set(self.catalogue["all_editions"])
                else:
                    if not append:
                        self.items.clear()
                    self.catalogue = data if operation == "artist" else None
                    self.search_context = data.get("context")
                    previous_urls = set(self.items)
                    for item in data["items"]:
                        self.items[item["url"]] = item
                    self.next_offset = data.get("offset", 0)
                    self.has_more = data.get("more", False)
                    if append and not set(self.items).difference(previous_urls):
                        self.has_more = False
                    self.render()
                    if self.catalogue is None:
                        self.status.set(f"{len(self.items)} results loaded")
                self.more_button.configure(state="normal" if self.has_more and self.catalogue is None else "disabled")
                self.all_button.configure(state="normal" if self.catalogue and self.table.get_children() else "disabled")
                self.all_button.configure(text="Download Filtered Releases" if self.catalogue and not all(v.get() for v in self.filters.values()) else "Download Discography")
                self.update_selection()
        except queue.Empty:
            pass
        self.poll_timer = self.after(100, self.poll)

    def browse(self):
        folder = filedialog.askdirectory(parent=self, initialdir=self.destination.get())
        if folder:
            self.destination.set(folder)

    def open_source(self):
        selected = self.table.selection()
        if selected:
            webbrowser.open(self.items[selected[0]]["url"])

    def download_selected(self, all_releases=False):
        if self.busy:
            return
        rows = self.table.get_children() if all_releases else self.table.selection()
        items = [self.items[row] for row in rows]
        if not items:
            self.status.set("Select releases or tracks first.")
            return
        if any(item["kind"] == "artist" for item in items):
            artists = [item for item in items if item["kind"] == "artist"]
            if len(artists) != 1:
                self.status.set("Select one artist to browse their discography.")
                return
            self.load_artist(artists[0]["url"])
            return
        try:
            self.busy = True
            self.cancel_event = threading.Event()
            self.status.set("Preparing download jobs...")
            self.all_button.configure(state="disabled")
            self.selected_button.configure(state="disabled")
            self.cancel_button.configure(state="normal")
            self.owner.queue_discovery_items(items, self.destination.get(), self.album_folders.get(), self.catalogue,
                                             on_done=self.queued, cancel=self.cancel_event)
        except (ValueError, OSError) as exc:
            self.busy = False
            self.render()
            messagebox.showerror("Music discovery", str(exc), parent=self)

    def queued(self, count, error):
        if not self.winfo_exists():
            return
        self.busy = False
        if error:
            self.status.set(error)
            self.cancel_button.configure(state="disabled")
            self.render()
            self.status.set(error)
            return
        self.owner.status_var.set(f"Queued {count} download jobs")
        self.owner.lift()
        self.close()

    def close(self):
        self.cancel_event.set()
        self.after_cancel(self.poll_timer)
        self.destroy()
