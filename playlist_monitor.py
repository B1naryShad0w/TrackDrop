"""Playlist monitoring system.

Manages a list of monitored playlists that are periodically checked
for new tracks and automatically synced to Navidrome.

Now delegates to the unified DataStore for persistence.
"""

import sys
import threading
import time
import asyncio
import uuid
from datetime import datetime
from typing import Optional

from persistence.data_store import get_data_store

_scheduler_thread: Optional[threading.Thread] = None
_scheduler_running = False


def get_monitored_playlists(username: str = None) -> list:
    """Return all monitored playlists, optionally filtered by username."""
    return get_data_store().get_monitored_playlists(username)


def add_monitored_playlist(
    url: str,
    name: str,
    platform: str,
    username: str,
    poll_interval_hours: int = 24,
) -> dict:
    """Add a playlist to be monitored. Returns the new entry."""
    return get_data_store().add_monitored_playlist(
        username=username,
        url=url,
        name=name,
        platform=platform,
        poll_interval_hours=poll_interval_hours,
    )


def update_monitored_playlist(playlist_id: str, updates: dict, username: str = None) -> Optional[dict]:
    """Update a monitored playlist's settings. Returns updated entry or None."""
    # If username not provided, search all users
    if username:
        return get_data_store().update_monitored_playlist(username, playlist_id, updates)

    # Search all users to find the playlist
    for user in get_data_store().get_all_users():
        result = get_data_store().update_monitored_playlist(user, playlist_id, updates)
        if result:
            return result
    return None


def remove_monitored_playlist(playlist_id: str, username: str = None) -> bool:
    """Remove a playlist from monitoring. Returns True if found and removed."""
    if username:
        return get_data_store().remove_monitored_playlist(username, playlist_id)

    # Search all users to find and remove the playlist
    for user in get_data_store().get_all_users():
        if get_data_store().remove_monitored_playlist(user, playlist_id):
            return True
    return False


def mark_synced(playlist_id: str, track_count: int = None, username: str = None):
    """Update last_synced (and optionally last_track_count) for a monitored playlist."""
    if username:
        get_data_store().mark_playlist_synced(username, playlist_id, track_count)
        return

    # Search all users to find the playlist
    for user in get_data_store().get_all_users():
        playlists = get_data_store().get_monitored_playlists(user)
        for p in playlists:
            if p["id"] == playlist_id:
                get_data_store().mark_playlist_synced(user, playlist_id, track_count)
                return


def _sync_playlist(entry: dict, navidrome_api, update_status_fn):
    """Run a sync for a single monitored playlist."""
    from downloaders.playlist_downloader import download_playlist, extract_playlist_tracks

    download_id = str(uuid.uuid4())
    print(f"[PlaylistMonitor] Syncing: {entry['name']} ({entry['url']})")

    track_count = None
    try:
        # Get track count before downloading
        _, _, tracks = extract_playlist_tracks(entry["url"])
        if tracks:
            track_count = len(tracks)

        asyncio.run(
            download_playlist(
                url=entry["url"],
                username=entry["username"],
                navidrome_api=navidrome_api,
                download_id=download_id,
                update_status_fn=update_status_fn,
            )
        )
    except Exception as e:
        print(f"[PlaylistMonitor] Error syncing {entry['name']}: {e}", file=sys.stderr)

    mark_synced(entry["id"], track_count, username=entry.get("username"))


def _scheduler_loop(navidrome_api, update_status_fn, downloads_queue):
    """Background loop that checks monitored playlists and syncs when due."""
    global _scheduler_running
    _scheduler_running = True

    while _scheduler_running:
        try:
            playlists = get_monitored_playlists()
            now = datetime.now()

            for entry in playlists:
                if not entry.get("enabled", True):
                    continue

                interval_hours = entry.get("poll_interval_hours", 24)
                last_synced = entry.get("last_synced")

                if last_synced:
                    try:
                        last_dt = datetime.fromisoformat(last_synced)
                        elapsed_hours = (now - last_dt).total_seconds() / 3600
                        if elapsed_hours < interval_hours:
                            continue
                    except (ValueError, TypeError):
                        pass

                # Time to sync — add to download queue and run
                download_id = str(uuid.uuid4())
                downloads_queue[download_id] = {
                    "id": download_id,
                    "username": entry.get("username", ""),
                    "artist": "Playlist Sync",
                    "title": entry["name"],
                    "status": "in_progress",
                    "start_time": datetime.now().isoformat(),
                    "message": "Auto-syncing monitored playlist...",
                    "current_track_count": 0,
                    "total_track_count": None,
                    "download_type": "playlist",
                    "tracks": [],
                    "downloaded_count": 0,
                    "skipped_count": 0,
                    "failed_count": 0,
                }

                _sync_playlist(entry, navidrome_api, update_status_fn)

        except Exception as e:
            print(f"[PlaylistMonitor] Scheduler error: {e}", file=sys.stderr)

        # Check every 5 minutes
        for _ in range(300):
            if not _scheduler_running:
                break
            time.sleep(1)


def start_scheduler(navidrome_api, update_status_fn, downloads_queue):
    """Start the background playlist monitoring scheduler."""
    global _scheduler_thread, _scheduler_running
    if _scheduler_thread and _scheduler_thread.is_alive():
        return

    _scheduler_thread = threading.Thread(
        target=_scheduler_loop,
        args=(navidrome_api, update_status_fn, downloads_queue),
        daemon=True,
    )
    _scheduler_thread.start()
    print("[PlaylistMonitor] Scheduler started.")


def stop_scheduler():
    """Stop the background scheduler."""
    global _scheduler_running
    _scheduler_running = False
