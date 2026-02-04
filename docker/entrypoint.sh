#!/bin/bash

export REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt

# Fix permissions for mounted volumes
mkdir -p /app/data
chown -R 1000:1000 /app/music /app/temp_downloads /app/data

# Generate config.py from environment variables
echo "# Generated config.py from Docker environment variables" > config.py
echo "import os" >> config.py
echo "" >> config.py

# Navidrome Configuration
echo "ROOT_ND = os.getenv(\"ROOT_ND\", \"${TRACKDROP_ROOT_ND:-}\")" >> config.py
echo "USER_ND = os.getenv(\"USER_ND\", \"${TRACKDROP_USER_ND:-}\")" >> config.py
echo "PASSWORD_ND = os.getenv(\"PASSWORD_ND\", \"${TRACKDROP_PASSWORD_ND:-}\")" >> config.py
echo "MUSIC_LIBRARY_PATH = os.getenv(\"MUSIC_LIBRARY_PATH\", \"${TRACKDROP_MUSIC_LIBRARY_PATH:-/app/music}\")" >> config.py
echo "# MUSIC_DOWNLOAD_PATH: where to organize downloads (defaults to MUSIC_LIBRARY_PATH)" >> config.py
echo "MUSIC_DOWNLOAD_PATH = os.getenv(\"MUSIC_DOWNLOAD_PATH\", \"${TRACKDROP_MUSIC_DOWNLOAD_PATH:-}\")" >> config.py
echo "if not MUSIC_DOWNLOAD_PATH: MUSIC_DOWNLOAD_PATH = MUSIC_LIBRARY_PATH" >> config.py
echo "TEMP_DOWNLOAD_FOLDER = os.getenv(\"TEMP_DOWNLOAD_FOLDER\", \"/app/temp_downloads\")" >> config.py
echo "" >> config.py

# ListenBrainz API Configuration (Optional)
echo "LISTENBRAINZ_ENABLED = os.getenv(\"LISTENBRAINZ_ENABLED\", \"${TRACKDROP_LISTENBRAINZ_ENABLED:-False}\").lower() == \"true\"" >> config.py
echo "ROOT_LB = os.getenv(\"ROOT_LB\", \"${TRACKDROP_ROOT_LB:-https://api.listenbrainz.org}\")" >> config.py
echo "TOKEN_LB = os.getenv(\"TOKEN_LB\", \"${TRACKDROP_TOKEN_LB:-}\")" >> config.py
echo "USER_LB = os.getenv(\"USER_LB\", \"${TRACKDROP_USER_LB:-}\")" >> config.py
echo "" >> config.py

# Last.fm API Configuration (only username needed for recommendations - per-user setting)
echo "LASTFM_ENABLED = os.getenv(\"LASTFM_ENABLED\", \"${TRACKDROP_LASTFM_ENABLED:-False}\").lower() == \"true\"" >> config.py
echo "" >> config.py

# LLM Suggestions Settings
echo "LLM_ENABLED = os.getenv(\"LLM_ENABLED\", \"${TRACKDROP_LLM_ENABLED:-false}\").lower() == \"true\"" >> config.py
echo "LLM_PROVIDER = os.getenv(\"LLM_PROVIDER\", \"${TRACKDROP_LLM_PROVIDER:-gemini}\")" >> config.py
echo "LLM_API_KEY = os.getenv(\"LLM_API_KEY\", \"${TRACKDROP_LLM_API_KEY:-}\")" >> config.py
echo "LLM_MODEL_NAME = os.getenv(\"LLM_MODEL_NAME\", \"${TRACKDROP_LLM_MODEL_NAME:-}\")" >> config.py
echo "LLM_BASE_URL = os.getenv(\"LLM_BASE_URL\", \"${TRACKDROP_LLM_BASE_URL:-}\")" >> config.py
echo "" >> config.py

# Download History Path (for API playlist mode tracking)
echo "DOWNLOAD_HISTORY_PATH = os.getenv(\"DOWNLOAD_HISTORY_PATH\", \"${TRACKDROP_DOWNLOAD_HISTORY_PATH:-/app/data/download_history.json}\")" >> config.py
echo "" >> config.py

# Admin credentials for library scan (startScan requires admin)
echo "ADMIN_USER = os.getenv(\"ADMIN_USER\", \"${TRACKDROP_ADMIN_USER:-}\")" >> config.py
echo "ADMIN_PASSWORD = os.getenv(\"ADMIN_PASSWORD\", \"${TRACKDROP_ADMIN_PASSWORD:-}\")" >> config.py
echo "NAVIDROME_DB_PATH = \"${TRACKDROP_NAVIDROME_DB_PATH:-}\"" >> config.py
echo "" >> config.py

