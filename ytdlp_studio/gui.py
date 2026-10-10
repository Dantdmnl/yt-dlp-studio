"""Desktop workflows and the shared download queue."""

from __future__ import annotations

import importlib
import os
import queue
import subprocess
import sys
import threading
import traceback
from pathlib import Path
from typing import Callable
import tkinter as tk
from tkinter import filedialog, font as tkfont, messagebox, ttk

from . import backend as core
from .formats import choices_for, selection_for
from .inspection import PreviewController, preview_summary
from .processes import hide_child_windows

try:
    import ttkbootstrap as tb
except ImportError:
    tb = None


class StudioApp(tk.Tk):
    def __init__(self) -> None:
        hide_child_windows()
        super().__init__()
        self.title(f"{core.APP_NAME} {core.APP_VERSION}")
        self.minsize(1100, 800)
        self.geometry("1180x840")
        self.settings = core.load_settings()
        self.jobs: queue.Queue[core.DownloadJob] = queue.Queue()
        self.log_queue: queue.Queue[str] = queue.Queue()
        self.worker: threading.Thread | None = None
        self.maintenance_worker: threading.Thread | None = None
        self.preparation_worker: threading.Thread | None = None
        self.preparation_cancel = threading.Event()
        self.preparing = False
        self.closing = False
        self.job_history: dict[str, core.DownloadJob] = {}
        self.ui_events: queue.Queue[tuple] = queue.Queue()
        self.stop_event = threading.Event()
        self.current_job: core.DownloadJob | None = None
        self.detected_urls: list[str] = []
        self.last_clipboard_text = ""
        self._sync_field_updating = False
        self.preview = PreviewController()
        self.preview_timer = None
        self.preview_data = None
        self.preview_url = ""
        self.preview_loading = False

        self._configure_style()
        self._vars: dict[str, tk.Variable] = {}
        self._build_ui()
        # ttkbootstrap registers styles lazily as widgets are created.
        self._configure_style()
        self._load_vars()
        self.apply_view_mode()
        self._wire_setting_traces()
        self.apply_matched_sync_field()
        environment = core.check_environment()
        self.environment_var.set(self._environment_summary(environment))
        if any("missing" in message.casefold() for message in environment):
            self.environment_label.configure(style="WarningBadge.TLabel")
        self._append_log("Environment:\n" + "\n".join(environment) + "\n\n")
        if self.settings.watch_clipboard:
            self.scan_clipboard(auto=True)
        self._poll_log_queue()
        self._poll_clipboard()
        self._refresh_status()
        self._poll_preview()
        self.bind("<Control-v>", lambda _event: self.scan_clipboard(auto=False))
        self.protocol("WM_DELETE_WINDOW", self.close_app)

    def _configure_style(self) -> None:
        self.configure(background="#eef2f7")
        default_font = tkfont.Font(family="Segoe UI", size=10)
        title_font = tkfont.Font(family="Segoe UI", size=18, weight="bold")
        subtitle_font = tkfont.Font(family="Segoe UI", size=10)
        section_font = tkfont.Font(family="Segoe UI", size=10, weight="bold")
        self._fonts = (default_font, title_font, subtitle_font, section_font)
        self.option_add("*Text.Font", default_font)
        self.option_add("*Listbox.Font", default_font)
        if tb:
            style = tb.Style("flatly")
        else:
            style = ttk.Style(self)
            try:
                style.theme_use("clam")
            except tk.TclError:
                pass
        self._style = style
        self.primary_button_style = "primary.TButton" if tb else "Accent.TButton"
        self.secondary_button_style = "secondary.TButton" if tb else "TButton"
        self.danger_button_style = "danger.TButton" if tb else "TButton"

        style.configure("TFrame", background="#eef2f7")
        style.configure("Surface.TFrame", background="#ffffff")
        style.configure("TLabel", background="#eef2f7", foreground="#111827", font=default_font)
        style.configure("Surface.TLabel", background="#ffffff", foreground="#111827")
        style.configure("Muted.TLabel", background="#ffffff", foreground="#64748b", font=subtitle_font)
        style.configure("PanelMuted.TLabel", background="#eef2f7", foreground="#64748b", font=subtitle_font)
        style.configure("Title.TLabel", background="#ffffff", foreground="#111827", font=title_font)
        style.configure("CardTitle.TLabel", background="#eef2f7", foreground="#0f172a", font=section_font)
        style.configure("TCheckbutton", background="#eef2f7", font=default_font)
        style.configure("TRadiobutton", background="#eef2f7", font=default_font)
        style.configure("Treeview", font=default_font, rowheight=24)
        style.configure("Treeview.Heading", font=section_font)
        style.configure("Badge.TLabel", background="#dcfce7", foreground="#166534", padding=(10, 4))
        style.configure("WarningBadge.TLabel", background="#fef3c7", foreground="#92400e", padding=(10, 4))
        style.configure("TLabelframe", background="#eef2f7", bordercolor="#d6dde8", relief="solid")
        style.configure("TLabelframe.Label", background="#eef2f7", foreground="#334155", font=section_font)
        style.configure("TButton", padding=(10, 7))
        style.configure("Accent.TButton", padding=(14, 9), foreground="#ffffff", background="#2563eb")
        style.map("Accent.TButton", background=[("active", "#1d4ed8"), ("pressed", "#1e40af")])
        if tb:
            style.configure("primary.TButton", background="#087f73", foreground="#ffffff")
            style.map("primary.TButton", background=[("active", "#06665c"), ("pressed", "#05564d")])

    def _environment_summary(self, messages: list[str]) -> str:
        if any("missing" in message.casefold() for message in messages):
            return "Environment needs attention"
        return "Environment ready"

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        header = ttk.Frame(self, style="Surface.TFrame", padding=(14, 12))
        header.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 10))
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text=f"{core.APP_NAME}", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            header,
            text="Music Library",
            style="Muted.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(2, 0))
        self.environment_var = tk.StringVar(value="Checking environment...")
        header_actions = ttk.Frame(header, style="Surface.TFrame")
        header_actions.grid(row=0, column=1, rowspan=2, sticky="e")
        ttk.Label(header_actions, text=f"v{core.APP_VERSION}", style="Muted.TLabel").grid(row=0, column=0, sticky="e", padx=(0, 10))
        self.environment_label = ttk.Label(header_actions, textvariable=self.environment_var, style="Badge.TLabel")
        self.environment_label.grid(row=0, column=1, sticky="e")
        ttk.Button(header_actions, text="Open Local Folder", command=self.open_output_dir).grid(row=0, column=2, sticky="e", padx=(10, 0))
        self.simple_mode_var = tk.BooleanVar(value=self.settings.simple_mode)
        view_switch = ttk.Frame(header, style="Surface.TFrame")
        view_switch.grid(row=2, column=0, columnspan=2, sticky="e", pady=(8, 0))
        ttk.Radiobutton(view_switch, text="Simple", variable=self.simple_mode_var, value=True,
                        command=self.apply_view_mode).grid(row=0, column=0)
        ttk.Radiobutton(view_switch, text="Advanced", variable=self.simple_mode_var, value=False,
                        command=self.apply_view_mode).grid(row=0, column=1, padx=(12, 0))

        toolbar = ttk.Frame(self, padding=(16, 0))
        self.advanced_toolbar = toolbar
        toolbar.grid(row=1, column=0, sticky="ew")
        toolbar.columnconfigure(0, weight=1)

        intake_tabs = ttk.Notebook(toolbar)
        intake_tabs.grid(row=0, column=0, sticky="ew")
        intake = ttk.Frame(intake_tabs, padding=12)
        search = ttk.Frame(intake_tabs, padding=12)
        intake_tabs.add(intake, text="Links")
        intake_tabs.add(search, text="Find Music")
        intake.columnconfigure(0, weight=1)
        self.clipboard_var = tk.StringVar(value="No links added")
        ttk.Label(intake, textvariable=self.clipboard_var).grid(row=0, column=0, sticky="w")
        self.provider_var = tk.StringVar(value=self.settings.provider)
        ttk.Combobox(intake, textvariable=self.provider_var, values=["Auto", "yt-dlp", "spotDL"],
                     state="readonly", width=12).grid(row=0, column=1, sticky="e")
        self.url_listbox = tk.Listbox(
            intake,
            height=3,
            activestyle="none",
            borderwidth=0,
            highlightthickness=1,
            highlightbackground="#cbd5e1",
            selectbackground="#2563eb",
            selectforeground="#ffffff",
        )
        self.url_listbox.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        list_actions = ttk.Frame(intake)
        list_actions.grid(row=2, column=1, sticky="e", padx=(10, 0), pady=(10, 0))
        ttk.Button(list_actions, text="Clipboard", command=lambda: self.scan_clipboard(auto=False)).grid(row=0, column=0)
        ttk.Button(list_actions, text="Remove", command=self.remove_selected_urls).grid(row=0, column=1, padx=5)
        ttk.Button(list_actions, text="Clear", command=self.clear_urls).grid(row=0, column=2)

        manual = ttk.Frame(intake)
        manual.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        manual.columnconfigure(0, weight=1)
        self.url_text = tk.Text(manual, height=1, wrap="word", borderwidth=0, highlightthickness=1, highlightbackground="#cbd5e1")
        self.url_text.grid(row=0, column=0, sticky="ew")
        ttk.Button(manual, text="Add Links", command=self.add_typed_urls).grid(row=0, column=1, sticky="ns", padx=(8, 0))

        search.columnconfigure(0, weight=1)
        search_bar = ttk.Frame(search)
        search_bar.grid(row=0, column=0, sticky="ew")
        search_bar.columnconfigure(0, weight=1)
        self.search_var = tk.StringVar()
        self.search_engine = tk.StringVar(value="YouTube")
        search_entry = ttk.Entry(search_bar, textvariable=self.search_var)
        search_entry.grid(row=0, column=0, sticky="ew")
        search_entry.bind("<Return>", lambda _event: self.search_music())
        ttk.Combobox(search_bar, textvariable=self.search_engine, values=["YouTube", "Spotify"],
                     state="readonly", width=12).grid(row=0, column=1, padx=8)
        self.search_button = ttk.Button(search_bar, text="Search", command=self.search_music)
        self.search_button.grid(row=0, column=2)

        buttons = ttk.Frame(toolbar)
        buttons.grid(row=0, column=1, sticky="n", padx=(10, 0))
        ttk.Button(buttons, text="Start Download", style=self.primary_button_style, command=self.add_job).grid(row=0, column=0, sticky="ew")
        ttk.Button(buttons, text="Preview Command", command=self.show_command).grid(row=1, column=0, sticky="ew", pady=6)
        self.stop_button = ttk.Button(buttons, text="Stop Downloads", style=self.danger_button_style, command=self.cancel_current)
        self.stop_button.grid(row=2, column=0, sticky="ew")

        main = ttk.PanedWindow(self, orient="horizontal")
        self.main_pane = main
        main.grid(row=2, column=0, sticky="nsew", padx=16, pady=(12, 12))

        left = ttk.Frame(main, padding=0)
        right = ttk.Frame(main, padding=0)
        self.settings_panel = left
        self.activity_panel = right
        main.add(left, weight=1)
        main.add(right, weight=2)

        self._build_settings(left)
        self._build_log(right)
        self._build_simple_panel()

        status = ttk.Frame(self, padding=(16, 0, 16, 12))
        status.grid(row=3, column=0, sticky="ew")
        status.columnconfigure(0, weight=1)
        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(status, textvariable=self.status_var).grid(row=0, column=0, sticky="w")
        ttk.Button(status, text="Save Settings", command=self.save_from_ui).grid(row=0, column=1, sticky="e")

    def _build_settings(self, parent: ttk.Frame) -> None:
        self._vars = {
            "output_dir": tk.StringVar(),
            "output_subfolder": tk.StringVar(),
            "mode": tk.StringVar(),
            "audio_preset": tk.StringVar(),
            "video_preset": tk.StringVar(),
            "filename_template": tk.StringVar(),
            "embed_thumbnail": tk.BooleanVar(),
            "embed_metadata": tk.BooleanVar(),
            "download_subtitles": tk.BooleanVar(),
            "playlist": tk.BooleanVar(),
            "sponsorblock": tk.BooleanVar(),
            "keep_original": tk.BooleanVar(),
            "write_archive": tk.BooleanVar(),
            "restrict_filenames": tk.BooleanVar(),
            "sync_enabled": tk.BooleanVar(),
            "sync_dir": tk.StringVar(),
            "sync_library_root": tk.StringVar(),
            "sync_match_output_folder": tk.BooleanVar(),
            "auto_dedupe_after_download": tk.BooleanVar(),
            "dedupe_exact_only": tk.BooleanVar(),
            "cookies_file": tk.StringVar(),
            "extra_args": tk.StringVar(),
            "provider": self.provider_var,
            "spotdl_template": tk.StringVar(),
            "spotdl_extra_args": tk.StringVar(),
            "watch_clipboard": tk.BooleanVar(),
            "simple_mode": self.simple_mode_var,
            "spotify_album_folders": tk.BooleanVar(),
            "spotify_all_editions": tk.BooleanVar(),
            "auto_preview": tk.BooleanVar(),
        }

        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)
        ttk.Label(parent, text="Settings", style="CardTitle.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 8))

        self.settings_tabs = ttk.Notebook(parent)
        self.settings_tabs.grid(row=1, column=0, sticky="nsew")

        format_tab = ttk.Frame(self.settings_tabs, padding=12)
        library_tab = ttk.Frame(self.settings_tabs, padding=12)
        sync_tab = ttk.Frame(self.settings_tabs, padding=12)
        cleanup_tab = ttk.Frame(self.settings_tabs, padding=12)
        advanced_tab = ttk.Frame(self.settings_tabs, padding=12)
        for tab in (format_tab, library_tab, sync_tab, cleanup_tab, advanced_tab):
            tab.columnconfigure(1, weight=1)

        self.settings_tabs.add(format_tab, text="Format")
        self.settings_tabs.add(library_tab, text="Library")
        self.settings_tabs.add(sync_tab, text="Sync")
        self.settings_tabs.add(cleanup_tab, text="Cleanup")
        self.settings_tabs.add(advanced_tab, text="Advanced")

        row = 0
        ttk.Label(format_tab, text="Download mode").grid(row=row, column=0, sticky="w", pady=(0, 8))
        mode = ttk.Frame(format_tab)
        mode.grid(row=row, column=1, sticky="ew")
        ttk.Radiobutton(mode, text="Audio", value="audio", variable=self._vars["mode"]).grid(row=0, column=0)
        ttk.Radiobutton(mode, text="Video", value="video", variable=self._vars["mode"]).grid(row=0, column=1, padx=10)

        row += 1
        ttk.Label(format_tab, text="Audio preset").grid(row=row, column=0, sticky="w", pady=8)
        ttk.Combobox(format_tab, textvariable=self._vars["audio_preset"], values=list(core.AUDIO_PRESETS), state="readonly").grid(
            row=row, column=1, sticky="ew", pady=6
        )

        row += 1
        ttk.Label(format_tab, text="Video preset").grid(row=row, column=0, sticky="w", pady=8)
        ttk.Combobox(format_tab, textvariable=self._vars["video_preset"], values=list(core.VIDEO_PRESETS), state="readonly").grid(
            row=row, column=1, sticky="ew", pady=6
        )

        row += 1
        ttk.Label(format_tab, text="Filename template").grid(row=row, column=0, sticky="w", pady=8)
        ttk.Entry(format_tab, textvariable=self._vars["filename_template"]).grid(row=row, column=1, sticky="ew", pady=6)

        row = 0
        ttk.Label(library_tab, text="Local library").grid(row=row, column=0, sticky="w", pady=8)
        output = ttk.Frame(library_tab)
        output.grid(row=row, column=1, sticky="ew", pady=6)
        output.columnconfigure(0, weight=1)
        ttk.Entry(output, textvariable=self._vars["output_dir"]).grid(row=0, column=0, sticky="ew")
        ttk.Button(output, text="Browse", command=self.browse_output).grid(row=0, column=1, padx=(6, 0))

        row += 1
        ttk.Label(library_tab, text="Artist/project folder").grid(row=row, column=0, sticky="w", pady=8)
        output_folder = ttk.Frame(library_tab)
        output_folder.grid(row=row, column=1, sticky="ew", pady=(0, 6))
        output_folder.columnconfigure(0, weight=1)
        ttk.Entry(output_folder, textvariable=self._vars["output_subfolder"]).grid(row=0, column=0, sticky="ew")
        ttk.Button(output_folder, text="Create & Use", command=self.apply_output_subfolder).grid(row=0, column=1, padx=(6, 0))
        ttk.Label(library_tab, text="Artist layout").grid(row=2, column=0, sticky="w", pady=10)
        layout = ttk.Frame(library_tab)
        layout.grid(row=2, column=1, sticky="w", pady=10)
        self.library_album_folders = ttk.Radiobutton(layout, text="Album folders", variable=self._vars["spotify_album_folders"], value=True)
        self.library_album_folders.grid(row=0, column=0)
        self.library_one_folder = ttk.Radiobutton(layout, text="One folder", variable=self._vars["spotify_album_folders"], value=False)
        self.library_one_folder.grid(row=0, column=1, padx=(12, 0))
        ttk.Checkbutton(library_tab, text="Artist downloads: include all editions", variable=self._vars["spotify_all_editions"]).grid(row=3, column=0, columnspan=2, sticky="w")

        sync_tab.columnconfigure(0, weight=1)
        row = 0
        ttk.Checkbutton(
            sync_tab,
            text="Mirror completed downloads to a second library",
            variable=self._vars["sync_enabled"],
        ).grid(row=row, column=0, columnspan=2, sticky="w", pady=(0, 12))

        row += 1
        ttk.Label(sync_tab, text="Mirror folder").grid(row=row, column=0, sticky="w", pady=(0, 6))
        sync_target = ttk.Frame(sync_tab)
        sync_target.grid(row=row, column=1, sticky="ew", pady=(0, 6))
        sync_target.columnconfigure(0, weight=1)
        ttk.Entry(sync_target, textvariable=self._vars["sync_dir"]).grid(row=0, column=0, sticky="ew")
        ttk.Button(sync_target, text="Browse", command=self.browse_sync).grid(row=0, column=1, padx=(8, 0))

        row += 1
        ttk.Checkbutton(
            sync_tab,
            text="Follow the local artist/project folder",
            variable=self._vars["sync_match_output_folder"],
        ).grid(row=row, column=1, sticky="w", pady=(4, 0))

        row += 1
        ttk.Button(sync_tab, text="Sync Now", command=self.sync_now).grid(row=row, column=1, sticky="e", pady=14)

        checks = ttk.LabelFrame(format_tab, text="yt-dlp options", padding=10)
        checks.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(14, 0))
        checks.columnconfigure(0, weight=1)
        options = [
            ("embed_thumbnail", "Embed thumbnail"),
            ("embed_metadata", "Embed metadata"),
            ("download_subtitles", "Download subtitles"),
            ("playlist", "Allow playlists"),
            ("keep_original", "Keep source files"),
            ("write_archive", "Download archive file"),
            ("restrict_filenames", "Filesystem-safe names"),
        ]
        for index, (name, label) in enumerate(options):
            ttk.Checkbutton(checks, text=label, variable=self._vars[name]).grid(
                row=index // 2, column=index % 2, sticky="w", padx=4, pady=3
            )

        sponsor = ttk.LabelFrame(cleanup_tab, text="SponsorBlock", padding=10)
        sponsor.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        ttk.Checkbutton(sponsor, text="Remove sponsors, outros and non-music", variable=self._vars["sponsorblock"]).grid(row=0, column=0, sticky="w")
        dedupe = ttk.LabelFrame(cleanup_tab, text="Duplicate review", padding=10)
        dedupe.grid(row=1, column=0, columnspan=2, sticky="ew")
        dedupe.columnconfigure((0, 1), weight=1)
        ttk.Checkbutton(dedupe, text="Exact files only", variable=self._vars["dedupe_exact_only"]).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 6)
        )
        ttk.Checkbutton(dedupe, text="Move exact duplicates after downloads", variable=self._vars["auto_dedupe_after_download"]).grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(0, 6)
        )
        ttk.Button(dedupe, text="Scan Only", command=lambda: self.start_dedupe(apply=False)).grid(
            row=2, column=0, sticky="ew", padx=(0, 5)
        )
        ttk.Button(dedupe, text="Move to Review Folder", command=lambda: self.start_dedupe(apply=True)).grid(
            row=2, column=1, sticky="ew", padx=(5, 0)
        )
        ttk.Label(cleanup_tab, text="Title matches are candidates and may include different recordings.",
                  style="PanelMuted.TLabel", wraplength=360).grid(row=2, column=0, columnspan=2, sticky="w", pady=10)

        row = 0
        ttk.Label(advanced_tab, text="Cookies file").grid(row=row, column=0, sticky="w", pady=8)
        cookies = ttk.Frame(advanced_tab)
        cookies.grid(row=row, column=1, sticky="ew", pady=6)
        cookies.columnconfigure(0, weight=1)
        ttk.Entry(cookies, textvariable=self._vars["cookies_file"]).grid(row=0, column=0, sticky="ew")
        ttk.Button(cookies, text="File", command=self.browse_cookies).grid(row=0, column=1, padx=(6, 0))

        row += 1
        ttk.Label(advanced_tab, text="Extra yt-dlp args").grid(row=row, column=0, sticky="w", pady=8)
        ttk.Entry(advanced_tab, textvariable=self._vars["extra_args"]).grid(row=row, column=1, sticky="ew", pady=6)
        ttk.Label(advanced_tab, text="Spotify filename").grid(row=2, column=0, sticky="w", pady=8)
        ttk.Entry(advanced_tab, textvariable=self._vars["spotdl_template"]).grid(row=2, column=1, sticky="ew", pady=6)
        ttk.Label(advanced_tab, text="Extra spotDL args").grid(row=3, column=0, sticky="w", pady=8)
        ttk.Entry(advanced_tab, textvariable=self._vars["spotdl_extra_args"]).grid(row=3, column=1, sticky="ew", pady=6)
        ttk.Checkbutton(advanced_tab, text="Capture links from clipboard", variable=self._vars["watch_clipboard"]).grid(row=4, column=0, columnspan=2, sticky="w", pady=12)
        ttk.Checkbutton(advanced_tab, text="Preview pasted links", variable=self._vars["auto_preview"]).grid(row=7, column=0, columnspan=2, sticky="w", pady=8)
        self.spotify_status = tk.StringVar(value="Spotify support installed" if core.spotdl_available() else "Spotify support not installed")
        ttk.Label(advanced_tab, textvariable=self.spotify_status).grid(row=5, column=0, columnspan=2, sticky="w")
        self.install_spotify_button = ttk.Button(advanced_tab, text="Install Spotify Support", command=self.install_spotify)
        self.install_spotify_button.grid(row=6, column=0, columnspan=2, sticky="ew", pady=8)

    def _build_log(self, parent: ttk.Frame) -> None:
        parent.rowconfigure(3, weight=1)
        parent.columnconfigure(0, weight=1)
        self.queue_var = tk.StringVar(value="Queue is empty")
        self.activity_heading = ttk.Label(parent, text="Activity", style="CardTitle.TLabel")
        self.activity_heading.grid(row=0, column=0, sticky="w", pady=(0, 8))
        top = ttk.Frame(parent, padding=(0, 0, 0, 6))
        self.log_toolbar = top
        top.grid(row=1, column=0, sticky="ew")
        top.columnconfigure(0, weight=1)
        self.queue_summary_label = ttk.Label(top, textvariable=self.queue_var)
        self.queue_summary_label.grid(row=0, column=0, sticky="w")
        self.copy_log_button = ttk.Button(top, text="Copy Log", command=self.copy_log)
        self.copy_log_button.grid(row=0, column=1, sticky="e", padx=(8, 0))
        self.clear_log_button = ttk.Button(top, text="Clear Log", command=self.clear_log)
        self.clear_log_button.grid(row=0, column=2, sticky="e", padx=(6, 0))
        jobs_panel = ttk.Frame(parent)
        self.jobs_panel = jobs_panel
        jobs_panel.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        jobs_panel.columnconfigure(0, weight=1)
        self.job_table = ttk.Treeview(jobs_panel, columns=("source", "items", "status"), show="headings", height=5)
        for key, title, width in (("source", "Source", 90), ("items", "Items", 250), ("status", "Status", 150)):
            self.job_table.heading(key, text=title)
            self.job_table.column(key, width=width, minwidth=60, stretch=key != "source")
        self.job_table.grid(row=0, column=0, sticky="ew")
        jobs_scroll = ttk.Scrollbar(jobs_panel, orient="vertical", command=self.job_table.yview)
        jobs_scroll.grid(row=0, column=1, sticky="ns")
        self.job_table.configure(yscrollcommand=jobs_scroll.set)
        job_actions = ttk.Frame(jobs_panel)
        job_actions.grid(row=1, column=0, sticky="e", pady=(6, 0))
        ttk.Button(job_actions, text="Retry Selected", command=self.retry_selected).grid(row=0, column=0)
        ttk.Button(job_actions, text="Cancel Queued", command=self.clear_pending).grid(row=0, column=1, padx=6)
        ttk.Button(job_actions, text="Open Folder", command=self.open_job_folder).grid(row=0, column=2, padx=6)
        ttk.Button(job_actions, text="Remove Finished", command=self.remove_finished).grid(row=0, column=3)

        self.log = tk.Text(
            parent,
            wrap="word",
            state="disabled",
            borderwidth=0,
            highlightthickness=1,
            highlightbackground="#cbd5e1",
            background="#ffffff",
            foreground="#0f172a",
            insertbackground="#0f172a",
            selectbackground="#2563eb",
            padx=12,
            pady=10,
            font=("Consolas", 9),
        )
        self.log.grid(row=3, column=0, sticky="nsew")
        log_scroll = ttk.Scrollbar(parent, orient="vertical", command=self.log.yview)
        self.log_scroll = log_scroll
        log_scroll.grid(row=3, column=1, sticky="ns")
        self.log.configure(yscrollcommand=log_scroll.set)

    def _build_simple_panel(self) -> None:
        panel = ttk.Frame(self, padding=(16, 0))
        self.simple_panel = panel
        panel.columnconfigure(0, minsize=80)
        panel.columnconfigure(2, minsize=96)
        panel.columnconfigure(1, weight=1)
        self.simple_link_var = tk.StringVar()
        self.details_var = tk.BooleanVar(value=False)
        ttk.Label(panel, text="Link").grid(row=0, column=0, sticky="w", padx=(0, 12))
        link = ttk.Entry(panel, textvariable=self.simple_link_var)
        link.grid(row=0, column=1, sticky="ew", pady=6)
        link.bind("<Return>", lambda _event: self.simple_download())
        link_actions = ttk.Frame(panel)
        link_actions.grid(row=0, column=2, padx=8)
        ttk.Button(link_actions, text="Paste", command=self.simple_paste, width=7).grid(row=0, column=0, padx=(0, 6))
        self.preview_button = ttk.Button(link_actions, text="Preview", command=self.preview_link, width=13)
        self.preview_button.grid(row=0, column=1)
        ttk.Button(panel, text="Download", style=self.primary_button_style,
                   command=self.simple_download).grid(row=0, column=3)
        preview = ttk.Frame(panel, height=78)
        preview.grid(row=1, column=0, columnspan=4, sticky="ew", pady=(6, 10))
        preview.columnconfigure(0, weight=1)
        self.preview_title = tk.StringVar(value="No link selected")
        self.preview_info = tk.StringVar()
        self.preview_title_label = ttk.Label(preview, textvariable=self.preview_title, style="CardTitle.TLabel", wraplength=750)
        self.preview_title_label.grid(row=0, column=0, sticky="w")
        self.preview_info_label = ttk.Label(preview, textvariable=self.preview_info, style="PanelMuted.TLabel", wraplength=750)
        self.preview_info_label.grid(row=1, column=0, sticky="w", pady=4)
        self.review_button = ttk.Button(preview, text="Review Artist", command=self.review_preview_artist, state="disabled")
        self.review_button.grid(row=0, column=1, rowspan=2, padx=(8, 0))
        self.review_button.grid_remove()
        preview.columnconfigure(1, minsize=100)
        preview.rowconfigure(2, minsize=16)
        self.preview_progress = ttk.Progressbar(preview, mode="indeterminate")
        self.preview_progress.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        self.preview_progress.grid_remove()
        self.bind("<Configure>", self._resize_preview, add=True)
        self.simple_link_var.trace_add("write", lambda *_: self._link_changed())
        self._vars["auto_preview"].trace_add("write", lambda *_: self._link_changed())
        ttk.Label(panel, text="Output").grid(row=2, column=0, sticky="w")
        formats = ttk.Frame(panel)
        formats.grid(row=2, column=1, columnspan=3, sticky="ew", pady=6)
        ttk.Radiobutton(formats, text="Audio", variable=self._vars["mode"], value="audio").grid(row=0, column=0)
        ttk.Radiobutton(formats, text="Video", variable=self._vars["mode"], value="video").grid(row=0, column=1, padx=12)
        ttk.Label(formats, text="Format").grid(row=0, column=2, padx=(4, 6))
        self.simple_format_var = tk.StringVar()
        self.simple_quality_var = tk.StringVar()
        self.simple_format = ttk.Combobox(formats, textvariable=self.simple_format_var, state="readonly", width=7)
        self.simple_format.grid(row=0, column=3)
        ttk.Label(formats, text="Quality").grid(row=0, column=4, padx=(12, 6))
        self.simple_quality = ttk.Combobox(formats, textvariable=self.simple_quality_var, state="readonly", width=16)
        self.simple_quality.grid(row=0, column=5)
        self.simple_format.bind("<<ComboboxSelected>>", lambda _: self.choose_simple_format())
        self.simple_quality.bind("<<ComboboxSelected>>", lambda _: self.choose_simple_format())
        ttk.Button(formats, text="Find Music / Artist", command=self.open_music_browser).grid(row=0, column=6, padx=(12, 0))
        self._vars["mode"].trace_add("write", lambda *_: self.update_simple_format())
        for key in ("audio_preset", "video_preset"):
            self._vars[key].trace_add("write", lambda *_: self.update_simple_format())
        self.update_simple_format()
        ttk.Label(panel, text="Save to").grid(row=3, column=0, sticky="w")
        ttk.Entry(panel, textvariable=self._vars["output_dir"]).grid(row=3, column=1, sticky="ew", pady=6)
        ttk.Button(panel, text="Browse", command=self.browse_output).grid(row=3, column=2, padx=8)
        ttk.Label(panel, text="Artist layout").grid(row=4, column=0, sticky="w")
        layout = ttk.Frame(panel)
        layout.grid(row=4, column=1, columnspan=3, sticky="w", pady=6)
        self.simple_album_folders = ttk.Radiobutton(layout, text="Album folders", variable=self._vars["spotify_album_folders"], value=True)
        self.simple_album_folders.grid(row=0, column=0)
        self.simple_one_folder = ttk.Radiobutton(layout, text="One folder", variable=self._vars["spotify_album_folders"], value=False)
        self.simple_one_folder.grid(row=0, column=1, padx=(12, 0))
        options = ttk.Frame(panel)
        options.grid(row=5, column=0, columnspan=4, sticky="ew", pady=(8, 0))
        ttk.Checkbutton(options, text="SponsorBlock", variable=self._vars["sponsorblock"]).grid(row=0, column=0)
        ttk.Checkbutton(options, text="Mirror to NAS", variable=self._vars["sync_enabled"]).grid(row=0, column=1, padx=16)
        ttk.Checkbutton(options, text="Details", variable=self.details_var,
                        command=self.apply_view_mode).grid(row=0, column=2, padx=16)
        self.simple_stop_button = ttk.Button(options, text="Stop Downloads", command=self.cancel_current)
        self.simple_stop_button.grid(row=0, column=3)
        self.simple_mirror_row = ttk.Frame(panel)
        self.simple_mirror_row.grid(row=6, column=0, columnspan=4, sticky="ew", pady=(8, 0))
        self.simple_mirror_row.columnconfigure(0, minsize=80)
        self.simple_mirror_row.columnconfigure(2, minsize=96)
        self.simple_mirror_row.columnconfigure(1, weight=1)
        ttk.Label(self.simple_mirror_row, text="Mirror to").grid(row=0, column=0, padx=(0, 12))
        ttk.Entry(self.simple_mirror_row, textvariable=self._vars["sync_dir"], state="readonly").grid(row=0, column=1, sticky="ew")
        ttk.Button(self.simple_mirror_row, text="Browse", command=self.browse_sync).grid(row=0, column=2, padx=8)
        self._vars["sync_enabled"].trace_add("write", lambda *_: self.update_simple_mirror())
        self.update_simple_mirror()

    def update_simple_mirror(self) -> None:
        if self._vars["sync_enabled"].get():
            self.simple_mirror_row.grid()
        else:
            self.simple_mirror_row.grid_remove()

    def update_simple_format(self) -> None:
        mode = self._vars["mode"].get()
        format_name, quality = selection_for(mode, self._vars["video_preset" if mode == "video" else "audio_preset"].get())
        choices = choices_for(mode)
        self.simple_format.configure(values=list(choices))
        self.simple_format_var.set(format_name)
        self.simple_quality.configure(values=list(choices[format_name]), state="readonly" if len(choices[format_name]) > 1 else "disabled")
        self.simple_quality_var.set(quality)

    def choose_simple_format(self) -> None:
        mode = self._vars["mode"].get()
        choices = choices_for(mode)[self.simple_format_var.get()]
        quality = self.simple_quality_var.get()
        if quality not in choices:
            quality = next(iter(choices))
        self._vars["video_preset" if mode == "video" else "audio_preset"].set(choices[quality])

    def apply_view_mode(self) -> None:
        simple = self.simple_mode_var.get()
        details = not simple or self.details_var.get()
        self.activity_heading.configure(text="Downloads" if simple else "Activity")
        if simple:
            self.advanced_toolbar.grid_remove()
            self.simple_panel.grid(row=1, column=0, sticky="ew")
            if str(self.settings_panel) in self.main_pane.panes():
                self.main_pane.forget(self.settings_panel)
            self.minsize(900, 700)
        else:
            self.simple_panel.grid_remove()
            self.advanced_toolbar.grid()
            if str(self.settings_panel) not in self.main_pane.panes():
                self.main_pane.insert(0, self.settings_panel, weight=1)
            self.minsize(1100, 800)
        if getattr(self, "_active_simple_view", None) != simple:
            self.geometry("1000x760" if simple else "1180x840")
            self._active_simple_view = simple
        self.jobs_panel.grid_configure(sticky="nsew" if simple and not details else "ew")
        self.job_table.configure(height=2 if simple and details else 5)
        self.log.configure(height=6 if simple else 24)
        self.jobs_panel.rowconfigure(0, weight=1 if simple and not details else 0)
        self.job_table.grid_configure(sticky="nsew")
        self.activity_panel.rowconfigure(2, weight=1 if simple and not details else 0)
        self.activity_panel.rowconfigure(3, weight=1 if details else 0, minsize=72 if details else 0)
        for widget in (self.log, self.log_scroll, self.copy_log_button, self.clear_log_button):
            widget.grid() if details else widget.grid_remove()
        self.log_toolbar.grid()

    def simple_paste(self) -> None:
        try:
            urls = core.extract_urls(self.clipboard_get())
        except tk.TclError:
            urls = []
        if urls:
            self.simple_link_var.set(" ".join(urls))
        else:
            self.status_var.set("No links found on the clipboard.")

    def _resize_preview(self, event) -> None:
        if event.widget is self:
            self.preview_title_label.configure(wraplength=max(400, event.width - 220))
            self.preview_info_label.configure(wraplength=max(400, event.width - 220))
            if hasattr(self, "simple_mirror_row"):
                for column in (2, 3):
                    self.simple_mirror_row.columnconfigure(column, minsize=self.simple_panel.grid_bbox(column, 0)[2])

    def _link_changed(self) -> None:
        if self.preview_timer is not None:
            self.after_cancel(self.preview_timer)
            self.preview_timer = None
        self.preview.invalidate()
        self.preview_data = None
        self.preview_url = ""
        self.preview_loading = False
        self.preview_progress.stop()
        self.preview_progress.grid_remove()
        self.preview_progress.configure(value=0)
        self.preview_button.configure(text="Preview")
        self.review_button.configure(state="disabled")
        self.review_button.grid_remove()
        urls = core.extract_urls(self.simple_link_var.get())
        self.preview_title.set(f"{len(urls)} links ready" if len(urls) > 1 else "Link ready" if urls else "No link selected")
        self.preview_info.set("Batch download" if len(urls) > 1 else "")
        if len(urls) == 1 and self._vars["auto_preview"].get():
            self.preview_timer = self.after(650, self.preview_link)

    def preview_link(self) -> None:
        if self.preview_timer is not None:
            self.after_cancel(self.preview_timer)
            self.preview_timer = None
        if self.preview_loading:
            self.preview.invalidate()
            self.preview_loading = False
            self.preview_progress.stop()
            self.preview_progress.grid_remove()
            self.preview_progress.configure(value=0)
            self.preview_button.configure(text="Preview")
            self.preview_title.set("Link ready")
            self.preview_info.set("Preview cancelled")
            return
        urls = core.extract_urls(self.simple_link_var.get())
        if len(urls) != 1:
            self.preview_info.set("Select one link to preview" if urls else "No link selected")
            return
        self.preview_url = urls[0]
        self.preview_loading = True
        self.preview_title.set("Reading link...")
        self.preview_info.set("")
        self.preview_button.configure(text="Cancel Preview")
        self.preview_progress.grid()
        self.preview_progress.start(12)
        self.preview.start(urls[0], self._settings_from_ui())

    def _poll_preview(self) -> None:
        result = self.preview.poll()
        if result is not None:
            data, error = result
            self.preview_loading = False
            self.preview_progress.stop()
            self.preview_progress.grid_remove()
            self.preview_progress.configure(value=0)
            self.preview_button.configure(text="Preview")
            if error:
                self.preview_title.set("Preview unavailable")
                self.preview_info.set(error[:180] + ("..." if len(error) > 180 else ""))
                self._append_log(f"Preview: {error}\n")
            else:
                self.preview_data = data
                title = data["title"]
                self.preview_title.set(title[:180] + ("..." if len(title) > 180 else ""))
                self.preview_info.set(preview_summary(data))
                self.review_button.configure(state="normal" if data["source"] == "Spotify" and data["kind"] == "artist" else "disabled")
                if data["source"] == "Spotify" and data["kind"] == "artist":
                    self.review_button.grid()
        self.after(120, self._poll_preview)

    def review_preview_artist(self) -> None:
        if self.preview_data and self.preview_data["kind"] == "artist":
            browser = self.open_music_browser(self.preview_data["url"], "Spotify")
            browser.load_artist(self.preview_data["url"])

    def open_job_folder(self) -> None:
        selected = self.job_table.selection()
        job = self.job_history.get(selected[0]) if selected else None
        folder = Path(job.settings.output_dir if job else self._vars["output_dir"].get()).expanduser()
        if not folder.is_dir():
            self.status_var.set("The download folder does not exist yet")
            return
        if os.name == "nt":
            os.startfile(folder)
        else:
            subprocess.Popen(["xdg-open", str(folder)])

    def remove_finished(self) -> None:
        active = {"Queued", "Downloading", "Syncing", "Reviewing", "Discovering"}
        for identity, job in list(self.job_history.items()):
            if job.status not in active and job is not self.current_job:
                self.job_table.delete(identity)
                del self.job_history[identity]

    def simple_download(self) -> None:
        urls = core.extract_urls(self.simple_link_var.get())
        if not urls:
            messagebox.showwarning(core.APP_NAME, "Enter a link first.")
            return
        settings = self._settings_from_ui()
        settings.provider = "Auto"
        try:
            core.save_settings(self._settings_from_ui())
            title = self.preview_data["title"] if len(urls) == 1 and self.preview_data and self.preview_url == urls[0] else ""
            self.queue_url_jobs(urls, settings, on_queued=lambda: self.consume_urls(urls), title=title)
        except (ValueError, OSError) as exc:
            messagebox.showerror(core.APP_NAME, str(exc))

    def consume_urls(self, urls: list[str]) -> None:
        if core.extract_urls(self.simple_link_var.get()) == urls:
            self.simple_link_var.set("")
        for index in reversed(range(len(self.detected_urls))):
            if self.detected_urls[index] in urls:
                del self.detected_urls[index]
                self.url_listbox.delete(index)
        self._update_clipboard_status(0)

    def queue_url_jobs(self, urls: list[str], settings: core.Settings, on_queued=None, title="") -> None:
        artists = [url for url in urls if core.is_spotify_artist(url) and core.provider_for(url, settings) == "spotDL"]
        if not artists:
            jobs = core.build_jobs(urls, settings)
            if title:
                for job in jobs:
                    job.label = title
            self.enqueue_jobs(jobs)
            if on_queued:
                on_queued()
            return
        cancel = threading.Event()
        placeholder = core.DownloadJob(urls, settings, provider="spotDL", status="Discovering",
                                       label=title or "Artist releases")
        def prepare():
            return core.prepare_jobs(urls, settings, cancel,
                                     lambda text: self.ui_events.put(("preparation", text, cancel)))
        def queued(count, error):
            if error:
                self._append_log(f"Artist preparation: {error}\n")
                self.status_var.set(error)
            else:
                self.status_var.set(f"Queued {count} download jobs")
                if on_queued:
                    on_queued()
        self.prepare_batch(prepare, queued, cancel, placeholder)

    def _load_vars(self) -> None:
        for key, var in self._vars.items():
            if key in core.SETTINGS_FIELDS:
                var.set(getattr(self.settings, key))

    def _settings_from_ui(self) -> core.Settings:
        values = {key: var.get() for key, var in self._vars.items() if key in core.SETTINGS_FIELDS}
        return core.Settings(**values)

    def _wire_setting_traces(self) -> None:
        self._vars["sync_dir"].trace_add("write", lambda *_args: self.capture_sync_root())
        self._vars["output_dir"].trace_add("write", lambda *_args: self.apply_matched_sync_field())
        self._vars["sync_enabled"].trace_add("write", lambda *_args: self.apply_matched_sync_field())
        self._vars["sync_match_output_folder"].trace_add("write", lambda *_args: self.apply_matched_sync_field())

    def capture_sync_root(self) -> None:
        if self._sync_field_updating:
            return
        settings = self._settings_from_ui()
        target = Path(settings.sync_dir).expanduser()
        source = Path(settings.output_dir).expanduser()
        if settings.sync_match_output_folder and target.name.casefold() == source.name.casefold():
            target = target.parent
        self._vars["sync_library_root"].set(str(target) if settings.sync_dir.strip() else "")

    def apply_matched_sync_field(self) -> None:
        if self._sync_field_updating:
            return
        try:
            settings = self._settings_from_ui()
            if settings.sync_dir.strip() and not settings.sync_library_root:
                self._vars["sync_library_root"].set(str(core.infer_sync_library_root(settings)))
                settings.sync_library_root = self._vars["sync_library_root"].get()
            if settings.sync_enabled and settings.sync_dir.strip() and settings.sync_match_output_folder:
                resolved = str(core.resolve_sync_target(settings))
                if resolved != settings.sync_dir:
                    self._sync_field_updating = True
                    self._vars["sync_dir"].set(resolved)
                    self._sync_field_updating = False
        except (OSError, RuntimeError, tk.TclError):
            self._sync_field_updating = False

    def save_from_ui(self) -> None:
        self.settings = self._settings_from_ui()
        core.save_settings(self.settings)
        self.status_var.set(f"Settings saved to {core.CONFIG_FILE}")

    def enqueue_jobs(self, jobs: list[core.DownloadJob]) -> None:
        if self.closing:
            return
        if self.stop_event.is_set():
            if self.worker and self.worker.is_alive():
                raise ValueError("Wait for the current download to stop before adding new jobs.")
            self.stop_event.clear()
        for job in jobs:
            self.job_history[job.id] = job
            self.job_table.insert("", "end", iid=job.id,
                                  values=(job.provider, job.label or f"{len(job.urls)}: {job.urls[0]}", "Queued"))
            self.jobs.put(job)
        self._start_worker()
        self.update_queue_summary()

    def retry_selected(self) -> None:
        for job_id in self.job_table.selection():
            old = self.job_history[job_id]
            if old.status in {"Queued", "Downloading", "Syncing", "Reviewing", "Discovering"}:
                continue
            try:
                jobs = []
                for source in old.retry_jobs or [old]:
                    urls = source.urls if source.status == "Cancelled" else source.failed_urls or source.urls
                    if any(core.is_spotify_artist(url) for url in urls):
                        self.queue_url_jobs(urls, source.settings, title=source.label)
                        continue
                    retries = core.build_jobs(urls, source.settings)
                    for job in retries:
                        job.label = source.label
                        job.release_id = source.release_id
                    jobs.extend(retries)
                if jobs:
                    self.enqueue_jobs(jobs)
            except (ValueError, OSError) as exc:
                messagebox.showerror(core.APP_NAME, str(exc))

    def clear_pending(self) -> None:
        if self.preparing:
            self.preparation_cancel.set()
        count = 0
        while True:
            try:
                job = self.jobs.get_nowait()
            except queue.Empty:
                break
            job.status = "Cancelled"
            self.ui_events.put(("job", job.id, "Cancelled"))
            self.jobs.task_done()
            count += 1
        if count:
            self.log_queue.put(f"Cancelled {count} queued jobs.\n")
        self.update_queue_summary()

    def set_job_status(self, job: core.DownloadJob, status: str) -> None:
        job.status = status
        if status in {"Completed", "Cancelled", "Mirror failed", "Failed"} or status.startswith(("Failed (", "Partial")):
            self.log_queue.put(f"{job.provider} job: {status}.\n")
        self.ui_events.put(("job", job.id, status))

    def maintenance_available(self) -> bool:
        if (self.worker and self.worker.is_alive()) or (self.maintenance_worker and self.maintenance_worker.is_alive()) or self.preparing:
            messagebox.showinfo(core.APP_NAME, "Wait for the current library operation to finish first.")
            return False
        return True

    def sync_now(self) -> None:
        if not self.maintenance_available():
            return
        settings = self._settings_from_ui()
        try:
            core.validate_download(settings)
            if not settings.sync_enabled:
                raise ValueError("Enable mirroring and choose a second library first.")
        except (ValueError, OSError) as exc:
            messagebox.showerror(core.APP_NAME, str(exc))
            return
        self.run_maintenance(lambda: core.sync_output_dir(settings, self.log_queue.put), "Sync")

    def run_maintenance(self, action: Callable, label: str) -> None:
        def worker() -> None:
            try:
                result = action()
                self.log_queue.put(f"{label} finished: {result}.\n")
                self.ui_events.put(("status", f"{label} failed ({result})" if result else f"{label} finished"))
            except Exception as exc:
                self.log_queue.put(f"{label} failed: {exc}\n")
                self.ui_events.put(("status", f"{label} failed"))
        self.status_var.set(f"{label} running...")
        self.maintenance_worker = threading.Thread(target=worker, daemon=True)
        self.maintenance_worker.start()

    def install_spotify(self) -> None:
        browser = getattr(self, "music_browser", None)
        if not self.maintenance_available():
            return
        if browser is not None and browser.winfo_exists() and browser.busy:
            messagebox.showinfo(core.APP_NAME, "Wait for music discovery to finish before changing its dependencies.")
            return
        requirements = Path(__file__).resolve().parents[1] / "requirements-spotify.txt"
        self.install_spotify_button.configure(state="disabled")
        def install() -> int:
            code = core.run_command([sys.executable, "-u", "-m", "pip", "install", "-r", str(requirements)], self.log_queue.put)
            self.ui_events.put(("spotify", code))
            return code
        self.run_maintenance(install, "Spotify setup")

    def search_music(self) -> None:
        browser = self.open_music_browser(self.search_var.get().strip(), self.search_engine.get())
        if browser.query.get().strip():
            browser.search()

    def open_music_browser(self, query: str = "", engine: str = "Spotify"):
        from .browser import MusicBrowser

        browser = getattr(self, "music_browser", None)
        if browser is None or not browser.winfo_exists():
            browser = self.music_browser = MusicBrowser(self, query, engine)
        elif not browser.busy:
            browser.engine.set(engine)
            if query:
                browser.query.set(query)
        browser.lift()
        browser.entry.focus_set()
        return browser

    @staticmethod
    def youtube_search_command(query: str, offset: int, limit: int) -> list[str]:
        return core.youtube_search_command(query, offset, limit)

    def queue_discovery_items(self, items: list[dict], destination: str, album_folders: bool,
                              catalogue: dict | None, on_done=None, cancel=None) -> int | None:
        settings = self._settings_from_ui()
        settings.output_dir = destination.strip()
        settings.provider = "Auto"
        settings.spotify_album_folders = album_folders
        def prepare():
            if catalogue is not None:
                return core.build_discography_jobs(catalogue, settings, items)
            if any(item["kind"] == "track" for item in items):
                settings.mode = "audio"
            return core.build_jobs([item["url"] for item in items], settings)
        if on_done is None:
            jobs = prepare()
            self.enqueue_jobs(jobs)
            return len(jobs)
        self.prepare_batch(prepare, on_done, cancel if cancel is not None else threading.Event())
        return None

    def prepare_batch(self, prepare: Callable, on_done: Callable, cancel: threading.Event, placeholder=None) -> None:
        if self.closing:
            raise ValueError("The app is closing.")
        if self.preparing:
            raise ValueError("A download batch is already being prepared.")
        if self.stop_event.is_set() and self.worker and self.worker.is_alive():
            raise ValueError("Wait for the current download to stop before preparing a new batch.")
        self.preparation_cancel = cancel
        self.preparing = True
        if placeholder:
            self.job_history[placeholder.id] = placeholder
            self.job_table.insert("", "end", iid=placeholder.id, values=(placeholder.provider, placeholder.label, placeholder.status))
        self.status_var.set("Preparing download jobs...")
        self.update_queue_summary()
        def worker():
            try:
                jobs, error = prepare(), None
            except Exception as exc:
                jobs, error = [], str(exc)
            self.ui_events.put(("prepared", jobs, error, on_done, cancel, placeholder))
        self.preparation_worker = threading.Thread(target=worker, daemon=True)
        try:
            self.preparation_worker.start()
        except (RuntimeError, OSError) as exc:
            self.preparing = False
            cancel.set()
            if placeholder:
                self.set_job_status(placeholder, "Failed")
            self.update_queue_summary()
            raise ValueError(f"Could not start download preparation: {exc}") from exc

    def close_app(self) -> None:
        if not self.closing and self.worker and self.worker.is_alive():
            if not messagebox.askyesno(core.APP_NAME, "Stop all downloads and close? Completed local files will be kept."):
                return
            self.closing = True
            self.cancel_current()
        self.preparation_cancel.set()
        if self.preparing:
            self.closing = True
        if self.preview_timer is not None:
            self.after_cancel(self.preview_timer)
            self.preview_timer = None
        self.preview.invalidate()
        if self.preview.busy:
            self.after(100, self.close_app)
            return
        browser = getattr(self, "music_browser", None)
        if browser is not None and browser.winfo_exists():
            browser.close()
        busy = (self.worker and self.worker.is_alive()) or (self.maintenance_worker and self.maintenance_worker.is_alive()) or (self.preparation_worker and self.preparation_worker.is_alive())
        if busy:
            if self.closing:
                self.after(100, self.close_app)
            else:
                messagebox.showinfo(core.APP_NAME, "Wait for the current library operation before closing.")
            return
        try:
            core.save_settings(self._settings_from_ui())
        except OSError as exc:
            messagebox.showerror(core.APP_NAME, f"Could not save settings: {exc}")
            return
        self.destroy()

    def destroy(self) -> None:
        self.preview.invalidate()
        self.preparation_cancel.set()
        browser = getattr(self, "music_browser", None)
        if browser is not None and browser.winfo_exists():
            browser.close()
        for timer in self.tk.call("after", "info"):
            self.after_cancel(timer)
        super().destroy()

    def report_callback_exception(self, exc_type, exc_value, exc_traceback) -> None:
        details = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
        self._append_log(details)
        messagebox.showerror(core.APP_NAME, str(exc_value))

    def start_dedupe(self, apply: bool) -> None:
        if not self.maintenance_available():
            return
        settings = self._settings_from_ui()
        output_dir = settings.output_dir
        exact_only = settings.dedupe_exact_only
        sync_dir = str(core.resolve_sync_target(settings)) if settings.sync_enabled and settings.sync_dir else None
        if apply:
            confirmed = messagebox.askyesno(
                core.APP_NAME,
                f"Move duplicate candidates from {output_dir} to {core.DUPLICATE_REVIEW_DIR}?",
            )
            if not confirmed:
                return
        self.run_maintenance(lambda: core.run_deduplicator(output_dir, self.log_queue.put, apply=apply,
                                                     exact_only=exact_only, sync_dir=sync_dir), "Duplicate review")

    def paste_urls(self) -> None:
        try:
            self.add_urls(core.extract_urls(self.clipboard_get()))
        except tk.TclError:
            pass

    def add_urls(self, urls: list[str]) -> int:
        added = 0
        for url in urls:
            if url in self.detected_urls:
                continue
            self.detected_urls.append(url)
            self.url_listbox.insert("end", url)
            added += 1
        self._update_clipboard_status(added)
        return added

    def add_typed_urls(self) -> None:
        urls = core.extract_urls(self.url_text.get("1.0", "end"))
        if not urls:
            messagebox.showwarning(core.APP_NAME, "No links found in the typed text.")
            return
        added = self.add_urls(urls)
        self.url_text.delete("1.0", "end")
        self.status_var.set(f"Added {added} typed link(s).")

    def scan_clipboard(self, auto: bool) -> None:
        try:
            text = self.clipboard_get()
        except tk.TclError:
            return
        if auto and text == self.last_clipboard_text:
            return
        self.last_clipboard_text = text
        urls = core.extract_urls(text)
        if not urls:
            if not auto:
                self.status_var.set("No links found on the clipboard.")
            return
        added = self.add_urls(urls)
        if added and self.simple_mode_var.get() and not self.simple_link_var.get().strip():
            self.simple_link_var.set(" ".join(urls))
        if added:
            self.status_var.set(f"Detected {added} new clipboard link(s).")
        elif not auto:
            self.status_var.set("Clipboard links are already in the list.")

    def remove_selected_urls(self) -> None:
        selected = list(self.url_listbox.curselection())
        if not selected:
            return
        for index in reversed(selected):
            del self.detected_urls[index]
            self.url_listbox.delete(index)
        self._update_clipboard_status(0)

    def clear_urls(self) -> None:
        self.detected_urls.clear()
        self.url_listbox.delete(0, "end")
        self._update_clipboard_status(0)

    def _update_clipboard_status(self, _added: int) -> None:
        count = len(self.detected_urls)
        if count:
            self.clipboard_var.set(f"{count} link(s) ready")
        else:
            self.clipboard_var.set("No links added")

    def browse_output(self) -> None:
        selected = filedialog.askdirectory(initialdir=self._vars["output_dir"].get() or str(Path.home()))
        if selected:
            self._vars["output_dir"].set(selected)

    def apply_output_subfolder(self) -> None:
        name = self._vars["output_subfolder"].get().strip().strip("\\/")
        if not name:
            return
        if name in {".", ".."} or any(char in name for char in '<>:"/\\|?*') or name.endswith((" ", ".")):
            messagebox.showwarning(core.APP_NAME, "Enter a single valid folder name.")
            return
        base = Path(self._vars["output_dir"].get()).expanduser()
        output_dir = base / name
        output_dir.mkdir(parents=True, exist_ok=True)
        self._vars["output_dir"].set(str(output_dir))
        self._vars["output_subfolder"].set("")
        self.status_var.set(f"Output folder ready: {output_dir}")

    def browse_cookies(self) -> None:
        selected = filedialog.askopenfilename(filetypes=[("Cookies text", "*.txt"), ("All files", "*.*")])
        if selected:
            self._vars["cookies_file"].set(selected)

    def browse_sync(self) -> None:
        selected = filedialog.askdirectory(initialdir=self._vars["sync_dir"].get() or str(Path.home()))
        if selected:
            self._sync_field_updating = True
            self._vars["sync_dir"].set(selected)
            self._sync_field_updating = False
            self.capture_sync_root()
            self._vars["sync_enabled"].set(True)
            self.apply_matched_sync_field()

    def add_job(self) -> None:
        typed_urls = core.extract_urls(self.url_text.get("1.0", "end"))
        if typed_urls:
            self.add_urls(typed_urls)
            self.url_text.delete("1.0", "end")
        urls = list(self.detected_urls)
        if not urls:
            self.scan_clipboard(auto=False)
            urls = list(self.detected_urls)
        if not urls:
            messagebox.showwarning(core.APP_NAME, "Copy or add one or more links first.")
            return
        try:
            self.settings = self._settings_from_ui()
            core.save_settings(self.settings)
            self.queue_url_jobs(urls, self.settings, on_queued=lambda: self.consume_urls(urls))
        except (ValueError, OSError) as exc:
            messagebox.showerror(core.APP_NAME, str(exc))
            return

    def show_command(self) -> None:
        typed_urls = core.extract_urls(self.url_text.get("1.0", "end"))
        urls = list(self.detected_urls) + [url for url in typed_urls if url not in self.detected_urls]
        if not urls:
            try:
                urls = core.extract_urls(self.clipboard_get())
            except tk.TclError:
                urls = []
        settings = self._settings_from_ui()
        try:
            jobs = core.build_jobs(urls or ["<URL>"], settings)
        except (ValueError, OSError) as exc:
            messagebox.showerror(core.APP_NAME, str(exc))
            return
        preview = "\nCommand preview:\n" + "\n".join(subprocess.list2cmdline(job.command) for job in jobs) + "\n"
        if settings.sync_enabled and settings.sync_dir:
            preview += f"Sync target preview:\n{core.resolve_sync_target(settings)}\n"
        self._append_log(preview + "\n")

    def open_output_dir(self) -> None:
        output_dir = Path(self._vars["output_dir"].get()).expanduser()
        output_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(output_dir)
        except AttributeError:
            subprocess.Popen(["xdg-open", str(output_dir)])

    def copy_log(self) -> None:
        text = self.log.get("1.0", "end-1c")
        self.clipboard_clear()
        self.clipboard_append(text)
        self.status_var.set("Log copied to clipboard.")

    def clear_log(self) -> None:
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")
        self.status_var.set("Log cleared.")

    def cancel_current(self) -> None:
        self.stop_event.set()
        self.preparation_cancel.set()
        self.clear_pending()
        self.status_var.set("Stopping downloads; completed files are kept...")

    def _start_worker(self) -> None:
        if self.closing or self.stop_event.is_set() or (self.worker and self.worker.is_alive()) or (self.maintenance_worker and self.maintenance_worker.is_alive()) or self.jobs.empty():
            return
        self.worker = threading.Thread(target=self._worker_loop, daemon=True)
        self.worker.start()

    def _worker_loop(self) -> None:
        try:
            while not self.stop_event.is_set():
                try:
                    job = self.jobs.get_nowait()
                except queue.Empty:
                    break
                self.current_job = job
                self.set_job_status(job, "Downloading")
                try:
                    self.log_queue.put(f"\nStarting {job.provider}: {len(job.urls)} item(s)\n")
                    before = core.snapshot_output_files(job.settings)
                    code = core.run_download_job(job, self.log_queue.put, self.stop_event)
                    if code == 130 or self.stop_event.is_set():
                        self.set_job_status(job, "Cancelled")
                    elif code:
                        core.cleanup_orphan_thumbnails(job.settings, before, self.log_queue.put)
                        if job.completed_files:
                            sync_code = core.finish_download_job(job, self.log_queue.put, self.stop_event)
                            if sync_code == 130:
                                self.set_job_status(job, "Cancelled")
                            else:
                                status = f"Partial ({len(job.failed_urls)} failed)" if job.failed_urls else "Partial (see report)"
                                self.set_job_status(job, status + ("; mirror failed" if sync_code else ""))
                        else:
                            self.set_job_status(job, f"Failed ({code})")
                    else:
                        self.set_job_status(job, "Syncing")
                        sync_code = core.finish_download_job(job, self.log_queue.put, self.stop_event)
                        self.set_job_status(job, "Cancelled" if sync_code == 130 else "Mirror failed" if sync_code else "Completed")
                except Exception as exc:
                    self.log_queue.put(f"Job failed: {exc}\n")
                    self.set_job_status(job, "Failed")
                finally:
                    self.jobs.task_done()
        finally:
            self.current_job = None

    def _append_log(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text)
        self.log.see("end")
        self.log.configure(state="disabled")

    def _poll_log_queue(self) -> None:
        try:
            for _ in range(100):
                self._append_log(self.log_queue.get_nowait())
        except queue.Empty:
            pass
        try:
            for _ in range(100):
                event = self.ui_events.get_nowait()
                if event[0] == "job":
                    if event[1] not in self.job_history or not self.job_table.exists(event[1]):
                        continue
                    self.job_table.set(event[1], "status", event[2])
                    self.status_var.set(f"{self.job_history[event[1]].provider}: {event[2]}")
                elif event[0] == "status":
                    self.status_var.set(event[1])
                elif event[0] == "spotify":
                    importlib.invalidate_caches()
                    self.spotify_status.set("Spotify support installed" if event[1] == 0 else "Setup failed; see activity log")
                    self.install_spotify_button.configure(state="normal")
                elif event[0] == "prepared":
                    _, jobs, error, callback, cancelled, placeholder = event
                    self.preparing = False
                    if cancelled.is_set() or self.closing:
                        error = "Preparation cancelled."
                    if error is None:
                        try:
                            self.enqueue_jobs(jobs)
                        except ValueError as exc:
                            error = str(exc)
                    if placeholder:
                        if error:
                            self.set_job_status(placeholder, "Cancelled" if cancelled.is_set() or self.closing else "Failed")
                        else:
                            self.job_table.delete(placeholder.id)
                            self.job_history.pop(placeholder.id, None)
                    callback(len(jobs) if error is None else 0, error)
                elif event[0] == "preparation" and event[2] is self.preparation_cancel and not event[2].is_set():
                    self.status_var.set(event[1])
        except queue.Empty:
            pass
        self._start_worker()
        self.update_queue_summary()
        self.after(120, self._poll_log_queue)

    def _poll_clipboard(self) -> None:
        if self._vars["watch_clipboard"].get():
            self.scan_clipboard(auto=True)
        self.after(1800, self._poll_clipboard)

    def _refresh_status(self) -> None:
        queued = self.jobs.qsize()
        if self.current_job:
            self.status_var.set(f"{self.current_job.provider}: {self.current_job.status}, {queued} queued")
        self.update_queue_summary()
        self.after(750, self._refresh_status)

    def update_queue_summary(self) -> None:
        queued = self.jobs.qsize()
        active = 1 if self.current_job else 0
        busy = active or queued or self.preparing
        self.stop_button.configure(state="normal" if busy else "disabled")
        self.simple_stop_button.configure(state="normal" if busy else "disabled")
        suffix = " | Discovering" if self.preparing else ""
        self.queue_var.set(f"Queued: {queued} | Active: {active}{suffix}")
