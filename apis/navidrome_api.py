"""Navidrome API wrapper for TrackDrop."""

import asyncio
import hashlib
import json
import os
import re
import shutil
import sqlite3
import time
import uuid

import requests
from config import TEMP_DOWNLOAD_FOLDER
from utils import sanitize_filename


class NavidromeAPI:
    """Handle all Navidrome/Subsonic API interactions."""

    def __init__(self, root_nd, user_nd, password_nd, music_library_path,
                 listenbrainz_enabled=False, lastfm_enabled=False, llm_enabled=False,
                 admin_user=None, admin_password=None, navidrome_db_path=None, **kwargs):
        self.root_nd = root_nd
        self.user_nd = user_nd
        self.password_nd = password_nd
        self.music_library_path = music_library_path
        self.listenbrainz_enabled = listenbrainz_enabled
        self.lastfm_enabled = lastfm_enabled
        self.llm_enabled = llm_enabled
        self.admin_user = admin_user or ''
        self.admin_password = admin_password or ''
        self.navidrome_db_path = navidrome_db_path or ''

    # ---- Authentication ----

    def _get_navidrome_auth_params(self):
        """Generate authentication parameters for Navidrome."""
        salt = os.urandom(6).hex()
        token = hashlib.md5((self.password_nd + salt).encode('utf-8')).hexdigest()
        return salt, token

    def _get_admin_auth_params(self):
        """Generate authentication parameters using admin credentials.
        Falls back to regular user credentials if admin creds are not configured."""
        user = self.admin_user if self.admin_user else self.user_nd
        password = self.admin_password if self.admin_password else self.password_nd
        salt = os.urandom(6).hex()
        token = hashlib.md5((password + salt).encode('utf-8')).hexdigest()
        return user, salt, token

    # ---- Song Operations ----

    def _get_all_songs(self, salt, token):
        """Fetch all songs from Navidrome."""
        url = f"{self.root_nd}/rest/search3.view"
        params = {
            'u': self.user_nd, 't': token, 's': salt,
            'v': '1.16.1', 'c': 'trackdrop', 'f': 'json',
            'query': '', 'songCount': 10000
        }
        try:
            response = requests.get(url, params=params, timeout=60)
            response.raise_for_status()
            data = response.json()
            if data['subsonic-response']['status'] == 'ok' and 'searchResult3' in data['subsonic-response']:
                return data['subsonic-response']['searchResult3'].get('song', [])
        except Exception as e:
            print(f"Error fetching songs from Navidrome: {e}")
        return []

    def _get_song_details(self, song_id, salt, token, user=None):
        """Fetch details of a specific song from Navidrome."""
        url = f"{self.root_nd}/rest/getSong.view"
        params = {
            'u': user or self.user_nd, 't': token, 's': salt,
            'v': '1.16.1', 'c': 'trackdrop', 'f': 'json', 'id': song_id
        }
        try:
            response = requests.get(url, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()
            if data['subsonic-response']['status'] == 'ok' and 'song' in data['subsonic-response']:
                return data['subsonic-response']['song']
        except Exception as e:
            print(f"Error fetching song details: {e}")
        return None

    def _check_song_protection(self, song_id):
        """Check if a song is protected from deletion by any user interaction.
        Returns a dict with:
          'protected': bool - whether the song should be kept (legacy, computed based on old rules)
          'reasons': list of strings explaining why it's protected
          'max_rating': int - highest rating across all users (0-5)
          'has_one_star': bool - whether any user explicitly gave 1 star
          'in_user_playlist': bool - whether song is in any non-recommendation playlist
          'has_interaction': bool - whether anyone has interacted (rated or starred)
          'is_starred': bool - whether anyone has starred/favorited
        """
        result = {
            'protected': False,
            'reasons': [],
            'max_rating': 0,
            'has_one_star': False,
            'in_user_playlist': False,
            'has_interaction': False,
            'is_starred': False,
        }
        recommendation_playlist_names = {
            'listenbrainz weekly', 'last.fm weekly', 'llm weekly'
        }

        # Try SQLite direct query first (checks all users)
        if self.navidrome_db_path and os.path.exists(self.navidrome_db_path):
            try:
                conn = sqlite3.connect(f"file:{self.navidrome_db_path}?mode=ro", uri=True)
                cursor = conn.cursor()

                # Check ratings and starred across all users
                cursor.execute(
                    "SELECT user_id, rating, starred, starred_at "
                    "FROM annotation WHERE item_id = ? AND item_type = 'media_file'",
                    (song_id,)
                )
                for row in cursor.fetchall():
                    user_id, db_rating, starred, starred_at = row
                    db_rating = db_rating or 0
                    # Navidrome stores ratings on 0-10 scale (each increment = half star)
                    # Convert to 0-5 scale: 2=1star, 4=2stars, 6=3stars, 8=4stars, 10=5stars
                    rating = db_rating / 2 if db_rating > 0 else 0

                    if rating > 0 or starred or starred_at:
                        result['has_interaction'] = True

                    if 0 < rating <= 1:
                        result['has_one_star'] = True
                        result['reasons'].append(f"rated {rating}/5 (low) by user {user_id[:8]}...")

                    if rating > 2:
                        result['reasons'].append(f"rated {rating}/5 by user {user_id[:8]}...")

                    result['max_rating'] = max(result['max_rating'], rating)

                    if starred or starred_at:
                        result['is_starred'] = True
                        result['reasons'].append(f"starred by user {user_id[:8]}...")
                        result['max_rating'] = max(result['max_rating'], 5)

                # Check if song is in any non-recommendation playlist
                cursor.execute(
                    "SELECT p.name, p.owner_id FROM playlist p "
                    "JOIN playlist_tracks pt ON p.id = pt.playlist_id "
                    "WHERE pt.media_file_id = ?",
                    (song_id,)
                )
                for row in cursor.fetchall():
                    playlist_name, owner_id = row
                    if playlist_name.lower() not in recommendation_playlist_names:
                        result['in_user_playlist'] = True
                        result['reasons'].append(f"in playlist '{playlist_name}'")

                conn.close()

                # Compute legacy 'protected' field for backwards compatibility
                result['protected'] = result['max_rating'] > 2 or result['is_starred'] or result['in_user_playlist']
                return result
            except Exception as e:
                print(f"Warning: Could not query Navidrome DB: {e}. Falling back to API.")

        # Fallback: check via Subsonic API (limited - only sees current user's data)
        salt, token = self._get_navidrome_auth_params()
        details = self._get_song_details(song_id, salt, token)
        if details:
            starred = details.get("starred")
            user_rating = details.get('userRating', 0)

            if user_rating > 0 or starred:
                result['has_interaction'] = True

            if 0 < user_rating <= 1:
                result['has_one_star'] = True
                result['reasons'].append(f"rated {user_rating}/5 (low) by {self.user_nd}")

            if starred:
                result['is_starred'] = True
                result['reasons'].append(f"starred by {self.user_nd}")
                result['max_rating'] = max(result['max_rating'], 5)

            if user_rating > 2:
                result['reasons'].append(f"rated {user_rating}/5 by {self.user_nd}")

            result['max_rating'] = max(result['max_rating'], user_rating)

        # Also check admin user if different
        if self.admin_user and self.admin_user != self.user_nd:
            admin_user, admin_salt, admin_token = self._get_admin_auth_params()
            admin_details = self._get_song_details(song_id, admin_salt, admin_token, user=admin_user)
            if admin_details:
                starred = admin_details.get("starred")
                admin_rating = admin_details.get('userRating', 0)

                if admin_rating > 0 or starred:
                    result['has_interaction'] = True

                if 0 < admin_rating <= 1:
                    result['has_one_star'] = True
                    result['reasons'].append(f"rated {admin_rating}/5 (low) by {self.admin_user}")

                if starred:
                    result['is_starred'] = True
                    result['reasons'].append(f"starred by {self.admin_user}")
                    result['max_rating'] = max(result['max_rating'], 5)

                if admin_rating > 2:
                    result['reasons'].append(f"rated {admin_rating}/5 by {self.admin_user}")

                result['max_rating'] = max(result['max_rating'], admin_rating)

        # Compute legacy 'protected' field
        result['protected'] = result['max_rating'] > 2 or result['is_starred'] or result['in_user_playlist']
        return result

    def _delete_song(self, song_path):
        """Delete a song file. Returns True if deleted successfully."""
        if not os.path.exists(song_path):
            print(f"File not found: {song_path}")
            return False
        if not os.path.isfile(song_path):
            print(f"Not a file: {song_path}")
            return False
        try:
            os.remove(song_path)
            print(f"Deleted: {song_path}")
            return True
        except OSError as e:
            print(f"Error deleting {song_path}: {e}")
            return False

    # ---- Path Resolution ----

    def _find_actual_song_path(self, navidrome_relative_path, song_details=None):
        """Find the actual file path on disk given the Navidrome relative path."""
        candidates = []

        # Source 1: Navidrome's path from API
        if song_details and song_details.get('path'):
            api_path = song_details['path']
            candidates.append(api_path)
            for prefix in ['/music/', '/data/music/', '/data/', '/media/', 'music/']:
                if api_path.startswith(prefix):
                    candidates.append(api_path[len(prefix):])

        # Source 2: Query Navidrome DB for the path
        if self.navidrome_db_path and os.path.exists(self.navidrome_db_path) and song_details:
            nd_id = song_details.get('id', '')
            if nd_id:
                try:
                    conn = sqlite3.connect(f"file:{self.navidrome_db_path}?mode=ro", uri=True)
                    cursor = conn.cursor()
                    cursor.execute("SELECT path FROM media_file WHERE id = ?", (nd_id,))
                    row = cursor.fetchone()
                    conn.close()
                    if row and row[0]:
                        db_path = row[0]
                        candidates.append(db_path)
                        for prefix in ['/music/', '/data/music/', '/data/', '/media/', 'music/']:
                            if db_path.startswith(prefix):
                                candidates.append(db_path[len(prefix):])
                except Exception:
                    pass

        # Source 3: The relative path from download history
        if navidrome_relative_path:
            candidates.append(navidrome_relative_path)

        # Deduplicate while preserving order
        seen = set()
        unique_candidates = []
        for c in candidates:
            if c not in seen:
                seen.add(c)
                unique_candidates.append(c)

        # Try each candidate
        for candidate in unique_candidates:
            if os.path.isabs(candidate) and os.path.exists(candidate):
                return candidate
            full = os.path.join(self.music_library_path, candidate)
            if os.path.exists(full):
                return full

        # Fallback: scan artist/album directory
        for rel_path in unique_candidates:
            path_parts = [p for p in rel_path.split('/') if p]
            if len(path_parts) < 2:
                continue

            for start_idx in range(len(path_parts) - 2):
                artist_name = path_parts[start_idx]
                album_name = path_parts[start_idx + 1] if start_idx + 2 < len(path_parts) else None
                if not album_name:
                    continue

                album_dir = os.path.join(self.music_library_path, artist_name, album_name)
                if not os.path.isdir(album_dir):
                    continue

                files = os.listdir(album_dir)
                audio_exts = ('.flac', '.mp3', '.ogg', '.m4a', '.aac', '.wma')
                audio_files = [f for f in files if any(f.lower().endswith(e) for e in audio_exts)]

                if len(audio_files) == 1:
                    return os.path.join(album_dir, audio_files[0])

                if song_details:
                    song_title = song_details.get('title', '').lower()
                    for f in audio_files:
                        if song_title and song_title in f.lower():
                            return os.path.join(album_dir, f)

        return None

    # ---- Search and Matching ----

    @staticmethod
    def _normalize_for_match(text):
        """Normalize a string for fuzzy matching."""
        t = text.lower().strip().rstrip('.')
        t = re.sub(r'\s*(feat\.?|ft\.?|featuring|with)\s+.*', '', t)
        t = re.sub(r'\s*[\(\[].*?[\)\]]', '', t)
        t = re.sub(r'\s+', ' ', t).strip()
        return t

    def _search_song_in_navidrome(self, artist, title, salt, token, album=None):
        """Search for a song in Navidrome by artist and title."""
        norm_artist = self._normalize_for_match(artist)
        norm_title = self._normalize_for_match(title)
        norm_album = self._normalize_for_match(album) if album else None

        queries = [title, f"{norm_artist} {norm_title}", f"{artist} {title}"]
        seen_ids = set()
        all_candidates = []

        for query in queries:
            url = f"{self.root_nd}/rest/search3.view"
            params = {
                'u': self.user_nd, 't': token, 's': salt,
                'v': '1.16.1', 'c': 'trackdrop', 'f': 'json',
                'query': query, 'songCount': 20, 'artistCount': 0, 'albumCount': 0
            }
            try:
                response = requests.get(url, params=params, timeout=30)
                response.raise_for_status()
                data = response.json()
                songs = data.get('subsonic-response', {}).get('searchResult3', {}).get('song', [])
                for s in songs:
                    sid = s.get('id', '')
                    if sid not in seen_ids:
                        seen_ids.add(sid)
                        all_candidates.append(s)
            except Exception as e:
                print(f"Error searching Navidrome: {e}")

        if not all_candidates:
            return None

        def _score(song):
            s_artist = self._normalize_for_match(song.get('artist', ''))
            s_title = self._normalize_for_match(song.get('title', ''))
            score = 0

            if s_title == norm_title:
                score += 100
            elif norm_title in s_title or s_title in norm_title:
                score += 60

            if s_artist == norm_artist:
                score += 100
            elif norm_artist in s_artist or s_artist in norm_artist:
                score += 60
            else:
                artist_words = set(re.split(r'[,&\s]+', norm_artist))
                s_artist_words = set(re.split(r'[,&\s]+', s_artist))
                overlap = artist_words & s_artist_words
                if overlap:
                    score += 30 * len(overlap)

            if norm_album:
                s_album = self._normalize_for_match(song.get('album', ''))
                if s_album == norm_album:
                    score += 50
                elif norm_album in s_album or s_album in norm_album:
                    score += 25
                else:
                    score -= 200

            return score

        scored = [(s, _score(s)) for s in all_candidates]
        scored.sort(key=lambda x: x[1], reverse=True)

        best_song, best_score = scored[0]
        if best_score >= 60:
            return best_song

        print(f"No confident match for '{artist} - {title}' (best score: {best_score})")
        return None

    def _find_song_by_path(self, file_path):
        """Look up a song in Navidrome's SQLite DB by file path."""
        if not self.navidrome_db_path or not os.path.exists(self.navidrome_db_path):
            return None

        try:
            conn = sqlite3.connect(f"file:{self.navidrome_db_path}?mode=ro", uri=True)
            cursor = conn.cursor()

            candidates = [file_path]
            music_bases = ['/app/music/', '/app/music']
            for base in music_bases:
                if file_path.startswith(base):
                    rel = file_path[len(base):]
                    candidates.extend([rel, f"/music/{rel}", f"/music/trackdrop/{rel}", f"trackdrop/{rel}"])
                    break

            for path_candidate in candidates:
                cursor.execute("SELECT id, path FROM media_file WHERE path = ?", (path_candidate,))
                row = cursor.fetchone()
                if row:
                    conn.close()
                    return {'id': row[0], 'path': row[1]}

            basename = os.path.basename(file_path)
            cursor.execute("SELECT id, path FROM media_file WHERE path LIKE ?", (f"%/{basename}",))
            rows = cursor.fetchall()
            if len(rows) == 1:
                conn.close()
                return {'id': rows[0][0], 'path': rows[0][1]}

            conn.close()
        except Exception as e:
            print(f"Error looking up song by path: {e}")
        return None

    # ---- Playlist Operations ----

    def _get_playlists(self, salt, token):
        """Get all playlists from Navidrome."""
        url = f"{self.root_nd}/rest/getPlaylists.view"
        params = {
            'u': self.user_nd, 't': token, 's': salt,
            'v': '1.16.1', 'c': 'trackdrop', 'f': 'json'
        }
        try:
            response = requests.get(url, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()
            return data.get('subsonic-response', {}).get('playlists', {}).get('playlist', [])
        except Exception as e:
            print(f"Error fetching playlists: {e}")
            return []

    def _find_playlist_by_name(self, name, salt, token):
        """Find a playlist by name."""
        for pl in self._get_playlists(salt, token):
            if pl.get('name') == name:
                return pl
        return None

    def _create_playlist(self, name, song_ids, salt, token):
        """Create a new playlist with the given song IDs."""
        url = f"{self.root_nd}/rest/createPlaylist.view"
        params = {
            'u': self.user_nd, 't': token, 's': salt,
            'v': '1.16.1', 'c': 'trackdrop', 'f': 'json', 'name': name
        }
        param_list = [(k, v) for k, v in params.items()]
        for sid in song_ids:
            param_list.append(('songId', sid))
        try:
            response = requests.get(url, params=param_list, timeout=60)
            response.raise_for_status()
            data = response.json()
            if data.get('subsonic-response', {}).get('status') == 'ok':
                print(f"Created playlist '{name}' with {len(song_ids)} tracks")
                return True
            print(f"Error creating playlist '{name}': {data}")
        except Exception as e:
            print(f"Error creating playlist '{name}': {e}")
        return False

    def _update_playlist(self, playlist_id, song_ids, salt, token):
        """Replace the contents of an existing playlist."""
        # Remove all existing songs
        current_songs = self._get_playlist_songs(playlist_id, salt, token)
        if current_songs:
            url = f"{self.root_nd}/rest/updatePlaylist.view"
            params = {
                'u': self.user_nd, 't': token, 's': salt,
                'v': '1.16.1', 'c': 'trackdrop', 'f': 'json', 'playlistId': playlist_id
            }
            param_list = [(k, v) for k, v in params.items()]
            for i in range(len(current_songs)):
                param_list.append(('songIndexToRemove', i))
            try:
                response = requests.get(url, params=param_list, timeout=60)
                response.raise_for_status()
            except Exception as e:
                print(f"Error removing songs from playlist: {e}")
                return False

        # Add new songs
        if song_ids:
            url = f"{self.root_nd}/rest/createPlaylist.view"
            params = {
                'u': self.user_nd, 't': token, 's': salt,
                'v': '1.16.1', 'c': 'trackdrop', 'f': 'json', 'playlistId': playlist_id
            }
            param_list = [(k, v) for k, v in params.items()]
            for sid in song_ids:
                param_list.append(('songId', sid))
            try:
                response = requests.get(url, params=param_list, timeout=60)
                response.raise_for_status()
            except Exception as e:
                print(f"Error adding songs to playlist: {e}")
                return False

        print(f"Updated playlist (id={playlist_id}): {len(song_ids)} tracks")
        return True

    def _get_playlist_songs(self, playlist_id, salt, token):
        """Get all songs in a playlist."""
        url = f"{self.root_nd}/rest/getPlaylist.view"
        params = {
            'u': self.user_nd, 't': token, 's': salt,
            'v': '1.16.1', 'c': 'trackdrop', 'f': 'json', 'id': playlist_id
        }
        try:
            response = requests.get(url, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()
            return data.get('subsonic-response', {}).get('playlist', {}).get('entry', [])
        except Exception as e:
            print(f"Error fetching playlist songs: {e}")
            return []

    # ---- Library Scan ----

    def _start_scan(self, _salt=None, _token=None, full_scan=False):
        """Trigger a Navidrome library scan.

        Args:
            full_scan: If True, forces a full rescan which detects deleted files
        """
        admin_user, salt, token = self._get_admin_auth_params()
        url = f"{self.root_nd}/rest/startScan.view"
        params = {
            'u': admin_user, 't': token, 's': salt,
            'v': '1.16.1', 'c': 'trackdrop', 'f': 'json'
        }
        if full_scan:
            params['fullScan'] = 'true'
        try:
            response = requests.get(url, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()
            if data.get('subsonic-response', {}).get('status') == 'ok':
                scan_type = "full library scan" if full_scan else "library scan"
                print(f"{scan_type.capitalize()} triggered")
                return True
            print(f"Error triggering scan: {data}")
        except Exception as e:
            print(f"Error triggering library scan: {e}")
        return False

    def _get_scan_status(self, _salt=None, _token=None):
        """Check if a library scan is in progress."""
        admin_user, salt, token = self._get_admin_auth_params()
        url = f"{self.root_nd}/rest/getScanStatus.view"
        params = {
            'u': admin_user, 't': token, 's': salt,
            'v': '1.16.1', 'c': 'trackdrop', 'f': 'json'
        }
        try:
            response = requests.get(url, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()
            return data.get('subsonic-response', {}).get('scanStatus', {}).get('scanning', False)
        except Exception:
            return False

    async def _wait_for_scan_async(self, timeout=60):
        """Wait for an ongoing library scan to complete (async version)."""
        import asyncio
        start = time.time()
        while time.time() - start < timeout:
            if not self._get_scan_status():
                return True
            await asyncio.sleep(2)
        return False

    def _wait_for_scan(self, timeout=120):
        """Wait for an ongoing library scan to complete (sync version)."""
        start = time.time()
        while time.time() - start < timeout:
            if not self._get_scan_status():
                print("Library scan completed")
                return True
            time.sleep(3)
        print(f"Scan did not complete within {timeout}s")
        return False

    # ---- Download History Management ----

    @staticmethod
    def _load_download_history(history_path):
        """Load the download history JSON."""
        if os.path.exists(history_path):
            try:
                with open(history_path, 'r') as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                print(f"Error loading download history: {e}")
        return {}

    @staticmethod
    def _save_download_history(history_path, history):
        """Save the download history JSON."""
        try:
            os.makedirs(os.path.dirname(history_path) if os.path.dirname(history_path) else '.', exist_ok=True)
            with open(history_path, 'w') as f:
                json.dump(history, f, indent=2)
        except IOError as e:
            print(f"Error saving download history: {e}")

    def add_to_download_history(self, history_path, source_name, track_entry):
        """Add a track entry to the download history."""
        history = self._load_download_history(history_path)
        if source_name not in history:
            history[source_name] = []

        for existing in history[source_name]:
            if (existing.get('artist', '').lower() == track_entry.get('artist', '').lower() and
                    existing.get('title', '').lower() == track_entry.get('title', '').lower()):
                existing['file_path'] = track_entry.get('file_path', existing.get('file_path'))
                self._save_download_history(history_path, history)
                return

        history[source_name].append(track_entry)
        self._save_download_history(history_path, history)

    def remove_from_download_history(self, history_path, source_name, artist, title):
        """Remove a track from the download history."""
        history = self._load_download_history(history_path)
        if source_name not in history:
            return
        history[source_name] = [
            t for t in history[source_name]
            if not (t.get('artist', '').lower() == artist.lower() and
                    t.get('title', '').lower() == title.lower())
        ]
        self._save_download_history(history_path, history)

    # ---- Update Playlists After Download ----

    def update_api_playlists(self, all_recommendations, history_path, downloaded_songs_info=None, file_path_map=None):
        """After downloading, update Navidrome API playlists for each source."""
        if downloaded_songs_info is None:
            downloaded_songs_info = []
        if file_path_map is None:
            file_path_map = {}

        salt, token = self._get_navidrome_auth_params()

        # Build lookup for downloaded songs
        downloaded_file_paths = {}
        downloaded_set = set()
        for s in downloaded_songs_info:
            key = (s.get('artist', '').lower(), s.get('title', '').lower())
            downloaded_set.add(key)
            temp_path = s.get('downloaded_path', '')
            if temp_path and temp_path in file_path_map:
                downloaded_file_paths[key] = file_path_map[temp_path]

        # Trigger scan if we downloaded new files
        if downloaded_songs_info:
            print("Triggering library scan for newly downloaded files...")
            self._start_scan()
            self._wait_for_scan()

        # Group tracks by source
        source_map = {
            'listenbrainz': 'ListenBrainz Weekly',
            'last.fm': 'Last.fm Weekly',
            'llm': 'LLM Weekly',
        }

        tracks_by_source = {}
        for song in all_recommendations:
            src = song.get('source', 'Unknown').lower()
            playlist_name = source_map.get(src, f"{song.get('source', 'Unknown')} Weekly")
            if playlist_name not in tracks_by_source:
                tracks_by_source[playlist_name] = []
            tracks_by_source[playlist_name].append(song)

        for playlist_name, songs in tracks_by_source.items():
            print(f"\n=== Updating playlist: {playlist_name} ===")
            song_ids = []

            for song in songs:
                nd_song = None
                song_key = (song.get('artist', '').lower(), song.get('title', '').lower())
                was_downloaded = song_key in downloaded_set

                # Try path-based lookup first for downloaded songs
                if was_downloaded and song_key in downloaded_file_paths:
                    final_path = downloaded_file_paths[song_key]
                    nd_song = self._find_song_by_path(final_path)
                    if nd_song:
                        print(f"  Found by path: {song['artist']} - {song['title']}")

                if not nd_song:
                    nd_song = self._search_song_in_navidrome(song['artist'], song['title'], salt, token)

                if nd_song:
                    song_ids.append(nd_song['id'])
                    if was_downloaded:
                        source_key = song.get('source', 'Unknown')
                        self.add_to_download_history(history_path, source_key, {
                            'artist': song['artist'],
                            'title': song['title'],
                            'album': song.get('album', ''),
                            'navidrome_id': nd_song['id'],
                            'file_path': nd_song.get('path', ''),
                            'recording_mbid': song.get('recording_mbid', ''),
                            'downloaded_at': time.strftime('%Y-%m-%dT%H:%M:%S')
                        })
                    else:
                        print(f"  Pre-existing: {song['artist']} - {song['title']}")
                else:
                    print(f"  Not found: {song['artist']} - {song['title']}")

            if not song_ids:
                print(f"  No tracks found for '{playlist_name}', skipping")
                continue

            existing = self._find_playlist_by_name(playlist_name, salt, token)
            if existing:
                self._update_playlist(existing['id'], song_ids, salt, token)
            else:
                self._create_playlist(playlist_name, song_ids, salt, token)

    # ---- Cleanup ----

    def _should_delete_song(self, protection, is_discover_weekly):
        """Determine if a song should be deleted based on protection info and source type.

        Discover Weekly rules (ListenBrainz/Last.fm/LLM Weekly):
          - Delete if no interaction at all (nobody added to library)
          - OR delete if max_rating <= 2 AND not in any user playlist

        Other songs rules:
          - Delete ONLY if user gave 1-star AND no other user has:
            - favorited (starred)
            - added to a playlist
            - given > 2 stars

        Returns: (should_delete: bool, reason: str)
        """
        if is_discover_weekly:
            # Rule 1: No interaction at all = delete
            if not protection['has_interaction']:
                return True, "no user interaction"

            # Rule 2: Low rating (<=2) and not in any user playlist = delete
            if protection['max_rating'] <= 2 and not protection['in_user_playlist']:
                return True, f"low rating ({protection['max_rating']}) and not in any playlist"

            # Otherwise keep it
            return False, None
        else:
            # Non-discover-weekly: only delete if 1-star AND no protection from others
            if not protection['has_one_star']:
                return False, None  # No explicit dislike, keep it

            # User gave 1-star, check if any other user protects it
            other_user_protection = (
                protection['is_starred'] or
                protection['in_user_playlist'] or
                protection['max_rating'] > 2
            )

            if other_user_protection:
                return False, None  # Another user protects it

            return True, "1-star with no other user protection"

    async def process_api_cleanup(self, history_path, listenbrainz_api=None, lastfm_api=None):
        """Cleanup routine: check ratings and delete songs based on user interactions.

        Discover Weekly playlists (ListenBrainz/Last.fm/LLM Weekly):
          - Auto-remove if nobody added to library
          - OR if nobody gave > 2 stars AND not in anyone's playlist

        Other songs:
          - Remove only if user gave 1-star AND no other user has favorited,
            added to playlist, or given > 2 stars
        """
        salt, token = self._get_navidrome_auth_params()
        history = self._load_download_history(history_path)

        if not history:
            print("No download history found. Nothing to clean up.")
            return

        deleted_songs = []
        kept_songs = []

        playlist_name_map = {
            'ListenBrainz': 'ListenBrainz Weekly',
            'Last.fm': 'Last.fm Weekly',
            'LLM': 'LLM Weekly',
        }

        # Sources that use "discover weekly" deletion rules
        discover_weekly_sources = {'ListenBrainz', 'Last.fm', 'LLM'}

        for source_name in list(history.keys()):
            tracks = history.get(source_name, [])
            if not tracks:
                continue

            is_discover_weekly = source_name in discover_weekly_sources
            rule_type = "discover-weekly" if is_discover_weekly else "standard"
            print(f"\n=== Cleanup for {source_name} ({len(tracks)} tracks, {rule_type} rules) ===")

            playlist_name = playlist_name_map.get(source_name, f"{source_name} Weekly")
            existing_playlist = self._find_playlist_by_name(playlist_name, salt, token)
            playlist_songs = self._get_playlist_songs(existing_playlist['id'], salt, token) if existing_playlist else []

            tracks_to_remove = []
            tracks_to_keep_ids = []

            for track in tracks:
                artist = track.get('artist', '')
                title = track.get('title', '')
                nd_id = track.get('navidrome_id', '')
                file_rel_path = track.get('file_path', '')
                label = f"{artist} - {title}"

                song_details = self._get_song_details(nd_id, salt, token) if nd_id else None

                if song_details is None:
                    print(f"  Not found in Navidrome: {label}")
                    tracks_to_remove.append(track)
                    continue

                protection = self._check_song_protection(nd_id)
                should_delete, delete_reason = self._should_delete_song(protection, is_discover_weekly)

                if not should_delete:
                    # Keep the song
                    reasons = '; '.join(protection['reasons']) if protection['reasons'] else 'user interaction'
                    print(f"  KEEP: {label} ({reasons})")
                    kept_songs.append(f"{label} (rating={protection['max_rating']})")
                    tracks_to_remove.append(track)  # Remove from history (now permanent)

                    # Submit positive feedback for high-rated tracks
                    if protection['max_rating'] == 5:
                        if source_name == 'ListenBrainz' and self.listenbrainz_enabled:
                            mbid = track.get('recording_mbid', '') or song_details.get('musicBrainzId', '')
                            if mbid and listenbrainz_api:
                                await listenbrainz_api.submit_feedback(mbid, 1)
                        elif source_name == 'Last.fm' and self.lastfm_enabled:
                            if lastfm_api:
                                try:
                                    await asyncio.to_thread(lastfm_api.love_track, title, artist)
                                except Exception as e:
                                    print(f"  Error submitting Last.fm love: {e}")

                    if nd_id:
                        tracks_to_keep_ids.append(nd_id)
                else:
                    # Delete the song
                    print(f"  DELETE: {label} ({delete_reason})")
                    file_path = self._find_actual_song_path(file_rel_path, song_details)
                    if file_path and os.path.exists(file_path):
                        if self._delete_song(file_path):
                            # Remove from Navidrome DB to avoid 'missing file' entries
                            if nd_id:
                                self.remove_song_from_navidrome_db(nd_id)
                            deleted_songs.append(f"{label} ({delete_reason})")
                    else:
                        print(f"    File not found on disk")
                        # Still remove from DB if song entry exists
                        if nd_id:
                            self.remove_song_from_navidrome_db(nd_id)
                        deleted_songs.append(f"{label} (file not found)")
                    tracks_to_remove.append(track)

                    # Submit negative feedback for 1-star
                    if protection['has_one_star']:
                        if source_name == 'ListenBrainz' and self.listenbrainz_enabled:
                            mbid = track.get('recording_mbid', '') or song_details.get('musicBrainzId', '')
                            if mbid and listenbrainz_api:
                                await listenbrainz_api.submit_feedback(mbid, -1)

            # Remove processed tracks from history
            for track in tracks_to_remove:
                self.remove_from_download_history(history_path, source_name, track.get('artist', ''), track.get('title', ''))

            # Update playlist to remove deleted songs
            if existing_playlist and playlist_songs:
                processed_ids = {t.get('navidrome_id', '') for t in tracks}
                new_song_ids = []
                for ps in playlist_songs:
                    ps_id = ps.get('id', '')
                    if ps_id not in processed_ids or ps_id in tracks_to_keep_ids:
                        new_song_ids.append(ps_id)
                self._update_playlist(existing_playlist['id'], new_song_ids, salt, token)

        # Summary
        print(f"\n{'='*50}")
        print("CLEANUP SUMMARY")
        print(f"{'='*50}")
        if deleted_songs:
            print(f"\nDeleted {len(deleted_songs)} songs:")
            for s in deleted_songs:
                print(f"  - {s}")
        if kept_songs:
            print(f"\nKept {len(kept_songs)} songs (now permanent in library):")
            for s in kept_songs:
                print(f"  - {s}")
        if not deleted_songs and not kept_songs:
            print("\nNo songs processed.")

        print("\nRemoving empty folders...")
        from utils import remove_empty_folders
        remove_empty_folders(self.music_library_path)

        if deleted_songs:
            print("Triggering full library scan to remove deleted entries...")
            self._start_scan(full_scan=True)
            await self._wait_for_scan_async(timeout=60)

    async def process_debug_cleanup(self, history_path):
        """Debug cleanup with detailed logging. Returns summary dict."""
        import sys

        print(f"\n{'='*60}")
        print(f"[DEBUG CLEANUP] Starting...")
        print(f"[DEBUG CLEANUP] History: {history_path}")
        print(f"[DEBUG CLEANUP] Music library: {self.music_library_path}")
        print(f"{'='*60}")
        sys.stdout.flush()

        salt, token = self._get_navidrome_auth_params()
        history = self._load_download_history(history_path)

        if not history:
            print("[DEBUG CLEANUP] No download history - nothing to delete")
            print("[DEBUG CLEANUP] (Run a download first to populate history)")
            sys.stdout.flush()

        summary = {'deleted': [], 'kept': [], 'failed': [], 'playlists_cleared': []}

        playlist_name_map = {
            'ListenBrainz': 'ListenBrainz Weekly',
            'Last.fm': 'Last.fm Weekly',
            'LLM': 'LLM Weekly',
        }

        remaining_history = {}

        # Process download history
        if history:
            total_tracks = sum(len(v) for v in history.values())
            print(f"\n[DEBUG CLEANUP] Processing {total_tracks} tracked downloads")
            sys.stdout.flush()

            for source_name in list(history.keys()):
                tracks = history.get(source_name, [])
                if not tracks:
                    continue

                remaining_tracks = []
                print(f"\n[DEBUG CLEANUP] Source: {source_name} ({len(tracks)} tracks)")

                for track in tracks:
                    artist = track.get('artist', '')
                    title = track.get('title', '')
                    nd_id = track.get('navidrome_id', '')
                    file_rel_path = track.get('file_path', '')
                    label = f"{artist} - {title}"

                    print(f"[DEBUG CLEANUP]   {label} (id={nd_id})")

                    if not nd_id:
                        summary['failed'].append(f"{label} (no navidrome_id)")
                        remaining_tracks.append(track)
                        continue

                    song_details = self._get_song_details(nd_id, salt, token)
                    if song_details is None:
                        summary['deleted'].append(f"{label} (already gone)")
                        continue

                    protection = self._check_song_protection(nd_id)
                    if protection['protected']:
                        reasons = '; '.join(protection['reasons'])
                        print(f"[DEBUG CLEANUP]     PROTECTED: {reasons}")
                        summary['kept'].append(f"{label} ({reasons})")
                        continue

                    print(f"[DEBUG CLEANUP]     Not protected (rating={protection['max_rating']})")

                    file_path = self._find_actual_song_path(file_rel_path, song_details)
                    if file_path and os.path.exists(file_path):
                        if self._delete_song(file_path):
                            # Remove from Navidrome DB to avoid 'missing file' entries
                            self.remove_song_from_navidrome_db(nd_id)
                            summary['deleted'].append(f"{label}")
                        else:
                            summary['failed'].append(f"{label} (delete failed)")
                            remaining_tracks.append(track)
                    else:
                        # File doesn't exist - still remove from DB
                        self.remove_song_from_navidrome_db(nd_id)
                        summary['failed'].append(f"{label} (file not found, removed from DB)")

                    sys.stdout.flush()

                if remaining_tracks:
                    remaining_history[source_name] = remaining_tracks

        # Clear recommendation playlists
        print(f"\n[DEBUG CLEANUP] Clearing recommendation playlists")
        for source_name, playlist_name in playlist_name_map.items():
            existing_playlist = self._find_playlist_by_name(playlist_name, salt, token)
            if not existing_playlist:
                continue
            song_count = existing_playlist.get('songCount', 0)
            print(f"[DEBUG CLEANUP]   Clearing '{playlist_name}' ({song_count} songs)")
            self._update_playlist(existing_playlist['id'], [], salt, token)
            summary['playlists_cleared'].append(f"{playlist_name} ({song_count} songs)")
            sys.stdout.flush()

        # Save remaining history
        self._save_download_history(history_path, remaining_history)

        # Clear streamrip databases
        print(f"\n[DEBUG CLEANUP] Clearing streamrip databases")
        for db_file in ['/app/temp_downloads/downloads.db', '/app/temp_downloads/failed_downloads.db']:
            if os.path.exists(db_file):
                try:
                    os.remove(db_file)
                    print(f"[DEBUG CLEANUP]   Removed: {db_file}")
                except OSError as e:
                    print(f"[DEBUG CLEANUP]   Failed: {e}")

        # Remove empty folders
        print(f"\n[DEBUG CLEANUP] Removing empty folders")
        from utils import remove_empty_folders
        remove_empty_folders(self.music_library_path)

        # Trigger full scan to remove deleted entries from Navidrome
        print(f"\n[DEBUG CLEANUP] Triggering full library scan")
        self._start_scan(full_scan=True)
        print(f"[DEBUG CLEANUP] Waiting for scan to complete...")
        await self._wait_for_scan_async(timeout=60)

        print(f"\n{'='*60}")
        print(f"[DEBUG CLEANUP] SUMMARY")
        print(f"[DEBUG CLEANUP]   Deleted: {len(summary['deleted'])}")
        print(f"[DEBUG CLEANUP]   Kept: {len(summary['kept'])}")
        print(f"[DEBUG CLEANUP]   Failed: {len(summary['failed'])}")
        print(f"[DEBUG CLEANUP]   Playlists cleared: {len(summary['playlists_cleared'])}")
        print(f"{'='*60}")
        sys.stdout.flush()

        return summary

    def _get_user_id_by_username(self, username):
        """Look up a Navidrome user ID by username."""
        if not self.navidrome_db_path or not os.path.exists(self.navidrome_db_path):
            print(f"Navidrome DB path not configured or doesn't exist: {self.navidrome_db_path}")
            return None
        try:
            conn = sqlite3.connect(f"file:{self.navidrome_db_path}?mode=ro", uri=True)
            cursor = conn.cursor()
            # Try 'name' column first (standard Navidrome), fall back to 'user_name'
            cursor.execute("SELECT id, name FROM user WHERE name = ? OR user_name = ?", (username, username))
            row = cursor.fetchone()
            if not row:
                # Debug: list all users to help troubleshoot
                cursor.execute("SELECT id, name FROM user LIMIT 10")
                all_users = cursor.fetchall()
                print(f"Available users in Navidrome: {[u[1] for u in all_users]}")
            conn.close()
            return row[0] if row else None
        except Exception as e:
            print(f"Error looking up user ID: {e}")
            return None

    def star_song_for_user(self, song_id, username):
        """Add a song to a user's favorites by writing to Navidrome DB.

        Args:
            song_id: The Navidrome song ID
            username: The username to add the favorite for

        Returns:
            True if successful, False otherwise
        """
        if not self.navidrome_db_path or not os.path.exists(self.navidrome_db_path):
            print(f"Cannot star song: Navidrome DB path not configured")
            return False

        user_id = self._get_user_id_by_username(username)
        if not user_id:
            print(f"Cannot star song: user '{username}' not found")
            return False

        try:
            # Open with write access
            conn = sqlite3.connect(self.navidrome_db_path)
            cursor = conn.cursor()

            # Check if annotation already exists
            cursor.execute(
                "SELECT starred FROM annotation WHERE item_id = ? AND item_type = 'media_file' AND user_id = ?",
                (song_id, user_id)
            )
            row = cursor.fetchone()

            from datetime import datetime
            now = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')

            if row:
                # Update existing annotation
                cursor.execute(
                    "UPDATE annotation SET starred = 1, starred_at = ? WHERE item_id = ? AND item_type = 'media_file' AND user_id = ?",
                    (now, song_id, user_id)
                )
            else:
                # Insert new annotation - need to generate a UUID for the id column
                annotation_id = str(uuid.uuid4())
                cursor.execute(
                    "INSERT INTO annotation (ann_id, user_id, item_id, item_type, starred, starred_at) VALUES (?, ?, ?, 'media_file', 1, ?)",
                    (annotation_id, user_id, song_id, now)
                )

            conn.commit()
            conn.close()
            print(f"Starred song {song_id} for user {username}")
            return True
        except Exception as e:
            print(f"Error starring song: {e}")
            return False

    def remove_song_from_navidrome_db(self, song_id):
        """Remove a song and its annotations from Navidrome DB.

        Call this when intentionally deleting files to avoid 'missing file' entries.

        Args:
            song_id: The Navidrome song ID to remove

        Returns:
            True if successful, False otherwise
        """
        if not self.navidrome_db_path or not os.path.exists(self.navidrome_db_path):
            print(f"Cannot remove from DB: Navidrome DB path not configured")
            return False

        try:
            conn = sqlite3.connect(self.navidrome_db_path)
            cursor = conn.cursor()

            # Remove annotations (ratings, stars, play counts, etc.)
            cursor.execute("DELETE FROM annotation WHERE item_id = ? AND item_type = 'media_file'", (song_id,))
            annotations_deleted = cursor.rowcount

            # Remove from playlist_tracks
            cursor.execute("DELETE FROM playlist_tracks WHERE media_file_id = ?", (song_id,))
            playlist_refs_deleted = cursor.rowcount

            # Remove from media_file table
            cursor.execute("DELETE FROM media_file WHERE id = ?", (song_id,))
            files_deleted = cursor.rowcount

            conn.commit()
            conn.close()

            if files_deleted > 0:
                print(f"Removed song {song_id} from Navidrome DB (annotations: {annotations_deleted}, playlist refs: {playlist_refs_deleted})")
            return True
        except Exception as e:
            print(f"Error removing song from Navidrome DB: {e}")
            return False

    async def preview_manual_cleanup(self, username):
        """
        Preview what would be deleted by manual cleanup for a specific user.
        Only considers songs that THIS USER rated 1-star.

        Args:
            username: The username whose 1-star ratings to check

        Returns:
            Dict with 'to_delete' and 'to_keep' lists for confirmation
        """
        results = {
            'to_delete': [],
            'to_keep': [],
            'scanned': 0,
            'errors': []
        }

        if not self.navidrome_db_path or not os.path.exists(self.navidrome_db_path):
            results['errors'].append("Navidrome database path not configured")
            return results

        user_id = self._get_user_id_by_username(username)
        if not user_id:
            results['errors'].append(f"Could not find user ID for '{username}'")
            return results

        try:
            conn = sqlite3.connect(f"file:{self.navidrome_db_path}?mode=ro", uri=True)
            cursor = conn.cursor()

            # Find songs rated 1 star or less BY THIS USER (includes half-star)
            # Navidrome uses integer scale: 1 = 1 star, 2 = 2 stars, etc.
            cursor.execute("""
                SELECT DISTINCT a.item_id, mf.title, mf.artist, mf.album, mf.path
                FROM annotation a
                JOIN media_file mf ON a.item_id = mf.id
                WHERE a.item_type = 'media_file' AND a.rating > 0 AND a.rating <= 1 AND a.user_id = ?
            """, (user_id,))

            low_rated_songs = cursor.fetchall()
            conn.close()

            results['scanned'] = len(low_rated_songs)

            for song_id, title, artist, album, db_path in low_rated_songs:
                protection = self._check_song_protection(song_id)

                # Can delete if no OTHER user has protected it
                # (we ignore the current user's 1-star since that's what triggered this)
                other_user_protection = (
                    protection['is_starred'] or
                    protection['in_user_playlist'] or
                    protection['max_rating'] > 2
                )

                song_info = {
                    'navidrome_id': song_id,
                    'artist': artist,
                    'title': title,
                    'album': album,
                    'path': db_path
                }

                if not other_user_protection:
                    song_info['reason'] = 'No other user protection'
                    results['to_delete'].append(song_info)
                else:
                    keep_reasons = []
                    if protection['is_starred']:
                        keep_reasons.append("favorited by another user")
                    if protection['in_user_playlist']:
                        keep_reasons.append("in a user playlist")
                    if protection['max_rating'] > 2:
                        keep_reasons.append(f"rated {protection['max_rating']}/5 by another user")
                    song_info['reason'] = '; '.join(keep_reasons)
                    results['to_keep'].append(song_info)

        except Exception as e:
            results['errors'].append(f"Database error: {str(e)}")
            print(f"Error during cleanup preview: {e}")

        return results

    async def process_manual_cleanup(self, username, song_ids=None):
        """
        Manual cleanup: Delete specified 1-star songs for a user.
        Only deletes songs from the provided list (from preview confirmation).

        Args:
            username: The username performing the cleanup
            song_ids: List of Navidrome song IDs to delete (from preview)

        Returns:
            Dict with 'deleted' and 'errors' lists
        """
        results = {
            'deleted': [],
            'errors': []
        }

        if not song_ids:
            results['errors'].append("No songs specified for deletion")
            return results

        if not self.navidrome_db_path or not os.path.exists(self.navidrome_db_path):
            results['errors'].append("Navidrome database path not configured")
            return results

        salt, token = self._get_navidrome_auth_params()

        for song_id in song_ids:
            try:
                song_details = self._get_song_details(song_id, salt, token)
                if not song_details:
                    results['errors'].append(f"Song not found: {song_id}")
                    continue

                artist = song_details.get('artist', 'Unknown')
                title = song_details.get('title', 'Unknown')
                label = f"{artist} - {title}"

                # Get the file path
                db_path = song_details.get('path', '')
                file_path = self._find_actual_song_path(db_path, song_details)

                if file_path and os.path.exists(file_path):
                    if self._delete_song(file_path):
                        # Remove from Navidrome DB to avoid 'missing file' entries
                        self.remove_song_from_navidrome_db(song_id)
                        results['deleted'].append({
                            'artist': artist,
                            'title': title,
                            'album': song_details.get('album', '')
                        })
                    else:
                        results['errors'].append(f"Failed to delete file: {label}")
                else:
                    # File doesn't exist on disk - still remove from DB
                    self.remove_song_from_navidrome_db(song_id)
                    results['errors'].append(f"File not found (removed from DB): {label}")

            except Exception as e:
                results['errors'].append(f"Error processing {song_id}: {str(e)}")

        # Clean up empty folders and trigger scan
        if results['deleted']:
            from utils import remove_empty_folders
            remove_empty_folders(self.music_library_path)
            self._start_scan(full_scan=True)
            await self._wait_for_scan_async(timeout=60)

        return results

    # ---- File Organization ----

    def organize_music_files(self, source_folder, destination_base_folder):
        """Organize music files from source to destination using Artist/Album structure."""
        from mutagen.id3 import ID3
        from mutagen.flac import FLAC
        from mutagen.mp4 import MP4
        from mutagen.oggvorbis import OggVorbis

        moved_files = {}

        audio_extensions = ('.mp3', '.flac', '.m4a', '.aac', '.ogg', '.wma')

        for root, dirs, files in os.walk(source_folder):
            for filename in files:
                if not filename.lower().endswith(audio_extensions):
                    continue

                file_path = os.path.join(root, filename)
                file_ext = os.path.splitext(filename)[1].lower()

                try:
                    def _get_tag(tags, key, default=''):
                        val = tags.get(key)
                        if val:
                            return val[0] if isinstance(val, list) else str(val)
                        return default

                    if file_ext == '.mp3':
                        audio = ID3(file_path)
                        folder_artist = str(audio.get('TPE2', [None])[0] or audio.get('TPE1', [None])[0] or 'Unknown Artist')
                        album = str(audio.get('TALB', ['Unknown Album'])[0])
                        title = str(audio.get('TIT2', [os.path.splitext(filename)[0]])[0])
                    elif file_ext == '.flac':
                        audio = FLAC(file_path)
                        folder_artist = _get_tag(audio, 'albumartist') or _get_tag(audio, 'artist', 'Unknown Artist')
                        album = _get_tag(audio, 'album', 'Unknown Album')
                        title = _get_tag(audio, 'title', os.path.splitext(filename)[0])
                    elif file_ext in ('.m4a', '.aac'):
                        audio = MP4(file_path)
                        folder_artist = _get_tag(audio, 'aART') or _get_tag(audio, '\xa9ART', 'Unknown Artist')
                        album = _get_tag(audio, '\xa9alb', 'Unknown Album')
                        title = _get_tag(audio, '\xa9nam', os.path.splitext(filename)[0])
                    elif file_ext in ('.ogg', '.wma'):
                        audio = OggVorbis(file_path)
                        folder_artist = _get_tag(audio, 'albumartist') or _get_tag(audio, 'artist', 'Unknown Artist')
                        album = _get_tag(audio, 'album', 'Unknown Album')
                        title = _get_tag(audio, 'title', os.path.splitext(filename)[0])
                    else:
                        folder_artist = "Unknown Artist"
                        album = "Unknown Album"
                        title = os.path.splitext(filename)[0]

                    folder_artist = sanitize_filename(folder_artist)
                    album = sanitize_filename(album)
                    title = sanitize_filename(title)

                    album_folder = os.path.join(destination_base_folder, folder_artist, album)
                    new_filename = f"{title}{file_ext}"
                    new_file_path = os.path.join(album_folder, new_filename)

                    counter = 1
                    while os.path.exists(new_file_path):
                        new_filename = f"{title} ({counter}){file_ext}"
                        new_file_path = os.path.join(album_folder, new_filename)
                        counter += 1

                    os.makedirs(album_folder, exist_ok=True)
                    shutil.move(file_path, new_file_path)
                    moved_files[file_path] = new_file_path

                except Exception as e:
                    print(f"Error organizing '{filename}': {e}")
                    unorganized_folder = os.path.join(destination_base_folder, "Unorganized")
                    os.makedirs(unorganized_folder, exist_ok=True)
                    shutil.move(file_path, os.path.join(unorganized_folder, filename))

        # Clean up
        def remove_empty_dirs(path):
            for root, dirs, files in os.walk(path, topdown=False):
                for dir_name in dirs:
                    dir_path = os.path.join(root, dir_name)
                    try:
                        os.rmdir(dir_path)
                    except OSError:
                        pass

        artwork_folder = os.path.join(source_folder, "__artwork")
        if os.path.exists(artwork_folder):
            try:
                shutil.rmtree(artwork_folder)
            except Exception:
                pass

        remove_empty_dirs(source_folder)
        os.system(f'chown -R 1000:1000 "{destination_base_folder}"')

        return moved_files
