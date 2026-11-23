"""Fetches and parses Spotify playlists via public embed page (__NEXT_DATA__)."""

from __future__ import annotations

import json
import logging
import re
from typing import Any
import requests
from bs4 import BeautifulSoup
from rich.console import Console

from src.models import PlaylistData, Track

logger = logging.getLogger(__name__)
console = Console()

SPOTIFY_URL_REGEX = re.compile(
    r"(?:spotify:playlist:|https?://open\.spotify\.com/(?:[a-zA-Z0-9\-_]+/)*(?:embed/)?playlist/)([a-zA-Z0-9]+)"
)
RAW_ID_REGEX = re.compile(r"^[a-zA-Z0-9]{15,30}$")

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)


def extract_playlist_id(source: str) -> str:
    """Extract Spotify playlist ID from URL or return raw ID if valid."""
    cleaned = source.strip()
    match = SPOTIFY_URL_REGEX.search(cleaned)
    if match:
        return match.group(1)
    if RAW_ID_REGEX.match(cleaned):
        return cleaned
    raise ValueError(
        f"Invalid Spotify playlist URL or ID: '{source}'. "
        "Expected format: https://open.spotify.com/playlist/{id} or raw 22-character ID."
    )


def _parse_artists_from_item(item: dict[str, Any]) -> list[str]:
    """Extract list of artist names from track item defensively."""
    artists: list[str] = []

    # 1. Check structured artists array if present
    raw_artists = item.get("artists")
    if isinstance(raw_artists, list):
        for art in raw_artists:
            if isinstance(art, dict) and "name" in art and art["name"]:
                artists.append(art["name"].strip())
            elif isinstance(art, str) and art.strip():
                artists.append(art.strip())

    # 2. Check structured authors array if present
    if not artists:
        raw_authors = item.get("authors")
        if isinstance(raw_authors, list):
            for auth in raw_authors:
                if isinstance(auth, dict) and "name" in auth and auth["name"]:
                    artists.append(auth["name"].strip())
                elif isinstance(auth, str) and auth.strip():
                    artists.append(auth.strip())

    # 3. Check subtitle string (typical in embed __NEXT_DATA__)
    subtitle = item.get("subtitle")
    if isinstance(subtitle, str) and subtitle.strip():
        # Replace non-breaking spaces and normalize
        normalized = subtitle.replace("\xa0", " ").replace("\u200b", "")
        # Split on comma or semicolon
        split_artists = [a.strip() for a in re.split(r"[,;]+", normalized) if a.strip()]
        for art in split_artists:
            if art not in artists:
                artists.append(art)

    return artists


def parse_embed_html(html_content: str, playlist_id: str) -> PlaylistData:
    """Parse Spotify embed page HTML and return PlaylistData."""
    soup = BeautifulSoup(html_content, "html.parser")
    script = soup.find("script", id="__NEXT_DATA__")
    if not script or not script.string:
        raise ValueError(
            f"Failed to find __NEXT_DATA__ in embed page for playlist '{playlist_id}'. "
            "The playlist might be private, deleted, or Spotify's embed markup has changed."
        )

    try:
        next_data = json.loads(script.string)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Malformed JSON in __NEXT_DATA__ for playlist '{playlist_id}': {exc}") from exc

    # Defensive traversal of Next.js state structure
    page_props = next_data.get("props", {}).get("pageProps", {})
    state_data = page_props.get("state", {}).get("data", {})
    entity = state_data.get("entity")

    # Fallback paths if entity is located elsewhere in props
    if not isinstance(entity, dict):
        entity = page_props.get("playlist") or page_props.get("entity") or {}

    if not entity:
        raise ValueError(
            f"Could not locate playlist entity data for '{playlist_id}'. "
            "Please verify that the playlist is public and accessible."
        )

    playlist_name = entity.get("name") or entity.get("title") or f"Spotify Playlist {playlist_id}"
    track_list_raw = entity.get("trackList")
    if track_list_raw is None:
        # Check alternative track list paths
        tracks_obj = entity.get("tracks")
        if isinstance(tracks_obj, dict):
            track_list_raw = tracks_obj.get("items", [])
        elif isinstance(tracks_obj, list):
            track_list_raw = tracks_obj
        else:
            track_list_raw = []

    parsed_tracks: list[Track] = []
    for item in track_list_raw:
        if not isinstance(item, dict):
            continue

        # If item has nested track object (e.g. {track: {...}})
        track_dict = item.get("track") if isinstance(item.get("track"), dict) else item

        title = track_dict.get("title") or track_dict.get("name")
        if not title:
            continue

        artists = _parse_artists_from_item(track_dict)
        album_name = None
        album_data = track_dict.get("album")
        if isinstance(album_data, dict):
            album_name = album_data.get("name")
        elif isinstance(album_data, str):
            album_name = album_data

        duration_ms = track_dict.get("duration") or track_dict.get("duration_ms")
        if duration_ms is not None:
            try:
                duration_ms = int(duration_ms)
            except (ValueError, TypeError):
                duration_ms = None

        parsed_tracks.append(
            Track(
                title=str(title).strip(),
                artists=artists,
                album=album_name.strip() if album_name else None,
                duration_ms=duration_ms,
            )
        )

    # 100-track warning requirement
    if len(parsed_tracks) == 100:
        console.print(
            "\n[bold yellow]⚠ Warning: Exactly 100 tracks were retrieved from Spotify's embed endpoint.[/bold yellow]"
        )
        console.print(
            "[yellow]Spotify's public embed page caps responses at 100 tracks. "
            "For playlists with more than 100 tracks, please export the playlist to a CSV file "
            "using Exportify (https://exportify.net) and run this tool on the CSV file.[/yellow]\n"
        )

    return PlaylistData(
        name=str(playlist_name).strip(),
        tracks=parsed_tracks,
        source_type="spotify_embed",
    )


def fetch_spotify_embed_playlist(source: str, timeout: int = 15) -> PlaylistData:
    """Fetch and parse a public Spotify playlist from its embed page."""
    playlist_id = extract_playlist_id(source)
    embed_url = f"https://open.spotify.com/embed/playlist/{playlist_id}"

    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Cache-Control": "no-cache",
    }

    try:
        response = requests.get(embed_url, headers=headers, timeout=timeout)
    except requests.RequestException as exc:
        raise ConnectionError(f"Failed to connect to Spotify embed endpoint: {exc}") from exc

    if response.status_code == 404:
        raise ValueError(
            f"Spotify playlist '{playlist_id}' not found (HTTP 404). "
            "Ensure the playlist ID is correct and that the playlist is public."
        )

    if not response.ok:
        raise ConnectionError(
            f"Spotify embed request failed with HTTP status {response.status_code}: {response.text[:200]}"
        )

    return parse_embed_html(response.text, playlist_id)
