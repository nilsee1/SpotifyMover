"""Track normalization and fuzzy matching logic using rapidfuzz."""

from __future__ import annotations

import concurrent.futures
import logging
import re
import threading
from typing import Any, Callable
import unicodedata
from rapidfuzz import fuzz

from src.models import MatchResult, Track
from src.navidrome import NavidromeClient

logger = logging.getLogger(__name__)

# Preserved recording-level descriptors that must NOT be stripped:
PRESERVED_TERMS_REGEX = re.compile(
    r"\b(?:live|acoustic|remix|instrumental|karaoke|unplugged|demo)\b",
    re.IGNORECASE,
)

# Refined packaging / release noise terms:
# - remaster(?:ed)? (including year prefixes like 2011 Remaster, Remastered 2009)
# - deluxe(?:\s+edition)?, expanded(?:\s+edition)?, special\s+edition, anniversary(?:\s+edition)?, collector'?s?\s+edition, standard\s+edition
# - radio\s+edit, single\s+version, album\s+version, original\s+mix
# - explicit, clean
# - feat\.?, ft\.?, featuring
# - bonus(?:\s+track)?
# - mono, stereo
NOISE_TERMS_PATTERN = (
    r"remaster(?:ed)?"
    r"|deluxe(?:\s+edition)?"
    r"|expanded(?:\s+edition)?"
    r"|special\s+edition"
    r"|anniversary(?:\s+edition)?"
    r"|collector'?s?\s+edition"
    r"|standard\s+edition"
    r"|radio\s+edit"
    r"|single\s+version"
    r"|album\s+version"
    r"|original\s+mix"
    r"|explicit"
    r"|clean"
    r"|feat\.?"
    r"|ft\.?"
    r"|featuring"
    r"|bonus(?:\s+track)?"
    r"|mono"
    r"|stereo"
)

# Noise keywords for parenthesis/bracket removal
NOISE_PAREN_REGEX = re.compile(
    rf"[\(\[][^\)\]]*(?:{NOISE_TERMS_PATTERN})[^\)\]]*[\)\]]",
    re.IGNORECASE,
)

