# yt-dlp Studio

An advanced local wrapper around `yt-dlp` for downloading music, videos, playlists, subtitles, and metadata.

Current app version: `0.1.0`

## Features

- Polished Tkinter/ttkbootstrap GUI with automatic clipboard link detection, tabbed settings, and live `yt-dlp` activity output
- Audio presets for MP3, M4A, Opus, and FLAC
- Video presets for best, 1080p, 720p, and small compatible MP4
- Metadata, thumbnail embedding, subtitle downloads, cookies file support, SponsorBlock removal, and download archive support
- Optional sync target for mirroring completed downloads to a second location, such as a NAS share, with automatic matching output folders
- Deduplicator that scans the output library and moves duplicate candidates to `_duplicates_review`
- CLI mode using the same command builder as the GUI
- Persistent settings stored in `%APPDATA%\ytdlp-studio\settings.json`

## Requirements

- Python 3.10+
- `yt-dlp`
- FFmpeg for conversion, thumbnail embedding, and audio extraction
- Deno or Node.js for reliable YouTube JavaScript challenge handling

Install the Python packages:

```powershell
.\install.ps1
```

Install packages and create Desktop/Start Menu shortcuts:

```powershell
.\install.ps1 -Shortcuts
```

The helper creates `.venv` in this project and installs dependencies there, including yt-dlp's default extras and a bundled FFmpeg provider. Manual equivalent:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

The app first uses FFmpeg from PATH when available. If PATH has no FFmpeg, it automatically uses the FFmpeg binary provided by `imageio-ffmpeg` inside `.venv`.

For current YouTube downloads, yt-dlp also needs a JavaScript runtime. Deno is recommended by yt-dlp and is used automatically when it is on PATH. Node.js also works and the app will pass it through with `--js-runtimes` when detected.

## Run The GUI

Use the installed Start Menu/Desktop shortcut, or launch manually:

```powershell
.\run-gui.ps1
```

`run-gui.ps1` starts the app with `pythonw.exe`, so no terminal window stays open. For a debug/foreground launch:

```powershell
.\run-gui.ps1 -Foreground
```

You can also double-click the silent launcher:

```text
yt-dlp-studio.vbs
```

Or directly:

```powershell
.\.venv\Scripts\python.exe .\ytdlp_wrapper.py
```

Copy one or more YouTube, playlist, or music links. The app scans the clipboard and adds detected links to the list automatically. Choose audio or video settings, then click `Start Download`.

You can still paste messy batches into the small manual box and click `Add Typed Links`.

Use `Artist/project folder` to quickly create/select a folder under the current output path. For example, enter `Maphra` while output is `D:\Music` to switch output to `D:\Music\Maphra`.

## Release Package

This project is designed to ship as a small source zip with the installer and launcher scripts included. A user can download the release zip, extract it, run `install.ps1 -Shortcuts`, then start the GUI from the Start Menu, Desktop shortcut, `run-gui.ps1`, or `yt-dlp-studio.vbs`.

Check the app version:

```powershell
.\.venv\Scripts\python.exe .\ytdlp_wrapper.py --version
```

To publish a GitHub release, update `APP_VERSION` and `CHANGELOG.md`, commit, tag the commit with a `v*` tag such as `v0.1.0`, then push the tag. The workflow in `.github/workflows/release.yml` builds and uploads the zip asset.

## CLI Examples

Download an MP3:

```powershell
.\.venv\Scripts\python.exe .\ytdlp_wrapper.py --audio --audio-preset "MP3 320 kbps" "https://example.com/watch?v=..."
```

Download a 1080p MP4:

```powershell
.\.venv\Scripts\python.exe .\ytdlp_wrapper.py --video --video-preset "1080p MP4" "https://example.com/watch?v=..."
```

Download locally, then sync completed files to a NAS/share:

```powershell
.\.venv\Scripts\python.exe .\ytdlp_wrapper.py --audio --sync-dir "\\NAS\Media\Music" "https://example.com/watch?v=..."
```

By default, sync treats `--sync-dir` as a library root and creates a matching output folder. If local output is `D:\Music\Maphra`, the example above syncs to `\\NAS\Media\Music\Maphra`.

Copy directly into the sync folder instead:

```powershell
.\.venv\Scripts\python.exe .\ytdlp_wrapper.py --audio --sync-dir "\\NAS\Media\Music\Maphra" --sync-flat "https://example.com/watch?v=..."
```

Preview the generated command:

```powershell
.\.venv\Scripts\python.exe .\ytdlp_wrapper.py --audio --print-command "https://example.com/watch?v=..."
```

Download without creating or using `download-archive.txt`:

```powershell
.\.venv\Scripts\python.exe .\ytdlp_wrapper.py --audio --no-archive "https://example.com/watch?v=..."
```

Scan the current output folder for duplicate media candidates:

```powershell
.\.venv\Scripts\python.exe .\ytdlp_wrapper.py --dedupe
```

Move duplicate candidates into `_duplicates_review`:

```powershell
.\.venv\Scripts\python.exe .\ytdlp_wrapper.py --dedupe-apply
```

Move duplicate candidates locally and in the matched sync folder:

```powershell
.\.venv\Scripts\python.exe .\ytdlp_wrapper.py --dedupe-apply --dedupe-sync
```

Only consider byte-identical files:

```powershell
.\.venv\Scripts\python.exe .\ytdlp_wrapper.py --dedupe --dedupe-exact
```

Run dedupe automatically after a successful download:

```powershell
.\.venv\Scripts\python.exe .\ytdlp_wrapper.py --audio --dedupe-after-download "https://example.com/watch?v=..."
```

## Notes

The app writes a `download-archive.txt` file in the output directory by default, so repeated runs skip URLs that already succeeded. Disable `Download archive file` in the GUI, or pass `--no-archive` in CLI mode, when you want no archive file involved. The archive file is treated as wrapper bookkeeping and is not mirrored to the sync target.

Sync is optional. When enabled, downloads finish in the local output folder first, then completed files are copied to the sync target. Temporary files such as `.part` downloads are skipped.

When `Match output folder` is enabled, the sync target field follows the local output folder while preserving the path style you typed or selected. If the sync target points at an old sibling folder under the same library parent, such as `G:\Multimedia\Music\Maphra` while local output is `D:\Music\Halocene`, the app rewrites the sync field to `G:\Multimedia\Music\Halocene`.

If a download fails after yt-dlp already fetched a thumbnail, the wrapper removes new orphan `.jpg`, `.png`, and `.webp` thumbnail files that do not have a matching media file.

The download archive prevents repeated downloads of the same archived URL. It does not guarantee that a playlist cannot contain the same song from another URL, a reupload, or a file already present from another tool such as SpotDL.

The deduplicator scans all supported media files in the output folder, not only files downloaded by this wrapper. It uses audio metadata when available, then falls back to cleaned title candidates from filenames so mixed libraries can match entries such as a clean SpotDL title beside a longer YouTube title. Apply mode never deletes files directly; it moves duplicate candidates into `_duplicates_review` inside the output folder so you can inspect them before deleting anything. When sync is enabled and dedupe is applied with sync support, the matching file in the sync destination is moved to its own `_duplicates_review` folder as well.
