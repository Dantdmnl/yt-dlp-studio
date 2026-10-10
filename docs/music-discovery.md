# Music Discovery

## Research Findings

- The previous YouTube lookup requested `ytsearch8`, limiting the UI to eight results. [yt-dlp's search and playlist options](https://github.com/yt-dlp/yt-dlp#usage-and-options) support larger searches and explicit result slicing.
- The installed spotDL 4.5.2 / SpotipyFree facade used a single search page and a capped artist overview. Its search facade did not apply the requested offset or result kind. Installed source inspection showed why increasing the UI's result limit alone would not repair this.
- [spotDL's artist implementation](https://github.com/spotDL/spotify-downloader/blob/master/spotdl/types/artist.py) coalesces some albums by title and applies aggressive song-title deduplication. That is unsuitable when preserving separate release editions is the goal.
- [SpotAPI](https://github.com/Aran404/SpotAPI) supplies paged `query_songs`, `query_artists`, and `get_artist_discography` methods. Its synchronous 1.x interface is used here; 2.x was undergoing an async rewrite when investigated. The optional requirements explicitly constrain the supported major version.
- [Spotify's artist-albums reference](https://developer.spotify.com/documentation/web-api/reference/get-an-artists-albums) distinguishes own releases from `appears_on` guest credits. The utility's discography scope is the artist's own release catalogue, not every recording on which they appear.
- [spotDL's usage documentation](https://spotdl.github.io/spotify-downloader/usage/) remains the reference for audio matching and downloading. Spotify provides the catalogue and metadata; spotDL finds audio on other supported services rather than downloading Spotify's audio stream.

## Implementation

`ytdlp_studio/catalogue.py` runs read-only discovery in a hidden, cancellable subprocess. It returns canonical Spotify links and structured metadata to the GUI. Public catalogue lookup does not require the utility to collect Spotify login credentials. The service interface can change; failures are surfaced instead of presenting an incomplete response as success.

Artist discovery advances by the number of release groups actually returned, validates each response, checks completion against the reported count, and rejects repeated, overlapping, or prematurely empty pages. A changed catalogue count during paging requires a retry instead of claiming a complete snapshot. Release IDs, not names, identify editions. One available edition per group is the default; all editions are optional. EPs retain their type and share the Singles/EPs filter.

Search offers explicit page sizes and offsets. YouTube requests a larger search prefix and slices the next range; its relevance ordering can move between requests. The browser suppresses duplicate URLs, counts the results actually loaded, and disables further loading if a page adds no new items. Failed requests preserve the previous results and pagination context.

The GUI queues explicit album links rather than delegating the whole artist to spotDL's name-based expansion. Preparation runs off the Tk thread and closes the browser only after successful queue insertion. Directly pasted artist links follow the same model: a temporary Discovering row is replaced with individual release rows before downloading, and pending/active counts stay visible with Details hidden. Each album has its own status, retry, archive scope, and local/mirror settings snapshot. CLI artist-link downloads retain execution-time expansion. Per-release folders use the year and sanitized title, adding edition suffixes for collisions. Release IDs live in `.studio-release.json`; legacy ID-suffixed folders remain recognized. The NAS destination preserves the artist/release hierarchy. Flat downloads remain available, with the existing shared archive and song-scanning semantics.

The isolated `download_host.py` sets hidden child-process defaults, including asyncio converters, and supplies the English YouTube Music compatibility client while retaining native matching. It emits validated per-track results and underlying extraction errors. Parent-side recovery retains failed track links and mirrors only readable audio on partial success. Windows Job Objects retain child ownership even if an intermediate downloader exits; Stop cancels the whole queued batch.

The same hidden-helper policy is installed before discovery dependencies import and when the GUI starts. Installed SpotAPI's logger calls `os.system("")` at import, which bypasses `Popen` and otherwise creates a console under `pythonw`. Empty shell calls are a no-op; meaningful Windows shell calls run through hidden `Popen`, retaining their exit status. Native regressions exercise this actual dependency import under both Python launchers without contacting Spotify.

## Verification

Unit tests cover more than two catalogue pages, edition identity, available-edition selection, truncated and repeated pages, track and artist parsing, request cancellation, timeout cleanup, Unicode, CLI expansion, and matching NAS folders. The isolated GUI smoke check exercises pagination, error recovery, release filters, and queuing without downloading music or touching the user's library.

Live metadata checks during implementation reached 81 release groups and 120 distinct editions in a large artist catalogue. Spotify track search and YouTube search each returned 25 items per page. Native spotDL resolved all 30 tracks of a representative album, including those beyond the first 25; discovery was also checked under the silent `pythonw` launcher. These were metadata-only checks, not full discography audio downloads. The counts are observations, not fixed test fixtures or guarantees about future catalogue sizes.