# Spotify API Configuration (for playlist extraction)
echo "SPOTIFY_CLIENT_ID = os.getenv(\"SPOTIFY_CLIENT_ID\", \"${TRACKDROP_SPOTIFY_CLIENT_ID:-}\")" >> config.py
echo "SPOTIFY_CLIENT_SECRET = os.getenv(\"SPOTIFY_CLIENT_SECRET\", \"${TRACKDROP_SPOTIFY_CLIENT_SECRET:-}\")" >> config.py
echo "" >> config.py

# Deezer Configuration (Optional - can be configured via web UI)
echo "DEEZER_ARL = os.getenv(\"DEEZER_ARL\", \"${TRACKDROP_DEEZER_ARL:-}\")" >> config.py
echo "" >> config.py

# Download Method (choose one)
echo "DOWNLOAD_METHOD = os.getenv(\"DOWNLOAD_METHOD\", \"${TRACKDROP_DOWNLOAD_METHOD:-streamrip}\")" >> config.py
echo "" >> config.py

# Album Recommendation Settings
echo "ALBUM_RECOMMENDATION_ENABLED = os.getenv(\"ALBUM_RECOMMENDATION_ENABLED\", \"${TRACKDROP_ALBUM_RECOMMENDATION_ENABLED:-false}\").lower() == \"true\"" >> config.py
echo "" >> config.py

# UI Visibility Settings
echo "HIDE_DOWNLOAD_FROM_LINK = os.getenv(\"HIDE_DOWNLOAD_FROM_LINK\", \"${TRACKDROP_HIDE_DOWNLOAD_FROM_LINK:-false}\").lower() == \"true\"" >> config.py
echo "HIDE_FRESH_RELEASES = os.getenv(\"HIDE_FRESH_RELEASES\", \"${TRACKDROP_HIDE_FRESH_RELEASES:-false}\").lower() == \"true\"" >> config.py
echo "" >> config.py

# Caching for fresh releases (in seconds)
echo "FRESH_RELEASES_CACHE_DURATION = int(os.getenv(\"FRESH_RELEASES_CACHE_DURATION\", \"${TRACKDROP_FRESH_RELEASES_CACHE_DURATION:-300}\"))" >> config.py
echo "" >> config.py

# Deezer API Rate Limiting
echo "DEEZER_MAX_CONCURRENT_REQUESTS = int(os.getenv(\"DEEZER_MAX_CONCURRENT_REQUESTS\", \"${TRACKDROP_DEEZER_MAX_CONCURRENT_REQUESTS:-3}\"))" >> config.py
echo "" >> config.py

# Set up cron job from persisted user settings (or defaults)
mkdir -p /app/logs
touch /app/logs/trackdrop.log
# Rebuild cron from per-user data files (user_*.json) or use default
echo "Restoring cron schedules from persisted user settings..."
/usr/local/bin/python3 -c "
import json, os, glob
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

def convert_local_to_utc(minute, hour, day_of_week, timezone_str):
    try:
        local_tz = ZoneInfo(timezone_str)
    except Exception:
        return minute, hour, day_of_week
    today = datetime.now()
    python_weekday = (day_of_week - 1) % 7
    days_ahead = (python_weekday - today.weekday()) % 7
    ref_date = today + timedelta(days=days_ahead)
    local_dt = datetime(ref_date.year, ref_date.month, ref_date.day, hour, minute, tzinfo=local_tz)
    utc_dt = local_dt.astimezone(ZoneInfo('UTC'))
    day_diff = (utc_dt.date() - local_dt.replace(tzinfo=None).date()).days
    utc_day = (day_of_week + day_diff) % 7
    return utc_dt.minute, utc_dt.hour, utc_day

data_dir = '/app/data'
cron_lines = []

# Read per-user data files (new format: user_<username>.json)
for filepath in glob.glob(os.path.join(data_dir, 'user_*.json')):
    filename = os.path.basename(filepath)
    # Extract username from filename (user_<username>.json)
    if filename.startswith('user_') and filename.endswith('.json'):
        username = filename[5:-5]  # Remove 'user_' prefix and '.json' suffix
        try:
            with open(filepath, 'r') as f:
                settings = json.load(f).get('settings', {})
            if not settings.get('cron_enabled', True):
                continue
            minute = settings.get('cron_minute', 0)
            hour = settings.get('cron_hour', 0)
            day = settings.get('cron_day', 1)  # Default Monday
            timezone = settings.get('cron_timezone', 'UTC')
            utc_minute, utc_hour, utc_day = convert_local_to_utc(minute, hour, day, timezone)
            cron_lines.append(f'{utc_minute} {utc_hour} * * {utc_day} root /usr/local/bin/python3 /app/trackdrop.py --user {username} >> /proc/1/fd/1 2>&1')
        except Exception as e:
            print(f'Error reading {filepath}: {e}')

