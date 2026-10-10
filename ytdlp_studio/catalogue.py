"""Paged music discovery in a separate process, without downloading audio."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

from .processes import terminate_tree, window_kwargs


def spotify_id(value: str, kind: str) -> str:
    if value.startswith("spotify:"):
        match = re.fullmatch(rf"spotify:{re.escape(kind)}:([A-Za-z0-9]{{22}})", value)
        if not match:
            raise ValueError(f"Invalid Spotify {kind} ID.")
        identity = match.group(1)
    else:
        parsed = urlparse(value)
        parts = parsed.path.strip("/").split("/")
        if parts and parts[0].startswith("intl-"):
            parts = parts[1:]
        if parsed.scheme not in {"http", "https"} or parsed.hostname != "open.spotify.com" or len(parts) != 2 or parts[0] != kind:
            raise ValueError(f"Enter a Spotify {kind} link.")
        identity = parts[1]
    if not re.fullmatch(r"[A-Za-z0-9]{22}", identity):
        raise ValueError(f"Invalid Spotify {kind} ID.")
    return identity


def unpack_item(item: dict) -> dict:
    return item.get("item", {}).get("data") or item.get("data") or item


def inspect_spotify(url: str, client=None) -> dict:
    kind = None
    for candidate in ("track", "album", "playlist", "artist"):
        try:
            identity = spotify_id(url, candidate)
        except ValueError:
            continue
        kind = candidate
        break
    if kind is None:
        raise ValueError("Enter a full Spotify track, album, playlist, or artist link.")
    if client is None:
        from spotapi import Artist, PublicAlbum, PublicPlaylist, Song
        client = {"track": lambda: Song(), "album": lambda: PublicAlbum(identity),
                  "playlist": lambda: PublicPlaylist(identity), "artist": lambda: Artist()}[kind]()
    if kind == "track":
        data = client.get_track_info(identity).get("data", {}).get("trackUnion", {})
    elif kind == "artist":
        data = client.get_artist(identity).get("data", {}).get("artistUnion", {})
    elif kind == "album":
        data = client.get_album_info(limit=1).get("data", {}).get("albumUnion", {})
    else:
        data = client.get_playlist_info(limit=1).get("data", {}).get("playlistV2", {})
    title = data.get("name") or data.get("profile", {}).get("name")
    if not isinstance(title, str) or not title:
        raise ValueError("Spotify did not return a title for this link.")
    artists = (data.get("artists", {}).get("items", []) or
               data.get("firstArtist", {}).get("items", []) + data.get("otherArtists", {}).get("items", []))
    creator = ", ".join(filter(None, (unpack_item(a).get("profile", {}).get("name", "") for a in artists)))
    count = data.get("tracksV2", {}).get("totalCount") if kind == "album" else data.get("content", {}).get("totalCount")
    duration = data.get("duration", {}).get("totalMilliseconds")
    return {"url": f"https://open.spotify.com/{kind}/{identity}", "source": "Spotify", "kind": kind,
            "title": title, "creator": creator, "duration": duration / 1000 if duration else None,
            "count": count, "heights": []}


def search_spotify(query: str, kind: str, offset: int, limit: int, client=None) -> dict:
    if client is None:
        from spotapi import Artist, Song
        client = Artist() if kind == "artists" else Song()
    if kind == "artists":
        response = client.query_artists(query, limit=limit, offset=offset)
        key, entity = "artists", "artist"
    else:
        response = client.query_songs(query, limit=limit, offset=offset)
        key, entity = "tracksV2", "track"
    page = response.get("data", {}).get("searchV2", {}).get(key)
    if not isinstance(page, dict) or not isinstance(page.get("items"), list):
        raise ValueError("Spotify returned an unexpected search response. Update Spotify support and retry.")
    items = []
    for raw in page["items"]:
        data = unpack_item(raw)
        uri = data.get("uri", "")
        identity = uri.split(":")[-1]
        if not uri.startswith(f"spotify:{entity}:") or not re.fullmatch(r"[A-Za-z0-9]{22}", identity):
            continue
        artists = [unpack_item(artist).get("profile", {}).get("name", "")
                   for artist in data.get("artists", {}).get("items", [])]
        seconds = int(data.get("duration", {}).get("totalMilliseconds", 0) / 1000)
        items.append({"url": f"https://open.spotify.com/{entity}/{identity}", "kind": entity,
                      "title": data.get("name") or data.get("profile", {}).get("name") or identity,
                      "artist": ", ".join(filter(None, artists)),
                      "info": f"{seconds // 60}:{seconds % 60:02d}" if seconds else "Artist"})
    next_offset = page.get("pagingInfo", {}).get("nextOffset")
    if next_offset is None:
        next_offset = offset + len(page["items"])
    total = int(page.get("totalCount", next_offset))
    more = bool(page["items"]) and next_offset > offset and next_offset < total
    return {"items": items, "offset": next_offset, "more": more, "total": total}


def artist_discography(url: str, all_editions: bool = False, client=None) -> dict:
    identity = spotify_id(url, "artist")
    if client is None:
        from spotapi import Artist
        client = Artist()
    overview = client.get_artist(identity).get("data", {}).get("artistUnion", {})
    name = overview.get("profile", {}).get("name")
    if not name:
        raise ValueError("Spotify did not return the artist's name.")
    releases, seen, page_signatures, seen_groups = [], set(), set(), set()
    offset, total = 0, None
    while total is None or offset < total:
        response = client.get_artist_discography(identity, section="all", offset=offset, limit=50)
        page = response.get("data", {}).get("artistUnion", {}).get("discography", {}).get("all")
        if not isinstance(page, dict) or not isinstance(page.get("items"), list) or "totalCount" not in page:
            raise ValueError("Spotify returned an incomplete discography response. No partial collection was queued.")
        reported_total = int(page["totalCount"])
        if total is not None and reported_total != total:
            raise ValueError("Spotify's catalogue changed while paging. Retry discovery.")
        total = reported_total
        groups = page["items"]
        if total < 0 or len(groups) + offset > total:
            raise ValueError("Spotify returned an invalid release count. Retry discovery.")
        if not groups and offset < total:
            raise ValueError(f"Spotify stopped at {offset} of {total} release groups. Retry discovery.")
        signature = tuple(tuple(release.get("id") for release in group.get("releases", {}).get("items", [])) for group in groups)
        if any(not isinstance(identity, str) or not re.fullmatch(r"[A-Za-z0-9]{22}", identity)
               for variants in signature for identity in variants):
            raise ValueError("Spotify returned an invalid release ID.")
        if signature in page_signatures and groups:
            raise ValueError("Spotify repeated a discography page. No partial collection was queued.")
        group_keys = {tuple(sorted(variants)) for variants in signature}
        if group_keys.intersection(seen_groups):
            raise ValueError("Spotify repeated release groups across pages. Retry discovery.")
        page_signatures.add(signature)
        seen_groups.update(group_keys)
        for group in groups:
            variants = group.get("releases", {}).get("items", [])
            if not variants:
                raise ValueError("A release group had no editions. Retry discovery.")
            selected = variants if all_editions else [next((r for r in variants if r.get("playability", {}).get("playable", True)), variants[0])]
            for release in selected:
                release_id = release.get("id", "")
                if not re.fullmatch(r"[A-Za-z0-9]{22}", release_id):
                    raise ValueError("Spotify returned an invalid release ID.")
                if release_id in seen:
                    continue
                seen.add(release_id)
                date = release.get("date", {}).get("isoString", "")[:10]
                tracks = int(release.get("tracks", {}).get("totalCount", 0))
                releases.append({"url": f"https://open.spotify.com/album/{release_id}", "id": release_id,
                                 "kind": release.get("type", "ALBUM").casefold(), "title": release.get("name", release_id),
                                 "artist": name, "date": date, "tracks": tracks,
                                 "playable": release.get("playability", {}).get("playable", True),
                                 "info": f"{date} | {tracks} tracks"})
        offset += len(groups)
        if not groups:
            break
    return {"artist": name, "artist_url": f"https://open.spotify.com/artist/{identity}",
            "items": releases, "groups": total, "all_editions": all_editions}


def run_json_command(command: list[str], cancel: threading.Event | None = None, timeout: int = 180,
                     cwd: Path | None = None) -> dict:
    if cancel and cancel.is_set():
        raise InterruptedError("Discovery cancelled.")
    environment = dict(os.environ, PYTHONIOENCODING="utf-8")
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment, cwd=cwd,
                               text=True, encoding="utf-8", errors="replace", **window_kwargs())
    deadline = time.monotonic() + timeout
    try:
        while True:
            if cancel and cancel.is_set():
                raise InterruptedError("Discovery cancelled.")
            if time.monotonic() > deadline:
                raise TimeoutError("Discovery timed out. Try a smaller search or retry the artist lookup.")
            try:
                stdout, stderr = process.communicate(timeout=0.2)
                break
            except subprocess.TimeoutExpired:
                continue
        if process.returncode:
            raise ValueError(stderr.strip()[-700:] or "Discovery failed. Check your connection.")
        data = json.loads(stdout)
        if not isinstance(data, dict):
            raise ValueError("Discovery returned an unexpected response.")
        return data
    finally:
        if process.poll() is None:
            terminate_tree(process)
        process.communicate()


def request_catalogue(operation: str, query: str, *, kind: str = "tracks", offset: int = 0,
                      limit: int = 25, all_editions: bool = False, cancel=None) -> dict:
    command = [sys.executable, "-B", "-m", "ytdlp_studio.catalogue", operation, query,
               "--kind", kind, "--offset", str(offset), "--limit", str(limit)]
    if all_editions:
        command.append("--all-editions")
    return run_json_command(command, cancel, cwd=Path(__file__).resolve().parents[1])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["search", "artist", "inspect"])
    parser.add_argument("query")
    parser.add_argument("--kind", choices=["tracks", "artists"], default="tracks")
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--limit", type=int, choices=[25, 50, 100], default=25)
    parser.add_argument("--all-editions", action="store_true")
    args = parser.parse_args()
    try:
        with contextlib.redirect_stdout(sys.stderr):
            if args.operation == "artist":
                result = artist_discography(args.query, args.all_editions)
            elif args.operation == "inspect":
                result = inspect_spotify(args.query)
            else:
                result = search_spotify(args.query, args.kind, args.offset, args.limit)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except ImportError:
        print("Spotify discovery is not installed. Run .\\install.ps1 -Spotify.", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"Spotify discovery failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
