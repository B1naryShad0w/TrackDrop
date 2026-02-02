"""Playlist URL download support.

Extracts tracklists from playlist URLs (Deezer, Spotify, Tidal, YouTube),
downloads each track individually via the existing TrackDownloader pipeline,
skips tracks already in Navidrome, creates a Navidrome playlist, and
writes a download history file for cleanup.
"""

import asyncio
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from typing import List, Optional, Dict

import requests

from config import TEMP_DOWNLOAD_FOLDER, MUSIC_LIBRARY_PATH
from downloaders.track_downloader import TrackDownloader
from apis.navidrome_api import NavidromeAPI
from utils import Tagger, update_status_file


PLAYLIST_HISTORY_DIR = os.getenv("RECOMMAND_PLAYLIST_HISTORY_DIR", "/app/data/playlist_history")


# ---------------------------------------------------------------------------
# Playlist track extraction
# ---------------------------------------------------------------------------

def _extract_deezer_playlist_tracks(playlist_id: str) -> tuple[str, List[dict]]:
    """Fetch tracks from a public Deezer playlist.
    Returns (playlist_name, [{'artist': ..., 'title': ..., 'deezer_id': ...}, ...])
    """
    url = f"https://api.deezer.com/playlist/{playlist_id}"
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    playlist_name = data.get("title", f"Deezer Playlist {playlist_id}")
    tracks = []
    tracks_data = data.get("tracks", {}).get("data", [])
    # Handle pagination
    next_url = data.get("tracks", {}).get("next")
    while next_url:
        resp = requests.get(next_url, timeout=15)
        resp.raise_for_status()
        page = resp.json()
        tracks_data.extend(page.get("data", []))
        next_url = page.get("next")

    for t in tracks_data:
        tracks.append({
            "artist": t.get("artist", {}).get("name", "Unknown"),
            "title": t.get("title", "Unknown"),
            "deezer_id": str(t.get("id", "")),
        })
    return playlist_name, tracks


