"""Track normalization and fuzzy matching logic using rapidfuzz."""

from __future__ import annotations

import logging
import re
from typing import Any, Callable
import unicodedata
from rapidfuzz import fuzz

from src.models import MatchResult, Track
from src.navidrome import NavidromeClient

logger = logging.getLogger(__name__)

# Noise keywords for parenthesis/bracket removal
NOISE_PAREN_REGEX = re.compile(
    r"[\(\[][^\)\]]*(?:"
    r"remaster(?:ed)?|"
    r"deluxe|"
    r"radio\s+edit|"
    r"single\s+version|"
    r"album\s+version|"
    r"explicit|"
    r"feat\.?|"
    r"ft\.?|"
    r"featuring|"
    r"bonus(?:\s+track)?|"
    r"live(?:\s+at|\s+on)?|"
    r"version|"
    r"edition|"
    r"anniversary|"
    r"original\s+mix|"
    r"mono|"
    r"stereo|"
    r"mix"
    r")[^\)\]]*[\)\]]",
    re.IGNORECASE,
)

# Noise after a hyphen (e.g., " - 2011 Remaster", " - Radio Edit")
NOISE_HYPHEN_REGEX = re.compile(
    r"\s+-\s+.*(?:"
    r"remaster(?:ed)?|"
    r"deluxe|"
    r"radio\s+edit|"
    r"single\s+version|"
    r"album\s+version|"
    r"explicit|"
    r"feat\.?|"
    r"ft\.?|"
    r"featuring|"
    r"bonus|"
    r"live|"
    r"version|"
    r"edition|"
    r"anniversary|"
    r"mix"
    r").*$",
    re.IGNORECASE,
)

# Standalone featuring clauses (e.g., "Song feat. Artist")
NOISE_FEAT_REGEX = re.compile(
    r"\b(?:feat\.?|ft\.?|featuring)\b.*$",
    re.IGNORECASE,
)


def strip_diacritics(text: str) -> str:
    """Normalize unicode and strip combining diacritics/accents."""
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(c for c in normalized if not unicodedata.combining(c))


def normalize_title(title: str) -> str:
    """Clean and normalize track title strictly for comparison.

    - Lowercase and strip diacritics
    - Remove common noise tags in parentheses/brackets (e.g. remastered, deluxe, feat.)
    - Remove common noise tags after hyphens
    - Remove trailing feat./ft. clauses
    - Strip punctuation and collapse whitespace
    """
    if not title:
        return ""

    # 1. Unicode decomposition and lowercasing
    text = strip_diacritics(title).lower()

    # 2. Remove noise tags in parentheses or brackets
    text = NOISE_PAREN_REGEX.sub(" ", text)

    # 3. Remove noise tags after hyphens
    text = NOISE_HYPHEN_REGEX.sub(" ", text)

    # 4. Remove standalone feat/ft clauses
    text = NOISE_FEAT_REGEX.sub(" ", text)

    # 5. Replace non-alphanumeric characters with spaces
    text = re.sub(r"[^\w\s]", " ", text)

    # 6. Collapse multiple whitespaces
    cleaned = re.sub(r"\s+", " ", text).strip()

    # Fallback to stripped original lowercased if cleaning emptied the title
    if not cleaned:
        fallback = re.sub(r"[^\w\s]", " ", strip_diacritics(title).lower())
        return re.sub(r"\s+", " ", fallback).strip()

    return cleaned


def normalize_artist(artist: str) -> str:
    """Clean and normalize artist name strictly for comparison."""
    if not artist:
        return ""

    text = strip_diacritics(artist).lower()
    # Remove featuring clauses from artist field if present
    text = NOISE_FEAT_REGEX.sub(" ", text)
    # Replace symbols like '&' with space, strip punctuation
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def parse_duration_seconds(raw_duration: Any) -> float | None:
    """Safely parse duration into seconds.

    Navidrome/Subsonic returns duration in seconds.
    If value is > 10,000, it is likely in milliseconds.
    """
    if raw_duration is None:
        return None
    try:
        val = float(raw_duration)
        if val <= 0:
            return None
        return val / 1000.0 if val > 10000 else val
    except (ValueError, TypeError):
        return None


