"""Unit tests for Exportify CSV parser."""

import io
from pathlib import Path
import pytest
from src.sources.exportify_csv import (
    _split_artist_string,
    derive_playlist_name_from_path,
    parse_exportify_csv,
)


def test_derive_playlist_name_from_path():
    assert derive_playlist_name_from_path("my_synthwave_mix.csv") == "my synthwave mix"
    assert derive_playlist_name_from_path("/path/to/classic-rock-favorites.csv") == "classic rock favorites"
    assert derive_playlist_name_from_path("80s_pop.csv") == "80s pop"


def test_split_artist_string_semicolon_with_comma_in_name():
    # Semicolon-separated artists where one artist contains a comma
    raw = "Tyler, The Creator; Kali Uchis"
    assert _split_artist_string(raw) == ["Tyler, The Creator", "Kali Uchis"]


def test_split_artist_string_comma_fallback():
    # Comma-separated artists when no semicolon is present
    raw = "Kendrick Lamar, SZA"
    assert _split_artist_string(raw) == ["Kendrick Lamar", "SZA"]


def test_parse_exportify_csv_standard():
    csv_data = (
        "Track Name,Artist Name(s),Album Name,Duration (ms)\n"
        "Blinding Lights,The Weeknd,After Hours,200040\n"
        '"Levitating","Dua Lipa, DaBaby","Future Nostalgia",203064\n'
        '"Get Lucky","Daft Punk; Pharrell Williams","Random Access Memories",369626\n'
        '"See You Again","Tyler, The Creator; Kali Uchis","Flower Boy",180000\n'
    )
    f = io.StringIO(csv_data)
    playlist = parse_exportify_csv(f, default_name="Test Mix")

    assert playlist.name == "Test Mix"
    assert len(playlist.tracks) == 4

    t1 = playlist.tracks[0]
    assert t1.title == "Blinding Lights"
    assert t1.artists == ["The Weeknd"]
    assert t1.album == "After Hours"
    assert t1.duration_ms == 200040

    t2 = playlist.tracks[1]
    assert t2.title == "Levitating"
    assert t2.artists == ["Dua Lipa", "DaBaby"]

    t3 = playlist.tracks[2]
    assert t3.title == "Get Lucky"
    assert t3.artists == ["Daft Punk", "Pharrell Williams"]

    t4 = playlist.tracks[3]
    assert t4.title == "See You Again"
    assert t4.artists == ["Tyler, The Creator", "Kali Uchis"]


def test_parse_exportify_csv_utf8_bom(tmp_path: Path):
    # Test handling of UTF-8-SIG (BOM)
    csv_file = tmp_path / "bom_playlist.csv"
    content = "Track Name,Artist Name(s),Album Name,Duration (ms)\nStay,The Kid LAROI,F*CK LOVE,141806\n"
    csv_file.write_bytes(content.encode("utf-8-sig"))

    playlist = parse_exportify_csv(csv_file)
    assert playlist.name == "bom playlist"
    assert len(playlist.tracks) == 1
    assert playlist.tracks[0].title == "Stay"
    assert playlist.tracks[0].artists == ["The Kid LAROI"]


def test_parse_exportify_csv_case_insensitive_headers():
    csv_data = (
        "TRACK NAME,artist(s),ALBUM,duration\n"
        "Starboy,The Weeknd,Starboy,230453\n"
    )
    f = io.StringIO(csv_data)
    playlist = parse_exportify_csv(f)
    assert len(playlist.tracks) == 1
    assert playlist.tracks[0].title == "Starboy"
    assert playlist.tracks[0].artists == ["The Weeknd"]


def test_parse_exportify_csv_missing_file():
    with pytest.raises(FileNotFoundError):
        parse_exportify_csv(Path("/nonexistent/file.csv"))


def test_parse_exportify_csv_missing_title_column():
    csv_data = "Artist,Album,Duration\nQueen,A Night At The Opera,354000\n"
    f = io.StringIO(csv_data)
    with pytest.raises(ValueError, match="Could not find Track Name"):
        parse_exportify_csv(f)
