# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

TrackDrop is a containerized music discovery and download system for Navidrome. It fetches recommendations from ListenBrainz, Last.fm, and LLM providers, downloads FLAC-quality music via Streamrip or Deemix, and organizes the library with proper metadata tagging.

## Commands

### Running Locally
```bash
# Download recommendations from all enabled sources
python trackdrop.py

# Download from specific source
python trackdrop.py --source listenbrainz
python trackdrop.py --source lastfm
python trackdrop.py --source llm
python trackdrop.py --source fresh_releases

# Run cleanup (process ratings and delete low-rated tracks)
python trackdrop.py --cleanup

# Run with specific user tracking
python trackdrop.py --user myusername

# Run Flask web UI
python web_ui/app.py
```

### Docker
```bash
docker compose -f docker/docker-compose.yml up -d
docker logs trackdrop
```

### Testing
```bash
pytest
pytest -x  # Stop on first failure
```

## Architecture

### Entry Points
- **`trackdrop.py`** - CLI for batch recommendation downloads and cleanup operations
- **`web_ui/app.py`** - Flask web UI (port 5000) for interactive downloads and configuration

### Core Components

**APIs (`apis/`)**
- `navidrome_api.py` - Subsonic API integration for library management, playlist creation, and rating-based cleanup
- `listenbrainz_api.py` - Weekly recommendations and fresh releases from ListenBrainz
- `lastfm_api.py` - Last.fm recommendations (public endpoint, only requires username)
- `deezer_api.py` - Track search with album-aware matching and fuzzy string normalization
- `llm_api.py` - LLM-powered recommendations (Gemini, OpenRouter, Llama)

**Downloaders (`downloaders/`)**
- `track_downloader.py` - Single track downloads with Streamrip/Deemix, metadata tagging, duplicate detection
- `playlist_downloader.py` - Multi-platform playlist extraction (Deezer, Spotify, YouTube, Tidal)
- `link_downloader.py` - Universal link resolution via Songlink/Odesli with YouTube fallback
- `album_downloader.py` - Full album downloads using TrackDownloader

**Web UI (`web_ui/`)**
- `app.py` - Flask app with authentication, download queue, cron scheduling, PWA support
- `user_manager.py` - User authentication via Navidrome credentials, per-user settings

**Utils Package (`utils/`)**
- `core.py` - Metadata tagging via Mutagen (MP3, FLAC, OGG, M4A), file operations, streamrip DB management
- `http.py` - HTTP utilities with retry logic
- `text.py` - String normalization and text processing

**Persistence (`persistence/`)**
- `data_store.py` - Unified thread-safe JSON-based data store for user settings, download history, pending cleanup, monitored playlists

**Other Key Files**
- `config.py` - Centralized configuration from environment variables
- `playlist_monitor.py` - Background scheduler for auto-syncing monitored playlists

### Data Flow

1. **Recommendations**: APIs fetch track lists → DeezerAPI searches for tracks → TrackDownloader downloads → Tagger adds metadata → NavidromeAPI organizes files and creates playlists
2. **Web Downloads**: Link submitted → Songlink resolves platform → Downloader processes → Status file updated → Flask polls for UI updates

### Data Storage

Per-user data is stored in unified JSON files:
```
/app/data/user_{username}.json   # Settings, download history, pending cleanup, monitored playlists
```

The DataStore class handles thread-safe access with RLock and auto-migrates from legacy scattered JSON files.

### Playlist Modes
- **Tags mode**: Metadata comments trigger Navidrome smart playlists
- **API mode**: Direct playlist management via Subsonic API

### Key Paths (Docker)
```
/app/music/                           # Music library
/app/temp_downloads/                  # Download staging
/app/data/                            # Persistent user data
/root/.config/streamrip/config.toml   # Streamrip configuration
```

## Configuration

Configuration is generated from environment variables in Docker (see `docker/entrypoint.sh`). Key variables:
- `TRACKDROP_ROOT_ND`, `TRACKDROP_USER_ND`, `TRACKDROP_PASSWORD_ND` - Navidrome connection
- `TRACKDROP_DEEZER_ARL` - Deezer authentication token
- `TRACKDROP_DOWNLOAD_METHOD` - `streamrip` (default) or `deemix`
- `TRACKDROP_PLAYLIST_MODE` - `tags` or `api`
- `TRACKDROP_LLM_ENABLED`, `TRACKDROP_LLM_PROVIDER`, `TRACKDROP_LLM_API_KEY` - LLM support
- `TRACKDROP_SPOTIFY_CLIENT_ID`, `TRACKDROP_SPOTIFY_CLIENT_SECRET` - Spotify playlist extraction

## Code Conventions

- Async operations use `aiohttp`/`aiofiles` for API calls and file I/O
- Metadata tagging uses Mutagen with format-specific handlers in `utils/core.py:Tagger`
- Album-aware duplicate detection checks artist + title + album before downloading
- Thread safety via RLock in DataStore for concurrent access
- Per-user state stored in unified JSON files via `persistence/data_store.py`

## Local Deployment (Server Scripts)

Scripts in `/root/` manage local Docker builds and deployments:

| Script | Purpose |
|--------|---------|
| `update_main.sh` | Pull `main` branch → build → tag as `trackdrop:latest` |
| `update_beta.sh` | Pull feature branch → build → tag as `trackdrop:dev` |
| `switch_to_main.sh` | Switch running container to `trackdrop:latest` |
| `switch_to_dev.sh` | Switch running container to `trackdrop:dev` |

**Workflow:**
1. Develop on feature branches, test with `update_beta.sh` (`trackdrop:dev`)
2. Merge to `main`, deploy with `update_main.sh` (`trackdrop:latest`)
3. Use `switch_to_*.sh` to toggle between images without rebuilding
