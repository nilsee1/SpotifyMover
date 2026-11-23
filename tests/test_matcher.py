"""Unit tests for fuzzy matching and scoring logic."""

from unittest.mock import MagicMock
from src.matcher import match_track, parse_duration_seconds, score_track_match
from src.models import Track


def test_parse_duration_seconds():
    # Subsonic seconds format
    assert parse_duration_seconds(215) == 215.0
    # Milliseconds format (>10,000)
    assert parse_duration_seconds(215000) == 215.0
    # String conversions
    assert parse_duration_seconds("180") == 180.0
    assert parse_duration_seconds("180000") == 180.0
    # Invalid or missing
    assert parse_duration_seconds(None) is None
    assert parse_duration_seconds(0) is None
    assert parse_duration_seconds("invalid") is None


def test_score_track_match_exact():
    track = Track(title="Bohemian Rhapsody", artists=["Queen"], duration_ms=354000)
    candidate = {"title": "Bohemian Rhapsody", "artist": "Queen", "duration": 354}
    score = score_track_match(track, candidate)
    assert score == 100.0


def test_score_track_match_remaster_and_noise():
    track = Track(title="Bohemian Rhapsody - 2011 Remaster", artists=["Queen"], duration_ms=354000)
    candidate = {"title": "Bohemian Rhapsody", "artist": "Queen", "duration": 355}
    score = score_track_match(track, candidate)
    assert score >= 90.0


def test_score_track_match_duration_tie_breaker():
    track = Track(title="Song A", artists=["Artist A"], duration_ms=200000)  # 200s
    cand_within_5s = {"title": "Song A", "artist": "Artist A", "duration": 203}  # diff = 3s
    cand_outside_5s = {"title": "Song A", "artist": "Artist A", "duration": 220}  # diff = 20s

    score_within = score_track_match(track, cand_within_5s)
    score_outside = score_track_match(track, cand_outside_5s)

    # Within 5s gets the +5 bonus (or reaches 100 cap)
    assert score_within >= score_outside


def test_score_track_match_different_song_same_artist():
    track = Track(title="Radio Ga Ga", artists=["Queen"])
    candidate = {"title": "Bohemian Rhapsody", "artist": "Queen"}
    score = score_track_match(track, candidate)
    # Different title should fall well below default 80 threshold
    assert score < 75.0


def test_score_track_match_same_song_different_artist():
    track = Track(title="Bohemian Rhapsody", artists=["Queen"])
    candidate = {"title": "Bohemian Rhapsody", "artist": "The Muppets"}
    score = score_track_match(track, candidate)
    # Different artist should fall well below 80 threshold
    assert score < 80.0


def test_match_track_fallback_strategy():
    mock_client = MagicMock()

    track = Track(title="Harder Better Faster Stronger", artists=["Daft Punk"])

    # First search query with artist fails to return good match
    mock_client.search_songs.side_effect = [
        [{"id": "song-1", "title": "Random Song", "artist": "Daft Punk", "duration": 180}],
        [{"id": "song-2", "title": "Harder, Better, Faster, Stronger", "artist": "Daft Punk", "duration": 224}],
    ]

    result = match_track(mock_client, track, threshold=80.0)

    assert result.matched is True
    assert result.navidrome_id == "song-2"
    assert mock_client.search_songs.call_count == 2