def score_track_match(track: Track, candidate: dict[str, Any]) -> float:
    """Calculate weighted fuzzy match score between a Track and a Navidrome candidate.

    Weighted title (60%) + artist (40%) + duration bonus (+5 if <= 5s diff).
    """
    cand_title = candidate.get("title", "")
    cand_artist = candidate.get("artist", "")

    norm_target_title = normalize_title(track.title)
    norm_cand_title = normalize_title(cand_title)

    # 1. Title similarity
    title_token_sort = fuzz.token_sort_ratio(norm_target_title, norm_cand_title)
    title_ratio = fuzz.ratio(norm_target_title, norm_cand_title)
    title_score = max(title_token_sort, title_ratio)

    # 2. Artist similarity
    norm_cand_artist = normalize_artist(cand_artist)
    artist_scores: list[float] = []

    target_artists = track.artists if track.artists else ([track.primary_artist] if track.primary_artist else [])
    if target_artists:
        for artist in target_artists:
            norm_art = normalize_artist(artist)
            if norm_art:
                artist_scores.append(fuzz.token_set_ratio(norm_art, norm_cand_artist))
                artist_scores.append(fuzz.token_sort_ratio(norm_art, norm_cand_artist))
        # Also check combined artist string
        combined_art = normalize_artist(" ".join(target_artists))
        artist_scores.append(fuzz.token_set_ratio(combined_art, norm_cand_artist))
        artist_scores.append(fuzz.token_sort_ratio(combined_art, norm_cand_artist))

    artist_score = max(artist_scores) if artist_scores else 50.0

    # Weighted base score: 60% title, 40% artist
    base_score = (title_score * 0.6) + (artist_score * 0.4)

    # 3. Duration bonus (+5 capped at 100 if within 5 seconds)
    target_dur_s = (track.duration_ms / 1000.0) if track.duration_ms else None
    cand_dur_s = parse_duration_seconds(candidate.get("duration"))

    if target_dur_s is not None and cand_dur_s is not None:
        diff_s = abs(target_dur_s - cand_dur_s)
        if diff_s <= 5.0:
            base_score = min(100.0, base_score + 5.0)

    return round(base_score, 2)


def match_track(
    client: NavidromeClient,
    track: Track,
    threshold: float = 80.0,
) -> MatchResult:
    """Find the best matching track in Navidrome using search strategy and fuzzy scoring.

    Search strategy:
      1. Query Navidrome search3 with "{primary_artist} {clean_title}"
      2. If no candidates pass threshold, run fallback query with "{clean_title}" only
      3. Deduplicate candidate songs by Subsonic id
    """
    clean_title_str = normalize_title(track.title)
    clean_artist_str = normalize_artist(track.primary_artist)

    # Query 1: primary artist + clean title
    if clean_artist_str:
        primary_query = f"{clean_artist_str} {clean_title_str}".strip()
    else:
        primary_query = clean_title_str

    candidates_by_id: dict[str, dict[str, Any]] = {}

    try:
        results_primary = client.search_songs(primary_query, count=20)
        for song in results_primary:
            sid = song.get("id")
            if sid:
                candidates_by_id[str(sid)] = song
    except Exception as exc:
        logger.warning("Error during primary search for '%s': %s", primary_query, exc)

    # Score primary candidates
    best_candidate: dict[str, Any] | None = None
    best_score = 0.0

    for song in candidates_by_id.values():
        score = score_track_match(track, song)
        if score > best_score:
            best_score = score
            best_candidate = song

    # If no candidate passes the threshold, run fallback query with clean_title only
    if best_score < threshold and clean_title_str and clean_title_str != primary_query:
        try:
            results_fallback = client.search_songs(clean_title_str, count=20)
            for song in results_fallback:
                sid = song.get("id")
                if sid and str(sid) not in candidates_by_id:
                    candidates_by_id[str(sid)] = song
                    score = score_track_match(track, song)
                    if score > best_score:
                        best_score = score
                        best_candidate = song
        except Exception as exc:
            logger.warning("Error during fallback search for '%s': %s", clean_title_str, exc)

    if best_candidate and best_score >= threshold:
        cand_dur_s = parse_duration_seconds(best_candidate.get("duration"))
        cand_dur_ms = int(cand_dur_s * 1000) if cand_dur_s is not None else None
        return MatchResult(
            track=track,
            matched=True,
            navidrome_id=str(best_candidate.get("id")),
            navidrome_title=best_candidate.get("title"),
            navidrome_artist=best_candidate.get("artist"),
            navidrome_duration_ms=cand_dur_ms,
            score=best_score,
            matched_song=best_candidate,
        )

    return MatchResult(
        track=track,
        matched=False,
        score=best_score,
        matched_song=best_candidate,
    )


def match_playlist_tracks(
    client: NavidromeClient,
    tracks: list[Track],
    threshold: float = 80.0,
    progress_callback: Callable[[int, int, Track, MatchResult], None] | None = None,
) -> list[MatchResult]:
    """Match a list of tracks against Navidrome with optional progress callback."""
    results: list[MatchResult] = []
    total = len(tracks)

    for idx, track in enumerate(tracks, start=1):
        result = match_track(client, track, threshold=threshold)
        results.append(result)
        if progress_callback:
            progress_callback(idx, total, track, result)

    return results
