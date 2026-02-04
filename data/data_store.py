"""
Unified Data Store for TrackDrop

This module consolidates all JSON-based data persistence into a single, thread-safe
data store. It replaces the scattered data files:
- user_settings.json -> users.{username}.settings
- download_history_{username}.json -> users.{username}.download_history
- pending_cleanup.json -> users.{username}.pending_cleanup
- monitored_playlists.json -> users.{username}.monitored_playlists

The data store maintains backward compatibility by:
1. Auto-migrating existing files on first access
2. Providing the same API surface as before
"""

import json
import os
import threading
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional


# Default paths
DEFAULT_DATA_DIR = os.getenv("TRACKDROP_DATA_DIR", "/app/data")
LEGACY_SETTINGS_FILE = os.getenv("TRACKDROP_USER_SETTINGS_PATH", "/app/data/user_settings.json")
LEGACY_HISTORY_PATH = os.getenv("TRACKDROP_DOWNLOAD_HISTORY_PATH", "/app/data/download_history.json")
LEGACY_CLEANUP_PATH = "/app/data/pending_cleanup.json"
LEGACY_PLAYLISTS_PATH = os.getenv("TRACKDROP_MONITORED_PLAYLISTS_PATH", "/app/data/monitored_playlists.json")


DEFAULT_USER_SETTINGS = {
    "listenbrainz_enabled": False,
    "listenbrainz_username": "",
    "listenbrainz_token": "",
    "lastfm_enabled": False,
    "lastfm_username": "",
    "lastfm_api_key": "",
    "lastfm_api_secret": "",
    "lastfm_session_key": "",
    "cron_minute": 0,
    "cron_hour": 0,
    "cron_day": 1,
    "cron_timezone": "US/Eastern",
    "cron_enabled": True,
    "playlist_sources": ["listenbrainz", "lastfm"],
    "first_time_setup_done": False,
    "api_key": "",
    "display_name": "",
}


