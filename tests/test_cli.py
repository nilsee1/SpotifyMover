"""Unit tests for CLI utility functions, argument parsing, and dry-run execution."""

from __future__ import annotations

import argparse
from pathlib import Path
from unittest import mock
from unittest.mock import MagicMock, patch
import pytest

from src.lidarr import LidarrClient, LidarrConfig
from src.models import (
    LidarrAlbumPlan,
    MatchResult,
    MissingAlbumGroup,
    PlaylistData,
    Track,
)
from sync_playlist import (
    build_arg_parser,
    detect_and_load_source,
    display_results_summary,
    export_unmatched_tracks,
    handle_lidarr_integration,
    main,
    non_negative_int,
    parse_selection_indices,
    parse_threshold,
    parse_workers,
    positive_int,
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


# =====================================================================
# CLI Argument Parsing Tests
# =====================================================================

def test_build_arg_parser_lidarr_flags():
    parser = build_arg_parser()
    args = parser.parse_args([
        "https://open.spotify.com/playlist/test",
        "--send-to-lidarr",
        "--lidarr-search",
        "--lidarr-min-tracks", "3",
        "--lidarr-select",
        "--yes",
    ])

    assert args.send_to_lidarr is True
    assert args.lidarr_search is True
    assert args.lidarr_min_tracks == 3
    assert args.lidarr_select is True
    assert args.yes is True


def test_build_arg_parser_lidarr_flags_defaults():
    parser = build_arg_parser()
    args = parser.parse_args(["https://open.spotify.com/playlist/test"])

    assert args.send_to_lidarr is False
    assert args.lidarr_search is False
    assert args.lidarr_min_tracks == 1
    assert args.lidarr_select is False
    assert args.yes is False


def test_build_arg_parser_short_yes_flag():
    parser = build_arg_parser()
    args = parser.parse_args(["https://open.spotify.com/playlist/test", "-y"])
    assert args.yes is True


def test_positive_int_validator():
    assert positive_int("1") == 1
    assert positive_int("5") == 5

    with pytest.raises(argparse.ArgumentTypeError, match="Value must be a positive integer >= 1"):
        positive_int("0")

    with pytest.raises(argparse.ArgumentTypeError, match="Value must be a positive integer >= 1"):
        positive_int("-2")

    with pytest.raises(argparse.ArgumentTypeError, match="Invalid positive integer"):
        positive_int("abc")


# =====================================================================
# Index / Range Selection Parser Tests
# =====================================================================

def test_parse_selection_indices_valid():
    assert parse_selection_indices("all", 5) == [1, 2, 3, 4, 5]
    assert parse_selection_indices("", 3) == [1, 2, 3]
    assert parse_selection_indices("none", 5) == []
    assert parse_selection_indices("1,3-5", 5) == [1, 3, 4, 5]
    assert parse_selection_indices("2, 4 , 1", 5) == [1, 2, 4]
    assert parse_selection_indices("1-3, 5", 5) == [1, 2, 3, 5]


def test_parse_selection_indices_invalid():
    # Out of bounds
    with pytest.raises(ValueError, match="out of bounds"):
        parse_selection_indices("6", 5)

    with pytest.raises(ValueError, match="out of bounds"):
        parse_selection_indices("1-10", 5)

    with pytest.raises(ValueError, match="out of bounds"):
        parse_selection_indices("0", 5)

    # Inverted range
    with pytest.raises(ValueError, match="cannot be greater than end"):
        parse_selection_indices("5-3", 5)

    # Non-integer
    with pytest.raises(ValueError, match="Invalid integer index"):
        parse_selection_indices("foo", 5)

    # Malformed range
    with pytest.raises(ValueError, match="Invalid range syntax"):
        parse_selection_indices("1-2-3", 5)


# =====================================================================
# Dry-Run Lidarr Integration Test
# =====================================================================

def test_dry_run_send_to_lidarr_skips_mutations():
    """Verify that --dry-run with --send-to-lidarr performs lookups and preview without mutations."""
    parser = build_arg_parser()
    args = parser.parse_args([
        "dummy_source",
        "--dry-run",
        "--send-to-lidarr",
    ])

    unmatched = [
        MatchResult(
            track=Track(title="Phantom", artists=["Justice"], album="†"),
            matched=False,
        )
    ]

    mock_cfg = LidarrConfig(url="https://lidarr.local", api_key="secret", tag="SpotifyMoverTool")

    client_mock = mock.MagicMock(spec=LidarrClient)
    client_mock.test_connection.return_value = {"status": "ok"}
    client_mock.resolve_root_folder.return_value = "/music"
    client_mock.resolve_quality_profile.return_value = 1
    client_mock.resolve_metadata_profile.return_value = 1

    plan = LidarrAlbumPlan(
        group=MissingAlbumGroup(artist="Justice", album="†", tracks=[unmatched[0].track]),
        status="new_artist_and_album",
        resolved_artist_name="Justice",
        resolved_album_title="†",
    )

    with mock.patch("sync_playlist.load_lidarr_config", return_value=mock_cfg):
        with mock.patch("sync_playlist.LidarrClient", return_value=client_mock):
            with mock.patch("sync_playlist.build_lidarr_plans", return_value=[plan]) as mock_build:
                with mock.patch("sync_playlist.apply_lidarr_plans") as mock_apply:
                    ret = handle_lidarr_integration(unmatched, args)

                    assert ret == 0
                    # Plan build / lookup was executed
                    mock_build.assert_called_once()
                    # But apply_lidarr_plans was NOT called (no POST / PUT mutations)
                    mock_apply.assert_not_called()
                    client_mock.add_artist.assert_not_called()
                    client_mock.monitor_albums.assert_not_called()
                    client_mock.ensure_tag.assert_not_called()
                    client_mock.trigger_album_search.assert_not_called()


# =====================================================================
# Two-Phase Lidarr Integration CLI Tests
# =====================================================================

def test_non_negative_int_validator():
    assert non_negative_int("0") == 0
    assert non_negative_int("15") == 15

    with pytest.raises(argparse.ArgumentTypeError, match="Value must be a non-negative integer >= 0"):
        non_negative_int("-1")

    with pytest.raises(argparse.ArgumentTypeError, match="Invalid non-negative integer"):
        non_negative_int("abc")


def test_build_arg_parser_lidarr_delay_flag():
    parser = build_arg_parser()
    args_default = parser.parse_args(["https://open.spotify.com/playlist/test"])
    assert args_default.lidarr_delay == 0

    args_custom = parser.parse_args(["https://open.spotify.com/playlist/test", "--lidarr-delay", "12"])
    assert args_custom.lidarr_delay == 12


def test_handle_lidarr_integration_user_confirms_ready():
    """Verify that when user confirms ready at prompt, both Phase 1 and Phase 2 run."""
    parser = build_arg_parser()
    args = parser.parse_args(["dummy_source", "--send-to-lidarr"])

    unmatched = [
        MatchResult(track=Track(title="Track 1", artists=["Artist A"], album="Album A"), matched=False)
    ]
    mock_cfg = LidarrConfig(url="https://lidarr.local", api_key="secret", tag="SpotifyMoverTool")

    client_mock = mock.MagicMock(spec=LidarrClient)
    client_mock.test_connection.return_value = {"status": "ok"}
    client_mock.resolve_root_folder.return_value = "/music"
    client_mock.resolve_quality_profile.return_value = 1
    client_mock.resolve_metadata_profile.return_value = 1

    plan = LidarrAlbumPlan(
        group=MissingAlbumGroup(artist="Artist A", album="Album A", tracks=[unmatched[0].track]),
        status="new_artist_and_album",
        resolved_artist_name="Artist A",
        resolved_album_title="Album A",
    )

    summary_phase1 = mock.MagicMock()
    summary_phase1.added_artists = ["Artist A"]

    summary_phase2 = mock.MagicMock()
    summary_phase2.added_artists = ["Artist A"]
    summary_phase2.monitored_albums = ["Artist A - Album A"]
    summary_phase2.failed = []
    summary_phase2.tagged_artist_ids = [10]
    summary_phase2.search_triggered = False

    with mock.patch("sync_playlist.load_lidarr_config", return_value=mock_cfg), \
         mock.patch("sync_playlist.LidarrClient", return_value=client_mock), \
         mock.patch("sync_playlist.build_lidarr_plans", return_value=[plan]), \
         mock.patch("sync_playlist.Confirm.ask", side_effect=[True, True]) as mock_confirm, \
         mock.patch("sync_playlist.import_lidarr_artists", return_value=(summary_phase1, 1)) as mock_import, \
         mock.patch("sync_playlist.monitor_lidarr_albums", return_value=summary_phase2) as mock_monitor:

        ret = handle_lidarr_integration(unmatched, args)

        assert ret == 0
        assert mock_confirm.call_count == 2
        mock_import.assert_called_once()
        mock_monitor.assert_called_once()


def test_handle_lidarr_integration_user_cancels_monitoring():
    """Verify that when user says 'No' to ready prompt, Phase 2 is skipped and artists stay imported."""
    parser = build_arg_parser()
    args = parser.parse_args(["dummy_source", "--send-to-lidarr"])

    unmatched = [
        MatchResult(track=Track(title="Track 1", artists=["Artist A"], album="Album A"), matched=False)
    ]
    mock_cfg = LidarrConfig(url="https://lidarr.local", api_key="secret", tag="SpotifyMoverTool")

    client_mock = mock.MagicMock(spec=LidarrClient)
    client_mock.test_connection.return_value = {"status": "ok"}
    client_mock.resolve_root_folder.return_value = "/music"
    client_mock.resolve_quality_profile.return_value = 1
    client_mock.resolve_metadata_profile.return_value = 1

    plan = LidarrAlbumPlan(
        group=MissingAlbumGroup(artist="Artist A", album="Album A", tracks=[unmatched[0].track]),
        status="new_artist_and_album",
        resolved_artist_name="Artist A",
        resolved_album_title="Album A",
    )

    summary_phase1 = mock.MagicMock()
    summary_phase1.added_artists = ["Artist A"]
    summary_phase1.monitored_albums = []
    summary_phase1.failed = []
    summary_phase1.tagged_artist_ids = [10]
    summary_phase1.search_triggered = False

    # First confirm (send to lidarr?): True. Second confirm (ready to monitor?): False.
    with mock.patch("sync_playlist.load_lidarr_config", return_value=mock_cfg), \
         mock.patch("sync_playlist.LidarrClient", return_value=client_mock), \
         mock.patch("sync_playlist.build_lidarr_plans", return_value=[plan]), \
         mock.patch("sync_playlist.Confirm.ask", side_effect=[True, False]), \
         mock.patch("sync_playlist.import_lidarr_artists", return_value=(summary_phase1, 1)) as mock_import, \
         mock.patch("sync_playlist.monitor_lidarr_albums") as mock_monitor:

        ret = handle_lidarr_integration(unmatched, args)

        assert ret == 0
        mock_import.assert_called_once()
        # Phase 2 was NOT called
        mock_monitor.assert_not_called()


def test_handle_lidarr_integration_yes_flag_skips_prompts():
    """Verify that with --yes, both phases run without asking user prompts."""
    parser = build_arg_parser()
    args = parser.parse_args(["dummy_source", "--send-to-lidarr", "--yes"])

    unmatched = [
        MatchResult(track=Track(title="Track 1", artists=["Artist A"], album="Album A"), matched=False)
    ]
    mock_cfg = LidarrConfig(url="https://lidarr.local", api_key="secret", tag="SpotifyMoverTool")

    client_mock = mock.MagicMock(spec=LidarrClient)
    client_mock.test_connection.return_value = {"status": "ok"}
    client_mock.resolve_root_folder.return_value = "/music"
    client_mock.resolve_quality_profile.return_value = 1
    client_mock.resolve_metadata_profile.return_value = 1

    plan = LidarrAlbumPlan(
        group=MissingAlbumGroup(artist="Artist A", album="Album A", tracks=[unmatched[0].track]),
        status="new_artist_and_album",
        resolved_artist_name="Artist A",
        resolved_album_title="Album A",
    )

    summary_phase1 = mock.MagicMock()
    summary_phase1.added_artists = ["Artist A"]

    summary_phase2 = mock.MagicMock()
    summary_phase2.added_artists = ["Artist A"]
    summary_phase2.monitored_albums = ["Artist A - Album A"]
    summary_phase2.failed = []
    summary_phase2.tagged_artist_ids = [10]
    summary_phase2.search_triggered = False

    with mock.patch("sync_playlist.load_lidarr_config", return_value=mock_cfg), \
         mock.patch("sync_playlist.LidarrClient", return_value=client_mock), \
         mock.patch("sync_playlist.build_lidarr_plans", return_value=[plan]), \
         mock.patch("sync_playlist.Confirm.ask") as mock_confirm, \
         mock.patch("sync_playlist.import_lidarr_artists", return_value=(summary_phase1, 1)) as mock_import, \
         mock.patch("sync_playlist.monitor_lidarr_albums", return_value=summary_phase2) as mock_monitor:

        ret = handle_lidarr_integration(unmatched, args)

        assert ret == 0
        mock_confirm.assert_not_called()
        mock_import.assert_called_once()
        mock_monitor.assert_called_once()


def test_handle_lidarr_integration_observes_delay():
    """Verify that --lidarr-delay triggers time.sleep before Phase 2."""
    parser = build_arg_parser()
    args = parser.parse_args(["dummy_source", "--send-to-lidarr", "--yes", "--lidarr-delay", "10"])

    unmatched = [
        MatchResult(track=Track(title="Track 1", artists=["Artist A"], album="Album A"), matched=False)
    ]
    mock_cfg = LidarrConfig(url="https://lidarr.local", api_key="secret", tag="SpotifyMoverTool")

    client_mock = mock.MagicMock(spec=LidarrClient)
    client_mock.test_connection.return_value = {"status": "ok"}
    client_mock.resolve_root_folder.return_value = "/music"
    client_mock.resolve_quality_profile.return_value = 1
    client_mock.resolve_metadata_profile.return_value = 1

    plan = LidarrAlbumPlan(
        group=MissingAlbumGroup(artist="Artist A", album="Album A", tracks=[unmatched[0].track]),
        status="new_artist_and_album",
        resolved_artist_name="Artist A",
        resolved_album_title="Album A",
    )

    summary = mock.MagicMock()
    summary.added_artists = ["Artist A"]
    summary.monitored_albums = ["Artist A - Album A"]
    summary.failed = []
    summary.tagged_artist_ids = [10]
    summary.search_triggered = False

    with mock.patch("sync_playlist.load_lidarr_config", return_value=mock_cfg), \
         mock.patch("sync_playlist.LidarrClient", return_value=client_mock), \
         mock.patch("sync_playlist.build_lidarr_plans", return_value=[plan]), \
         mock.patch("sync_playlist.import_lidarr_artists", return_value=(summary, 1)), \
         mock.patch("sync_playlist.monitor_lidarr_albums", return_value=summary), \
         mock.patch("sync_playlist.time.sleep") as mock_sleep:

        ret = handle_lidarr_integration(unmatched, args)

        assert ret == 0
        mock_sleep.assert_called_once_with(10)
