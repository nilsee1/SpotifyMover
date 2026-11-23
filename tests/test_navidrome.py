"""Unit tests for Navidrome / Subsonic API client."""

import hashlib
import json
from unittest.mock import MagicMock, patch
import pytest
import requests
from src.navidrome import NavidromeClient, SubsonicError, load_navidrome_credentials


def test_load_navidrome_credentials_valid(monkeypatch):
    monkeypatch.setenv("NAVIDROME_URL", "https://music.example.com/")
    monkeypatch.setenv("NAVIDROME_USER", "admin")
    monkeypatch.setenv("NAVIDROME_PASSWORD", "secret")

    url, user, password = load_navidrome_credentials()
    # Verifies trailing slash stripped
    assert url == "https://music.example.com"
    assert user == "admin"
    assert password == "secret"


def test_load_navidrome_credentials_missing(monkeypatch):
    monkeypatch.delenv("NAVIDROME_URL", raising=False)
    monkeypatch.delenv("NAVIDROME_USER", raising=False)
    monkeypatch.delenv("NAVIDROME_PASSWORD", raising=False)

    with pytest.raises(ValueError, match="Missing required Navidrome environment variable"):
        load_navidrome_credentials()


def test_auth_params_subsonic_token():
    client = NavidromeClient(url="https://music.example.com", user="testuser", password="secretpassword")
    auth = client._get_auth_params()

    assert auth["u"] == "testuser"
    assert auth["v"] == "1.16.1"
    assert auth["c"] == "spotify-to-navidrome"
    assert auth["f"] == "json"
    assert "s" in auth
    assert "t" in auth

    # Verify md5(password + salt)
    expected_token = hashlib.md5(("secretpassword" + auth["s"]).encode("utf-8")).hexdigest()
    assert auth["t"] == expected_token


def test_ping_success():
    client = NavidromeClient(url="https://music.example.com", user="testuser", password="secret")
    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.json.return_value = {"subsonic-response": {"status": "ok", "version": "1.16.1"}}

    with patch.object(client.session, "get", return_value=mock_resp) as mock_get:
        assert client.ping() is True
        assert "ping.view" in mock_get.call_args[0][0]


def test_subsonic_error_handling():
    client = NavidromeClient(url="https://music.example.com", user="testuser", password="secret")
    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.json.return_value = {
        "subsonic-response": {
            "status": "failed",
            "error": {"code": 40, "message": "Wrong username or password"},
        }
    }

    with patch.object(client.session, "get", return_value=mock_resp):
        with pytest.raises(SubsonicError) as exc_info:
            client.ping()
        assert exc_info.value.code == 40
        assert "Wrong username or password" in exc_info.value.message


def test_create_or_update_playlist_post_form_data():
    client = NavidromeClient(url="https://music.example.com", user="testuser", password="secret")
    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.json.return_value = {"subsonic-response": {"status": "ok", "playlist": {"id": "pl-1", "name": "Favorites"}}}

    with patch.object(client.session, "post", return_value=mock_resp) as mock_post:
        # Create new
        client.create_or_update_playlist("Favorites", ["s1", "s2", "s3"])
        args, kwargs = mock_post.call_args
        assert "createPlaylist.view" in args[0]
        # Verify songId is passed as POST form data (not query param)
        form_data = kwargs["data"]
        assert ("name", "Favorites") in form_data
        assert ("songId", "s1") in form_data
        assert ("songId", "s2") in form_data
        assert ("songId", "s3") in form_data

        # Update existing
        client.create_or_update_playlist("Favorites", ["s1", "s2"], playlist_id="pl-1")
        _, kwargs_update = mock_post.call_args
        form_data_update = kwargs_update["data"]
        assert ("playlistId", "pl-1") in form_data_update