def _get_spotify_client_token() -> Optional[str]:
    """Get a Spotify access token using Client Credentials flow.
    Requires SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET env vars or config."""
    import base64
    client_id = os.getenv("SPOTIFY_CLIENT_ID", "") or getattr(__import__("config"), "SPOTIFY_CLIENT_ID", "")
    client_secret = os.getenv("SPOTIFY_CLIENT_SECRET", "") or getattr(__import__("config"), "SPOTIFY_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        print("Spotify Client Credentials not configured. "
              "Set SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET to enable Spotify playlist support.",
              file=sys.stderr)
        return None
    try:
        auth = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        resp = requests.post(
            "https://accounts.spotify.com/api/token",
            headers={
                "Authorization": f"Basic {auth}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={"grant_type": "client_credentials"},
            timeout=10,
        )
        if resp.status_code == 200:
            return resp.json().get("access_token")
        print(f"Spotify token request failed ({resp.status_code}): {resp.text[:200]}", file=sys.stderr)
    except Exception as e:
        print(f"Failed to get Spotify client token: {e}", file=sys.stderr)
    return None


def _extract_spotify_playlist_tracks(playlist_id: str) -> tuple[str, List[dict]]:
    """Fetch tracks from a public Spotify playlist using Client Credentials.
    Returns (playlist_name, [{'artist': ..., 'title': ...}, ...])
    """
    token = _get_spotify_client_token()
    if not token:
        return f"Spotify Playlist {playlist_id}", []

    headers = {"Authorization": f"Bearer {token}"}
    url = f"https://api.spotify.com/v1/playlists/{playlist_id}"
    params = {"fields": "name,tracks.items(track(name,artists(name))),tracks.next"}
    resp = requests.get(url, headers=headers, params=params, timeout=15)
    if resp.status_code != 200:
        print(f"Spotify API returned {resp.status_code}: {resp.text[:200]}", file=sys.stderr)
        return f"Spotify Playlist {playlist_id}", []

    data = resp.json()
    playlist_name = data.get("name", f"Spotify Playlist {playlist_id}")
    tracks = []
    items = data.get("tracks", {}).get("items", [])
    next_url = data.get("tracks", {}).get("next")

    while next_url:
        resp = requests.get(next_url, headers=headers, timeout=15)
        if resp.status_code != 200:
            break
        page = resp.json()
        items.extend(page.get("items", []))
        next_url = page.get("next")

    for item in items:
        track = item.get("track")
        if not track:
            continue
        artists = [a["name"] for a in track.get("artists", []) if a.get("name")]
        artist_str = ", ".join(artists) if artists else "Unknown"
        tracks.append({
            "artist": artist_str,
            "title": track.get("name", "Unknown"),
        })
    return playlist_name, tracks


def _extract_youtube_playlist_tracks(playlist_id: str) -> tuple[str, List[dict]]:
    """Extract tracks from a YouTube/YouTube Music playlist using yt-dlp.
    Returns (playlist_name, [{'artist': ..., 'title': ..., 'youtube_url': ...}, ...])
    """
    url = f"https://www.youtube.com/playlist?list={playlist_id}"
    try:
        result = subprocess.run(
            ["yt-dlp", "--flat-playlist", "--dump-json", "--no-warnings", url],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            print(f"yt-dlp error: {result.stderr[:500]}", file=sys.stderr)
            return f"YouTube Playlist {playlist_id}", []
    except FileNotFoundError:
        print("yt-dlp not found. Cannot extract YouTube playlists.", file=sys.stderr)
        return f"YouTube Playlist {playlist_id}", []
    except subprocess.TimeoutExpired:
        print("yt-dlp timed out extracting YouTube playlist.", file=sys.stderr)
        return f"YouTube Playlist {playlist_id}", []

    tracks = []
    playlist_name = f"YouTube Playlist {playlist_id}"
    for line in result.stdout.strip().split("\n"):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        # yt-dlp flat-playlist entries have 'title', 'url' or 'id'
        title = entry.get("title", "")
        video_id = entry.get("id", entry.get("url", ""))
        # Try to parse "Artist - Title" from the video title
        artist, track_title = _parse_artist_title(title)
        video_url = f"https://www.youtube.com/watch?v={video_id}" if video_id and not video_id.startswith("http") else video_id
        tracks.append({
            "artist": artist,
            "title": track_title,
            "youtube_url": video_url,
        })
        # Use playlist title from first entry if available
        if entry.get("playlist_title"):
            playlist_name = entry["playlist_title"]
    return playlist_name, tracks


def _parse_artist_title(raw_title: str) -> tuple[str, str]:
    """Try to split 'Artist - Title' from a YouTube video title.
    Falls back to ('Unknown', raw_title) if no separator found."""
    # Common separators: " - ", " – ", " — "
    for sep in [" - ", " – ", " — ", " | "]:
        if sep in raw_title:
            parts = raw_title.split(sep, 1)
            return parts[0].strip(), parts[1].strip()
    return "Unknown", raw_title.strip()


def _get_tidal_client_token() -> Optional[str]:
    """Get a Tidal access token using Client Credentials flow.
    Requires TIDAL_CLIENT_ID and TIDAL_CLIENT_SECRET."""
    import base64
    client_id = os.getenv("TIDAL_CLIENT_ID", "") or getattr(__import__("config"), "TIDAL_CLIENT_ID", "")
    client_secret = os.getenv("TIDAL_CLIENT_SECRET", "") or getattr(__import__("config"), "TIDAL_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        print("Tidal Client Credentials not configured. "
              "Set TIDAL_CLIENT_ID and TIDAL_CLIENT_SECRET to enable Tidal playlist support.",
              file=sys.stderr)
        return None
    try:
        auth = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        resp = requests.post(
            "https://auth.tidal.com/v1/oauth2/token",
            headers={
                "Authorization": f"Basic {auth}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={"grant_type": "client_credentials"},
            timeout=10,
        )
        if resp.status_code == 200:
            return resp.json().get("access_token")
        print(f"Tidal token request failed ({resp.status_code}): {resp.text[:200]}", file=sys.stderr)
    except Exception as e:
        print(f"Failed to get Tidal client token: {e}", file=sys.stderr)
    return None


def _extract_tidal_playlist_tracks(playlist_uuid: str) -> tuple[str, List[dict]]:
    """Fetch tracks from a Tidal playlist.

    Strategy:
    1. Try the Tidal embed page — it loads track data in the HTML/JS
       without authentication.
    2. Fall back to the Developer API (client credentials + v1) if
       embed scraping fails and credentials are configured.

    Returns (playlist_name, [{'artist': ..., 'title': ...}, ...])
    """
    # --- Strategy 1: Embed page scraping (no auth needed) ---
    tracks, name = _tidal_embed_extract(playlist_uuid)
    if tracks:
        return name, tracks

    # --- Strategy 2: Developer API with client credentials ---
    token = _get_tidal_client_token()
    if not token:
        return f"Tidal Playlist {playlist_uuid}", []

    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    try:
        resp = requests.get(
            f"https://api.tidal.com/v1/playlists/{playlist_uuid}",
            headers=headers, params={"countryCode": "US"}, timeout=15,
        )
        if resp.status_code != 200:
            print(f"Tidal API playlist failed ({resp.status_code}): {resp.text[:500]}", file=sys.stderr)
            return f"Tidal Playlist {playlist_uuid}", []

        pl_data = resp.json()
        playlist_name = pl_data.get("title", f"Tidal Playlist {playlist_uuid}")
        total_tracks = pl_data.get("numberOfTracks", 0)
        tracks = []
        offset = 0
        limit = 100
        while offset < max(total_tracks, 1):
            tresp = requests.get(
                f"https://api.tidal.com/v1/playlists/{playlist_uuid}/tracks",
                headers=headers,
                params={"countryCode": "US", "limit": limit, "offset": offset},
                timeout=15,
            )
            if tresp.status_code != 200:
                print(f"Tidal API playlist tracks failed ({tresp.status_code}): {tresp.text[:500]}", file=sys.stderr)
                break
            items = tresp.json().get("items", [])
            if not items:
                break
            for t in items:
                artists = [a.get("name", "") for a in t.get("artists", []) if a.get("name")]
                artist_str = ", ".join(artists) if artists else t.get("artist", {}).get("name", "Unknown")
                tracks.append({"artist": artist_str, "title": t.get("title", "Unknown")})
            offset += limit
        return playlist_name, tracks
    except Exception as e:
        print(f"Failed to extract Tidal playlist: {e}", file=sys.stderr)
        return f"Tidal Playlist {playlist_uuid}", []


def _tidal_embed_extract(playlist_uuid: str) -> tuple[List[dict], str]:
    """Try to extract track data from Tidal's embed page.
    Returns (tracks_list, playlist_name) or ([], "") on failure."""
    try:
        resp = requests.get(
            f"https://embed.tidal.com/playlists/{playlist_uuid}",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=15,
        )
        if resp.status_code != 200:
            print(f"Tidal embed page returned {resp.status_code}", file=sys.stderr)
            return [], ""

        html = resp.text

        # Debug: dump a portion so we can see what's available
        print(f"DEBUG Tidal embed page size: {len(html)} bytes", file=sys.stderr)

        # Look for JSON data embedded in script tags (common patterns)
        # Try __NEXT_DATA__, window.__DATA__, or inline JSON
        import re as _re
        tracks = []
        playlist_name = f"Tidal Playlist {playlist_uuid}"

        # Pattern 1: __NEXT_DATA__ or similar JSON blob
        for pattern in [
            r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>',
            r'window\.__DATA__\s*=\s*({.*?});',
            r'window\.__INITIAL_STATE__\s*=\s*({.*?});',
            r'"tracks"\s*:\s*(\[.*?\])',
        ]:
            match = _re.search(pattern, html, _re.DOTALL)
            if match:
                print(f"DEBUG Tidal embed matched pattern: {pattern[:40]}", file=sys.stderr)
                try:
                    blob = json.loads(match.group(1))
                    tracks, playlist_name = _parse_tidal_json_blob(blob, playlist_uuid)
                    if tracks:
                        return tracks, playlist_name
                except json.JSONDecodeError:
                    pass

        # Pattern 2: meta tags for playlist title
        title_match = _re.search(r'<meta\s+property="og:title"\s+content="([^"]+)"', html)
        if title_match:
            playlist_name = title_match.group(1)

        # Pattern 3: structured track entries in HTML
        # Look for track titles/artists in the rendered HTML
        track_matches = _re.findall(
            r'data-track-title="([^"]+)"[^>]*data-track-artist="([^"]+)"', html
        )
        if track_matches:
            for title, artist in track_matches:
                tracks.append({"artist": artist, "title": title})
            return tracks, playlist_name

        # Dump first 2000 chars for debugging if nothing matched
        print(f"DEBUG Tidal embed first 2000 chars: {html[:2000]}", file=sys.stderr)
        return [], ""

    except Exception as e:
        print(f"Tidal embed extraction failed: {e}", file=sys.stderr)
        return [], ""


def _parse_tidal_json_blob(blob, playlist_uuid: str) -> tuple[List[dict], str]:
    """Try to extract tracks from a JSON blob found in the embed page."""
    tracks = []
    playlist_name = f"Tidal Playlist {playlist_uuid}"

    # Handle various JSON structures
    if isinstance(blob, list):
        # Direct track array
        for t in blob:
            if isinstance(t, dict) and ("title" in t or "name" in t):
                title = t.get("title", t.get("name", "Unknown"))
                artist = t.get("artist", {}).get("name", "") if isinstance(t.get("artist"), dict) else t.get("artist", "Unknown")
                if isinstance(t.get("artists"), list):
                    artist = ", ".join(a.get("name", "") for a in t["artists"] if a.get("name"))
                tracks.append({"artist": artist or "Unknown", "title": title})
    elif isinstance(blob, dict):
        # Look for playlist name
        if "title" in blob:
            playlist_name = blob["title"]
        elif "name" in blob:
            playlist_name = blob["name"]

        # Recurse into common keys
        for key in ["tracks", "items", "data", "playlist", "props", "pageProps"]:
            if key in blob:
                sub_tracks, sub_name = _parse_tidal_json_blob(blob[key], playlist_uuid)
                if sub_tracks:
                    return sub_tracks, sub_name if sub_name != f"Tidal Playlist {playlist_uuid}" else playlist_name

    return tracks, playlist_name


def extract_playlist_tracks(url: str) -> tuple[str, str, List[dict]]:
    """Detect platform from URL and extract playlist tracks.
    Returns (platform, playlist_name, tracks_list).
    """
    # Deezer
    m = re.search(r"deezer\.com(?:/[a-z]{2})?/playlist/(\d+)", url)
    if m:
        name, tracks = _extract_deezer_playlist_tracks(m.group(1))
        return "deezer", name, tracks

    # Deezer short link - resolve first
    m = re.search(r"link\.deezer\.com/s/([a-zA-Z0-9]+)", url)
    if m:
        try:
            resolved = requests.get(f"https://link.deezer.com/s/{m.group(1)}", allow_redirects=True, timeout=10)
            resolved_url = resolved.url
            m2 = re.search(r"/playlist/(\d+)", resolved_url)
            if m2:
                name, tracks = _extract_deezer_playlist_tracks(m2.group(1))
                return "deezer", name, tracks
        except Exception:
            pass

    # Spotify
    m = re.search(r"open\.spotify\.com/playlist/([a-zA-Z0-9]+)", url)
    if m:
        name, tracks = _extract_spotify_playlist_tracks(m.group(1))
        return "spotify", name, tracks

    # Tidal
    m = re.search(r"(?:listen\.tidal\.com|tidal\.com)/(?:browse/)?playlist/([0-9a-zA-Z-]+)", url)
    if m:
        name, tracks = _extract_tidal_playlist_tracks(m.group(1))
        return "tidal", name, tracks

    # YouTube / YouTube Music
    m = re.search(r"(?:music\.youtube\.com|youtube\.com)/playlist\?list=([a-zA-Z0-9_-]+)", url)
    if m:
        name, tracks = _extract_youtube_playlist_tracks(m.group(1))
        return "youtube", name, tracks

    return "unknown", "Unknown Playlist", []


def is_playlist_url(url: str) -> bool:
    """Check if a URL is a supported playlist URL."""
    patterns = [
        r"deezer\.com(?:/[a-z]{2})?/playlist/\d+",
        r"link\.deezer\.com/s/[a-zA-Z0-9]+",
        r"open\.spotify\.com/playlist/[a-zA-Z0-9]+",
        r"(?:listen\.tidal\.com|tidal\.com)/(?:browse/)?playlist/[0-9a-zA-Z-]+",
        r"(?:music\.youtube\.com|youtube\.com)/playlist\?list=[a-zA-Z0-9_-]+",
    ]
    for pat in patterns:
        if re.search(pat, url):
            return True
    return False


# ---------------------------------------------------------------------------
# Playlist download history
# ---------------------------------------------------------------------------

def _get_playlist_history_path(playlist_name: str, username: str) -> str:
    """Return path to the JSON history file for a given playlist download."""
    os.makedirs(PLAYLIST_HISTORY_DIR, exist_ok=True)
    safe_name = re.sub(r'[^\w\s-]', '_', playlist_name).strip().replace(' ', '_')
    safe_user = username.replace('/', '_').replace('\\', '_')
    return os.path.join(PLAYLIST_HISTORY_DIR, f"{safe_user}_{safe_name}.json")


def _load_playlist_history(path: str) -> dict:
    if os.path.exists(path):
        try:
            with open(path, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {"playlist_name": "", "url": "", "username": "", "created_at": "", "tracks": []}


def _save_playlist_history(path: str, history: dict):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        json.dump(history, f, indent=2)


# ---------------------------------------------------------------------------
# Main playlist download logic
# ---------------------------------------------------------------------------

async def download_playlist(
    url: str,
    username: str,
    navidrome_api: NavidromeAPI,
    download_id: str,
    update_status_fn=None,
):
    """Download all tracks from a playlist URL, create a Navidrome playlist.

    Args:
        url: The playlist URL.
        username: The Navidrome username (for playlist ownership).
        navidrome_api: NavidromeAPI instance.
        download_id: UUID for progress tracking.
        update_status_fn: Callback(download_id, status, message, title, current, total).
    """
    # Per-track status list for UI
    track_statuses = []  # [{artist, title, status, message}, ...]

    def _update(status, message, title=None, current=None, total=None):
        extra = {
            "tracks": track_statuses,
            "skipped_count": skipped_count,
            "failed_count": failed_count,
            "downloaded_count": downloaded_count,
            "download_type": "playlist",
        }
        if update_status_fn:
            update_status_fn(download_id, status, message, title, current, total, **extra)
        update_status_file(download_id, status, message, title, current, total, **extra)

    downloaded_count = 0
    skipped_count = 0
    failed_count = 0

    _update("in_progress", "Extracting playlist tracks...")

    platform, playlist_name, playlist_tracks = extract_playlist_tracks(url)

    if not playlist_tracks:
        _update("failed", f"Could not extract tracks from playlist URL. Platform: {platform}")
        return

    total = len(playlist_tracks)
    # Initialize track statuses as pending
    track_statuses = [{"artist": t.get("artist", "Unknown"), "title": t.get("title", "Unknown"), "status": "pending", "message": ""} for t in playlist_tracks]

    _update("in_progress", f"Found {total} tracks. Starting downloads...",
            title=playlist_name, total=total)

    # Prepare history
    history_path = _get_playlist_history_path(playlist_name, username)
    history = _load_playlist_history(history_path)
    history["playlist_name"] = playlist_name
    history["url"] = url
    history["username"] = username
    history["created_at"] = history.get("created_at") or datetime.now().isoformat()

    # Set up downloaders
    from config import ALBUM_RECOMMENDATION_COMMENT
    tagger = Tagger(ALBUM_RECOMMENDATION_COMMENT)
    track_downloader = TrackDownloader(tagger)
    salt, token = navidrome_api._get_navidrome_auth_params()

    all_navidrome_ids = []
    newly_downloaded = []

    for i, track in enumerate(playlist_tracks):
        artist = track.get("artist", "Unknown")
        title = track.get("title", "Unknown")
        label = f"{artist} - {title}"

        track_statuses[i]["status"] = "in_progress"
        track_statuses[i]["message"] = "Checking library..."
        _update("in_progress",
                f"Processing {i+1}/{total}: {label}",
                title=playlist_name, current=downloaded_count, total=total)

        # Check if already in Navidrome
        existing = navidrome_api._search_song_in_navidrome(artist, title, salt, token)
        if existing:
            print(f"  Already in Navidrome: {label} (id={existing['id']})")
            all_navidrome_ids.append(existing["id"])
            skipped_count += 1
            track_statuses[i]["status"] = "skipped"
            track_statuses[i]["message"] = "Already in library"
            _update("in_progress",
                    f"Processing {i+1}/{total}: {label} (already in library)",
                    title=playlist_name, current=downloaded_count, total=total)
            continue

        # Download via TrackDownloader
        track_statuses[i]["message"] = "Searching Deezer..."
        _update("in_progress",
                f"Downloading {i+1}/{total}: {label}",
                title=playlist_name, current=downloaded_count, total=total)

        song_info = {
            "artist": artist,
            "title": title,
            "album": "",
            "release_date": "",
            "recording_mbid": "",
            "source": "Playlist",
        }
        if track.get("deezer_id"):
            song_info["deezer_id"] = track["deezer_id"]

        try:
            downloaded_path = await track_downloader.download_track(song_info)
        except Exception as e:
            print(f"  Error downloading {label}: {e}", file=sys.stderr)
            downloaded_path = None

        if downloaded_path:
            downloaded_count += 1
            newly_downloaded.append({
                "artist": artist,
                "title": title,
                "album": song_info.get("album", ""),
                "downloaded_path": downloaded_path,
                "downloaded_at": datetime.now().isoformat(),
            })
            track_statuses[i]["status"] = "completed"
            track_statuses[i]["message"] = "Downloaded"
            _update("in_progress",
                    f"Downloaded {i+1}/{total}: {label}",
                    title=playlist_name, current=downloaded_count, total=total)
        else:
            failed_count += 1
            track_statuses[i]["status"] = "failed"
            track_statuses[i]["message"] = "Not found on Deezer"
            print(f"  Failed to download: {label}")
            _update("in_progress",
                    f"Failed {i+1}/{total}: {label}",
                    title=playlist_name, current=downloaded_count, total=total)

    # Organize downloaded files into library
    if newly_downloaded:
        _update("in_progress", "Organizing downloaded files...",
                title=playlist_name, current=downloaded_count, total=total)
        file_path_map = navidrome_api.organize_music_files(TEMP_DOWNLOAD_FOLDER, MUSIC_LIBRARY_PATH)

        _update("in_progress", "Scanning library for new files...",
                title=playlist_name, current=downloaded_count, total=total)
        navidrome_api._start_scan()
        navidrome_api._wait_for_scan(timeout=120)

        salt, token = navidrome_api._get_navidrome_auth_params()

        for entry in newly_downloaded:
            nd_song = navidrome_api._search_song_in_navidrome(
                entry["artist"], entry["title"], salt, token
            )
            if nd_song:
                entry["navidrome_id"] = nd_song["id"]
                entry["file_path"] = nd_song.get("path", "")
                all_navidrome_ids.append(nd_song["id"])
            else:
                print(f"  Could not find in Navidrome after scan: {entry['artist']} - {entry['title']}")

    # Create/update Navidrome playlist
    if all_navidrome_ids:
        _update("in_progress", f"Creating Navidrome playlist '{playlist_name}'...",
                title=playlist_name, current=downloaded_count, total=total)
        existing_pl = navidrome_api._find_playlist_by_name(playlist_name, salt, token)
        if existing_pl:
            navidrome_api._update_playlist(existing_pl["id"], all_navidrome_ids, salt, token)
        else:
            navidrome_api._create_playlist(playlist_name, all_navidrome_ids, salt, token)

    # Save download history (only newly downloaded tracks)
    if newly_downloaded:
        existing_entries = {
            (t["artist"].lower(), t["title"].lower())
            for t in history.get("tracks", [])
        }
        for entry in newly_downloaded:
            key = (entry["artist"].lower(), entry["title"].lower())
            if key not in existing_entries:
                history["tracks"].append(entry)
        history["last_updated"] = datetime.now().isoformat()
        _save_playlist_history(history_path, history)
        print(f"Saved playlist download history to {history_path}")

    msg = (f"{downloaded_count} downloaded, "
           f"{skipped_count} already in library, {failed_count} failed. "
           f"Playlist created with {len(all_navidrome_ids)} tracks.")
    _update("completed", msg, title=playlist_name, current=downloaded_count, total=total)
    print(msg)