class DataStore:
    """
    Thread-safe unified data store for TrackDrop.

    Provides a single interface for all data persistence needs:
    - User settings
    - Download history
    - Pending cleanup tracking
    - Monitored playlists

    Data is stored in a single JSON file per user for atomicity and simplicity.
    A global metadata file tracks all known users.
    """

    def __init__(self, data_dir: str = DEFAULT_DATA_DIR):
        self._data_dir = data_dir
        self._lock = threading.RLock()
        self._user_locks: Dict[str, threading.RLock] = {}
        os.makedirs(data_dir, exist_ok=True)
        self._migrate_legacy_data()

    def _get_user_lock(self, username: str) -> threading.RLock:
        """Get or create a lock for a specific user."""
        with self._lock:
            if username not in self._user_locks:
                self._user_locks[username] = threading.RLock()
            return self._user_locks[username]

    def _get_user_file_path(self, username: str) -> str:
        """Get the path to a user's data file."""
        safe_user = username.replace('/', '_').replace('\\', '_')
        return os.path.join(self._data_dir, f"user_{safe_user}.json")

    def _load_user_data(self, username: str) -> Dict[str, Any]:
        """Load all data for a user."""
        file_path = self._get_user_file_path(username)
        if not os.path.exists(file_path):
            return self._create_default_user_data()
        try:
            with open(file_path, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return self._create_default_user_data()

    def _save_user_data(self, username: str, data: Dict[str, Any]):
        """Save all data for a user."""
        file_path = self._get_user_file_path(username)
        data['_last_modified'] = datetime.now().isoformat()
        with open(file_path, 'w') as f:
            json.dump(data, f, indent=2)

    def _create_default_user_data(self) -> Dict[str, Any]:
        """Create default data structure for a new user."""
        return {
            "settings": dict(DEFAULT_USER_SETTINGS),
            "download_history": {},
            "pending_cleanup": {},
            "monitored_playlists": [],
            "_version": 1,
            "_created": datetime.now().isoformat(),
        }

    # -------------------------------------------------------------------------
    # Migration from legacy files
    # -------------------------------------------------------------------------

    def _migrate_legacy_data(self):
        """Migrate data from legacy files if they exist and haven't been migrated."""
        migration_marker = os.path.join(self._data_dir, ".migration_complete_v1")
        if os.path.exists(migration_marker):
            return

        print("[DataStore] Checking for legacy data to migrate...")

        # Migrate user settings
        self._migrate_user_settings()

        # Migrate download history files
        self._migrate_download_history()

        # Migrate pending cleanup
        self._migrate_pending_cleanup()

        # Migrate monitored playlists
        self._migrate_monitored_playlists()

        # Mark migration as complete
        with open(migration_marker, 'w') as f:
            f.write(datetime.now().isoformat())

        print("[DataStore] Migration complete.")

    def _migrate_user_settings(self):
        """Migrate legacy user_settings.json."""
        if not os.path.exists(LEGACY_SETTINGS_FILE):
            return

        try:
            with open(LEGACY_SETTINGS_FILE, 'r') as f:
                legacy_data = json.load(f)

            for username, settings in legacy_data.items():
                user_lock = self._get_user_lock(username)
                with user_lock:
                    data = self._load_user_data(username)
                    data['settings'].update(settings)
                    self._save_user_data(username, data)
                    print(f"[DataStore] Migrated settings for user: {username}")
        except Exception as e:
            print(f"[DataStore] Error migrating user settings: {e}")

    def _migrate_download_history(self):
        """Migrate legacy download_history files."""
        # Migrate global history file
        if os.path.exists(LEGACY_HISTORY_PATH):
            try:
                with open(LEGACY_HISTORY_PATH, 'r') as f:
                    legacy_history = json.load(f)
                # Global history goes to a 'global' user or first user
                # We'll skip this for now as per-user history is preferred
            except Exception as e:
                print(f"[DataStore] Error migrating global history: {e}")

        # Migrate per-user history files
        history_dir = os.path.dirname(LEGACY_HISTORY_PATH)
        if os.path.exists(history_dir):
            for filename in os.listdir(history_dir):
                if filename.startswith("download_history_") and filename.endswith(".json"):
                    username = filename[17:-5]  # Extract username from filename
                    file_path = os.path.join(history_dir, filename)
                    try:
                        with open(file_path, 'r') as f:
                            legacy_history = json.load(f)

                        user_lock = self._get_user_lock(username)
                        with user_lock:
                            data = self._load_user_data(username)
                            data['download_history'] = legacy_history
                            self._save_user_data(username, data)
                            print(f"[DataStore] Migrated download history for user: {username}")
                    except Exception as e:
                        print(f"[DataStore] Error migrating history for {username}: {e}")

    def _migrate_pending_cleanup(self):
        """Migrate legacy pending_cleanup.json."""
        if not os.path.exists(LEGACY_CLEANUP_PATH):
            return

        try:
            with open(LEGACY_CLEANUP_PATH, 'r') as f:
                legacy_data = json.load(f)

            # Old format: {pending: {source: [tracks]}} or {source: [tracks]}
            pending = legacy_data.get('pending', legacy_data)

            # Since cleanup is global, we store it under a special '__global__' user
            # or distribute based on username in track info
            user_lock = self._get_user_lock('__global__')
            with user_lock:
                data = self._load_user_data('__global__')
                data['pending_cleanup'] = pending
                self._save_user_data('__global__', data)
                print("[DataStore] Migrated pending cleanup data")
        except Exception as e:
            print(f"[DataStore] Error migrating pending cleanup: {e}")

    def _migrate_monitored_playlists(self):
        """Migrate legacy monitored_playlists.json."""
        if not os.path.exists(LEGACY_PLAYLISTS_PATH):
            return

        try:
            with open(LEGACY_PLAYLISTS_PATH, 'r') as f:
                legacy_playlists = json.load(f)

            # Group by username
            by_user: Dict[str, List] = {}
            for playlist in legacy_playlists:
                username = playlist.get('username', '__global__')
                if username not in by_user:
                    by_user[username] = []
                by_user[username].append(playlist)

            for username, playlists in by_user.items():
                user_lock = self._get_user_lock(username)
                with user_lock:
                    data = self._load_user_data(username)
                    data['monitored_playlists'] = playlists
                    self._save_user_data(username, data)
                    print(f"[DataStore] Migrated {len(playlists)} monitored playlists for user: {username}")
        except Exception as e:
            print(f"[DataStore] Error migrating monitored playlists: {e}")

    # -------------------------------------------------------------------------
    # User Settings API (replaces UserManager)
    # -------------------------------------------------------------------------

    def get_user_settings(self, username: str) -> Dict[str, Any]:
        """Get settings for a user, filling in defaults for missing keys."""
        user_lock = self._get_user_lock(username)
        with user_lock:
            data = self._load_user_data(username)
            merged = dict(DEFAULT_USER_SETTINGS)
            merged.update(data.get('settings', {}))
            return merged

    def update_user_settings(self, username: str, settings_dict: Dict[str, Any]) -> bool:
        """Merge updates into a user's settings."""
        user_lock = self._get_user_lock(username)
        with user_lock:
            data = self._load_user_data(username)
            data['settings'].update(settings_dict)
            self._save_user_data(username, data)
        return True

    def is_first_time(self, username: str) -> bool:
        """Check whether the user has completed first-time setup."""
        return not self.get_user_settings(username).get("first_time_setup_done", False)

    def mark_setup_done(self, username: str):
        """Mark first-time setup as complete for a user."""
        self.update_user_settings(username, {"first_time_setup_done": True})

    def get_all_users(self) -> List[str]:
        """Return a list of all usernames with stored data."""
        users = set()
        for filename in os.listdir(self._data_dir):
            if filename.startswith("user_") and filename.endswith(".json"):
                # Extract username from user_{safe_user}.json
                username = filename[5:-5]
                if username != '__global__':
                    users.add(username)
        return list(users)

    def generate_api_key(self, username: str) -> str:
        """Generate a new API key for the user and save it."""
        import random
        import string
        api_key = "".join(random.choices(string.ascii_letters + string.digits, k=32))
        self.update_user_settings(username, {"api_key": api_key})
        return api_key

    def get_user_by_api_key(self, api_key: str) -> Optional[str]:
        """Look up username by API key. Returns None if not found."""
        if not api_key:
            return None
        for username in self.get_all_users():
            settings = self.get_user_settings(username)
            if settings.get("api_key") == api_key:
                return username
        return None

    # -------------------------------------------------------------------------
    # Download History API (replaces navidrome_api history methods)
    # -------------------------------------------------------------------------

    def get_download_history(self, username: str) -> Dict[str, List[Dict]]:
        """Get download history for a user."""
        user_lock = self._get_user_lock(username)
        with user_lock:
            data = self._load_user_data(username)
            return data.get('download_history', {})

    def add_to_download_history(self, username: str, source_name: str, track_entry: Dict[str, Any]):
        """Add a track entry to the download history."""
        user_lock = self._get_user_lock(username)
        with user_lock:
            data = self._load_user_data(username)
            history = data.get('download_history', {})

            if source_name not in history:
                history[source_name] = []

            # Check for existing entry
            artist_lower = track_entry.get('artist', '').lower()
            title_lower = track_entry.get('title', '').lower()

            for existing in history[source_name]:
                if (existing.get('artist', '').lower() == artist_lower and
                        existing.get('title', '').lower() == title_lower):
                    existing['file_path'] = track_entry.get('file_path', existing.get('file_path'))
                    existing['updated_at'] = datetime.now().isoformat()
                    data['download_history'] = history
                    self._save_user_data(username, data)
                    return

            # Add new entry
            track_entry['added_at'] = datetime.now().isoformat()
            history[source_name].append(track_entry)
            data['download_history'] = history
            self._save_user_data(username, data)

    def remove_from_download_history(self, username: str, source_name: str, artist: str, title: str):
        """Remove a track from the download history."""
        user_lock = self._get_user_lock(username)
        with user_lock:
            data = self._load_user_data(username)
            history = data.get('download_history', {})

            if source_name not in history:
                return

            history[source_name] = [
                t for t in history[source_name]
                if not (t.get('artist', '').lower() == artist.lower() and
                        t.get('title', '').lower() == title.lower())
            ]
            data['download_history'] = history
            self._save_user_data(username, data)

    def clear_download_history(self, username: str, sources: Optional[List[str]] = None):
        """Clear download history, optionally for specific sources only."""
        user_lock = self._get_user_lock(username)
        with user_lock:
            data = self._load_user_data(username)
            if sources:
                for source in sources:
                    data['download_history'].pop(source, None)
            else:
                data['download_history'] = {}
            self._save_user_data(username, data)

    def set_download_history(self, username: str, history: Dict[str, List[Dict]]):
        """Replace the entire download history for a user."""
        user_lock = self._get_user_lock(username)
        with user_lock:
            data = self._load_user_data(username)
            data['download_history'] = history
            self._save_user_data(username, data)

    # -------------------------------------------------------------------------
    # Pending Cleanup API (replaces CleanupManager)
    # -------------------------------------------------------------------------

    def get_pending_cleanup(self, username: str = '__global__', source: Optional[str] = None) -> Dict[str, List[Dict]]:
        """Get pending cleanup songs, optionally filtered by source."""
        user_lock = self._get_user_lock(username)
        with user_lock:
            data = self._load_user_data(username)
            pending = data.get('pending_cleanup', {})
            if source:
                return {source: pending.get(source, [])}
            return pending

    def add_pending_cleanup(self, username: str, source: str, track_info: Dict[str, Any]):
        """Add a song to pending cleanup."""
        user_lock = self._get_user_lock(username)
        with user_lock:
            data = self._load_user_data(username)
            pending = data.get('pending_cleanup', {})

            if source not in pending:
                pending[source] = []

            artist_lower = track_info.get('artist', '').lower()
            title_lower = track_info.get('title', '').lower()

            # Update existing or add new
            for existing in pending[source]:
                if (existing.get('artist', '').lower() == artist_lower and
                        existing.get('title', '').lower() == title_lower):
                    existing.update(track_info)
                    existing['added_at'] = track_info.get('added_at', datetime.now().isoformat())
                    data['pending_cleanup'] = pending
                    self._save_user_data(username, data)
                    return

            track_info['added_at'] = datetime.now().isoformat()
            pending[source].append(track_info)
            data['pending_cleanup'] = pending
            self._save_user_data(username, data)
            print(f"  Added to pending cleanup: {track_info.get('artist')} - {track_info.get('title')}")

    def remove_pending_cleanup(self, username: str, source: str, artist: str, title: str, reason: Optional[str] = None) -> bool:
        """Remove a song from pending cleanup."""
        user_lock = self._get_user_lock(username)
        with user_lock:
            data = self._load_user_data(username)
            pending = data.get('pending_cleanup', {})

            if source not in pending:
                return False

            original_count = len(pending[source])
            pending[source] = [
                t for t in pending[source]
                if not (t.get('artist', '').lower() == artist.lower() and
                        t.get('title', '').lower() == title.lower())
            ]

            if len(pending[source]) < original_count:
                data['pending_cleanup'] = pending
                self._save_user_data(username, data)
                if reason:
                    print(f"  Removed from pending cleanup ({reason}): {artist} - {title}")
                return True
            return False

    def remove_pending_by_navidrome_id(self, username: str, navidrome_id: str, reason: Optional[str] = None) -> bool:
        """Remove a song from pending cleanup by its Navidrome ID."""
        user_lock = self._get_user_lock(username)
        with user_lock:
            data = self._load_user_data(username)
            pending = data.get('pending_cleanup', {})
            removed = False

            for source in list(pending.keys()):
                original_count = len(pending[source])
                pending[source] = [
                    t for t in pending[source]
                    if t.get('navidrome_id') != navidrome_id
                ]
                if len(pending[source]) < original_count:
                    removed = True

            if removed:
                data['pending_cleanup'] = pending
                self._save_user_data(username, data)
                if reason:
                    print(f"  Removed from pending cleanup ({reason}): ID {navidrome_id}")
            return removed

    def is_pending_cleanup(self, username: str, artist: str, title: str) -> bool:
        """Check if a song is in the pending cleanup database."""
        user_lock = self._get_user_lock(username)
        with user_lock:
            data = self._load_user_data(username)
            pending = data.get('pending_cleanup', {})
            artist_lower = artist.lower()
            title_lower = title.lower()

            for source_tracks in pending.values():
                for track in source_tracks:
                    if (track.get('artist', '').lower() == artist_lower and
                            track.get('title', '').lower() == title_lower):
                        return True
            return False

    def get_pending_count(self, username: str = '__global__') -> int:
        """Get total count of songs pending cleanup."""
        user_lock = self._get_user_lock(username)
        with user_lock:
            data = self._load_user_data(username)
            pending = data.get('pending_cleanup', {})
            return sum(len(tracks) for tracks in pending.values())

    def mark_as_manually_downloaded(self, username: str, artist: str, title: str) -> bool:
        """Mark a song as manually downloaded, removing from all pending cleanup sources."""
        user_lock = self._get_user_lock(username)
        with user_lock:
            data = self._load_user_data(username)
            pending = data.get('pending_cleanup', {})
            removed = False
            artist_lower = artist.lower()
            title_lower = title.lower()

            for source in list(pending.keys()):
                original_count = len(pending[source])
                pending[source] = [
                    t for t in pending[source]
                    if not (t.get('artist', '').lower() == artist_lower and
                            t.get('title', '').lower() == title_lower)
                ]
                if len(pending[source]) < original_count:
                    removed = True
                    print(f"  Removed from pending cleanup (manual download): {artist} - {title}")

            if removed:
                data['pending_cleanup'] = pending
                self._save_user_data(username, data)
            return removed

    def clear_pending_cleanup(self, username: str = '__global__'):
        """Clear all pending cleanup data."""
        user_lock = self._get_user_lock(username)
        with user_lock:
            data = self._load_user_data(username)
            data['pending_cleanup'] = {}
            self._save_user_data(username, data)

    # -------------------------------------------------------------------------
    # Monitored Playlists API (replaces playlist_monitor functions)
    # -------------------------------------------------------------------------

    def get_monitored_playlists(self, username: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get monitored playlists, optionally filtered by username."""
        if username:
            user_lock = self._get_user_lock(username)
            with user_lock:
                data = self._load_user_data(username)
                return data.get('monitored_playlists', [])

        # If no username, get all playlists from all users
        all_playlists = []
        for user in self.get_all_users():
            all_playlists.extend(self.get_monitored_playlists(user))
        # Also check global
        all_playlists.extend(self.get_monitored_playlists('__global__'))
        return all_playlists

    def add_monitored_playlist(
        self,
        username: str,
        url: str,
        name: str,
        platform: str,
        poll_interval_hours: int = 24,
    ) -> Dict[str, Any]:
        """Add a playlist to be monitored. Returns the new entry."""
        user_lock = self._get_user_lock(username)
        with user_lock:
            data = self._load_user_data(username)
            playlists = data.get('monitored_playlists', [])

            # Don't add duplicates
            for p in playlists:
                if p["url"] == url:
                    return p

            entry = {
                "id": str(uuid.uuid4()),
                "url": url,
                "name": name,
                "platform": platform,
                "username": username,
                "poll_interval_hours": poll_interval_hours,
                "enabled": True,
                "added_at": datetime.now().isoformat(),
                "last_synced": None,
                "last_track_count": 0,
            }
            playlists.append(entry)
            data['monitored_playlists'] = playlists
            self._save_user_data(username, data)
            return entry

    def update_monitored_playlist(self, username: str, playlist_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Update a monitored playlist's settings."""
        user_lock = self._get_user_lock(username)
        with user_lock:
            data = self._load_user_data(username)
            playlists = data.get('monitored_playlists', [])

            for p in playlists:
                if p["id"] == playlist_id:
                    for key in ("poll_interval_hours", "enabled", "name"):
                        if key in updates:
                            p[key] = updates[key]
                    data['monitored_playlists'] = playlists
                    self._save_user_data(username, data)
                    return p
            return None

    def remove_monitored_playlist(self, username: str, playlist_id: str) -> bool:
        """Remove a playlist from monitoring."""
        user_lock = self._get_user_lock(username)
        with user_lock:
            data = self._load_user_data(username)
            playlists = data.get('monitored_playlists', [])
            original_len = len(playlists)
            playlists = [p for p in playlists if p["id"] != playlist_id]

            if len(playlists) < original_len:
                data['monitored_playlists'] = playlists
                self._save_user_data(username, data)
                return True
            return False

    def mark_playlist_synced(self, username: str, playlist_id: str, track_count: Optional[int] = None):
        """Update last_synced for a monitored playlist."""
        user_lock = self._get_user_lock(username)
        with user_lock:
            data = self._load_user_data(username)
            playlists = data.get('monitored_playlists', [])

            for p in playlists:
                if p["id"] == playlist_id:
                    p["last_synced"] = datetime.now().isoformat()
                    if track_count is not None:
                        p["last_track_count"] = track_count
                    break

            data['monitored_playlists'] = playlists
            self._save_user_data(username, data)

    # -------------------------------------------------------------------------
    # Legacy compatibility: Get user history path
    # -------------------------------------------------------------------------

    def get_user_history_path(self, username: str) -> str:
        """
        For backward compatibility with code that still uses file paths.
        This now points to the unified user data file.
        """
        return self._get_user_file_path(username)


# Global singleton instance
_data_store: Optional[DataStore] = None
_data_store_lock = threading.Lock()


def get_data_store(data_dir: str = DEFAULT_DATA_DIR) -> DataStore:
    """Get the global DataStore singleton."""
    global _data_store
    with _data_store_lock:
        if _data_store is None:
            _data_store = DataStore(data_dir)
        return _data_store
