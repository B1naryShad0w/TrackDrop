"""
Shared text processing utilities for TrackDrop.

This module consolidates duplicate string normalization and cleaning
functions from deezer_api.py and album_downloader.py.
"""

import re
from typing import Optional


def normalize_string(s: str) -> str:
    """
    Normalize strings for comparison by replacing special characters.

    Handles common character substitutions and removes non-alphanumeric characters.
    Used for fuzzy matching of artist names, titles, etc.

    Args:
        s: The string to normalize

    Returns:
        Normalized lowercase string with special chars replaced
    """
    if not s:
        return ""

    s = s.lower()
    # Common character substitutions
    s = s.replace(''', "'")
    s = s.replace(''', "'")
    s = s.replace('"', '"')
    s = s.replace('"', '"')
    s = s.replace('ø', 'o')
    s = s.replace('Ø', 'o')
    s = s.replace('é', 'e')
    s = s.replace('è', 'e')
    s = s.replace('ê', 'e')
    s = s.replace('ë', 'e')
    s = s.replace('à', 'a')
    s = s.replace('á', 'a')
    s = s.replace('â', 'a')
    s = s.replace('ñ', 'n')
    s = s.replace('ü', 'u')
    s = s.replace('ö', 'o')
    s = s.replace('ä', 'a')
    # Replace non-alphanumeric with space
    s = re.sub(r'\W+', ' ', s)
    return s.strip()


def clean_title(title: str) -> str:
    """
    Remove common suffixes from track titles to improve search accuracy.

    Removes content in parentheses/brackets and common suffixes like
    "(Official Video)", "(Live)", "(Remix)", etc.

    Args:
        title: The track title to clean

    Returns:
        Cleaned title with suffixes removed
    """
    if not title:
        return ""

    # Remove featuring info in parentheses or brackets
    title = re.sub(r'\s*\(feat\..*?\)', '', title, flags=re.IGNORECASE)
    title = re.sub(r'\s*\[feat\..*?\]', '', title, flags=re.IGNORECASE)
    title = re.sub(r'\s*\(ft\..*?\)', '', title, flags=re.IGNORECASE)
    title = re.sub(r'\s*\[ft\..*?\]', '', title, flags=re.IGNORECASE)

    # Remove all other parenthetical content
    title = re.sub(r'\s*\([^)]*\)', '', title)
    title = re.sub(r'\s*\[[^\]]*\]', '', title)

    # Remove common suffixes
    suffixes = [
        " (Official Music Video)", " (Official Video)", " (Live)", " (Remix)",
        " (Extended Mix)", " (Radio Edit)", " (Acoustic)", " (Instrumental)",
        " (Lyric Video)", " (Visualizer)", " (Audio)", " (Album Version)",
        " (Single Version)", " (Original Mix)", " - Remastered", " - Remaster",
        " (Remastered)", " [Remastered]", " - Single", " - EP",
    ]
    for suffix in suffixes:
        if title.lower().endswith(suffix.lower()):
            title = title[:-len(suffix)]

    return title.strip()


def sanitize_for_matching(s: str) -> str:
    """
    Sanitize strings for fuzzy matching in album/track comparisons.

    This is a more aggressive normalization than normalize_string(),
    designed for comparing album titles and track lists where we want
    to ignore minor differences.

    Args:
        s: The string to sanitize

    Returns:
        Sanitized string ready for comparison
    """
    if not s:
        return ""

    s = s.lower()
    s = s.replace(''', "'")
    s = s.replace(''', "'")

    # Remove disc/volume indicators
    s = re.sub(r'\s*[\(\[]?(?:disc|disk|cd|volume|vol\.?)\s*\d+[\)\]]?', '', s, flags=re.IGNORECASE)

    # Remove edition info
    s = re.sub(r'\s*[\(\[]?(?:deluxe|special|limited|expanded|anniversary|remaster(?:ed)?)\s*(?:edition|version)?[\)\]]?', '', s, flags=re.IGNORECASE)

    # Remove year in parentheses
    s = re.sub(r'\s*[\(\[]\d{4}[\)\]]', '', s)

    # Replace non-alphanumeric with space
    s = re.sub(r'\W+', ' ', s)

    # Normalize multiple spaces
    s = re.sub(r'\s+', ' ', s)

    return s.strip()


def sanitize_filename(filename: str) -> str:
    """
    Replace problematic characters in filenames with underscores.

    Args:
        filename: The filename to sanitize

    Returns:
        Sanitized filename safe for filesystem use
    """
    if not filename:
        return ""
    return re.sub(r'[\\/:*?"<>|]', '_', filename)


def extract_artist_names(artist_string: str) -> list:
    """
    Extract individual artist names from a combined artist string.

    Handles common separators like "&", "feat.", "featuring", "ft.", ",", "and".

    Args:
        artist_string: Combined artist string (e.g., "Artist A & Artist B feat. Artist C")

    Returns:
        List of individual artist names
    """
    if not artist_string:
        return []

    # Split by common separators
    # First handle featuring
    parts = re.split(r'\s*(?:feat\.?|featuring|ft\.?)\s*', artist_string, flags=re.IGNORECASE)

    all_artists = []
    for part in parts:
        # Split by other separators
        sub_parts = re.split(r'\s*[,&]\s*|\s+and\s+', part, flags=re.IGNORECASE)
        all_artists.extend([p.strip() for p in sub_parts if p.strip()])

    return all_artists


def strings_match(s1: str, s2: str, threshold: float = 0.9) -> bool:
    """
    Check if two strings match using normalized comparison.

    Args:
        s1: First string
        s2: Second string
        threshold: Similarity threshold (not used currently, for future fuzzy matching)

    Returns:
        True if strings match after normalization
    """
    return normalize_string(s1) == normalize_string(s2)


def artist_matches(search_artist: str, result_artist: str) -> bool:
    """
    Check if a search artist matches a result artist.

    Handles cases where the result might be a subset of the search
    (e.g., "Artist A" in "Artist A & Artist B").

    Args:
        search_artist: The artist name being searched for
        result_artist: The artist name from search results

    Returns:
        True if there's a match
    """
    search_norm = normalize_string(search_artist)
    result_norm = normalize_string(result_artist)

    # Direct match
    if search_norm == result_norm:
        return True

    # Check if result is contained in search or vice versa
    if search_norm in result_norm or result_norm in search_norm:
        return True

    # Check individual artists
    search_artists = set(normalize_string(a) for a in extract_artist_names(search_artist))
    result_artists = set(normalize_string(a) for a in extract_artist_names(result_artist))

    # Any overlap counts as a match
    return bool(search_artists & result_artists)
