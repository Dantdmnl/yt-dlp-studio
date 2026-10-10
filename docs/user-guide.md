# User Guide

## Everyday Downloads

The app starts in Simple mode. Paste a supported link and optionally inspect its title, source, duration or collection size before downloading. Previews fetch metadata only. Editing a link cancels the previous preview; preview errors do not prevent a download attempt. Disable automatic preview or clipboard capture in Advanced if you prefer manual input.

Choose Audio/Video, Format, Quality and a local Save to folder, then click Download. For batches, use Advanced's link staging. Leave the provider on Auto to route Spotify links to spotDL and other supported links to yt-dlp.

Each queued job remembers the settings used when it was added. Retry Selected retries reported failed Spotify tracks with the original destination and options; if no track report exists it retries the source job. The visible counter separates pending jobs from the active job, even with Details hidden. Stop Downloads cancels discovery/preparation, the active job and queued jobs; it does not automatically restart them. Cancel Queued also cancels pending preparation but leaves the active download running. Open Folder opens its destination; Remove Finished clears terminal queue entries without deleting files. The queue is session-only and is not restored after closing the app. Keep source links if you will need them later.

## Formats And Quality

Audio output supports MP3, M4A, Opus and FLAC; video includes bounded resolution presets. Video quality is an upper limit, not an upscaling request. FLAC cannot restore quality lost in a lossy source, and a higher selected MP3 bitrate cannot improve the source recording.

Spotify uses spotDL's own metadata/artwork, source bitrate and filename settings. Its options are separate from yt-dlp templates, subtitles and source-file retention. Matching audio can be a different recording; review unfamiliar matches.

## Search And Artist Releases

Open Find Music / Artist, select YouTube or Spotify, and search. Choose 25, 50 or 100 results per request. Load More adds another page while suppressing repeated URLs. Upstream search rankings and availability can change.

For Spotify, choose Artists, select the intended artist and open Browse Discography. You can also enter a full artist link directly. Discovery retrieves the complete paged catalogue before displaying it; incomplete or repeated pages produce an error instead of silently queuing a partial collection.

Filter albums, singles/EPs or compilations, then download selected releases or the entire visible collection. One available edition per release group is selected by default. All editions includes alternate editions, which may intentionally repeat recordings. Track-entry counts are not counts of unique songs.

The scope is the artist's own releases available from Spotify, not guest appearances on other artists' releases or material absent from the catalogue. Regional availability may affect results. Spotify lookup requires the optional installation; the utility does not ask for Spotify login credentials.

Choose **Artist layout** beneath Save to in Simple mode, under Library in Advanced, or in the artist browser. The choice is shared across these views and saved with your preferences.

**Album folders** creates readable `YEAR - Album Title` folders, with disc/track-number filenames. Same-title editions get an `(edition 2)` suffix when needed. A small `.studio-release.json` identity file keeps editions distinct without putting Spotify IDs in folder names; keep it with the release, independently of the optional download archive. Existing ID-suffixed folders are recognized and reused until explicitly migrated.

**One folder** saves songs directly in Save to, usually as `Artist - Song.mp3`, and mirrors directly into the matching artist directory without album subfolders. Custom artist templates use only their filename component; templates without a song-title placeholder fall back to the default. Each release remains a separate retryable queue job, but shares the folder and optional archive. Existing filenames are skipped rather than overwritten. Same-named tracks from different editions can therefore collapse onto an existing file; use Album folders to preserve every edition separately.

Changing layout affects new jobs and does not move existing downloads or NAS files. Already queued jobs retain their original layout. With One folder, existing tagged songs can still be recognized in older subfolders and skipped; use a fresh destination when you need a completely flat new library. Pasting an artist URL into the normal download field also expands the catalogue; browsing first gives you more control.

A directly pasted artist link initially shows a Discovering row. The desktop app prepares its complete catalogue in the background, then replaces that row with one visible job per release before any of those releases download. Failed discovery retains the source link and a retryable failed row; stopping discovery adds no hidden download batch. New links entered while preparation is running are preserved.

Download Discography prepares jobs in the background and closes the browser after they reach the queue. Cancel or close the browser during preparation to prevent the batch being added. Partial Spotify jobs keep and mirror validated completed audio, retain a failed status, and save a recovery report under `%APPDATA%\ytdlp-studio\reports`. Cancellation keeps local files but does not start a new mirror operation; use Sync Now afterward if needed.

## Optional NAS Mirroring

Choose a local library, enable Mirror to NAS and select the second library root. With folder matching enabled, local `D:\Music\ArtistName` and mirror root `\\NAS\Media\Music` resolve to `\\NAS\Media\Music\ArtistName`. The displayed mirror field follows the local artist/project folder and retains the path style you selected. Artist layout applies on both sides: Album folders keeps matching release subfolders; One folder uses the artist directory directly.

Disable folder matching to copy directly into the selected mirror directory. Local and mirror paths must be separate. Missing destination folders are created when needed; the share must be accessible with your Windows account.

Downloads finish locally first. Mirroring replaces files atomically and skips partial transfers, download archives and `_duplicates_review`. A failed mirror does not remove the local download. Retry or use Sync Now once the share is available. Mirroring copies files; it is not a general deletion-based two-way synchronizer and does not erase unrelated NAS files.

## Archives And Duplicate Safety

Download archive file is optional. yt-dlp uses `download-archive.txt`; spotDL uses `spotdl-archive.txt`. With archives enabled, successful source entries are skipped on subsequent runs. Turning archives off stops using or creating them, but does not delete an existing archive. Archives do not recognize every reupload or file from another downloader.

Spotify also scans existing tagged songs and skips matches. With album folders, scanning and archives are scoped to each release, preserving intentionally repeated songs across albums.

The deduplicator scans supported media regardless of which downloader created it. Manual title/metadata scans suggest possible matches; they cannot prove that two recordings are the same. Start with a scan and inspect candidates before applying. Apply moves candidates to `_duplicates_review` rather than permanently deleting them. Keep backups before library maintenance.

Automatic post-download cleanup uses byte-identical files only. When mirror duplicate handling is enabled, a same-path NAS copy is moved only after its bytes are verified against the local duplicate. A different NAS recording is left untouched. No method guarantees that an entire playlist contains no musically equivalent recordings.

## SponsorBlock And Advanced Controls

SponsorBlock is enabled for new settings. Remove sponsors, outros and non-music applies to both download providers and cuts tagged sponsors, self-promotion, intros, outros, previews, filler and interaction reminders before mirroring.

This relies on community segments for the matched YouTube recording, not automatic ad detection. Untagged sections and unsupported sources remain intact. Changing this option does not rewrite existing downloads or archive entries. Cutting requires both FFmpeg and ffprobe, installed during setup.

Advanced also exposes cookies, templates, subtitles, extra arguments, archive toggles, clipboard/preview preferences and command preview. Cookie exports are credentials: keep them private and outside the source tree. Extra arguments can change backend behavior; inspect the command before using unfamiliar options.

See [troubleshooting](troubleshooting.md), [CLI examples](cli.md) or return to the [README](../README.md).
