"""Unit tests for MusicBrainz metadata resolution."""

from __future__ import annotations

import unittest.mock as mock
import pytest
import requests

from src.musicbrainz import MusicBrainzResolver, resolve_album


def test_musicbrainz_resolve_prefers_official_album():
    """Verify resolver prefers official Album/Single over Compilation or Bootleg."""
    session_mock = mock.MagicMock(spec=requests.Session)
    mock_response = mock.MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "recordings": [
            {
                "id": "rec-1",
                "title": "Master of Puppets",
                "artist-credit": [{"name": "Metallica"}],
                "releases": [
                    {
                        "id": "rel-comp",
                        "title": "Greatest Hits Collection",
                        "status": "Official",
                        "release-group": {
                            "id": "rg-comp-id",
                            "primary-type": "Compilation",
                            "secondary-types": ["Compilation"],
                        },
                    },
                    {
                        "id": "rel-album",
                        "title": "Master of Puppets",
                        "status": "Official",
                        "release-group": {
                            "id": "rg-album-id",
                            "primary-type": "Album",
                            "secondary-types": [],
                        },
                    },
                ],
            }
        ]
    }
    session_mock.get.return_value = mock_response

    resolver = MusicBrainzResolver(rate_limit_delay=0.0, session=session_mock)
    album, rgid = resolver.resolve_album("Metallica", "Master of Puppets")

    assert album == "Master of Puppets"
    assert rgid == "rg-album-id"
    assert session_mock.get.call_count == 1


def test_musicbrainz_caching_avoids_duplicate_http_calls():
    """Verify in-memory cache prevents second HTTP request for the same track."""
    session_mock = mock.MagicMock(spec=requests.Session)
    mock_response = mock.MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "recordings": [
            {
                "id": "rec-1",
                "title": "One",
                "artist-credit": [{"name": "Metallica"}],
                "releases": [
                    {
                        "id": "rel-1",
                        "title": "...And Justice for All",
                        "status": "Official",
                        "release-group": {
                            "id": "rg-justice",
                            "primary-type": "Album",
                        },
                    }
                ],
            }
        ]
    }
    session_mock.get.return_value = mock_response

    resolver = MusicBrainzResolver(rate_limit_delay=0.0, session=session_mock)

    # First call - live lookup
    res1 = resolver.resolve_album("Metallica", "One")
    assert res1 == ("...And Justice for All", "rg-justice")
    assert session_mock.get.call_count == 1

    # Second call with same artist/title (varying case/spaces) - cached
    res2 = resolver.resolve_album("metallica", "One")
    assert res2 == ("...And Justice for All", "rg-justice")
    assert session_mock.get.call_count == 1


def test_musicbrainz_graceful_fallback_on_network_error():
    """Verify network errors and timeouts log warning and return (None, None)."""
    session_mock = mock.MagicMock(spec=requests.Session)
    session_mock.get.side_effect = requests.Timeout("Connection timed out")

    resolver = MusicBrainzResolver(rate_limit_delay=0.0, session=session_mock)
    album, rgid = resolver.resolve_album("Unknown Artist", "Ghost Track")

    assert album is None
    assert rgid is None

    # Cached negative lookup should also not retry
    album2, rgid2 = resolver.resolve_album("Unknown Artist", "Ghost Track")
    assert album2 is None
    assert rgid2 is None
    assert session_mock.get.call_count == 1


def test_musicbrainz_empty_recordings_returns_none():
    """Verify that empty recordings list returns (None, None)."""
    session_mock = mock.MagicMock(spec=requests.Session)
    mock_response = mock.MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"recordings": []}
    session_mock.get.return_value = mock_response

    resolver = MusicBrainzResolver(rate_limit_delay=0.0, session=session_mock)
    album, rgid = resolver.resolve_album("Nonexistent Artist 123", "Nonexistent Track 456")

    assert album is None
    assert rgid is None


def test_musicbrainz_low_confidence_match_returns_none():
    """Verify recordings below 80 score threshold are rejected."""
    session_mock = mock.MagicMock(spec=requests.Session)
    mock_response = mock.MagicMock()
    mock_response.status_code = 200
    # Returned recording title is completely different
    mock_response.json.return_value = {
        "recordings": [
            {
                "id": "rec-diff",
                "title": "Totally Different Title",
                "artist-credit": [{"name": "Different Artist"}],
                "releases": [
                    {
                        "id": "rel-diff",
                        "title": "Some Album",
                        "status": "Official",
                        "release-group": {"id": "rg-diff", "primary-type": "Album"},
                    }
                ],
            }
        ]
    }
    session_mock.get.return_value = mock_response

    resolver = MusicBrainzResolver(rate_limit_delay=0.0, session=session_mock)
    album, rgid = resolver.resolve_album("Artist X", "Song Y")

    assert album is None
    assert rgid is None
