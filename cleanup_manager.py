"""
Cleanup Manager for TrackDrop

This module manages the cleanup of automatically downloaded songs.

Paradigm:
- Auto-downloaded songs are tracked in a "pending cleanup" database
- Songs are removed from the database when:
  - They become protected (user rates > 2 stars, favorites, adds to playlist)
  - A user manually downloads the same song via link
- Automated cleanup (cron) only processes songs in the pending database
- Manual cleanup scans the entire library for 1-star songs
"""

import json
import os
import sqlite3
import time
from typing import Optional


class CleanupManager:
    """Manages the pending cleanup database and cleanup operations."""

    def __init__(self, db_path: str, navidrome_db_path: str = None):
        """
        Initialize the cleanup manager.

        Args:
            db_path: Path to the pending cleanup database JSON file
            navidrome_db_path: Path to Navidrome's SQLite database (for library scans)
        """
        self.db_path = db_path
        self.navidrome_db_path = navidrome_db_path
        self._ensure_db_exists()

    def _ensure_db_exists(self):
        """Ensure the database file exists."""
        if not os.path.exists(self.db_path):
            self._save_db({'pending': {}, 'version': 2})

    def _load_db(self) -> dict:
        """Load the pending cleanup database."""
        try:
            with open(self.db_path, 'r') as f:
                data = json.load(f)
                # Migrate from old format if needed
                if 'pending' not in data:
                    # Old format: {source: [tracks]}
                    # New format: {pending: {source: [tracks]}, version: 2}
                    data = {'pending': data, 'version': 2}
                    self._save_db(data)
                return data
        except (json.JSONDecodeError, IOError):
            return {'pending': {}, 'version': 2}

    def _save_db(self, data: dict):
        """Save the pending cleanup database."""
        try:
            os.makedirs(os.path.dirname(self.db_path) if os.path.dirname(self.db_path) else '.', exist_ok=True)
            with open(self.db_path, 'w') as f:
                json.dump(data, f, indent=2)
        except IOError as e:
            print(f"Error saving cleanup database: {e}")

    def add_pending_song(self, source: str, track_info: dict):
        """
        Add an auto-downloaded song to the pending cleanup database.

        Args:
            source: Source name (e.g., 'ListenBrainz', 'Last.fm', 'LLM')
            track_info: Dict with artist, title, album, navidrome_id, file_path, etc.
        """
        data = self._load_db()
        if source not in data['pending']:
            data['pending'][source] = []

        # Check if already exists
        artist_lower = track_info.get('artist', '').lower()
        title_lower = track_info.get('title', '').lower()

        for existing in data['pending'][source]:
            if (existing.get('artist', '').lower() == artist_lower and
                    existing.get('title', '').lower() == title_lower):
                # Update existing entry
                existing.update(track_info)
                existing['added_at'] = track_info.get('added_at', time.strftime('%Y-%m-%dT%H:%M:%S'))
                self._save_db(data)
                return

        # Add new entry
        track_info['added_at'] = time.strftime('%Y-%m-%dT%H:%M:%S')
        data['pending'][source].append(track_info)
        self._save_db(data)
        print(f"  Added to pending cleanup: {track_info.get('artist')} - {track_info.get('title')}")

    def remove_pending_song(self, source: str, artist: str, title: str, reason: str = None):
        """
        Remove a song from the pending cleanup database.

        Args:
            source: Source name
            artist: Artist name
            title: Track title
            reason: Optional reason for removal (for logging)
        """
        data = self._load_db()
        if source not in data['pending']:
            return False

        original_count = len(data['pending'][source])
        data['pending'][source] = [
            t for t in data['pending'][source]
            if not (t.get('artist', '').lower() == artist.lower() and
                    t.get('title', '').lower() == title.lower())
        ]

        if len(data['pending'][source]) < original_count:
            self._save_db(data)
            if reason:
                print(f"  Removed from pending cleanup ({reason}): {artist} - {title}")
            return True
        return False

    def remove_by_navidrome_id(self, navidrome_id: str, reason: str = None):
        """Remove a song from pending cleanup by its Navidrome ID."""
        data = self._load_db()
        removed = False

        for source in list(data['pending'].keys()):
            original_count = len(data['pending'][source])
            data['pending'][source] = [
                t for t in data['pending'][source]
                if t.get('navidrome_id') != navidrome_id
            ]
            if len(data['pending'][source]) < original_count:
                removed = True

        if removed:
            self._save_db(data)
            if reason:
                print(f"  Removed from pending cleanup ({reason}): ID {navidrome_id}")
        return removed

    def is_pending(self, artist: str, title: str) -> bool:
        """Check if a song is in the pending cleanup database."""
        data = self._load_db()
        artist_lower = artist.lower()
        title_lower = title.lower()

        for source in data['pending'].values():
            for track in source:
                if (track.get('artist', '').lower() == artist_lower and
                        track.get('title', '').lower() == title_lower):
                    return True
        return False

    def get_pending_songs(self, source: str = None) -> dict:
        """
        Get all pending songs, optionally filtered by source.

        Returns:
            Dict of {source: [tracks]} or just [tracks] if source specified
        """
        data = self._load_db()
        if source:
            return data['pending'].get(source, [])
        return data['pending']

    def get_pending_count(self) -> int:
        """Get total count of songs pending cleanup."""
        data = self._load_db()
        return sum(len(tracks) for tracks in data['pending'].values())

    def mark_as_manually_downloaded(self, artist: str, title: str):
        """
        Mark a song as manually downloaded, removing it from pending cleanup.
        This should be called when a user downloads a song via link.
        """
        data = self._load_db()
        removed = False

        artist_lower = artist.lower()
        title_lower = title.lower()

        for source in list(data['pending'].keys()):
            original_count = len(data['pending'][source])
            data['pending'][source] = [
                t for t in data['pending'][source]
                if not (t.get('artist', '').lower() == artist_lower and
                        t.get('title', '').lower() == title_lower)
            ]
            if len(data['pending'][source]) < original_count:
                removed = True
                print(f"  Removed from pending cleanup (manual download): {artist} - {title}")

        if removed:
            self._save_db(data)
        return removed

    def scan_library_for_one_star(self, navidrome_api) -> list:
        """
        Scan the entire Navidrome library for songs with 1-star rating.
        This is for manual cleanup triggered from the UI.

        Returns:
            List of dicts with song info and whether it can be deleted
        """
        results = []

        if not self.navidrome_db_path or not os.path.exists(self.navidrome_db_path):
            print("Navidrome database path not configured or not found")
            return results

        try:
            conn = sqlite3.connect(f"file:{self.navidrome_db_path}?mode=ro", uri=True)
            cursor = conn.cursor()

            # Find all songs with 1-star rating by the current user
            # We'll need to check each one for protection by other users
            cursor.execute("""
                SELECT DISTINCT a.item_id, a.user_id, mf.title, mf.artist, mf.album, mf.path
                FROM annotation a
                JOIN media_file mf ON a.item_id = mf.id
                WHERE a.item_type = 'media_file' AND a.rating = 1
            """)

            one_star_songs = cursor.fetchall()
            conn.close()

            print(f"Found {len(one_star_songs)} songs with 1-star ratings")

            for song_id, user_id, title, artist, album, path in one_star_songs:
                # Check if protected by other users
                protection = navidrome_api._check_song_protection(song_id)

                # For manual cleanup, we only delete if:
                # - User gave 1-star
                # - No other user has favorited, added to playlist, or given > 2 stars
                can_delete = protection['has_one_star'] and not (
                    protection['is_starred'] or
                    protection['in_user_playlist'] or
                    protection['max_rating'] > 2
                )

                results.append({
                    'navidrome_id': song_id,
                    'artist': artist,
                    'title': title,
                    'album': album,
                    'path': path,
                    'rated_by': user_id[:8] + '...',
                    'can_delete': can_delete,
                    'protection': protection,
                    'keep_reason': self._get_keep_reason(protection) if not can_delete else None
                })

        except Exception as e:
            print(f"Error scanning library: {e}")

        return results

    def _get_keep_reason(self, protection: dict) -> str:
        """Get a human-readable reason why a song is being kept."""
        reasons = []
        if protection['is_starred']:
            reasons.append("favorited by another user")
        if protection['in_user_playlist']:
            reasons.append("in a user playlist")
        if protection['max_rating'] > 2:
            reasons.append(f"rated {protection['max_rating']}/5 by another user")
        return "; ".join(reasons) if reasons else "unknown"
