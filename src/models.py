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
    warnings: list[str] = field(default_factory=list)
    is_truncated: bool = False


@dataclass
class MissingAlbumGroup:
    """Represents a group of missing tracks grouped by artist and album."""

    artist: str
    album: str | None
    musicbrainz_release_group_id: str | None = None
    tracks: list[Track] = field(default_factory=list)


@dataclass
class LidarrAlbumPlan:
    """Represents a planned action in Lidarr for a MissingAlbumGroup."""

    group: MissingAlbumGroup
    status: str  # 'new_artist_and_album', 'existing_artist_unmonitored', 'already_monitored', 'not_found', 'skipped_min_tracks'
    lidarr_album: dict[str, Any] | None = None
    lidarr_artist: dict[str, Any] | None = None
    resolved_artist_name: str | None = None
    resolved_album_title: str | None = None
    match_score: float = 0.0
    reason: str | None = None


@dataclass
class LidarrSyncSummary:
    """Summary of operations performed or planned during Lidarr synchronization."""

    added_artists: list[str] = field(default_factory=list)
    monitored_albums: list[str] = field(default_factory=list)
    already_monitored: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)  # (item_name, error_message)
    tagged_artist_ids: list[int] = field(default_factory=list)
    search_triggered: bool = False
    search_command_id: int | None = None