# Noise after a hyphen: only strip trailing hyphenated metadata matching refined noise terms
NOISE_HYPHEN_REGEX = re.compile(
    rf"\s+-\s+[^-]*(?:{NOISE_TERMS_PATTERN})[^-]*$",
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
    - Strip apostrophes before replacing remaining non-alphanumeric characters with spaces
    - Strip punctuation and collapse whitespace
    """
    if not title:
        return ""

    # 1. Unicode decomposition and lowercasing
    text = strip_diacritics(title).lower()

    # 2. Remove noise tags in parentheses or brackets (unless preserved descriptor is inside)
    def _clean_paren(match: re.Match[str]) -> str:
        content = match.group(0)
        if PRESERVED_TERMS_REGEX.search(content):
            return content
        return " "

    text = NOISE_PAREN_REGEX.sub(_clean_paren, text)

    # 3. Remove noise tags after hyphens (unless preserved descriptor is inside)
    def _clean_hyphen(match: re.Match[str]) -> str:
        content = match.group(0)
        if PRESERVED_TERMS_REGEX.search(content):
            return content
        return " "

    text = NOISE_HYPHEN_REGEX.sub(_clean_hyphen, text)

    # 4. Remove standalone feat/ft clauses
    text = NOISE_FEAT_REGEX.sub(" ", text)

    # 5. Strip apostrophes (' , ’ , `) before replacing remaining non-alphanumerics with spaces
    text = re.sub(r"['’`]", "", text)

    # 6. Replace remaining non-alphanumeric characters with spaces
    text = re.sub(r"[^\w\s]", " ", text)

    # 7. Collapse multiple whitespaces
    cleaned = re.sub(r"\s+", " ", text).strip()

    # Fallback to stripped original lowercased if cleaning emptied the title
    if not cleaned:
        fallback = re.sub(r"['’`]", "", strip_diacritics(title).lower())
        fallback = re.sub(r"[^\w\s]", " ", fallback)
        return re.sub(r"\s+", " ", fallback).strip()

    return cleaned


def normalize_artist(artist: str) -> str:
    """Clean and normalize artist name strictly for comparison."""
    if not artist:
        return ""

    text = strip_diacritics(artist).lower()
    # Remove featuring clauses from artist field if present
    text = NOISE_FEAT_REGEX.sub(" ", text)
    # Strip apostrophes before replacing remaining non-alphanumerics with spaces
    text = re.sub(r"['’`]", "", text)
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

    Weighted title (60%) + artist (40%) + duration bonus/penalty + album tie-breaker.
    Clamped to [0.0, 100.0].
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

    # Split cand_artist on common multi-artist delimiters into individual candidate artist segments
    cand_segments_raw = re.split(r"[;,]|&|\b(?:feat\.?|ft\.?|featuring)\b", cand_artist, flags=re.IGNORECASE)
    cand_segments = [normalize_artist(seg) for seg in cand_segments_raw if normalize_artist(seg)]

    target_artists = track.artists if track.artists else ([track.primary_artist] if track.primary_artist else [])
    norm_primary_artist = normalize_artist(track.primary_artist)

    artist_scores: list[float] = []

    if target_artists:
        # Score primary_artist against norm_cand_artist
        if norm_primary_artist and norm_cand_artist:
            artist_scores.append(
                max(
                    fuzz.token_sort_ratio(norm_primary_artist, norm_cand_artist),
                    fuzz.ratio(norm_primary_artist, norm_cand_artist),
                )
            )

        # Score combined_art (" ".join(target_artists)) against norm_cand_artist
        combined_art = normalize_artist(" ".join(target_artists))
        if combined_art and norm_cand_artist:
            artist_scores.append(
                max(
                    fuzz.token_sort_ratio(combined_art, norm_cand_artist),
                    fuzz.ratio(combined_art, norm_cand_artist),
                )
            )

        # Score primary_artist against individual candidate segments
        if norm_primary_artist:
            for seg in cand_segments:
                artist_scores.append(
                    max(
                        fuzz.token_sort_ratio(norm_primary_artist, seg),
                        fuzz.ratio(norm_primary_artist, seg),
                    )
                )

        # For secondary artists (index >= 1), evaluate against candidate segments with a 0.85 discount
        if len(target_artists) > 1:
            for sec_art in target_artists[1:]:
                norm_sec = normalize_artist(sec_art)
                if not norm_sec:
                    continue
                for seg in cand_segments:
                    score = max(
                        fuzz.token_sort_ratio(norm_sec, seg),
                        fuzz.ratio(norm_sec, seg),
                    )
                    artist_scores.append(score * 0.85)
                if norm_cand_artist:
                    score = max(
                        fuzz.token_sort_ratio(norm_sec, norm_cand_artist),
                        fuzz.ratio(norm_sec, norm_cand_artist),
                    )
                    artist_scores.append(score * 0.85)

    artist_score = max(artist_scores) if artist_scores else 50.0

    # Weighted base score: 60% title, 40% artist
    base_score = (title_score * 0.6) + (artist_score * 0.4)

    # 3. Duration bonus and penalty
    target_dur_s = (track.duration_ms / 1000.0) if track.duration_ms else None
    cand_dur_s = parse_duration_seconds(candidate.get("duration"))

    if target_dur_s is not None and cand_dur_s is not None:
        diff_s = abs(target_dur_s - cand_dur_s)
        if diff_s <= 5.0:
            base_score += 5.0
        elif 5.0 < diff_s <= 15.0:
            pass  # neutral
        elif 15.0 < diff_s <= 30.0:
            base_score -= 5.0
        elif 30.0 < diff_s <= 60.0:
            base_score -= 10.0
        else:  # diff_s > 60.0
            base_score -= 15.0

    # 4. Album tie-breaker bonus
    cand_album = candidate.get("album")
    if track.album and cand_album:
        norm_track_album = normalize_title(track.album)
        norm_cand_album = normalize_title(str(cand_album))
        if norm_track_album and norm_cand_album:
            album_sim = fuzz.token_sort_ratio(norm_track_album, norm_cand_album)
            if album_sim >= 85.0:
                base_score += 2.0

    # Clamp result to [0.0, 100.0]
    base_score = max(0.0, min(100.0, base_score))
    return round(base_score, 2)


def match_track(
    client: NavidromeClient,
    track: Track,
    threshold: float = 80.0,
    search_cache: dict[tuple[str, int], list[dict[str, Any]]] | None = None,
    cache_lock: threading.Lock | None = None,
    in_flight: dict[tuple[str, int], threading.Event] | None = None,
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

    def _search(query_str: str, count: int = 20) -> list[dict[str, Any]]:
        if not query_str or not query_str.strip():
            return []
        cleaned_query = query_str.strip()
        key = (cleaned_query, count)

        if search_cache is not None and cache_lock is not None:
            while True:
                with cache_lock:
                    if key in search_cache:
                        return search_cache[key]
                    if in_flight is not None:
                        if key in in_flight:
                            evt = in_flight[key]
                        else:
                            evt = None
                            in_flight[key] = threading.Event()
                    else:
                        evt = None

                if evt is not None:
                    evt.wait()
                    continue
                else:
                    break

            try:
                res = client.search_songs(cleaned_query, count=count)
                with cache_lock:
                    search_cache[key] = res
                return res
            finally:
                if in_flight is not None:
                    with cache_lock:
                        done_evt = in_flight.pop(key, None)
                        if done_evt is not None:
                            done_evt.set()

        elif search_cache is not None:
            if key in search_cache:
                return search_cache[key]
            res = client.search_songs(cleaned_query, count=count)
            search_cache[key] = res
            return res
        else:
            return client.search_songs(cleaned_query, count=count)

    try:
        results_primary = _search(primary_query, count=20)
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
            results_fallback = _search(clean_title_str, count=20)
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
    max_workers: int = 4,
) -> list[MatchResult]:
    """Match a list of tracks against Navidrome with optional progress callback and concurrency.

    Args:
        client: NavidromeClient instance.
        tracks: List of input tracks to match.
        threshold: Fuzzy score threshold (0-100) to consider a track matched.
        progress_callback: Optional callback(completed_count, total, track, result).
        max_workers: Concurrency worker count. If <= 1, executes sequentially.
    """
    total = len(tracks)
    if total == 0:
        return []

    search_cache: dict[tuple[str, int], list[dict[str, Any]]] = {}
    in_flight: dict[tuple[str, int], threading.Event] = {}
    cache_lock = threading.Lock()

    if max_workers <= 1:
        results: list[MatchResult] = []
        for idx, track in enumerate(tracks, start=1):
            result = match_track(
                client=client,
                track=track,
                threshold=threshold,
                search_cache=search_cache,
                cache_lock=cache_lock,
                in_flight=in_flight,
            )
            results.append(result)
            if progress_callback:
                progress_callback(idx, total, track, result)
        return results

    # Concurrent matching with ThreadPoolExecutor
    ordered_results: list[MatchResult | None] = [None] * total
    completed_count = 0

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_track_info = {
            executor.submit(
                match_track,
                client,
                track,
                threshold,
                search_cache,
                cache_lock,
                in_flight,
            ): (idx, track)
            for idx, track in enumerate(tracks)
        }

        for future in concurrent.futures.as_completed(future_to_track_info):
            idx, track = future_to_track_info[future]
            result = future.result()
            ordered_results[idx] = result
            completed_count += 1
            if progress_callback:
                progress_callback(completed_count, total, track, result)

    return [r for r in ordered_results if r is not None]
