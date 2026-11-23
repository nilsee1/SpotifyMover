"""Data models for tracks, playlists, and match results."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Track:
    """Represents an input audio track from Spotify or Exportify."""

    title: str
    artists: list[str] = field(default_factory=list)
    album: str | None = None
    duration_ms: int | None = None

    @property
    def primary_artist(self) -> str:
        """Return the primary (first) artist, or an empty string."""
        return self.artists[0].strip() if self.artists else ""

    @property
    def artist_display(self) -> str:
        """Formatted comma-separated artist string for display."""
        return ", ".join(self.artists) if self.artists else "Unknown Artist"

    def __str__(self) -> str:
        return f"{self.artist_display} - {self.title}"


@dataclass
class MatchResult:
    """Represents the matching result between an input track and Navidrome."""

    track: Track
    matched: bool
    navidrome_id: str | None = None
    navidrome_title: str | None = None
    navidrome_artist: str | None = None
    navidrome_duration_ms: int | None = None
    score: float = 0.0
    matched_song: dict[str, Any] | None = None

    @property
    def summary_display(self) -> str:
        """Human-readable display for match result."""
        if self.matched:
            return f"{self.navidrome_artist} - {self.navidrome_title} ({self.score:.1f}%)"
        return "Not found"


@dataclass
class PlaylistData:
    """Represents a parsed playlist with name and tracks."""

    name: str
    tracks: list[Track] = field(default_factory=list)
    source_type: str = "unknown"
