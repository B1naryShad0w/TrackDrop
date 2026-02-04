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
from utils import update_status_file

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
    auto_cleanup: bool = False,
) -> dict:
    """Add a playlist to be monitored. Returns the new entry.

    Args:
        url: URL of the playlist
        name: Display name for the playlist
        platform: Platform (spotify, deezer, youtube, tidal)
        username: User who owns this monitored playlist
        poll_interval_hours: How often to check for updates
        auto_cleanup: If True, old tracks are cleaned up when playlist is refetched
    """
    return get_data_store().add_monitored_playlist(
        username=username,
        url=url,
        name=name,
        platform=platform,
        poll_interval_hours=poll_interval_hours,
        auto_cleanup=auto_cleanup,
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


def _sync_playlist(entry: dict, navidrome_api, update_status_fn, downloads_queue=None, download_id=None):
    """Run a sync for a single monitored playlist."""
    from downloaders.playlist_downloader import download_playlist, extract_playlist_tracks
    from utils import get_user_history_path

    if download_id is None:
        download_id = str(uuid.uuid4())
    playlist_id = entry["id"]
    username = entry.get("username", "")
    auto_cleanup = entry.get("auto_cleanup", False)
    playlist_name = entry.get("name", "")
    navidrome_playlist_id = entry.get("navidrome_playlist_id")
    print(f"[PlaylistMonitor] Syncing: {playlist_name} ({entry['url']})")

    # Extract current playlist tracks FIRST
    track_count = None
    current_tracks = None
    source_name = None
    try:
        _, source_name, tracks = extract_playlist_tracks(entry["url"])
        if tracks:
            track_count = len(tracks)
            # Build list with artist/title for cleanup comparison
            current_tracks = [{'artist': t.get('artist', ''), 'title': t.get('title', '')} for t in tracks]
    except Exception as e:
        print(f"[PlaylistMonitor] Warning: Could not extract tracks from {entry['url']}: {e}", file=sys.stderr)

    # Run auto-cleanup AFTER extracting tracks - only delete songs no longer in playlist
    # Safety: Only run cleanup if we successfully got the current track list
    if auto_cleanup and username and current_tracks is not None:
        source_key = f"playlist_{playlist_id}"
        history_path = get_user_history_path(username)
        print(f"[PlaylistMonitor] Running auto-cleanup for {playlist_name}...")
        try:
            # Pass playlist name so songs in this playlist aren't protected from cleanup
            # Pass username for DataStore access
            # Pass current_tracks so only removed songs get deleted
            cleanup_result = navidrome_api.cleanup_source(
                source_key, history_path,
                exclude_playlist=playlist_name,
                username=username,
                current_tracks=current_tracks
            )
            print(f"[PlaylistMonitor] Cleanup: deleted {cleanup_result['deleted']}, kept {cleanup_result['kept']}")
        except Exception as e:
            print(f"[PlaylistMonitor] Cleanup error: {e}", file=sys.stderr)
    elif auto_cleanup and username and current_tracks is None:
        print(f"[PlaylistMonitor] Skipping cleanup - could not fetch current playlist tracks")

    try:
        result = asyncio.run(
            download_playlist(
                url=entry["url"],
                username=username,
                navidrome_api=navidrome_api,
                download_id=download_id,
                update_status_fn=update_status_fn,
                playlist_name_override=playlist_name,  # Use stored name (Navidrome name)
                playlist_id=playlist_id if auto_cleanup else None,  # Track history if auto_cleanup enabled
                navidrome_playlist_id=navidrome_playlist_id,
            )
        )

        # Update monitored playlist entry with navidrome_playlist_id and sync name from Navidrome
        updates = {}
        nd_playlist_id = result.get("navidrome_playlist_id") if result else navidrome_playlist_id

        if result and result.get("navidrome_playlist_id") and result["navidrome_playlist_id"] != navidrome_playlist_id:
            updates["navidrome_playlist_id"] = result["navidrome_playlist_id"]
            nd_playlist_id = result["navidrome_playlist_id"]
            print(f"[PlaylistMonitor] Stored Navidrome playlist ID: {nd_playlist_id}")

        # Sync name from Navidrome playlist (so UI shows what's in Navidrome, not source)
        # Always update download queue and status file with Navidrome name
        # (download_playlist wrote source name, we need to overwrite it)
        if nd_playlist_id:
            nd_playlist = navidrome_api._get_playlist_by_id(nd_playlist_id)
            if nd_playlist and nd_playlist.get("name"):
                nd_name = nd_playlist["name"]
                # Always update download queue and status file
                if downloads_queue and download_id and download_id in downloads_queue:
                    downloads_queue[download_id]["title"] = nd_name
                update_status_file(download_id, 'completed', None, title=nd_name)

                # Only update the monitored playlist entry if name changed
                if nd_name != playlist_name:
                    updates["name"] = nd_name
                    print(f"[PlaylistMonitor] Synced name from Navidrome: '{playlist_name}' -> '{nd_name}'")

        if updates:
            update_monitored_playlist(playlist_id, updates, username=username)

    except Exception as e:
        print(f"[PlaylistMonitor] Error syncing {playlist_name}: {e}", file=sys.stderr)

    mark_synced(playlist_id, track_count, username=username)


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

                _sync_playlist(entry, navidrome_api, update_status_fn, downloads_queue, download_id)

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
