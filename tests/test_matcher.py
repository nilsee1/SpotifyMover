"""Unit tests for fuzzy matching and scoring logic."""

import time
from unittest.mock import MagicMock
from src.matcher import (
    match_playlist_tracks,
    match_track,
    parse_duration_seconds,
    score_track_match,
)
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
    cand_outside_5s = {"title": "Song A", "artist": "Artist A", "duration": 210}  # diff = 10s (neutral)

    score_within = score_track_match(track, cand_within_5s)
    score_outside = score_track_match(track, cand_outside_5s)

    # Within 5s gets the +5 bonus
    assert score_within == 100.0
    assert score_outside == 100.0  # title 100*0.6 + artist 100*0.4 = 100.0


def test_score_track_match_duration_penalties():
    track = Track(title="My Song", artists=["My Artist"], duration_ms=180000)  # 180s
    cand_neutral = {"title": "My Song", "artist": "My Artist", "duration": 190}  # diff = 10s (neutral)
    cand_penalty_5 = {"title": "My Song", "artist": "My Artist", "duration": 205}  # diff = 25s (-5.0)
    cand_penalty_10 = {"title": "My Song", "artist": "My Artist", "duration": 225}  # diff = 45s (-10.0)
    cand_penalty_15 = {"title": "My Song", "artist": "My Artist", "duration": 300}  # diff = 120s (-15.0)

    score_neutral = score_track_match(track, cand_neutral)
    score_p5 = score_track_match(track, cand_penalty_5)
    score_p10 = score_track_match(track, cand_penalty_10)
    score_p15 = score_track_match(track, cand_penalty_15)

    assert score_neutral == 100.0
    assert score_p5 == 95.0
    assert score_p10 == 90.0
    assert score_p15 == 85.0


def test_score_track_match_short_token_not_100():
    # "The" must not match "The Rolling Stones" with 100% artist score
    track = Track(title="Paint It Black", artists=["The"])
    cand = {"title": "Paint It Black", "artist": "The Rolling Stones"}
    score = score_track_match(track, cand)
    # Title is 100 (60 pts). Artist should NOT be 100 (would be 40 pts -> 100 total).
    assert score < 75.0


def test_score_track_match_secondary_artist_discount():
    # Secondary artist alone should be discounted by 0.85 and not yield 100% artist score
    track = Track(title="Levitating", artists=["Wrong Singer", "DaBaby"])
    cand = {"title": "Levitating", "artist": "Dua Lipa feat. DaBaby"}
    score = score_track_match(track, cand)
    # Title 100 (60 pts). Artist discounted: DaBaby matches cand segment (100 * 0.85 = 85 -> 34 pts)
    # Total <= 94.0, not 100.0
    assert score <= 94.0


def test_score_track_match_album_tie_breaker():
    track = Track(
        title="Bohemian Rhapsody",
        artists=["Queen"],
        album="A Night at the Opera",
        duration_ms=354000,
    )
    # Both candidates have a 10s duration diff (neutral, so base score is 100.0)
    # To observe album tie-breaker bonus without clamping at 100, use slightly imperfect title
    track_slightly_diff = Track(
        title="Bohemian Rhapsody (Live)",  # Live is preserved -> title norm "bohemian rhapsody live"
        artists=["Queen"],
        album="A Night at the Opera",
        duration_ms=354000,
    )
    cand_compilation = {
        "title": "Bohemian Rhapsody",
        "artist": "Queen",
        "album": "Greatest Hits",
        "duration": 364,
    }
    cand_album_match = {
        "title": "Bohemian Rhapsody",
        "artist": "Queen",
        "album": "A Night at the Opera",
        "duration": 364,
    }

    score_comp = score_track_match(track_slightly_diff, cand_compilation)
    score_album = score_track_match(track_slightly_diff, cand_album_match)

    assert score_album == score_comp + 2.0


def test_score_track_match_different_song_same_artist():
    track = Track(title="Radio Ga Ga", artists=["Queen"])
    candidate = {"title": "Bohemian Rhapsody", "artist": "Queen"}
    score = score_track_match(track, candidate)
    assert score < 75.0


def test_score_track_match_same_song_different_artist():
    track = Track(title="Bohemian Rhapsody", artists=["Queen"])
    candidate = {"title": "Bohemian Rhapsody", "artist": "The Muppets"}
    score = score_track_match(track, candidate)
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


def test_match_playlist_tracks_concurrent_ordering_and_caching():
    mock_client = MagicMock()

    # Simulate search results with slight sleep to test concurrency and ordering
    def mock_search(query: str, count: int = 20):
        time.sleep(0.01)
        if "track 1" in query.lower():
            return [{"id": "id-1", "title": "Track 1", "artist": "Artist 1"}]
        elif "track 2" in query.lower():
            return [{"id": "id-2", "title": "Track 2", "artist": "Artist 2"}]
        elif "track 3" in query.lower():
            return [{"id": "id-3", "title": "Track 3", "artist": "Artist 3"}]
        return []

    mock_client.search_songs.side_effect = mock_search

    tracks = [
        Track(title="Track 1", artists=["Artist 1"]),
        Track(title="Track 2", artists=["Artist 2"]),
        Track(title="Track 1", artists=["Artist 1"]),  # Duplicate track
        Track(title="Track 3", artists=["Artist 3"]),
    ]

    progress_calls = []

    def progress_cb(completed: int, total: int, track: Track, result):
        progress_calls.append((completed, total, track.title, result.navidrome_id))

    results = match_playlist_tracks(
        client=mock_client,
        tracks=tracks,
        threshold=80.0,
        progress_callback=progress_cb,
        max_workers=4,
    )

    # 1. Output order strictly matches input order
    assert len(results) == 4
    assert results[0].navidrome_id == "id-1"
    assert results[1].navidrome_id == "id-2"
    assert results[2].navidrome_id == "id-1"
    assert results[3].navidrome_id == "id-3"

    # 2. Duplicate track (Track 1) used search cache, so search_songs was called only 3 times
    assert mock_client.search_songs.call_count == 3

    # 3. Progress callback received all 4 items
    assert len(progress_calls) == 4
    assert progress_calls[-1][0] == 4
    assert progress_calls[-1][1] == 4
