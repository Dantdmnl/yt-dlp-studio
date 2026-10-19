# Troubleshooting And Updates

## Setup Or Launch Fails

Extract the whole zip, including `ytdlp_studio`, into a writable, permanent folder. Double-click `Setup.cmd` and read the visible setup result. You need Windows 10+ (64-bit), Python 3.10+, internet access and access to package/media-tool download hosts. Setup uses a local `.venv`, not your global Python packages.

The application is a general-purpose client and does not grant rights to
downloaded media. Use it only for material you are authorized to download,
convert or mirror. You are responsible for following local law, source
licenses and platform terms, including the terms of YouTube and Spotify.

If Windows blocks downloaded scripts, inspect the files and allow them only if you trust their source. On managed computers, follow your administrator's policy rather than bypassing organizational controls. `Setup.cmd` supplies an execution policy for its own PowerShell process; it does not change the machine's policy.

Setup installs yt-dlp without its optional Brotli package. Some new Python versions
do not yet have a Windows Brotli wheel, which would make pip require Microsoft C++
Build Tools on an otherwise clean system. yt-dlp continues to work without Brotli;
the package is only an optional response-compression helper.

Before installing packages, setup checks that PyPI can be reached over verified
HTTPS. The check uses a Windows-compatible single-line Python probe. If setup
reports that a certificate is not valid yet or is no longer valid,
check Windows **Settings > Time & language > Date & time**, enable **Set time
automatically**, confirm the time zone, and run `w32tm /resync` from an elevated
Command Prompt. Do not work around this by disabling certificate verification.

If script association prevents the silent VBS launcher opening, launch from PowerShell in the app folder:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\run-gui.ps1
```

For a visible diagnostic launch:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\run-gui.ps1 -Foreground
```

Startup error details are saved to `%APPDATA%\ytdlp-studio\startup-error.log`. Review logs for private paths, links and tokens before sharing them.

## YouTube Or Conversion Fails

Setup installs a complete FFmpeg/ffprobe pair, downloading and verifying the Windows build when needed. Tools live under `.venv\tools\ffmpeg`; a complete pair from PATH is also supported. An FFmpeg-only fallback may convert audio but cannot perform SponsorBlock cutting.

Run Setup.cmd again to repair dependencies or tools. At the end of an interactive setup, you can now choose to install Deno through WinGet (`DenoLand.Deno`). This is an explicit opt-in and may require a new terminal or app restart before the updated PATH is visible. The app also checks Deno's standard per-user installation path (`%USERPROFILE%\.deno\bin\deno.exe`). If WinGet is unavailable, install [Deno](https://docs.deno.com/runtime/getting_started/installation/) or [Node.js](https://nodejs.org/en/download) 22+, make it available on PATH and restart the app. The utility detects supported runtimes; it does not silently install system software.

Use Details to inspect failed jobs. Service availability, removed media, access restrictions and backend changes can all cause errors. Previews and downloads are separate requests; a successful preview does not guarantee a successful download.

## Spotify Search Or Artist Downloads Fail

Run Setup.cmd again and answer Yes to Spotify support, or enable installation from Advanced settings. Core yt-dlp downloads work without this optional component.

Use a full `https://open.spotify.com/artist/...` link for artist preview/review. Shortened links can be submitted to the backend but are not supported by every preview feature. Failed/incomplete catalogue discovery should be retried, not treated as a complete discography. Upstream interfaces can change; do not share login cookies or credentials to work around them.

The isolated Spotify downloader uses English YouTube Music searches to work around empty filtered results seen with spotDL's default German client. It retains native spotDL recording selection rather than accepting arbitrary first search results. See the related [upstream failure report](https://github.com/spotDL/spotify-downloader/issues/2779). A successful match still does not guarantee audio extraction or availability.

Open Details for the underlying yt-dlp warning/error, not just the generic spotDL message. Failure reports persist under `%APPDATA%\ytdlp-studio\reports`; Retry Selected uses known failed track links. Completed, valid audio from partial albums is mirrored without falsely marking the album successful. Reports contain private paths and links; redact them before sharing. Stop Downloads cancels the current process tree and queued jobs; Cancel Queued only clears pending jobs.

Launchers, nested FFmpeg/runtime children and Spotify discovery's dependency shell calls are hidden during normal use. Close and reopen an already running app after updating source files so it uses the new helper policy. Foreground launching remains available for diagnostics. Direct artist downloads should show a Discovering row followed by individual release rows, with separate pending and active counts, not one long-running artist row with zero queued jobs.

## Mirror Is Unavailable

Open the destination in File Explorer and verify your Windows account can create files there. Check the selected root and folder-matching option. Completed local downloads remain available if the NAS is offline. Retry mirroring once the share is reachable.

## Update Or Move The App

Close the app, back up anything important, extract the new release over the existing app folder and run Setup.cmd again. Select Spotify again if you use it. Personal settings stay in `%APPDATA%\ytdlp-studio`; setup does not remove your music or NAS files.

Virtual environments are not portable. After relocating the app, recreate only its environment:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 -Recreate -Spotify -Shortcuts
```

Omit `-Spotify` if you do not need it. `-Recreate` removes the app's `.venv` and rebuilds dependencies; do not use it as a routine update step. If Python was removed or moved, install a supported Python first. Existing shortcuts must point at the new folder.

## Report A Bug

Include the app version, Windows/Python versions, which provider was used, steps to reproduce and the relevant redacted Details/startup error. State whether mirroring, archives and SponsorBlock were enabled. Never attach cookie exports, credentials, personal settings or your music library.
