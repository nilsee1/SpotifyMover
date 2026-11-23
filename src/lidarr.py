"""Lidarr API v1 client, configuration loader, and album monitoring workflow."""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
import os
from pathlib import Path
import time
from typing import Any, Callable
from dotenv import load_dotenv
from rapidfuzz import fuzz
import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

from src.matcher import normalize_artist, normalize_title
from src.models import (
    LidarrAlbumPlan,
    LidarrSyncSummary,
    MissingAlbumGroup,
    Track,
)
from src.musicbrainz import MusicBrainzResolver

logger = logging.getLogger(__name__)


@dataclass
class LidarrConfig:
    """Configuration options for Lidarr server integration."""

    url: str
    api_key: str
    root_folder: str | None = None
    quality_profile: str | None = None
    metadata_profile: str | None = None
    tag: str = "SpotifyMoverTool"


class LidarrError(Exception):
    """Exception raised when an error occurs during Lidarr API interaction."""

    def __init__(self, message: str, status_code: int | None = None, response_body: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body


def load_lidarr_config(env_file: str | Path | None = None) -> LidarrConfig:
    """Load and validate Lidarr credentials and settings from environment/.env.

    Uses the same lookup and repository-root fallback logic as load_navidrome_credentials.
    """
    if env_file:
        load_dotenv(dotenv_path=env_file)
    else:
        load_dotenv()
        # Fallback to repo root .env if not found in cwd
        repo_root_env = Path(__file__).resolve().parent.parent / ".env"
        if repo_root_env.is_file():
            load_dotenv(dotenv_path=repo_root_env)

    url = os.getenv("LIDARR_URL")
    api_key = os.getenv("LIDARR_API_KEY")

    missing = []
    if not url or not url.strip():
        missing.append("LIDARR_URL")
    if not api_key or not api_key.strip():
        missing.append("LIDARR_API_KEY")

    if missing:
        raise ValueError(
            f"Missing required Lidarr environment variable(s): {', '.join(missing)}.\n"
            "Please configure them in your .env file or environment.\n"
            "Example:\n"
            "  LIDARR_URL=https://lidarr.example.com\n"
            "  LIDARR_API_KEY=your_lidarr_api_key\n"
            "  LIDARR_ROOT_FOLDER=/music\n"
            "  LIDARR_QUALITY_PROFILE=Standard\n"
            "  LIDARR_METADATA_PROFILE=Standard"
        )

    cleaned_url = url.strip().rstrip("/")
    cleaned_api_key = api_key.strip()

    root_folder = os.getenv("LIDARR_ROOT_FOLDER")
    if root_folder and root_folder.strip():
        root_folder = root_folder.strip()
    else:
        root_folder = None

    quality_profile = os.getenv("LIDARR_QUALITY_PROFILE")
    if quality_profile and quality_profile.strip():
        quality_profile = quality_profile.strip()
    else:
        quality_profile = None

    metadata_profile = os.getenv("LIDARR_METADATA_PROFILE")
    if metadata_profile and metadata_profile.strip():
        metadata_profile = metadata_profile.strip()
    else:
        metadata_profile = None

    tag = os.getenv("LIDARR_TAG", "SpotifyMoverTool").strip() or "SpotifyMoverTool"

    return LidarrConfig(
        url=cleaned_url,
        api_key=cleaned_api_key,
        root_folder=root_folder,
        quality_profile=quality_profile,
        metadata_profile=metadata_profile,
        tag=tag,
    )


class LidarrClient:
    """Client for interacting with Lidarr REST API v1."""

    def __init__(
        self,
        url: str,
        api_key: str,
        timeout: int = 20,
        session: requests.Session | None = None,
    ):
        self.url = url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.session = session or requests.Session()

        self.session.headers.update({
            "X-Api-Key": self.api_key,
            "Accept": "application/json",
        })

        # Retry configuration: GET requests ONLY
        retries = Retry(
            total=3,
            backoff_factor=0.5,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=frozenset(["GET"]),
        )
        adapter = HTTPAdapter(max_retries=retries)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

    def _request(
        self,
        method: str,
        endpoint: str,
        params: dict[str, Any] | None = None,
        json: Any = None,
    ) -> Any:
        """Send an HTTP request to Lidarr API v1."""
        url = f"{self.url}/api/v1/{endpoint.lstrip('/')}"
        method = method.upper()

        try:
            response = self.session.request(
                method=method,
                url=url,
                params=params,
                json=json,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise ConnectionError(f"Failed to connect to Lidarr server at {self.url}: {exc}") from exc

        if not response.ok:
            error_body = response.text[:300]
            try:
                parsed_json = response.json()
                if isinstance(parsed_json, (list, dict)):
                    error_body = parsed_json
            except Exception:
                pass
            raise LidarrError(
                f"Lidarr HTTP {response.status_code} on {method} {endpoint}: {error_body}",
                status_code=response.status_code,
                response_body=error_body,
            )

        if response.status_code == 204 or not response.content:
            return None

        try:
            return response.json()
        except ValueError as exc:
            raise LidarrError(f"Invalid JSON returned from Lidarr {endpoint}: {response.text[:200]}") from exc

    def test_connection(self) -> dict[str, Any]:
        """Verify Lidarr connectivity via GET /api/v1/system/status."""
        return self._request("GET", "system/status")

    # --- Profiles and Root Folder ---

    def get_root_folders(self) -> list[dict[str, Any]]:
        """Fetch all configured root folders."""
        res = self._request("GET", "rootfolder")
        return res if isinstance(res, list) else []

    def get_quality_profiles(self) -> list[dict[str, Any]]:
        """Fetch all configured quality profiles."""
        res = self._request("GET", "qualityprofile")
        return res if isinstance(res, list) else []

    def get_metadata_profiles(self) -> list[dict[str, Any]]:
        """Fetch all configured metadata profiles."""
        res = self._request("GET", "metadataprofile")
        return res if isinstance(res, list) else []

    def resolve_root_folder(self, configured: str | None) -> str:
        """Resolve root folder path.

        If omitted, auto-select if exactly 1 exists, else raise ValueError listing options.
        If specified, match by exact ID or case-insensitive path (trailing slashes stripped).
        """
        folders = self.get_root_folders()
        if not folders:
            raise ValueError("No root folders configured in Lidarr. Please add at least one root folder in Lidarr settings.")

        if not configured:
            if len(folders) == 1:
                return str(folders[0]["path"])
            folder_list = ", ".join(f"'{f.get('path')}'" for f in folders)
            raise ValueError(
                f"LIDARR_ROOT_FOLDER not specified and Lidarr has multiple root folders ({folder_list}). "
                "Please configure LIDARR_ROOT_FOLDER in your .env."
            )

        target = configured.strip().rstrip("/")
        for f in folders:
            if str(f.get("id")) == target:
                return str(f["path"])
            cand_path = str(f.get("path", "")).strip().rstrip("/")
            if cand_path.lower() == target.lower():
                return str(f["path"])

        available = ", ".join(f"'{f.get('path')}'" for f in folders)
        raise ValueError(
            f"Configured root folder '{configured}' was not found in Lidarr. Available root folders: {available}."
        )

    def resolve_quality_profile(self, configured: str | None) -> int:
        """Resolve quality profile ID.

        If omitted, auto-select if exactly 1 exists, else raise ValueError listing options.
        If specified, match by exact ID or case-insensitive name.
        """
        profiles = self.get_quality_profiles()
        if not profiles:
            raise ValueError("No quality profiles configured in Lidarr.")

        if not configured:
            if len(profiles) == 1:
                return int(profiles[0]["id"])
            profile_list = ", ".join(f"'{p.get('name')}' ({p.get('id')})" for p in profiles)
            raise ValueError(
                f"LIDARR_QUALITY_PROFILE not specified and Lidarr has multiple quality profiles ({profile_list}). "
                "Please configure LIDARR_QUALITY_PROFILE in your .env."
            )

        target = configured.strip()
        for p in profiles:
            if str(p.get("id")) == target:
                return int(p["id"])
            if str(p.get("name", "")).strip().lower() == target.lower():
                return int(p["id"])

        available = ", ".join(f"'{p.get('name')}' ({p.get('id')})" for p in profiles)
        raise ValueError(
            f"Configured quality profile '{configured}' was not found in Lidarr. Available profiles: {available}."
        )

    def resolve_metadata_profile(self, configured: str | None) -> int:
        """Resolve metadata profile ID.

        If omitted, auto-select if exactly 1 exists, else raise ValueError listing options.
        If specified, match by exact ID or case-insensitive name.
        """
        profiles = self.get_metadata_profiles()
        if not profiles:
            raise ValueError("No metadata profiles configured in Lidarr.")

        if not configured:
            if len(profiles) == 1:
                return int(profiles[0]["id"])
            profile_list = ", ".join(f"'{p.get('name')}' ({p.get('id')})" for p in profiles)
            raise ValueError(
                f"LIDARR_METADATA_PROFILE not specified and Lidarr has multiple metadata profiles ({profile_list}). "
                "Please configure LIDARR_METADATA_PROFILE in your .env."
            )

        target = configured.strip()
        for p in profiles:
            if str(p.get("id")) == target:
                return int(p["id"])
            if str(p.get("name", "")).strip().lower() == target.lower():
                return int(p["id"])

        available = ", ".join(f"'{p.get('name')}' ({p.get('id')})" for p in profiles)
        raise ValueError(
            f"Configured metadata profile '{configured}' was not found in Lidarr. Available profiles: {available}."
        )

    # --- Tags ---

    def get_tags(self) -> list[dict[str, Any]]:
        """Fetch all tags via GET /api/v1/tag."""
        res = self._request("GET", "tag")
        return res if isinstance(res, list) else []

    def ensure_tag(self, label: str = "SpotifyMoverTool") -> int:
        """Find or create a tag by label.

        Matches existing tags case-insensitively.
        If POST /api/v1/tag fails with HTTP 400 due to uppercase characters,
        automatically retries with lowercase label.
        """
        clean_label = label.strip()
        tags = self.get_tags()
        for tag in tags:
            if tag.get("label", "").strip().lower() == clean_label.lower():
                return int(tag["id"])

        try:
            created = self._request("POST", "tag", json={"label": clean_label})
            return int(created["id"])
        except LidarrError as exc:
            if exc.status_code == 400 and clean_label != clean_label.lower():
                logger.info(
                    "Lidarr rejected mixed-case tag '%s' (HTTP 400). Retrying with lowercase '%s'...",
                    clean_label,
                    clean_label.lower(),
                )
                created = self._request("POST", "tag", json={"label": clean_label.lower()})
                return int(created["id"])
            raise

    def ensure_artist_tagged(self, artist_payload: dict[str, Any], tag_id: int) -> dict[str, Any]:
        """Ensure tag_id is added to an existing artist without removing any existing tags."""
        existing_tags = list(artist_payload.get("tags") or [])
        if tag_id in existing_tags:
            return artist_payload

        updated_artist = dict(artist_payload)
        updated_artist["tags"] = existing_tags + [tag_id]
        artist_id = artist_payload.get("id")
        if not artist_id:
            raise LidarrError(f"Artist payload is missing an 'id': {artist_payload}")

        return self._request("PUT", f"artist/{artist_id}", json=updated_artist)

    # --- Artists and Albums ---

    def get_artists(self) -> list[dict[str, Any]]:
        """Fetch all artists in library via GET /api/v1/artist."""
        res = self._request("GET", "artist")
        return res if isinstance(res, list) else []

    def lookup_artist(self, term: str) -> list[dict[str, Any]]:
        """Look up artists via GET /api/v1/artist/lookup."""
        res = self._request("GET", "artist/lookup", params={"term": term})
        return res if isinstance(res, list) else []

    def add_artist(self, artist_payload: dict[str, Any]) -> dict[str, Any]:
        """Add a new artist to Lidarr via POST /api/v1/artist."""
        return self._request("POST", "artist", json=artist_payload)

    def get_artist_albums(self, artist_id: int) -> list[dict[str, Any]]:
        """Fetch albums for an artist via GET /api/v1/album?artistId={artist_id}."""
        res = self._request("GET", "album", params={"artistId": artist_id})
        return res if isinstance(res, list) else []

    def lookup_album(self, term: str) -> list[dict[str, Any]]:
        """Look up albums via GET /api/v1/album/lookup."""
        res = self._request("GET", "album/lookup", params={"term": term})
        return res if isinstance(res, list) else []

    def add_album(self, album_payload: dict[str, Any]) -> dict[str, Any]:
        """Add an album via POST /api/v1/album."""
        return self._request("POST", "album", json=album_payload)

    def monitor_albums(self, album_ids: list[int], monitored: bool = True) -> Any:
        """Set monitored status for a list of album IDs via PUT /api/v1/album/monitor."""
        if not album_ids:
            return None
        return self._request("PUT", "album/monitor", json={"albumIds": album_ids, "monitored": monitored})

    def trigger_album_search(self, album_ids: list[int]) -> dict[str, Any]:
        """Trigger an AlbumSearch command for given album IDs via POST /api/v1/command."""
        if not album_ids:
            return {}
        return self._request("POST", "command", json={"name": "AlbumSearch", "albumIds": album_ids})


def build_lidarr_plans(
    client: LidarrClient,
    missing_tracks: list[Track],
    min_tracks: int = 1,
    mb_resolver: MusicBrainzResolver | None = None,
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> list[LidarrAlbumPlan]:
    """Group missing tracks and build resolution plans against Lidarr.

    1. Resolves missing album metadata via MusicBrainz when necessary.
    2. Groups missing tracks by artist and album release group.
    3. Filters groups by min_tracks threshold.
    4. Matches groups to Lidarr albums and identifies existing vs new artists.
    """
    resolver = mb_resolver or MusicBrainzResolver()

    # Step 1: Resolve missing album metadata and prepare groups
    groups_by_key: dict[tuple[str, str], MissingAlbumGroup] = {}
    unresolved_plans: list[LidarrAlbumPlan] = []

    total_tracks = len(missing_tracks)
    for idx, track in enumerate(missing_tracks, start=1):
        if progress_callback:
            progress_callback(idx, total_tracks, f"Resolving metadata: {track.artist_display} - {track.title}")

        resolved_album = track.album
        release_group_mbid = None

        if not resolved_album:
            resolved_album, release_group_mbid = resolver.resolve_album(track.primary_artist, track.title)

        if not resolved_album:
            group = MissingAlbumGroup(artist=track.primary_artist, album=None, tracks=[track])
            unresolved_plans.append(
                LidarrAlbumPlan(
                    group=group,
                    status="not_found",
                    reason="Album metadata missing and MusicBrainz lookup found no match",
                )
            )
            continue

        norm_art = normalize_artist(track.primary_artist)
        album_key = release_group_mbid or normalize_title(resolved_album)
        group_key = (norm_art, album_key)

        if group_key not in groups_by_key:
            groups_by_key[group_key] = MissingAlbumGroup(
                artist=track.primary_artist,
                album=resolved_album,
                musicbrainz_release_group_id=release_group_mbid,
                tracks=[],
            )
        groups_by_key[group_key].tracks.append(track)
        if release_group_mbid and not groups_by_key[group_key].musicbrainz_release_group_id:
            groups_by_key[group_key].musicbrainz_release_group_id = release_group_mbid

    # Step 2: Separate groups filtered out by min_tracks
    actionable_groups: list[MissingAlbumGroup] = []
    plans: list[LidarrAlbumPlan] = list(unresolved_plans)

    for group in groups_by_key.values():
        if len(group.tracks) < min_tracks:
            plans.append(
                LidarrAlbumPlan(
                    group=group,
                    status="skipped_min_tracks",
                    reason=f"Fewer than {min_tracks} track(s) ({len(group.tracks)})",
                )
            )
        else:
            actionable_groups.append(group)

    if not actionable_groups:
        return plans

    # Step 3: Fetch existing Lidarr library artists once
    existing_artists = client.get_artists()
    by_mbid: dict[str, dict[str, Any]] = {}
    by_name: dict[str, dict[str, Any]] = {}

    for a in existing_artists:
        mbid = a.get("foreignArtistId")
        if mbid:
            by_mbid[str(mbid)] = a
        name = a.get("artistName")
        if name:
            by_name[normalize_artist(name)] = a

    artist_albums_cache: dict[int, list[dict[str, Any]]] = {}

    def get_cached_albums(artist_id: int) -> list[dict[str, Any]]:
        if artist_id not in artist_albums_cache:
            artist_albums_cache[artist_id] = client.get_artist_albums(artist_id)
        return artist_albums_cache[artist_id]

    # Step 4: Look up album candidates in Lidarr
    for group in actionable_groups:
        target_norm_artist = normalize_artist(group.artist)
        target_norm_album = normalize_title(group.album or "")

        candidates: list[dict[str, Any]] = []

        if group.musicbrainz_release_group_id:
            try:
                candidates = client.lookup_album(f"lidarr:{group.musicbrainz_release_group_id}")
            except Exception as exc:
                logger.warning("Error looking up album by MBID '%s': %s", group.musicbrainz_release_group_id, exc)

        if not candidates:
            search_query = f"{group.artist} {group.album or ''}".strip()
            try:
                candidates = client.lookup_album(search_query)
            except Exception as exc:
                logger.warning("Error looking up album by query '%s': %s", search_query, exc)

        best_cand: dict[str, Any] | None = None
        best_score = 0.0

        for cand in candidates:
            cand_rg_id = cand.get("foreignAlbumId")
            if group.musicbrainz_release_group_id and cand_rg_id == group.musicbrainz_release_group_id:
                best_cand = cand
                best_score = 100.0
                break

            cand_title = cand.get("title") or ""
            cand_artist_obj = cand.get("artist") or {}
            cand_artist_name = (
                cand_artist_obj.get("artistName")
                if isinstance(cand_artist_obj, dict)
                else (cand.get("artistName") or "")
            )

            title_score = max(
                fuzz.token_sort_ratio(target_norm_album, normalize_title(cand_title)),
                fuzz.ratio(target_norm_album, normalize_title(cand_title)),
            )
            artist_score = max(
                fuzz.token_set_ratio(target_norm_artist, normalize_artist(cand_artist_name)),
                fuzz.token_sort_ratio(target_norm_artist, normalize_artist(cand_artist_name)),
            )
            combined = (title_score * 0.6) + (artist_score * 0.4)

            if combined > best_score:
                best_score = combined
                best_cand = cand

        if not best_cand or best_score < 75.0:
            plans.append(
                LidarrAlbumPlan(
                    group=group,
                    status="not_found",
                    reason=f"No matching album found in Lidarr (best score: {best_score:.1f}%)" if best_score > 0 else "No album candidates returned from Lidarr",
                    match_score=best_score,
                )
            )
            continue

        cand_artist_obj = best_cand.get("artist") if isinstance(best_cand.get("artist"), dict) else {}
        cand_foreign_artist_id = cand_artist_obj.get("foreignArtistId") or best_cand.get("foreignArtistId")
        cand_artist_name = cand_artist_obj.get("artistName") or best_cand.get("artistName") or group.artist
        cand_album_title = best_cand.get("title") or group.album
        cand_foreign_album_id = best_cand.get("foreignAlbumId")

        matched_existing_artist = None
        if cand_foreign_artist_id and str(cand_foreign_artist_id) in by_mbid:
            matched_existing_artist = by_mbid[str(cand_foreign_artist_id)]
        elif normalize_artist(cand_artist_name) in by_name:
            matched_existing_artist = by_name[normalize_artist(cand_artist_name)]

        if not matched_existing_artist:
            # Artist is not yet in Lidarr
            plan = LidarrAlbumPlan(
                group=group,
                status="new_artist_and_album",
                lidarr_album=best_cand,
                lidarr_artist=cand_artist_obj or None,
                resolved_artist_name=cand_artist_name,
                resolved_album_title=cand_album_title,
                match_score=best_score,
            )
        else:
            # Artist exists in Lidarr
            artist_id = matched_existing_artist["id"]
            existing_albums = get_cached_albums(artist_id)

            matched_existing_album = None
            for ea in existing_albums:
                if cand_foreign_album_id and ea.get("foreignAlbumId") == cand_foreign_album_id:
                    matched_existing_album = ea
                    break
                if normalize_title(ea.get("title", "")) == normalize_title(cand_album_title):
                    matched_existing_album = ea
                    break

            if matched_existing_album and matched_existing_album.get("monitored") is True:
                status = "already_monitored"
                target_album = matched_existing_album
            else:
                status = "existing_artist_unmonitored"
                target_album = matched_existing_album if matched_existing_album else best_cand

            plan = LidarrAlbumPlan(
                group=group,
                status=status,
                lidarr_album=target_album,
                lidarr_artist=matched_existing_artist,
                resolved_artist_name=matched_existing_artist.get("artistName", cand_artist_name),
                resolved_album_title=cand_album_title,
                match_score=best_score,
            )

        plans.append(plan)

    return plans


def apply_lidarr_plans(
    client: LidarrClient,
    plans: list[LidarrAlbumPlan],
    root_folder: str,
    quality_profile_id: int,
    metadata_profile_id: int,
    tag: str = "SpotifyMoverTool",
    trigger_search: bool = False,
    progress_callback: Callable[[int, int, LidarrAlbumPlan], None] | None = None,
) -> LidarrSyncSummary:
    """Safely apply approved Lidarr plans without downloading full discographies.

    - Adds missing artists with monitor=none and searchForMissingAlbums=False.
    - Tags artists with the configured tag (preserving pre-existing tags).
    - Monitored albums are explicitly updated via PUT /api/v1/album/monitor.
    - Optional single batched AlbumSearch command triggered at the end.
    """
    summary = LidarrSyncSummary()
    tag_id = client.ensure_tag(tag)

    created_artists_by_mbid: dict[str, dict[str, Any]] = {}
    monitored_album_ids: list[int] = []

    total_plans = len(plans)

    for idx, plan in enumerate(plans, start=1):
        if plan.status == "already_monitored":
            summary.already_monitored.append(
                f"{plan.resolved_artist_name or plan.group.artist} - {plan.resolved_album_title or plan.group.album}"
            )
            if progress_callback:
                progress_callback(idx, total_plans, plan)
            continue

        if plan.status == "skipped_min_tracks":
            summary.skipped.append(
                f"{plan.group.artist} - {plan.group.album or 'Unknown'} ({plan.reason})"
            )
            if progress_callback:
                progress_callback(idx, total_plans, plan)
            continue

        if plan.status == "not_found":
            summary.failed.append(
                (f"{plan.group.artist} - {plan.group.album or 'Unknown'}", plan.reason or "Not found")
            )
            if progress_callback:
                progress_callback(idx, total_plans, plan)
            continue

        # Case A: New Artist and Album
        if plan.status == "new_artist_and_album":
            try:
                artist_meta = plan.lidarr_artist or (plan.lidarr_album or {}).get("artist") or {}
                foreign_artist_id = artist_meta.get("foreignArtistId")

                if foreign_artist_id and str(foreign_artist_id) in created_artists_by_mbid:
                    artist = created_artists_by_mbid[str(foreign_artist_id)]
                else:
                    if not artist_meta.get("artistName") and foreign_artist_id:
                        looked_up = client.lookup_artist(f"lidarr:{foreign_artist_id}")
                        if looked_up:
                            artist_meta = looked_up[0]

                    artist_payload = dict(artist_meta)
                    artist_payload["rootFolderPath"] = root_folder
                    artist_payload["qualityProfileId"] = quality_profile_id
                    artist_payload["metadataProfileId"] = metadata_profile_id
                    artist_payload["monitored"] = True
                    artist_payload["tags"] = [tag_id]
                    artist_payload["addOptions"] = {
                        "monitor": "none",
                        "searchForMissingAlbums": False,
                    }
                    artist = client.add_artist(artist_payload)
                    if foreign_artist_id:
                        created_artists_by_mbid[str(foreign_artist_id)] = artist

                    artist_name = artist.get("artistName", plan.resolved_artist_name or "Unknown Artist")
                    summary.added_artists.append(artist_name)
                    if artist.get("id") and artist["id"] not in summary.tagged_artist_ids:
                        summary.tagged_artist_ids.append(artist["id"])

                artist_id = artist["id"]
                target_album_mbid = (plan.lidarr_album or {}).get("foreignAlbumId")
                target_album_id: int | None = None

                # Locate target album under artist
                albums = client.get_artist_albums(artist_id)
                for alb in albums:
                    if target_album_mbid and alb.get("foreignAlbumId") == target_album_mbid:
                        target_album_id = alb.get("id")
                        break
                    if normalize_title(alb.get("title", "")) == normalize_title(plan.resolved_album_title or ""):
                        target_album_id = alb.get("id")
                        break

                # Handle Lidarr async metadata population quirk if album not listed yet
                if not target_album_id:
                    for _ in range(3):
                        time.sleep(1.5)
                        albums = client.get_artist_albums(artist_id)
                        for alb in albums:
                            if target_album_mbid and alb.get("foreignAlbumId") == target_album_mbid:
                                target_album_id = alb.get("id")
                                break
                            if normalize_title(alb.get("title", "")) == normalize_title(plan.resolved_album_title or ""):
                                target_album_id = alb.get("id")
                                break
                        if target_album_id:
                            break

                # If still not found, add album explicitly
                if not target_album_id and plan.lidarr_album:
                    album_payload = dict(plan.lidarr_album)
                    album_payload["artistId"] = artist_id
                    album_payload["monitored"] = True
                    album_payload["addOptions"] = {"searchForNewAlbum": False}
                    added_album = client.add_album(album_payload)
                    target_album_id = added_album.get("id")

                if target_album_id:
                    client.monitor_albums([target_album_id], monitored=True)
                    monitored_album_ids.append(target_album_id)
                    summary.monitored_albums.append(
                        f"{plan.resolved_artist_name or artist.get('artistName')} - {plan.resolved_album_title}"
                    )
                else:
                    raise LidarrError(
                        f"Could not locate or add album '{plan.resolved_album_title}' for newly added artist '{artist.get('artistName')}'"
                    )

            except Exception as exc:
                logger.error("Failed to add new artist and album '%s': %s", plan.resolved_album_title, exc)
                summary.failed.append(
                    (f"{plan.resolved_artist_name or plan.group.artist} - {plan.resolved_album_title or plan.group.album}", str(exc))
                )

        # Case B: Existing Artist, Unmonitored Album
        elif plan.status == "existing_artist_unmonitored":
            try:
                existing_artist = plan.lidarr_artist or {}
                artist_id = existing_artist.get("id")
                if not artist_id:
                    raise LidarrError(f"Existing artist ID missing in plan for '{plan.resolved_artist_name}'")

                target_album_mbid = (plan.lidarr_album or {}).get("foreignAlbumId")
                target_album_id = (plan.lidarr_album or {}).get("id")

                if not target_album_id:
                    albums = client.get_artist_albums(artist_id)
                    for alb in albums:
                        if target_album_mbid and alb.get("foreignAlbumId") == target_album_mbid:
                            target_album_id = alb.get("id")
                            break
                        if normalize_title(alb.get("title", "")) == normalize_title(plan.resolved_album_title or ""):
                            target_album_id = alb.get("id")
                            break

                # Album not present on existing artist (e.g. Single/EP) -> add explicitly
                if not target_album_id and plan.lidarr_album:
                    album_payload = dict(plan.lidarr_album)
                    album_payload["artistId"] = artist_id
                    album_payload["monitored"] = True
                    album_payload["addOptions"] = {"searchForNewAlbum": False}
                    added_album = client.add_album(album_payload)
                    target_album_id = added_album.get("id")

                if target_album_id:
                    client.monitor_albums([target_album_id], monitored=True)
                    monitored_album_ids.append(target_album_id)

                    # Ensure existing artist has tag_id without overwriting existing tags
                    client.ensure_artist_tagged(existing_artist, tag_id)
                    if artist_id not in summary.tagged_artist_ids:
                        summary.tagged_artist_ids.append(artist_id)

                    summary.monitored_albums.append(
                        f"{plan.resolved_artist_name or existing_artist.get('artistName')} - {plan.resolved_album_title}"
                    )
                else:
                    raise LidarrError(
                        f"Could not locate or add album '{plan.resolved_album_title}' for existing artist '{existing_artist.get('artistName')}'"
                    )

            except Exception as exc:
                logger.error("Failed to monitor album for existing artist '%s': %s", plan.resolved_album_title, exc)
                summary.failed.append(
                    (f"{plan.resolved_artist_name or plan.group.artist} - {plan.resolved_album_title or plan.group.album}", str(exc))
                )

        if progress_callback:
            progress_callback(idx, total_plans, plan)

    # Step 4: Optional batched search trigger
    if trigger_search and monitored_album_ids:
        try:
            cmd = client.trigger_album_search(monitored_album_ids)
            summary.search_triggered = True
            if isinstance(cmd, dict) and "id" in cmd:
                summary.search_command_id = int(cmd["id"])
        except Exception as exc:
            logger.error("Failed to trigger batched Lidarr AlbumSearch: %s", exc)
            summary.failed.append(("Lidarr AlbumSearch Command", str(exc)))

    return summary
