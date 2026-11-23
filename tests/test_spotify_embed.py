"""Unit tests for Spotify embed page parser."""

import json
import pytest
from src.sources.spotify_embed import (
    extract_playlist_id,
    parse_embed_html,
)


@pytest.mark.parametrize(
    "source,expected_id",
    [
        ("https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M", "37i9dQZF1DXcBWIGoYBM5M"),
        ("https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M?si=abc123def", "37i9dQZF1DXcBWIGoYBM5M"),
        ("https://open.spotify.com/intl-de/playlist/37i9dQZF1DXcBWIGoYBM5M", "37i9dQZF1DXcBWIGoYBM5M"),
        ("https://open.spotify.com/embed/playlist/37i9dQZF1DXcBWIGoYBM5M", "37i9dQZF1DXcBWIGoYBM5M"),
        ("spotify:playlist:37i9dQZF1DXcBWIGoYBM5M", "37i9dQZF1DXcBWIGoYBM5M"),
        ("37i9dQZF1DXcBWIGoYBM5M", "37i9dQZF1DXcBWIGoYBM5M"),
    ],
)
def test_extract_playlist_id_valid(source, expected_id):
    assert extract_playlist_id(source) == expected_id


def test_extract_playlist_id_invalid():
    with pytest.raises(ValueError, match="Invalid Spotify playlist URL or ID"):
        extract_playlist_id("not-a-valid-spotify-id!")


def test_parse_embed_html_valid():
    sample_json = {
        "props": {
            "pageProps": {
                "state": {
                    "data": {
                        "entity": {
                            "name": "Synthwave Hits",
                            "trackList": [
                                {
                                    "title": "Midnight City",
                                    "subtitle": "M83",
                                    "duration": 243000,
                                    "album": {"name": "Hurry Up, We're Dreaming"},
                                },
                                {
                                    "title": "Nightcall",
                                    "subtitle": "Kavinsky,\xa0Lovefoxxx",
                                    "duration": 259000,
                                },
                            ],
                        }
                    }
                }
            }
        }
    }
    html = f'<html><head><script id="__NEXT_DATA__" type="application/json">{json.dumps(sample_json)}</script></head><body></body></html>'

    playlist = parse_embed_html(html, "37i9dQZF1DXcBWIGoYBM5M")
    assert playlist.name == "Synthwave Hits"
    assert len(playlist.tracks) == 2

    t1 = playlist.tracks[0]
    assert t1.title == "Midnight City"
    assert t1.artists == ["M83"]
    assert t1.duration_ms == 243000
    assert t1.album == "Hurry Up, We're Dreaming"

    t2 = playlist.tracks[1]
    assert t2.title == "Nightcall"
    # Tests non-breaking space and comma splitting
    assert t2.artists == ["Kavinsky", "Lovefoxxx"]


def test_parse_embed_html_missing_script():
    html = "<html><head></head><body>No Next Data</body></html>"
    with pytest.raises(ValueError, match="Failed to find __NEXT_DATA__"):
        parse_embed_html(html, "test_id")


def test_parse_embed_html_100_track_warning(capsys):
    tracks = [{"title": f"Track {i}", "subtitle": "Artist", "duration": 180000} for i in range(100)]
    sample_json = {
        "props": {
            "pageProps": {
                "state": {
                    "data": {
                        "entity": {
                            "name": "Big 100 Playlist",
                            "trackList": tracks,
                        }
                    }
                }
            }
        }
    }
    html = f'<html><head><script id="__NEXT_DATA__" type="application/json">{json.dumps(sample_json)}</script></head><body></body></html>'
    playlist = parse_embed_html(html, "big_id")

    assert len(playlist.tracks) == 100
    # Confirm that 100 tracks are parsed
