"""Unit tests for Navidrome / Subsonic API client."""

import hashlib
import json
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest
import requests
from requests.adapters import HTTPAdapter
from src.navidrome import NavidromeClient, SubsonicError, load_navidrome_credentials


def test_load_navidrome_credentials_valid(monkeypatch, tmp_path):
    env_file = tmp_path / ".env.test"
    env_file.write_text("NAVIDROME_URL=https://music.example.com/\nNAVIDROME_USER=admin\nNAVIDROME_PASSWORD=secret\n")

    monkeypatch.setenv("NAVIDROME_URL", "https://music.example.com/")
    monkeypatch.setenv("NAVIDROME_USER", "admin")
    monkeypatch.setenv("NAVIDROME_PASSWORD", "secret")

    url, user, password = load_navidrome_credentials(env_file=env_file)
    # Verifies trailing slash stripped
    assert url == "https://music.example.com"
    assert user == "admin"
    assert password == "secret"


def test_load_navidrome_credentials_missing(monkeypatch, tmp_path):
    empty_env = tmp_path / ".env.empty"
    empty_env.write_text("")
    monkeypatch.delenv("NAVIDROME_URL", raising=False)
    monkeypatch.delenv("NAVIDROME_USER", raising=False)
    monkeypatch.delenv("NAVIDROME_PASSWORD", raising=False)

    with patch("src.navidrome.load_dotenv"):
        with pytest.raises(ValueError, match="Missing required Navidrome environment variable"):
            load_navidrome_credentials(env_file=empty_env)


def test_load_navidrome_credentials_project_root_fallback(monkeypatch):
    """Verify that when env_file is None, load_dotenv is called first with None, then with project .env."""
    calls = []

    def mock_load_dotenv(dotenv_path=None, override=True):
        calls.append((dotenv_path, override))
        if dotenv_path is not None:
            monkeypatch.setenv("NAVIDROME_URL", "https://navidrome.root")
            monkeypatch.setenv("NAVIDROME_USER", "rootuser")
            monkeypatch.setenv("NAVIDROME_PASSWORD", "rootpass")

    monkeypatch.delenv("NAVIDROME_URL", raising=False)
    monkeypatch.delenv("NAVIDROME_USER", raising=False)
    monkeypatch.delenv("NAVIDROME_PASSWORD", raising=False)

    with patch("src.navidrome.load_dotenv", side_effect=mock_load_dotenv), \
         patch.object(Path, "is_file", return_value=True):
        url, user, password = load_navidrome_credentials(env_file=None)
        assert url == "https://navidrome.root"
        assert user == "rootuser"
        assert password == "rootpass"
        # First call has dotenv_path=None
        assert calls[0] == (None, True)
        # Second call has project_env path and override=False
        assert calls[1][1] is False
        assert calls[1][0].name == ".env"


def test_navidrome_client_session_retry_adapter():
    """Verify NavidromeClient session mounts retry-enabled HTTPAdapter for GET requests."""
    client = NavidromeClient(url="https://music.example.com", user="testuser", password="secret")
    adapter = client.session.adapters.get("https://")
    assert isinstance(adapter, HTTPAdapter)
    assert adapter._pool_connections == 16
    assert adapter._pool_maxsize == 16

    retry = adapter.max_retries
    assert retry.total == 3
    assert retry.backoff_factor == 0.5
    assert set(retry.status_forcelist) == {429, 500, 502, 503, 504}
    assert retry.allowed_methods == frozenset(["GET"])


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
