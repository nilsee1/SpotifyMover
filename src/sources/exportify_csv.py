"""Parses playlist data from Exportify CSV files."""

from __future__ import annotations

import csv
from pathlib import Path
import re
from typing import TextIO

from src.models import PlaylistData, Track


def derive_playlist_name_from_path(file_path: str | Path) -> str:
    """Derive default playlist name from CSV filename stem (replace _ and - with spaces)."""
    stem = Path(file_path).stem
    # Replace hyphens and underscores with spaces, collapse spaces
    cleaned = re.sub(r"[-_]+", " ", stem).strip()
    return cleaned if cleaned else "Exported Playlist"


def _split_artist_string(artist_str: str) -> list[str]:
    """Split multi-artist string on comma or semicolon and clean up names."""
    if not artist_str:
        return []
    # Replace non-breaking spaces
    normalized = artist_str.replace("\xa0", " ").replace("\u200b", "")
    parts = re.split(r"[,;]+", normalized)
    artists = [p.strip() for p in parts if p.strip()]
    return artists


def _find_column_key(row_keys: list[str], candidates: list[str]) -> str | None:
    """Find key matching one of candidates case-insensitively and stripped."""
    candidate_set = {c.strip().lower() for c in candidates}
    for key in row_keys:
        if key and key.strip().lower() in candidate_set:
            return key
    return None


def parse_exportify_csv(file_or_path: str | Path | TextIO, default_name: str | None = None) -> PlaylistData:
    """Parse an Exportify CSV file into PlaylistData.

    Args:
        file_or_path: Path string, Path object, or open text file object.
        default_name: Optional override for the playlist name.
    """
    if isinstance(file_or_path, (str, Path)):
        path = Path(file_or_path)
        if not path.is_file():
            raise FileNotFoundError(f"Exportify CSV file not found: {path}")
        derived_name = default_name or derive_playlist_name_from_path(path)
        with open(path, mode="r", encoding="utf-8-sig", newline="") as f:
            return _parse_csv_content(f, derived_name)
    else:
        derived_name = default_name or "Exported Playlist"
        return _parse_csv_content(file_or_path, derived_name)


def _parse_csv_content(f: TextIO, playlist_name: str) -> PlaylistData:
    """Parse lines of CSV file."""
    reader = csv.DictReader(f)
    if not reader.fieldnames:
        raise ValueError("CSV file is empty or has invalid header.")

    fieldnames = list(reader.fieldnames)
    title_key = _find_column_key(
        fieldnames,
        ["track name", "track", "title", "name", "song name"],
    )
    artist_key = _find_column_key(
        fieldnames,
        ["artist name(s)", "artist name", "artist(s)", "artists", "artist"],
    )
    album_key = _find_column_key(
        fieldnames,
        ["album name", "album", "album title"],
    )
    duration_key = _find_column_key(
        fieldnames,
        ["duration (ms)", "duration_ms", "duration ms", "duration", "time (ms)"],
    )

    if not title_key:
        raise ValueError(
            f"Could not find Track Name column in CSV headers: {fieldnames}. "
            "Exportify CSVs must contain 'Track Name'."
        )

    tracks: list[Track] = []
    for row in reader:
        raw_title = row.get(title_key)
        if not raw_title or not raw_title.strip():
            continue

        raw_artists = row.get(artist_key, "") if artist_key else ""
        artists = _split_artist_string(raw_artists or "")

        raw_album = row.get(album_key) if album_key else None
        album = raw_album.strip() if raw_album and raw_album.strip() else None

        duration_ms = None
        if duration_key:
            raw_dur = row.get(duration_key)
            if raw_dur and raw_dur.strip():
                try:
                    duration_ms = int(float(raw_dur.strip()))
                except ValueError:
                    duration_ms = None

        tracks.append(
            Track(
                title=raw_title.strip(),
                artists=artists,
                album=album,
                duration_ms=duration_ms,
            )
        )

    return PlaylistData(
        name=playlist_name,
        tracks=tracks,
        source_type="exportify_csv",
    )
