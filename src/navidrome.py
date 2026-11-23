"""Subsonic API client for Navidrome."""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
import secrets
from typing import Any
from dotenv import load_dotenv
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)


class SubsonicError(Exception):
    """Exception raised when Subsonic API returns an error response."""

    def __init__(self, code: int | None, message: str):
        super().__init__(f"Subsonic API Error [{code}]: {message}")
        self.code = code
        self.message = message


def load_navidrome_credentials(env_file: str | Path | None = None) -> tuple[str, str, str]:
    """Load and validate Navidrome credentials from environment/.env.

    Returns:
        tuple of (url, username, password)
    Raises:
        ValueError: if any required credential is missing.
    """
    if env_file:
        load_dotenv(dotenv_path=env_file)
    else:
        load_dotenv()
        project_env = Path(__file__).resolve().parent.parent / ".env"
        if project_env.is_file():
            load_dotenv(dotenv_path=project_env, override=False)

    url = os.getenv("NAVIDROME_URL")
    user = os.getenv("NAVIDROME_USER")
    password = os.getenv("NAVIDROME_PASSWORD")

    missing = []
    if not url:
        missing.append("NAVIDROME_URL")
    if not user:
        missing.append("NAVIDROME_USER")
    if not password:
        missing.append("NAVIDROME_PASSWORD")

    if missing:
        raise ValueError(
            f"Missing required Navidrome environment variable(s): {', '.join(missing)}.\n"
            "Please configure them in your .env file or environment.\n"
            "Example:\n"
            "  NAVIDROME_URL=https://music.example.com\n"
            "  NAVIDROME_USER=your_username\n"
            "  NAVIDROME_PASSWORD=your_password"
        )

    # Clean up URL (strip whitespace and trailing slashes)
    cleaned_url = url.strip().rstrip("/")
    return cleaned_url, user.strip(), password


class NavidromeClient:
    """Client for Navidrome using Subsonic API v1.16.1 token authentication."""

    def __init__(
        self,
        url: str,
        user: str,
        password: str,
        client_name: str = "spotify-to-navidrome",
        api_version: str = "1.16.1",
        timeout: int = 15,
        session: requests.Session | None = None,
        pool_connections: int = 16,
        pool_maxsize: int = 16,
    ):
        self.url = url.rstrip("/")
        self.user = user
        self.password = password
        self.client_name = client_name
        self.api_version = api_version
        self.timeout = timeout

        if session is not None:
            self.session = session
        else:
            self.session = requests.Session()
            retry_strategy = Retry(
                total=3,
                backoff_factor=0.5,
                status_forcelist=[429, 500, 502, 503, 504],
                allowed_methods=frozenset(["GET"]),
            )
            adapter = HTTPAdapter(
                pool_connections=pool_connections,
                pool_maxsize=pool_maxsize,
                max_retries=retry_strategy,
            )
            self.session.mount("http://", adapter)
            self.session.mount("https://", adapter)

    def _get_auth_params(self) -> dict[str, str]:
        """Generate Subsonic token authentication parameters (never sending plaintext password)."""
        salt = secrets.token_hex(8)
        token = hashlib.md5((self.password + salt).encode("utf-8")).hexdigest()
        return {
            "u": self.user,
            "t": token,
            "s": salt,
            "v": self.api_version,
            "c": self.client_name,
            "f": "json",
        }

    def _request(
        self,
        endpoint: str,
        method: str = "GET",
        params: dict[str, Any] | None = None,
        data: Any = None,
    ) -> dict[str, Any]:
        """Send authenticated request to Subsonic REST endpoint."""
        url = f"{self.url}/rest/{endpoint.lstrip('/')}"
        query_params = self._get_auth_params()
        if params:
            query_params.update(params)

        try:
            if method.upper() == "POST":
                response = self.session.post(
                    url,
                    params=query_params,
                    data=data,
                    timeout=self.timeout,
                )
            else:
                response = self.session.get(
                    url,
                    params=query_params,
                    timeout=self.timeout,
                )
        except requests.RequestException as exc:
            raise ConnectionError(f"Failed to connect to Navidrome server at {self.url}: {exc}") from exc

        if not response.ok:
            raise ConnectionError(
                f"Navidrome HTTP request failed ({response.status_code}): {response.text[:200]}"
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise ValueError(f"Invalid JSON response from Navidrome endpoint '{endpoint}': {response.text[:200]}") from exc

        sub_resp = payload.get("subsonic-response", {})
        status = sub_resp.get("status")
        if status != "ok":
            error_info = sub_resp.get("error", {})
            code = error_info.get("code")
            msg = error_info.get("message", "Unknown Subsonic error")
            raise SubsonicError(code=code, message=msg)

        return sub_resp

    def ping(self) -> bool:
        """Verify server reachability and credentials via rest/ping.view."""
        resp = self._request("ping.view")
        return resp.get("status") == "ok"

    def search_songs(self, query: str, count: int = 20) -> list[dict[str, Any]]:
        """Search songs via rest/search3.view."""
        if not query or not query.strip():
            return []

        params = {
            "query": query.strip(),
            "artistCount": 0,
            "albumCount": 0,
            "songCount": count,
        }
        resp = self._request("search3.view", params=params)
        search_result = resp.get("searchResult3", {})
        songs = search_result.get("song", [])
        return songs if isinstance(songs, list) else []

    def get_playlists(self) -> list[dict[str, Any]]:
        """Fetch all playlists via rest/getPlaylists.view."""
        resp = self._request("getPlaylists.view")
        playlists_obj = resp.get("playlists", {})
        playlist_list = playlists_obj.get("playlist", [])
        return playlist_list if isinstance(playlist_list, list) else []

    def find_playlist_by_name(self, name: str) -> dict[str, Any] | None:
        """Find an existing playlist by name (case-insensitive)."""
        target = name.strip().lower()
        for pl in self.get_playlists():
            if pl.get("name", "").strip().lower() == target:
                return pl
        return None

    def create_or_update_playlist(
        self,
        name: str,
        song_ids: list[str],
        playlist_id: str | None = None,
    ) -> dict[str, Any]:
        """Create a new playlist or update an existing one via rest/createPlaylist.view.

        Sends songId parameters as HTTP POST form data (application/x-www-form-urlencoded)
        to prevent HTTP 414 (URI Too Long) on large playlists.
        """
        form_data: list[tuple[str, str]] = []
        if playlist_id:
            form_data.append(("playlistId", playlist_id))
            form_data.append(("name", name))
        else:
            form_data.append(("name", name))

        for sid in song_ids:
            form_data.append(("songId", sid))

        resp = self._request("createPlaylist.view", method="POST", data=form_data)
        return resp.get("playlist", {})
