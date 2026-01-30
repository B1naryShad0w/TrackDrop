"""User authentication, settings persistence, and session management."""

import hashlib
import json
import os
import random
import string
import threading
from functools import wraps

import requests
from flask import redirect, session, url_for

import config

SETTINGS_FILE = os.getenv("RECOMMAND_USER_SETTINGS_PATH", "/app/data/user_settings.json")

DEFAULT_SETTINGS = {
    "listenbrainz_enabled": False,
    "listenbrainz_username": "",
    "listenbrainz_token": "",
    "lastfm_enabled": False,
    "lastfm_username": "",
    "lastfm_password": "",
    "lastfm_api_key": "",
    "lastfm_api_secret": "",
    "lastfm_session_key": "",
    "cron_minute": 0,
    "cron_hour": 0,
    "cron_day": 2,
    "cron_timezone": "UTC",
    "cron_enabled": True,
    "playlist_sources": ["listenbrainz", "lastfm"],
    "first_time_setup_done": False,
}


def authenticate_navidrome(username, password):
    """Validate credentials against Navidrome's Subsonic API using MD5+salt auth."""
    salt = "".join(random.choices(string.ascii_lowercase + string.digits, k=12))
    token = hashlib.md5((password + salt).encode()).hexdigest()
    try:
        resp = requests.get(
            f"{config.ROOT_ND}/rest/ping.view",
            params={
                "u": username,
                "t": token,
                "s": salt,
                "v": "1.16.1",
                "c": "re-command",
                "f": "json",
            },
            timeout=10,
        )
        data = resp.json()
        return data.get("subsonic-response", {}).get("status") == "ok"
    except Exception:
        return False


class UserManager:
    """Thread-safe per-user settings stored in a JSON file."""

    def __init__(self, settings_file=SETTINGS_FILE):
        self._file = settings_file
        self._lock = threading.Lock()

    def _load(self):
        if not os.path.exists(self._file):
            return {}
        with open(self._file, "r") as f:
            return json.load(f)

    def _save(self, data):
        os.makedirs(os.path.dirname(self._file), exist_ok=True)
        with open(self._file, "w") as f:
            json.dump(data, f, indent=2)

    def authenticate(self, username, password):
        """Validate credentials against Navidrome."""
        return authenticate_navidrome(username, password)

    def get_user_settings(self, username):
        """Return settings for a user, filling in defaults for missing keys."""
        with self._lock:
            data = self._load()
        user = data.get(username, {})
        merged = dict(DEFAULT_SETTINGS)
        merged.update(user)
        return merged

    def update_user_settings(self, username, settings_dict):
        """Merge updates into a user's settings. Returns True on success."""
        with self._lock:
            data = self._load()
            current = data.get(username, {})
            current.update(settings_dict)
            data[username] = current
            self._save(data)
        return True

    def is_first_time(self, username):
        """Check whether the user has completed first-time setup."""
        return not self.get_user_settings(username).get("first_time_setup_done", False)

    def mark_setup_done(self, username):
        """Mark first-time setup as complete for a user."""
        self.update_user_settings(username, {"first_time_setup_done": True})

    def get_all_users(self):
        """Return a list of all usernames with stored settings."""
        with self._lock:
            data = self._load()
        return list(data.keys())


# ---------------------------------------------------------------------------
# Flask session helpers
# ---------------------------------------------------------------------------

def get_current_user():
    """Return the currently logged-in username, or None."""
    return session.get("username")


def login_required(f):
    """Decorator that redirects to /login when no session is active."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if "username" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated
