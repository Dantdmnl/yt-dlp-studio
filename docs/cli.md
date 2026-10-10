# Command Line

Run commands from the app folder after setup. The CLI shares saved GUI settings; flags override them for this invocation without saving those overrides. Explicitly select output/mirroring for scripts. Quote links, paths and presets.

```powershell
.\.venv\Scripts\python.exe -B -m ytdlp_studio --help
.\.venv\Scripts\python.exe -B -m ytdlp_studio --version
```

The legacy `ytdlp_wrapper.py` entry point remains supported. Omit all download/maintenance arguments to open the GUI.

## Downloads

Replace VIDEO_URL, PLAYLIST_ID and ARTIST_ID with actual supported links/IDs:

```powershell
.\.venv\Scripts\python.exe -m ytdlp_studio --audio --audio-preset "MP3 320 kbps" -o "D:\Music" --no-sync "VIDEO_URL"
.\.venv\Scripts\python.exe -m ytdlp_studio --video --video-preset "1080p MP4" -o "D:\Videos" --no-sync "VIDEO_URL"
.\.venv\Scripts\python.exe -m ytdlp_studio --audio -o "D:\Music\ArtistName" --sync-dir "\\NAS\Media\Music" "VIDEO_URL"
.\.venv\Scripts\python.exe -m ytdlp_studio --audio --no-archive --no-sponsorblock --no-sync "VIDEO_URL"
.\.venv\Scripts\python.exe -m ytdlp_studio --audio --print-command "VIDEO_URL"
.\.venv\Scripts\python.exe -m ytdlp_studio "https://open.spotify.com/playlist/PLAYLIST_ID" -o "D:\Music\Playlist" --no-sync
.\.venv\Scripts\python.exe -m ytdlp_studio --provider spotDL "Artist - Song" -o "D:\Music" --no-sync
```

Folder matching is on by default: `--sync-dir "\\NAS\Media\Music"` with local `D:\Music\ArtistName` mirrors into `\\NAS\Media\Music\ArtistName`. Use `--sync-flat` to copy directly into the given directory. `--no-sync` overrides saved mirroring. `--archive` / `--no-archive` control both providers' separate archives; `--sponsorblock` / `--no-sponsorblock` control segment cutting.

`--print-command` does not download audio/video, but may make metadata requests for artist expansion. Commands can contain private cookie paths or extra arguments; redact them before sharing.

## Artist Releases

Spotify support is required. Prefer the GUI release browser when reviewing editions before downloading:

```powershell
.\.venv\Scripts\python.exe -m ytdlp_studio --discography "https://open.spotify.com/artist/ARTIST_ID" -o "D:\Music\ArtistName" --sync-dir "\\NAS\Media\Music"
.\.venv\Scripts\python.exe -m ytdlp_studio --discography "https://open.spotify.com/artist/ARTIST_ID" --all-editions --print-command
.\.venv\Scripts\python.exe -m ytdlp_studio --discography "https://open.spotify.com/artist/ARTIST_ID" --flat-discography --no-archive --no-sync
```

Default output uses per-release folders; `--all-editions` includes alternate editions and `--flat-discography` selects the GUI's One folder layout. Flat artist jobs share their output directory, mirror target and optional archive, while retaining separate release status. Existing files are skipped, not overwritten; older downloads are not moved. See the [user guide](user-guide.md) for filename collisions, catalogue scope and intentionally repeated recordings.

## Library Maintenance

Scan before applying duplicate moves:

```powershell
.\.venv\Scripts\python.exe -m ytdlp_studio --dedupe --dedupe-exact -o "D:\Music" --no-sync
.\.venv\Scripts\python.exe -m ytdlp_studio --dedupe-apply --dedupe-exact -o "D:\Music\ArtistName" --sync-dir "\\NAS\Media\Music" --dedupe-sync
.\.venv\Scripts\python.exe -m ytdlp_studio --sync-only -o "D:\Music\ArtistName" --sync-dir "\\NAS\Media\Music"
```

Apply moves candidates into `_duplicates_review`, not permanent deletion. Omitting `--dedupe-exact` uses the saved matching preference and may allow title/metadata candidates; inspect these carefully. Mirror copies are moved only after content verification. `--dedupe-after-download` enables byte-identical cleanup after successful downloads.

Exit status is nonzero for reported failures. Playlist processing may continue after a failed item, but the job is not reported as successful. See [troubleshooting](troubleshooting.md) for provider and dependency failures.