if cron_lines:
    with open('/etc/cron.d/trackdrop-cron', 'w') as f:
        f.write('\n'.join(cron_lines) + '\n')
    os.chmod('/etc/cron.d/trackdrop-cron', 0o644)
    print(f'Restored {len(cron_lines)} cron schedule(s)')
else:
    print('No enabled cron schedules found in user settings, using default (Monday 00:00)')
    # Write default so cron has something
    with open('/etc/cron.d/trackdrop-cron', 'w') as f:
        f.write('0 0 * * 1 root /usr/local/bin/python3 /app/trackdrop.py >> /proc/1/fd/1 2>&1\n')
    os.chmod('/etc/cron.d/trackdrop-cron', 0o644)
"

# Replace ARL placeholder in streamrip_config.toml
# Use temp file + cat to avoid sed -i rename failures on overlay/mounted filesystems
STREAMRIP_CONFIG="/root/.config/streamrip/config.toml"
if [ -n "${TRACKDROP_DEEZER_ARL}" ]; then
    sed "s|arl = \"REPLACE_WITH_ARL\"|arl = \"${TRACKDROP_DEEZER_ARL}\"|" "$STREAMRIP_CONFIG" > "${STREAMRIP_CONFIG}.tmp" && cat "${STREAMRIP_CONFIG}.tmp" > "$STREAMRIP_CONFIG" && rm "${STREAMRIP_CONFIG}.tmp"
    # Create .arl file for deemix in /root/.config/deemix/
    echo "${TRACKDROP_DEEZER_ARL}" > /root/.config/deemix/.arl
fi

# Replace downloads folder in streamrip_config.toml
sed 's|folder = "/home/ubuntu/StreamripDownloads"|folder = "/app/temp_downloads"|' "$STREAMRIP_CONFIG" > "${STREAMRIP_CONFIG}.tmp" && cat "${STREAMRIP_CONFIG}.tmp" > "$STREAMRIP_CONFIG" && rm "${STREAMRIP_CONFIG}.tmp"

# Set Deezer quality to 2 (FLAC lossless) in streamrip_config.toml
sed '/^\[deezer\]/,/^\[[a-z]*\]/ s/quality = [0-9]*/quality = 2/' "$STREAMRIP_CONFIG" > "${STREAMRIP_CONFIG}.tmp" && cat "${STREAMRIP_CONFIG}.tmp" > "$STREAMRIP_CONFIG" && rm "${STREAMRIP_CONFIG}.tmp"

# Deemix Configuration - set maxBitrate to 9 (FLAC) for lossless downloads
DEEMIX_CONFIG_PATH="/root/.config/deemix/config.json"
if [ ! -f "$DEEMIX_CONFIG_PATH" ]; then
    echo "Creating default deemix config.json"
    mkdir -p "$(dirname "$DEEMIX_CONFIG_PATH")"
    echo '{"maxBitrate": "9"}' > "$DEEMIX_CONFIG_PATH"
else
    echo "Updating deemix config.json"
    jq '.maxBitrate = "9"' "$DEEMIX_CONFIG_PATH" > "$DEEMIX_CONFIG_PATH.tmp" && mv "$DEEMIX_CONFIG_PATH.tmp" "$DEEMIX_CONFIG_PATH"
fi

# Start syslog service (required for cron)
# Disable kernel log module since containers can't access /proc/kmsg
if [ -f /etc/rsyslog.conf ]; then
    sed 's/^module(load="imklog")/#module(load="imklog")/' /etc/rsyslog.conf > /etc/rsyslog.conf.tmp && cat /etc/rsyslog.conf.tmp > /etc/rsyslog.conf && rm /etc/rsyslog.conf.tmp
fi
rsyslogd

# Give syslog a moment to start
sleep 2

# Start cron service
cron &

# Start Gunicorn server for the Flask app as the main process (PID 1)
# This ensures proper SIGTERM handling for graceful shutdown
exec gunicorn --bind 0.0.0.0:5000 --timeout 300 "web_ui.app:app"
