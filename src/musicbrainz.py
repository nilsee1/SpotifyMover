"""MusicBrainz API client for resolving track album metadata."""

from __future__ import annotations

import logging
import re
import time
from typing import Any
from rapidfuzz import fuzz
import requests

from src import __version__
from src.matcher import normalize_artist, normalize_title

logger = logging.getLogger(__name__)

MUSICBRAINZ_API_URL = "https://musicbrainz.org/ws/2/recording/"
USER_AGENT = f"SpotifyMover/{__version__} (https://github.com/nilsee1/SpotifyMover)"


def _score_release(release: dict[str, Any]) -> float:
    """Calculate preference score bonus/penalty for a release.

    Prefers official releases and primary types Album, Single, EP over
    Compilation, Bootleg, or Soundtrack.
    """
    bonus = 0.0
    status = (release.get("status") or "").strip().lower()
    if status == "official":
        bonus += 5.0
    elif status == "bootleg":
        bonus -= 15.0

    rg = release.get("release-group") or {}
    p_type = (rg.get("primary-type") or rg.get("type") or "").strip().lower()
    raw_sec_types = rg.get("secondary-types") or []
    sec_types = [str(st).strip().lower() for st in raw_sec_types]

    if p_type in ("album", "single", "ep"):
        bonus += 5.0
    if "compilation" in sec_types or p_type == "compilation":
        bonus -= 10.0
    if "soundtrack" in sec_types or p_type == "soundtrack":
        bonus -= 5.0
    if "bootleg" in sec_types or p_type == "bootleg":
        bonus -= 15.0

    return bonus


class MusicBrainzResolver:
    """Resolves track metadata against MusicBrainz Web Service with rate limiting and caching."""

    def __init__(
        self,
        rate_limit_delay: float = 1.1,
        timeout: float = 10.0,
        session: requests.Session | None = None,
    ):
        self.rate_limit_delay = rate_limit_delay
        self.timeout = timeout
        self.session = session or requests.Session()
        self._last_request_time: float = 0.0
        self._cache: dict[tuple[str, str], tuple[str | None, str | None]] = {}

    def clear_cache(self) -> None:
        """Clear the in-memory lookup cache."""
        self._cache.clear()

    def _wait_for_rate_limit(self) -> None:
        """Enforce minimum interval between consecutive live HTTP requests."""
        if self.rate_limit_delay <= 0:
            return
        now = time.monotonic()
        elapsed = now - self._last_request_time
        if elapsed < self.rate_limit_delay:
            time.sleep(self.rate_limit_delay - elapsed)
        self._last_request_time = time.monotonic()

    def resolve_album(self, artist: str, title: str) -> tuple[str | None, str | None]:
        """Resolve (album_title, release_group_mbid) for given artist and track title.

        Returns (None, None) if no confident match is found or an error occurs.
        """
        norm_art = normalize_artist(artist)
        norm_tit = normalize_title(title)

        if not norm_art or not norm_tit:
            return None, None

        cache_key = (norm_art, norm_tit)
        if cache_key in self._cache:
            return self._cache[cache_key]

        # Clean strings for Lucene query
        clean_tit = re.sub(r'["\\]', '', norm_tit).strip()
        clean_art = re.sub(r'["\\]', '', norm_art).strip()
        lucene_query = f'recording:"{clean_tit}" AND artist:"{clean_art}"'

        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        }
        params = {
            "query": lucene_query,
            "fmt": "json",
            "limit": 5,
        }

        self._wait_for_rate_limit()

        try:
            response = self.session.get(
                MUSICBRAINZ_API_URL,
                params=params,
                headers=headers,
                timeout=self.timeout,
            )
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            logger.warning(
                "MusicBrainz resolution failed for '%s - %s': %s",
                artist,
                title,
                exc,
            )
            self._cache[cache_key] = (None, None)
            return None, None

        recordings = data.get("recordings", [])
        if not recordings:
            self._cache[cache_key] = (None, None)
            return None, None

        best_match: tuple[str, str] | None = None
        best_total_score = -1.0

        for rec in recordings:
            rec_title = rec.get("title") or ""
            rec_norm_title = normalize_title(rec_title)

            credits = rec.get("artist-credit") or []
            if isinstance(credits, list):
                credit_names = []
                for c in credits:
                    if isinstance(c, dict):
                        name = c.get("name") or (c.get("artist", {}).get("name") if isinstance(c.get("artist"), dict) else "")
                        if name:
                            credit_names.append(name)
                rec_artist_str = " ".join(credit_names)
            else:
                rec_artist_str = str(credits)
            rec_norm_artist = normalize_artist(rec_artist_str)

            title_score = max(
                fuzz.token_sort_ratio(norm_tit, rec_norm_title),
                fuzz.ratio(norm_tit, rec_norm_title),
            )
            artist_score = max(
                fuzz.token_set_ratio(norm_art, rec_norm_artist),
                fuzz.token_sort_ratio(norm_art, rec_norm_artist),
            )
            base_score = (title_score * 0.6) + (artist_score * 0.4)

            # Recordings must meet confidence threshold
            if base_score < 80.0:
                continue

            releases = rec.get("releases") or []
            for rel in releases:
                rel_title = rel.get("title")
                rg = rel.get("release-group") or {}
                rg_id = rg.get("id")
                if not rel_title or not rg_id:
                    continue

                rel_bonus = _score_release(rel)
                candidate_score = base_score + rel_bonus
                if candidate_score > best_total_score:
                    best_total_score = candidate_score
                    best_match = (str(rel_title), str(rg_id))

        result = best_match if best_match else (None, None)
        self._cache[cache_key] = result
        return result


_DEFAULT_RESOLVER = MusicBrainzResolver()


def resolve_album(artist: str, title: str) -> tuple[str | None, str | None]:
    """Module-level helper to resolve track album metadata using the default resolver."""
    return _DEFAULT_RESOLVER.resolve_album(artist, title)
