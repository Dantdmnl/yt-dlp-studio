# Changelog

## 0.2.1 - 2026-10-10

- Expose shared Album folders / One folder choices in Simple, Advanced and the artist browser, preserving saved preferences and queued-job snapshots.
- Keep flat artist filenames inside the chosen local/NAS roots even when custom templates contain subfolders; retain separate release statuses and optional shared archives.
- Expand layout, native filename, mirror idempotence, persistence, CLI and retry tests.
- Resolve Windows short-path aliases before mirroring, preserve readable filenames and reject files outside the chosen library.
- Expand directly pasted artist links into visible release jobs before downloading, with cancellable discovery and separate active/pending counts in both views.
- Prevent Spotify discovery's import-time native shell popup and hide dependency shell calls in GUI/background hosts.
- Keep releases private and marked prerelease pending full artist-download acceptance; the old 0.2.0 ZIP does not contain these fixes.
- Prepare discography queues off the UI thread and close the browser after successful queueing.
- Hide nested synchronous/async FFmpeg and runtime children; retain Windows process-tree ownership after parent exit.
- Stop active and pending downloads together without automatic restart; distinguish pending-only cancellation.
- Use English YouTube Music lookup as a scoped compatibility fix while retaining native spotDL matching.
- Surface original extraction errors, persist recovery reports, retry failed tracks and mirror validated audio from partial albums.
- Use readable release folders with internal edition identity; add hash-validated, reversible local/NAS migration tooling.
- Add responsive GUI, child-process, partial-download, naming and migration safety regressions.

## 0.2.0 - 2026-10-10

- Added double-click setup with optional Spotify selection, shortcuts, clearer repair guidance and JavaScript runtime feedback.
- Simplified the quick start and separated the user guide, CLI examples and troubleshooting documentation.
- Expanded private-data/download/cache ignore rules, moved development artifacts under build, and added repository hygiene checks.
- Added private-file leak tests, SHA-256 release checksums and test-gated release packaging.
- Added a ClipGrab-inspired paste/preview/download flow, separate format/quality selectors, metadata-only previews, and artist review.
- Added debounced, cancellable previews with stale-response protection and optional automatic inspection.
- Added queue folder access and terminal-entry cleanup without deleting downloads.
- Organized runtime code into the `ytdlp_studio` package, retained existing launchers and CLI commands, and centralized the version.
- Fixed masked yt-dlp post-processing failures, unbounded video-quality fallbacks, misleading maintenance success, and delayed queue events after removal.
- Hardened Spotify ID/count validation, process-tree cancellation, installer deletion boundaries, and silent-launch feedback.
- Added hermetic GUI, preview, CLI, media-tools, and runnable-release tests; added lint/coverage and a Python 3.10/3.12 CI matrix.
- Added an allowlisted source-release builder with version/tag matching and updated user/contributor/release docs.

## 0.1.0 - Initial Development

- Added paged Spotify track/artist and YouTube search with 25/50/100-result pages, Load More, cancellation, and unique result handling.
- Added complete paged artist release discovery, release-type filters including EPs, optional alternate editions, and `--discography` CLI support.
- Added separate retryable album jobs, collision-resistant album folders, and matching NAS album hierarchy; preserved archive and flat-folder options.
- Added discovery validation and regression tests for capped/repeated pages, same-title editions, cancellation, Unicode, GUI pagination, and album mirrors.

- Added a default Simple download view with shared format, destination, SponsorBlock, and NAS controls; retained Advanced mode and optional log details.

- Enabled SponsorBlock removal for new settings, with a shared Cleanup control for sponsors, outros, and non-music sections.
- Added `--no-sponsorblock`, complete FFmpeg/ffprobe installation with checksum verification, and actual segment-cutting checks.
- Passed the media-tools path to spotDL's native SponsorBlock processors as well as its converter.

- Added optional spotDL installation, automatic Spotify routing, and separate Spotify settings.
- Added YouTube/Spotify music search, a visible session queue, retry, and pending-job cancellation.
- Added manual mirroring and validation of local/NAS folder combinations.
- Protected mirror files with atomic replacement and excluded archives and duplicate review files.
- Fixed silent-process cancellation and added job failure recovery and regression checks.
- Limited automatic duplicate cleanup to byte-identical recordings.

- Added the Tkinter GUI for yt-dlp downloads with clipboard link detection.
- Added ttkbootstrap-powered visual styling, a professional app header, tabbed settings, and a cleaner activity panel.
- Added audio/video presets, metadata and thumbnail embedding, cookies support, SponsorBlock, and archive toggling.
- Added optional sync to a second folder, including matched artist/project folders for NAS-style libraries.
- Added duplicate review tooling for mixed yt-dlp and other music downloader libraries.
- Added hidden-window launch scripts, foreground debug launch, and optional Desktop/Start Menu shortcuts.
- Improved installer and launcher feedback, including first-run setup prompts and clearer repair guidance.
- Added installer pause behavior for manual runs, with `-NoPause` for launchers and automation.
- Added a GitHub release workflow that packages the app into a zip when a `v*` tag is pushed.
