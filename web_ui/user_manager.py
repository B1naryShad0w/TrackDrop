"""User authentication, settings persistence, and session management.

This module now delegates to the unified DataStore for persistence
while maintaining backward compatibility with existing code.
"""

import hashlib
import random
import string
from functools import wraps

import requests
from flask import redirect, session, url_for

import config
from data.data_store import get_data_store, DEFAULT_USER_SETTINGS

# Re-export DEFAULT_SETTINGS for backward compatibility
DEFAULT_SETTINGS = DEFAULT_USER_SETTINGS


def authenticate_navidrome(username, password):
    """Validate credentials against Navidrome's Subsonic API using MD5+salt auth.

    Returns (success: bool, error_reason: str or None).
    error_reason is None on success, 'offline' if Navidrome is unreachable,
    or 'invalid' if the credentials are wrong.
    """
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
                "c": "trackdrop",
                "f": "json",
            },
            timeout=10,
        )
        data = resp.json()
        if data.get("subsonic-response", {}).get("status") == "ok":
            return True, None
        return False, "invalid"
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
        return False, "offline"
    except Exception:
        return False, "offline"


class UserManager:
    """Thread-safe per-user settings management.

    This class now delegates to the unified DataStore for persistence
    while maintaining the same API for backward compatibility.
    """

    def __init__(self, settings_file=None):
        # The settings_file parameter is kept for backward compatibility but ignored
        self._data_store = get_data_store()

    def authenticate(self, username, password):
        """Validate credentials against Navidrome.

        Returns (success, error_reason) where error_reason is None on success,
        'offline' if Navidrome is unreachable, or 'invalid' for bad credentials.
        """
        return authenticate_navidrome(username, password)

    def get_user_settings(self, username):
        """Return settings for a user, filling in defaults for missing keys."""
        return self._data_store.get_user_settings(username)

    def update_user_settings(self, username, settings_dict):
        """Merge updates into a user's settings. Returns True on success."""
        return self._data_store.update_user_settings(username, settings_dict)

    def is_first_time(self, username):
        """Check whether the user has completed first-time setup."""
        return self._data_store.is_first_time(username)

    def mark_setup_done(self, username):
        """Mark first-time setup as complete for a user."""
        self._data_store.mark_setup_done(username)

    def get_all_users(self):
        """Return a list of all usernames with stored settings."""
        return self._data_store.get_all_users()

    def generate_api_key(self, username):
        """Generate a new API key for the user and save it."""
        return self._data_store.generate_api_key(username)

    def get_user_by_api_key(self, api_key):
        """Look up username by API key. Returns None if not found."""
        return self._data_store.get_user_by_api_key(api_key)


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
