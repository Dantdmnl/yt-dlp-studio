# Development

Use a Git checkout for development. The source release zip contains the runtime and documentation, but deliberately excludes tests, development requirements and build tools.

## Structure

```text
ytdlp_studio/
  backend.py       Settings, command builders, jobs, deduplication, mirroring
  gui.py           Desktop views and shared queue
  cli.py           Argument validation and lazy GUI startup
  browser.py       Paged search and artist release selection
  catalogue.py     Public Spotify metadata and pagination
  inspection.py    Metadata-only preview and stale-response protection
  formats.py       Simple Format/Quality choices
  processes.py     Hidden subprocesses and process-tree cancellation
  download_host.py Isolated provider compatibility and per-track reports
  library.py       Validated reversible legacy-folder migration
  media_tools.py   FFmpeg/ffprobe discovery and verified installation
  version.py       Application name/version
tests/             Unit/GUI tests, fixtures, explicit local integrations
tools/             Source-release builder and explicit library migration
docs/              User guide, CLI, troubleshooting, discovery and contributor notes
```

Root `ytdlp_wrapper.py` and `media_tools.py` are compatibility launchers. CLI and backend imports do not require Tk; GUI imports are lazy. Runtime packages remain in `.venv`, and user settings remain in `%APPDATA%\ytdlp-studio`, outside the repository. Do not move or rename the package independently of its root launcher scripts.

## Setup And Checks

```powershell
.\install.ps1 -Spotify -NoPause
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -B -m coverage run -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -B -m coverage run --append tests/gui_smoke.py --no-screenshots
.\.venv\Scripts\python.exe -B -m coverage run --append tests/integration_local.py
.\.venv\Scripts\python.exe -B -m coverage run --append tests/integration_sponsorblock.py
.\.venv\Scripts\python.exe -B -m coverage run --append tests/integration_spotdl.py
.\.venv\Scripts\python.exe -m coverage report --fail-under=60
.\.venv\Scripts\python.exe -m pip check
git diff --check
```

For window-only screenshots, run `tests/gui_smoke.py` without `--no-screenshots`. Images are written to ignored `build/ui-check`. Tests use fixtures, isolated settings, and temporary media; they never use the real music library or initiate real music downloads. Public-provider availability is deliberately not a unit-test dependency. GUI tests require Tk/display access; Windows is the supported desktop and CI platform.

Ruff and coverage data are kept under ignored `build/ruff-cache` and `build/coverage`, not scattered in the project root. Keep runtime environments in `.venv`, generated releases in `dist`, and temporary downloads outside the repository. Source and test fixtures remain visible to Git; do not add a broad `*.json`, `*.txt` or image ignore that hides legitimate assets.

Repository tests exercise actual Git ignore rules, verify no ignored files are already tracked, check local documentation links and parse PowerShell scripts. Packaging tests inject private sample files into a temporary source tree and prove they cannot enter the release. These checks complement, not replace, reviewing the staged diff for credentials and personal data.

CI runs Python 3.10/3.12 with core and optional Spotify requirements, static checks, unit/GUI regressions, generated-audio integrations, a coverage baseline, and source-release validation. New behavior should receive focused tests, especially cancellation, partial failures, archive semantics, and local/NAS identity. Lint/coverage are development dependencies, not requirements for end users.

## Workflow Decisions

[ClipGrab's documented flow](https://clipgrab.org/faqs/howto-download-youtube-video) inspired link inspection and separate output choices. This is an independent implementation; no ClipGrab code or artwork is copied. Preview requests are debounced and run off the Tk thread; each result carries a generation so an old response cannot overwrite a newer link. Cancel/edit/close terminate outstanding metadata subprocesses, including their child processes. Preview settings are snapshotted rather than read from Tk inside workers.

The visible queue is session-only. Every job snapshots its destination, provider, archive, and mirror settings. UI events for removed terminal jobs are discarded. Maintenance uses nonzero results as failures. yt-dlp continues to the next playlist item on download errors, but errors are not globally suppressed: failed download/conversion jobs retain a nonzero result.

The media-tools installer verifies the archive checksum and both executables before replacing local tools. Source release packaging uses an allowlist, validates required modules, rejects unsafe file paths, and checks the tag against the single version definition. See [release.md](release.md) for publishing steps.

## Legacy Folder Migration

Keep backups, stop downloads and verify the artist/root paths before library maintenance. In a developer checkout, preview the old ID-suffixed folders without changing files:

```powershell
.\.venv\Scripts\python.exe -B tools/migrate_library.py --artist https://open.spotify.com/artist/ARTIST_ID --local D:/Music/ArtistName --mirror \\NAS\Media\Music\ArtistName
```

Replace `ARTIST_ID` with the real 22-character identity. Omit `--mirror` for local-only migration. Add `--apply` only after reviewing the preview. The tool validates artist membership, rejects links/junctions and destination collisions, hashes all files, verifies each existing NAS file against its local counterpart, and never merges or overwrites folders. Missing NAS releases remain missing; this does not retroactively sync audio. It revalidates before renaming, attempts rollback on failure, and saves a private audit under `%APPDATA%\ytdlp-studio\reports`. If rollback reports a problem, inspect that audit and the actual paths before doing anything further. Empty libraries are a no-op.
