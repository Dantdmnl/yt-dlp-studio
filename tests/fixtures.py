"""Small public-catalogue response fixtures; no network or user data."""

from unittest.mock import Mock

ARTIST = "https://open.spotify.com/artist/" + "A" * 22


def edition(index, title="Same Album", playable=True, kind="ALBUM"):
    return {"id": f"{index:022d}", "name": title, "type": kind,
            "playability": {"playable": playable}, "date": {"isoString": "2025-03-10T00:00:00Z"},
            "tracks": {"totalCount": 12}}


def group(*releases):
    return {"releases": {"items": list(releases)}}


def page(groups, total):
    return {"data": {"artistUnion": {"discography": {"all": {"totalCount": total, "items": groups}}}}}


def client_for(groups):
    client = Mock()
    client.get_artist.return_value = {"data": {"artistUnion": {"profile": {"name": "Sample Artist"}}}}
    client.get_artist_discography.side_effect = lambda _, section, offset, limit: page(groups[offset:offset + limit], len(groups))
    return client
