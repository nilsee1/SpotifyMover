"""Unit tests for CLI utility functions."""

from pathlib import Path
from src.models import MatchResult, Track
from sync_playlist import detect_and_load_source, export_unmatched_tracks


def test_export_unmatched_tracks_txt(tmp_path: Path):
    txt_file = tmp_path / "missing.txt"
    unmatched = [
        MatchResult(track=Track(title="Track One", artists=["Artist A"]), matched=False),
        MatchResult(track=Track(title="Track Two", artists=["Artist B", "Artist C"]), matched=False),
    ]

    export_unmatched_tracks(unmatched, txt_file)
    content = txt_file.read_text(encoding="utf-8")
    assert "Artist A - Track One" in content
    assert "Artist B, Artist C - Track Two" in content


def test_export_unmatched_tracks_csv(tmp_path: Path):
    csv_file = tmp_path / "missing.csv"
    unmatched = [
        MatchResult(
            track=Track(title="Track One", artists=["Artist A"], album="Album A", duration_ms=210000),
            matched=False,
        ),
    ]

    export_unmatched_tracks(unmatched, csv_file)
    content = csv_file.read_text(encoding="utf-8-sig")
    assert "Artist,Title,Album,Duration (s)" in content
    assert "Artist A,Track One,Album A,210.0" in content
