"""Unit tests for Lidarr API client and synchronization workflows."""

from __future__ import annotations

import os
from pathlib import Path
from unittest import mock
import pytest
import requests

from src.lidarr import (
    LidarrClient,
    LidarrConfig,
    LidarrError,
    apply_lidarr_plans,
    build_lidarr_plans,
    load_lidarr_config,
)
from src.models import (
    LidarrAlbumPlan,
    MissingAlbumGroup,
    Track,
)
from src.musicbrainz import MusicBrainzResolver


# =====================================================================
# 1. Configuration & Resolution Tests
# =====================================================================

def test_load_lidarr_config_valid(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LIDARR_URL", "https://lidarr.local:8686/")
    monkeypatch.setenv("LIDARR_API_KEY", "secretkey123")
    monkeypatch.setenv("LIDARR_ROOT_FOLDER", "/music")
    monkeypatch.setenv("LIDARR_QUALITY_PROFILE", "Lossless")
    monkeypatch.setenv("LIDARR_METADATA_PROFILE", "Standard")
    monkeypatch.setenv("LIDARR_TAG", "CustomTag")

    cfg = load_lidarr_config()
    assert cfg.url == "https://lidarr.local:8686"
    assert cfg.api_key == "secretkey123"
    assert cfg.root_folder == "/music"
    assert cfg.quality_profile == "Lossless"
    assert cfg.metadata_profile == "Standard"
    assert cfg.tag == "CustomTag"


def test_load_lidarr_config_missing_required(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("LIDARR_URL", raising=False)
    monkeypatch.delenv("LIDARR_API_KEY", raising=False)

    with pytest.raises(ValueError, match="Missing required Lidarr environment variable"):
        load_lidarr_config(env_file=Path("/nonexistent/.env"))


def test_resolve_root_folder_auto_select_single(monkeypatch: pytest.MonkeyPatch):
    client = LidarrClient(url="https://lidarr.local", api_key="test")
    with mock.patch.object(client, "get_root_folders", return_value=[{"id": 1, "path": "/data/music"}]):
        assert client.resolve_root_folder(None) == "/data/music"


def test_resolve_root_folder_ambiguous_raises(monkeypatch: pytest.MonkeyPatch):
    client = LidarrClient(url="https://lidarr.local", api_key="test")
    folders = [{"id": 1, "path": "/music1"}, {"id": 2, "path": "/music2"}]
    with mock.patch.object(client, "get_root_folders", return_value=folders):
        with pytest.raises(ValueError, match="LIDARR_ROOT_FOLDER not specified and Lidarr has multiple"):
            client.resolve_root_folder(None)


def test_resolve_root_folder_by_path_and_id():
    client = LidarrClient(url="https://lidarr.local", api_key="test")
    folders = [{"id": 1, "path": "/music/flac/"}, {"id": 2, "path": "/music/mp3"}]
    with mock.patch.object(client, "get_root_folders", return_value=folders):
        # Match by path with trailing slash tolerance and case insensitivity
        assert client.resolve_root_folder("/MUSIC/FLAC") == "/music/flac/"
        # Match by ID
        assert client.resolve_root_folder("2") == "/music/mp3"

    with mock.patch.object(client, "get_root_folders", return_value=folders):
        with pytest.raises(ValueError, match="Configured root folder '/invalid' was not found"):
            client.resolve_root_folder("/invalid")


def test_resolve_quality_profile_name_and_id():
    client = LidarrClient(url="https://lidarr.local", api_key="test")
    profiles = [{"id": 1, "name": "Standard"}, {"id": 5, "name": "FLAC Only"}]
    with mock.patch.object(client, "get_quality_profiles", return_value=profiles):
        # By case-insensitive name
        assert client.resolve_quality_profile("flac only") == 5
        # By ID
        assert client.resolve_quality_profile("1") == 1

    with mock.patch.object(client, "get_quality_profiles", return_value=profiles):
        with pytest.raises(ValueError, match="Configured quality profile 'Unknown' was not found"):
            client.resolve_quality_profile("Unknown")


def test_resolve_metadata_profile_auto_select():
    client = LidarrClient(url="https://lidarr.local", api_key="test")
    profiles = [{"id": 3, "name": "Standard + Singles"}]
    with mock.patch.object(client, "get_metadata_profiles", return_value=profiles):
        assert client.resolve_metadata_profile(None) == 3


def test_resolve_metadata_profile_ambiguous_raises():
    client = LidarrClient(url="https://lidarr.local", api_key="test")
    profiles = [{"id": 1, "name": "Standard"}, {"id": 2, "name": "Singles"}]
    with mock.patch.object(client, "get_metadata_profiles", return_value=profiles):
        with pytest.raises(ValueError, match="LIDARR_METADATA_PROFILE not specified and Lidarr has multiple"):
            client.resolve_metadata_profile(None)


# =====================================================================
# 2. Tag Handling & Lidarr Quirk Tests
# =====================================================================

def test_ensure_tag_existing_case_insensitive():
    client = LidarrClient(url="https://lidarr.local", api_key="test")
    with mock.patch.object(client, "get_tags", return_value=[{"id": 42, "label": "spotifymovertool"}]):
        with mock.patch.object(client, "_request") as mock_req:
            tag_id = client.ensure_tag("SpotifyMoverTool")
            assert tag_id == 42
            mock_req.assert_not_called()


def test_ensure_tag_creates_new():
    client = LidarrClient(url="https://lidarr.local", api_key="test")
    with mock.patch.object(client, "get_tags", return_value=[]):
        with mock.patch.object(client, "_request", return_value={"id": 99, "label": "customtag"}) as mock_req:
            tag_id = client.ensure_tag("customtag")
            assert tag_id == 99
            mock_req.assert_called_once_with("POST", "tag", json={"label": "customtag"})


def test_ensure_tag_retry_lowercase_on_http_400():
    client = LidarrClient(url="https://lidarr.local", api_key="test")
    with mock.patch.object(client, "get_tags", return_value=[]):
        def fake_request(method, endpoint, json=None, **kwargs):
            if json.get("label") == "SpotifyMoverTool":
                raise LidarrError("Validation failed: lowercase required", status_code=400)
            if json.get("label") == "spotifymovertool":
                return {"id": 77, "label": "spotifymovertool"}
            raise RuntimeError("Unexpected call")

        with mock.patch.object(client, "_request", side_effect=fake_request):
            tag_id = client.ensure_tag("SpotifyMoverTool")
            assert tag_id == 77


def test_ensure_artist_tagged_preserves_existing_tags():
    client = LidarrClient(url="https://lidarr.local", api_key="test")
    artist = {"id": 10, "artistName": "Queen", "tags": [1, 2]}

    with mock.patch.object(client, "_request", return_value={"id": 10, "tags": [1, 2, 99]}) as mock_req:
        client.ensure_artist_tagged(artist, 99)
        mock_req.assert_called_once()
        sent_body = mock_req.call_args[1]["json"]
        assert sent_body["tags"] == [1, 2, 99]
        assert sent_body["artistName"] == "Queen"


def test_ensure_artist_tagged_already_tagged():
    client = LidarrClient(url="https://lidarr.local", api_key="test")
    artist = {"id": 10, "artistName": "Queen", "tags": [1, 99]}

    with mock.patch.object(client, "_request") as mock_req:
        client.ensure_artist_tagged(artist, 99)
        mock_req.assert_not_called()


# =====================================================================
# 3. Plan Building & Grouping Tests
# =====================================================================

def test_build_lidarr_plans_grouping_and_min_tracks():
    client = LidarrClient(url="https://lidarr.local", api_key="test")
    tracks = [
        Track(title="Song 1", artists=["Band A"], album="Album 1"),
        Track(title="Song 2", artists=["Band A"], album="Album 1"),
        Track(title="Solo Track", artists=["Band B"], album="Album 2"),
    ]

    mb_mock = mock.MagicMock(spec=MusicBrainzResolver)

    with mock.patch.object(client, "get_artists", return_value=[]):
        with mock.patch.object(client, "lookup_album", return_value=[
            {"id": 101, "title": "Album 1", "foreignAlbumId": "alb-1", "artist": {"artistName": "Band A", "foreignArtistId": "art-a"}}
        ]):
            # Min tracks = 2: Album 1 has 2 tracks (actionable), Album 2 has 1 track (skipped)
            plans = build_lidarr_plans(client, tracks, min_tracks=2, mb_resolver=mb_mock)

            skipped = [p for p in plans if p.status == "skipped_min_tracks"]
            actionable = [p for p in plans if p.status == "new_artist_and_album"]

            assert len(skipped) == 1
            assert skipped[0].group.album == "Album 2"
            assert len(actionable) == 1
            assert actionable[0].group.album == "Album 1"
            assert len(actionable[0].group.tracks) == 2


def test_build_lidarr_plans_status_detection():
    client = LidarrClient(url="https://lidarr.local", api_key="test")
    tracks = [
        Track(title="Song A", artists=["Artist 1"], album="Album A"),
        Track(title="Song B", artists=["Artist 2"], album="Album B"),
        Track(title="Song C", artists=["Artist 2"], album="Album C"),
    ]

    existing_artists = [
        {"id": 20, "artistName": "Artist 2", "foreignArtistId": "art-2"}
    ]
    artist_2_albums = [
        {"id": 201, "title": "Album B", "foreignAlbumId": "rg-b", "monitored": True},
        {"id": 202, "title": "Album C", "foreignAlbumId": "rg-c", "monitored": False},
    ]

    def mock_lookup(term: str):
        if "Album A" in term:
            return [{"id": 100, "title": "Album A", "foreignAlbumId": "rg-a", "artist": {"artistName": "Artist 1", "foreignArtistId": "art-1"}}]
        if "Album B" in term:
            return [{"id": 201, "title": "Album B", "foreignAlbumId": "rg-b", "artist": {"artistName": "Artist 2", "foreignArtistId": "art-2"}}]
        if "Album C" in term:
            return [{"id": 202, "title": "Album C", "foreignAlbumId": "rg-c", "artist": {"artistName": "Artist 2", "foreignArtistId": "art-2"}}]
        return []

    with mock.patch.object(client, "get_artists", return_value=existing_artists):
        with mock.patch.object(client, "get_artist_albums", return_value=artist_2_albums):
            with mock.patch.object(client, "lookup_album", side_effect=mock_lookup):
                plans = build_lidarr_plans(client, tracks, min_tracks=1)

                status_by_album = {p.group.album: p.status for p in plans}
                assert status_by_album["Album A"] == "new_artist_and_album"
                assert status_by_album["Album B"] == "already_monitored"
                assert status_by_album["Album C"] == "existing_artist_unmonitored"


def test_build_lidarr_plans_not_found_on_missing_metadata_and_lidarr():
    client = LidarrClient(url="https://lidarr.local", api_key="test")
    # Track with no album and MB cannot resolve it
    track_no_meta = Track(title="Unknown Sound", artists=["Mysterious Artist"], album=None)
    mb_mock = mock.MagicMock(spec=MusicBrainzResolver)
    mb_mock.resolve_album.return_value = (None, None)

    plans = build_lidarr_plans(client, [track_no_meta], mb_resolver=mb_mock)
    assert len(plans) == 1
    assert plans[0].status == "not_found"
    assert "Album metadata missing" in (plans[0].reason or "")


# =====================================================================
# 4. Applying Plans (Safe Execution & Quirks)
# =====================================================================

def test_apply_lidarr_plans_new_artist():
    client = LidarrClient(url="https://lidarr.local", api_key="test")
    plan = LidarrAlbumPlan(
        group=MissingAlbumGroup(artist="New Band", album="Debut Album"),
        status="new_artist_and_album",
        lidarr_album={"foreignAlbumId": "alb-debut", "title": "Debut Album"},
        lidarr_artist={"artistName": "New Band", "foreignArtistId": "art-new"},
        resolved_artist_name="New Band",
        resolved_album_title="Debut Album",
    )

    with mock.patch.object(client, "ensure_tag", return_value=55):
        with mock.patch.object(client, "add_artist", return_value={"id": 10, "artistName": "New Band"}) as mock_add_art:
            with mock.patch.object(client, "get_artist_albums", return_value=[{"id": 1001, "foreignAlbumId": "alb-debut", "title": "Debut Album"}]):
                with mock.patch.object(client, "monitor_albums") as mock_mon:
                    summary = apply_lidarr_plans(
                        client=client,
                        plans=[plan],
                        root_folder="/music",
                        quality_profile_id=1,
                        metadata_profile_id=2,
                        tag="SpotifyMoverTool",
                    )

                    # Verify artist created with safe options
                    mock_add_art.assert_called_once()
                    artist_body = mock_add_art.call_args[0][0]
                    assert artist_body["addOptions"] == {"monitor": "none", "searchForMissingAlbums": False}
                    assert artist_body["tags"] == [55]
                    assert artist_body["monitored"] is True

                    # Verify ONLY Debut Album is monitored
                    mock_mon.assert_called_once_with([1001], monitored=True)
                    assert summary.added_artists == ["New Band"]
                    assert "New Band - Debut Album" in summary.monitored_albums


def test_apply_lidarr_plans_two_albums_same_new_artist():
    """Verify artist is created only ONCE when two missing albums belong to the same new artist."""
    client = LidarrClient(url="https://lidarr.local", api_key="test")
    plan1 = LidarrAlbumPlan(
        group=MissingAlbumGroup(artist="Polyphia", album="New Levels New Devils"),
        status="new_artist_and_album",
        lidarr_album={"foreignAlbumId": "rg-1", "title": "New Levels New Devils"},
        lidarr_artist={"artistName": "Polyphia", "foreignArtistId": "mbid-poly"},
        resolved_artist_name="Polyphia",
        resolved_album_title="New Levels New Devils",
    )
    plan2 = LidarrAlbumPlan(
        group=MissingAlbumGroup(artist="Polyphia", album="Remember That You Will Die"),
        status="new_artist_and_album",
        lidarr_album={"foreignAlbumId": "rg-2", "title": "Remember That You Will Die"},
        lidarr_artist={"artistName": "Polyphia", "foreignArtistId": "mbid-poly"},
        resolved_artist_name="Polyphia",
        resolved_album_title="Remember That You Will Die",
    )

    albums_under_artist = [
        {"id": 501, "foreignAlbumId": "rg-1", "title": "New Levels New Devils"},
        {"id": 502, "foreignAlbumId": "rg-2", "title": "Remember That You Will Die"},
    ]

    with mock.patch.object(client, "ensure_tag", return_value=12):
        with mock.patch.object(client, "add_artist", return_value={"id": 99, "artistName": "Polyphia"}) as mock_add_art:
            with mock.patch.object(client, "get_artist_albums", return_value=albums_under_artist):
                with mock.patch.object(client, "monitor_albums") as mock_mon:
                    summary = apply_lidarr_plans(
                        client=client,
                        plans=[plan1, plan2],
                        root_folder="/music",
                        quality_profile_id=1,
                        metadata_profile_id=1,
                    )

                    # Only created once!
                    assert mock_add_art.call_count == 1
                    assert len(summary.added_artists) == 1
                    assert mock_mon.call_count == 2
                    assert len(summary.monitored_albums) == 2


def test_apply_lidarr_plans_existing_artist_unmonitored():
    """Verify existing artist's album is monitored and tag is added without losing existing tags."""
    client = LidarrClient(url="https://lidarr.local", api_key="test")
    existing_artist = {"id": 40, "artistName": "Daft Punk", "tags": [1, 2]}
    plan = LidarrAlbumPlan(
        group=MissingAlbumGroup(artist="Daft Punk", album="Discovery"),
        status="existing_artist_unmonitored",
        lidarr_album={"id": 4001, "title": "Discovery", "foreignAlbumId": "rg-disc"},
        lidarr_artist=existing_artist,
        resolved_artist_name="Daft Punk",
        resolved_album_title="Discovery",
    )

    with mock.patch.object(client, "ensure_tag", return_value=88):
        with mock.patch.object(client, "monitor_albums") as mock_mon:
            with mock.patch.object(client, "ensure_artist_tagged") as mock_tag:
                summary = apply_lidarr_plans(
                    client=client,
                    plans=[plan],
                    root_folder="/music",
                    quality_profile_id=1,
                    metadata_profile_id=1,
                )

                mock_mon.assert_called_once_with([4001], monitored=True)
                mock_tag.assert_called_once_with(existing_artist, 88)
                assert summary.added_artists == []
                assert summary.monitored_albums == ["Daft Punk - Discovery"]


def test_apply_lidarr_plans_already_monitored_skips_mutations():
    """Verify already monitored albums do not trigger any album monitoring or tag updates."""
    client = LidarrClient(url="https://lidarr.local", api_key="test")
    plan = LidarrAlbumPlan(
        group=MissingAlbumGroup(artist="Radiohead", album="OK Computer"),
        status="already_monitored",
        lidarr_album={"id": 901, "title": "OK Computer", "monitored": True},
        lidarr_artist={"id": 90, "artistName": "Radiohead", "tags": []},
        resolved_artist_name="Radiohead",
        resolved_album_title="OK Computer",
    )

    with mock.patch.object(client, "ensure_tag", return_value=1):
        with mock.patch.object(client, "monitor_albums") as mock_mon:
            with mock.patch.object(client, "ensure_artist_tagged") as mock_tag:
                summary = apply_lidarr_plans(
                    client=client,
                    plans=[plan],
                    root_folder="/music",
                    quality_profile_id=1,
                    metadata_profile_id=1,
                )

                mock_mon.assert_not_called()
                mock_tag.assert_not_called()
                assert len(summary.already_monitored) == 1


def test_trigger_album_search_batched_only_when_requested():
    """Verify batched AlbumSearch is sent only when trigger_search is True."""
    client = LidarrClient(url="https://lidarr.local", api_key="test")
    plan = LidarrAlbumPlan(
        group=MissingAlbumGroup(artist="Artist", album="Album"),
        status="existing_artist_unmonitored",
        lidarr_album={"id": 7001, "title": "Album"},
        lidarr_artist={"id": 70, "artistName": "Artist"},
        resolved_artist_name="Artist",
        resolved_album_title="Album",
    )

    # 1. When trigger_search is False -> command NOT sent
    with mock.patch.object(client, "ensure_tag", return_value=1):
        with mock.patch.object(client, "monitor_albums"):
            with mock.patch.object(client, "ensure_artist_tagged"):
                with mock.patch.object(client, "trigger_album_search") as mock_cmd:
                    summary = apply_lidarr_plans(
                        client=client,
                        plans=[plan],
                        root_folder="/music",
                        quality_profile_id=1,
                        metadata_profile_id=1,
                        trigger_search=False,
                    )
                    mock_cmd.assert_not_called()
                    assert summary.search_triggered is False

    # 2. When trigger_search is True -> command IS sent with all album IDs
    with mock.patch.object(client, "ensure_tag", return_value=1):
        with mock.patch.object(client, "monitor_albums"):
            with mock.patch.object(client, "ensure_artist_tagged"):
                with mock.patch.object(client, "trigger_album_search", return_value={"id": 345}) as mock_cmd:
                    summary = apply_lidarr_plans(
                        client=client,
                        plans=[plan],
                        root_folder="/music",
                        quality_profile_id=1,
                        metadata_profile_id=1,
                        trigger_search=True,
                    )
                    mock_cmd.assert_called_once_with([7001])
                    assert summary.search_triggered is True
                    assert summary.search_command_id == 345
