"""Unit tests for CLI utility functions."""

import argparse
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from src.models import MatchResult, PlaylistData, Track
from sync_playlist import (
    build_arg_parser,
    detect_and_load_source,
    display_results_summary,
    export_unmatched_tracks,
    main,
    parse_threshold,
    parse_workers,
)


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


def test_threshold_validation():
    parser = build_arg_parser()

    # Valid values
    assert parser.parse_args(["--threshold", "0"]).threshold == 0
    assert parser.parse_args(["--threshold", "50"]).threshold == 50
    assert parser.parse_args(["--threshold", "100"]).threshold == 100

    # Invalid values
    with pytest.raises(SystemExit):
        parser.parse_args(["--threshold", "-5"])

    with pytest.raises(SystemExit):
        parser.parse_args(["--threshold", "105"])

    with pytest.raises(argparse.ArgumentTypeError):
        parse_threshold("-1")

    with pytest.raises(argparse.ArgumentTypeError):
        parse_threshold("101")


def test_workers_validation():
    parser = build_arg_parser()

    # Valid values
    assert parser.parse_args(["--workers", "1"]).workers == 1
    assert parser.parse_args(["--workers", "8"]).workers == 8

    # Invalid values
    with pytest.raises(SystemExit):
        parser.parse_args(["--workers", "0"])

    with pytest.raises(SystemExit):
        parser.parse_args(["--workers", "-2"])

    with pytest.raises(argparse.ArgumentTypeError):
        parse_workers("0")


def test_cli_displays_playlist_warnings(capsys):
    dummy_playlist = PlaylistData(
        name="Big Playlist",
        tracks=[],
        source_type="spotify_embed",
        warnings=["Notice: Exactly 100 tracks were retrieved from embed."],
        is_truncated=True,
    )

    with patch("sys.argv", ["sync_playlist.py", "https://open.spotify.com/playlist/test", "--dry-run"]), \
         patch("sync_playlist.load_navidrome_credentials", return_value=("https://music.example.com", "admin", "secret")), \
         patch("sync_playlist.NavidromeClient.ping", return_value=True), \
         patch("sync_playlist.detect_and_load_source", return_value=dummy_playlist):
        ret = main()
        assert ret == 0

    captured = capsys.readouterr()
    assert "Notice: Exactly 100 tracks were retrieved from embed." in captured.out


def test_display_results_summary_verbose():
    unmatched_track = Track(title="Song X", artists=["Artist Y"])
    candidate_song = {"title": "Song Z", "artist": "Artist Y"}
    results = [
        MatchResult(
            track=unmatched_track,
            matched=False,
            score=70.0,
            matched_song=candidate_song,
        )
    ]

    # Non-verbose
    matched, unmatched = display_results_summary("Test Pl", results, verbose=False)
    assert len(unmatched) == 1

    # Verbose shows best candidate
    matched_v, unmatched_v = display_results_summary("Test Pl", results, verbose=True)
    assert len(unmatched_v) == 1
