#!/usr/bin/env python3
"""
TrackDrop - CLI entry point for automated music downloads.

Downloads recommendations from ListenBrainz, Last.fm, or LLM and manages
them in Navidrome playlists using API-based playlist management.
"""

import argparse
import asyncio
import sys

import json
import os

from config import (
    ROOT_ND, USER_ND, PASSWORD_ND, MUSIC_LIBRARY_PATH, MUSIC_DOWNLOAD_PATH, TEMP_DOWNLOAD_FOLDER,
    LISTENBRAINZ_ENABLED, ROOT_LB, TOKEN_LB, USER_LB,
    LASTFM_ENABLED, LASTFM_API_KEY, LASTFM_API_SECRET, LASTFM_USERNAME,
    LASTFM_PASSWORD, LASTFM_SESSION_KEY,
    LLM_ENABLED, LLM_PROVIDER, LLM_API_KEY, LLM_MODEL_NAME,
    ALBUM_RECOMMENDATION_ENABLED, DEEZER_ARL, DOWNLOAD_METHOD,
    ADMIN_USER, ADMIN_PASSWORD, NAVIDROME_DB_PATH,
)
from utils import initialize_streamrip_db, update_status_file, get_user_history_path, Tagger


def load_user_settings(username):
    """Load user-specific settings from the user settings file.

    Returns a dict with user settings, or empty dict if not found.
    """
    settings_file = os.getenv("TRACKDROP_USER_SETTINGS_PATH", "/app/data/user_settings.json")
    if not os.path.exists(settings_file):
        return {}
    try:
        with open(settings_file, 'r') as f:
            all_settings = json.load(f)
        return all_settings.get(username, {})
    except (json.JSONDecodeError, IOError) as e:
        print(f"Warning: Could not load user settings: {e}")
        return {}


def create_navidrome_api(username=None, user_settings=None):
    """Create a NavidromeAPI instance with current configuration.

    If username and user_settings are provided, use the user's stored password
    for playlist creation under their account. Otherwise use global config.
    """
    from apis.navidrome_api import NavidromeAPI

    # Use user's stored password if available (for per-user playlists)
    if username and user_settings and user_settings.get('navidrome_password'):
        user = username.lower()
        password = user_settings.get('navidrome_password')
    else:
        user = USER_ND
        password = PASSWORD_ND

    return NavidromeAPI(
        root_nd=ROOT_ND,
        user_nd=user,
        password_nd=password,
        music_library_path=MUSIC_LIBRARY_PATH,
        listenbrainz_enabled=LISTENBRAINZ_ENABLED,
        lastfm_enabled=LASTFM_ENABLED,
        llm_enabled=LLM_ENABLED,
        admin_user=ADMIN_USER,
        admin_password=ADMIN_PASSWORD,
        navidrome_db_path=NAVIDROME_DB_PATH,
    )


def create_listenbrainz_api(user_settings=None):
    """Create a ListenBrainzAPI instance.

    If user_settings is provided, use those values; otherwise fall back to global config.
    """
    from apis.listenbrainz_api import ListenBrainzAPI

    # Use user settings if available, otherwise global config
    if user_settings and user_settings.get('listenbrainz_enabled'):
        token = user_settings.get('listenbrainz_token') or TOKEN_LB
        user = user_settings.get('listenbrainz_username') or USER_LB
        enabled = True
    else:
        token = TOKEN_LB
        user = USER_LB
        enabled = LISTENBRAINZ_ENABLED

    return ListenBrainzAPI(
        root_lb=ROOT_LB,
        token_lb=token,
        user_lb=user,
        listenbrainz_enabled=enabled,
    )


def create_lastfm_api(user_settings=None):
    """Create a LastFmAPI instance.

    If user_settings is provided, use those values; otherwise fall back to global config.
    """
    from apis.lastfm_api import LastFmAPI

    # Use user settings if available, otherwise global config
    if user_settings and user_settings.get('lastfm_enabled'):
        api_key = user_settings.get('lastfm_api_key') or LASTFM_API_KEY
        api_secret = user_settings.get('lastfm_api_secret') or LASTFM_API_SECRET
        username = user_settings.get('lastfm_username') or LASTFM_USERNAME
        session_key = user_settings.get('lastfm_session_key') or LASTFM_SESSION_KEY
        enabled = True
    else:
        api_key = LASTFM_API_KEY
        api_secret = LASTFM_API_SECRET
        username = LASTFM_USERNAME
        session_key = LASTFM_SESSION_KEY
        enabled = LASTFM_ENABLED

    return LastFmAPI(
        api_key=api_key,
        api_secret=api_secret,
        username=username,
        password=LASTFM_PASSWORD,  # Not used, kept for compatibility
        session_key=session_key,
        lastfm_enabled=enabled,
    )


async def process_cleanup(username=None):
    """Run cleanup routine: check ratings and delete low-rated tracks."""
    print("\n" + "=" * 60)
    print("TrackDrop - Cleanup")
    print("=" * 60)

    cleanup_user = username or USER_ND
    history_path = get_user_history_path(cleanup_user)
    print(f"Running cleanup for user: {cleanup_user}")
    print(f"History file: {history_path}")

    # Load user-specific settings
    user_settings = load_user_settings(cleanup_user) if username else {}
    lb_enabled = user_settings.get('listenbrainz_enabled', LISTENBRAINZ_ENABLED)
    lf_enabled = user_settings.get('lastfm_enabled', LASTFM_ENABLED)

    # Use user's stored credentials for playlist operations
    navidrome_api = create_navidrome_api(username=cleanup_user if username else None, user_settings=user_settings)
    listenbrainz_api = create_listenbrainz_api(user_settings) if lb_enabled else None
    lastfm_api = create_lastfm_api(user_settings) if lf_enabled else None

    await navidrome_api.process_api_cleanup(
        history_path=history_path,
        listenbrainz_api=listenbrainz_api,
        lastfm_api=lastfm_api,
    )

    print("\n" + "=" * 60)
    print("Cleanup complete!")
    print("=" * 60)


async def process_recommendations(source="all", bypass_playlist_check=False, download_id=None, username=None):
    """Download recommendations from enabled sources and update Navidrome playlists."""
    print("\n" + "=" * 60)
    print(f"TrackDrop - Processing Recommendations (source: {source})")
    print("=" * 60)

    rec_user = username or USER_ND
    history_path = get_user_history_path(rec_user)
    print(f"User: {rec_user}")
    print(f"History file: {history_path}")

    # Load user-specific settings
    user_settings = load_user_settings(rec_user) if username else {}
    lb_enabled = user_settings.get('listenbrainz_enabled', LISTENBRAINZ_ENABLED)
    lf_enabled = user_settings.get('lastfm_enabled', LASTFM_ENABLED)

    if user_settings:
        print(f"Using user-specific settings (LB: {lb_enabled}, LF: {lf_enabled})")

    # Initialize APIs
    tagger = Tagger()
    # Use user's stored credentials for playlist operations
    navidrome_api = create_navidrome_api(username=rec_user if username else None, user_settings=user_settings)
    listenbrainz_api = create_listenbrainz_api(user_settings) if lb_enabled else None
    lastfm_api = create_lastfm_api(user_settings) if lf_enabled else None

    from downloaders.track_downloader import TrackDownloader
    track_downloader = TrackDownloader(tagger)

    all_recommendations = []

    # ListenBrainz recommendations
    if source in ["all", "listenbrainz"] and lb_enabled:
        print("\n--- ListenBrainz Recommendations ---")
        try:
            if bypass_playlist_check or await listenbrainz_api.has_playlist_changed():
                lb_recs = await listenbrainz_api.get_listenbrainz_recommendations()
                if lb_recs:
                    print(f"Found {len(lb_recs)} ListenBrainz recommendations")
                    for song in lb_recs:
                        print(f"  {song['artist']} - {song['title']}")
                    all_recommendations.extend(lb_recs)
                else:
                    print("No ListenBrainz recommendations found")
            else:
                print("Playlist unchanged, skipping (use --bypass-playlist-check to force)")
        except Exception as e:
            print(f"Error fetching ListenBrainz recommendations: {e}")
    elif source == "listenbrainz" and not lb_enabled:
        print("ListenBrainz is not enabled")

    # Last.fm recommendations
    if source in ["all", "lastfm"] and lf_enabled:
        print("\n--- Last.fm Recommendations ---")
        try:
            lf_recs = await lastfm_api.get_lastfm_recommendations()
            if lf_recs:
                print(f"Found {len(lf_recs)} Last.fm recommendations")
                for song in lf_recs:
                    print(f"  {song['artist']} - {song['title']}")
                all_recommendations.extend(lf_recs)
            else:
                print("No Last.fm recommendations found")
        except Exception as e:
            print(f"Error fetching Last.fm recommendations: {e}")
    elif source == "lastfm" and not lf_enabled:
        print("Last.fm is not enabled")

    # LLM recommendations
    if source in ["all", "llm"] and LLM_ENABLED and LLM_API_KEY:
        print("\n--- LLM Recommendations ---")
        try:
            from apis.llm_api import LlmAPI
            llm_api = LlmAPI(
                provider=LLM_PROVIDER,
                gemini_api_key=LLM_API_KEY if LLM_PROVIDER == 'gemini' else None,
                openrouter_api_key=LLM_API_KEY if LLM_PROVIDER == 'openrouter' else None,
                model_name=LLM_MODEL_NAME,
            )
            scrobbles = await listenbrainz_api.get_weekly_scrobbles() if listenbrainz_api else []
            if scrobbles:
                llm_recs = llm_api.get_recommendations(scrobbles)
                if llm_recs:
                    print(f"Found {len(llm_recs)} LLM recommendations")
                    for rec in llm_recs:
                        rec['recording_mbid'] = ''
                        rec['release_date'] = ''
                        rec['caa_release_mbid'] = None
                        rec['caa_id'] = None
                        rec['source'] = 'LLM'
                        print(f"  {rec['artist']} - {rec['title']}")
                    all_recommendations.extend(llm_recs)
                else:
                    print("LLM failed to generate recommendations")
            else:
                print("No recent scrobbles found for LLM input")
        except Exception as e:
            print(f"Error generating LLM recommendations: {e}")
    elif source == "llm":
        if not LLM_ENABLED:
            print("LLM is not enabled")
        elif not LLM_API_KEY:
            print("LLM API key is not configured")

    # Deduplicate
    unique_recommendations = []
    seen = set()
    for rec in all_recommendations:
        key = (rec['artist'].lower(), rec['title'].lower())
        if key not in seen:
            unique_recommendations.append(rec)
            seen.add(key)

    if not unique_recommendations:
        print("\nNo recommendations to process")
        update_status_file(download_id, "completed", "No recommendations found", "No Recommendations")
        return 0, 0

    print(f"\n--- Downloading {len(unique_recommendations)} Tracks ---")

    # Track status for UI updates
    track_statuses = [
        {"artist": s.get("artist", "Unknown"), "title": s.get("title", "Unknown"), "status": "pending", "message": ""}
        for s in unique_recommendations
    ]
    downloaded_songs = []
    failed_count = 0
    skipped_count = 0
    total = len(unique_recommendations)

    def update_progress(status, message):
        update_status_file(
            download_id, status, message, "Downloading Recommendations",
            current_track_count=len(downloaded_songs),
            total_track_count=total,
            tracks=track_statuses,
            downloaded_count=len(downloaded_songs),
            failed_count=failed_count,
            skipped_count=skipped_count,
            download_type="playlist",
        )

    update_progress("in_progress", f"Starting download of {total} tracks")

    for i, song in enumerate(unique_recommendations):
        label = f"{song['artist']} - {song['title']}"
        print(f"\n[{i+1}/{total}] {label}")
        track_statuses[i]["status"] = "in_progress"
        track_statuses[i]["message"] = "Downloading..."
        update_progress("in_progress", f"Downloading {i+1}/{total}: {label}")

        try:
            lb_recommendation = song.get('source', '').lower() == 'listenbrainz'
            downloaded_path = await track_downloader.download_track(
                song, lb_recommendation=lb_recommendation, navidrome_api=navidrome_api
            )

            if downloaded_path:
                song['downloaded_path'] = downloaded_path
                downloaded_songs.append(song)
                track_statuses[i]["status"] = "completed"
                track_statuses[i]["message"] = "Downloaded"
                print(f"  Downloaded: {downloaded_path}")
            elif song.get('_duplicate'):
                skipped_count += 1
                track_statuses[i]["status"] = "skipped"
                track_statuses[i]["message"] = "Already in library"
                print(f"  Skipped: Already in library")
            else:
                failed_count += 1
                track_statuses[i]["status"] = "failed"
                track_statuses[i]["message"] = "Not found"
                print(f"  Failed: Not found on Deezer")
        except Exception as e:
            failed_count += 1
            track_statuses[i]["status"] = "failed"
            track_statuses[i]["message"] = str(e)[:80]
            print(f"  Error: {e}")

    # Organize and update playlists
    moved_files = {}
    if downloaded_songs:
        print("\n--- Organizing Files ---")
        moved_files = navidrome_api.organize_music_files(TEMP_DOWNLOAD_FOLDER, MUSIC_DOWNLOAD_PATH)

    print("\n--- Updating Playlists ---")
    navidrome_api.update_api_playlists(
        unique_recommendations, history_path, downloaded_songs, file_path_map=moved_files
    )

    # Final status
    parts = [f"{len(downloaded_songs)} downloaded"]
    if skipped_count:
        parts.append(f"{skipped_count} already in library")
    if failed_count:
        parts.append(f"{failed_count} failed")
    message = ", ".join(parts)

    print("\n" + "=" * 60)
    print("Download Complete!")
    print(f"  {message}")
    print("=" * 60)

    update_status_file(
        download_id, "completed", message, "Download Complete",
        current_track_count=len(downloaded_songs), total_track_count=total,
        tracks=track_statuses, downloaded_count=len(downloaded_songs),
        skipped_count=skipped_count, failed_count=failed_count,
        download_type="playlist",
    )

    return len(downloaded_songs), total


async def process_fresh_releases(download_id=None):
    """Download albums from Fresh Releases."""
    print("\n" + "=" * 60)
    print("TrackDrop - Fresh Releases")
    print("=" * 60)

    if not LISTENBRAINZ_ENABLED:
        print("ListenBrainz is not enabled (required for Fresh Releases)")
        update_status_file(download_id, "failed", "ListenBrainz not enabled", "Configuration Error")
        return

    if not ALBUM_RECOMMENDATION_ENABLED:
        print("Album recommendations are disabled")
        update_status_file(download_id, "completed", "Album recommendations disabled", "Disabled")
        return

    tagger = Tagger()
    listenbrainz_api = create_listenbrainz_api()
    navidrome_api = create_navidrome_api()

    from downloaders.album_downloader import AlbumDownloader
    album_downloader = AlbumDownloader(tagger)

    print("\nFetching fresh releases from ListenBrainz...")
    fresh_data = await listenbrainz_api.get_fresh_releases()
    releases = fresh_data.get('payload', {}).get('releases', [])

    if not releases:
        print("No fresh releases found")
        update_status_file(download_id, "completed", "No fresh releases found", "No Releases")
        return

    print(f"Found {len(releases)} fresh releases:")
    for release in releases:
        artist = release.get('artist_credit_name', 'Unknown Artist')
        album = release.get('release_name', 'Unknown Album')
        date = release.get('release_date', '')
        print(f"  {artist} - {album} ({date})")

    total = len(releases)
    downloaded_albums = []
    update_status_file(download_id, "in_progress", f"Downloading {total} albums", "Fresh Releases",
                       current_track_count=0, total_track_count=total)

    for i, release in enumerate(releases):
        artist = release.get('artist_credit_name', 'Unknown Artist')
        album_name = release.get('release_name', 'Unknown Album')
        print(f"\n[{i+1}/{total}] {artist} - {album_name}")

        album_info = {
            'artist': artist,
            'album': album_name,
            'release_date': release.get('release_date'),
            'album_art': release.get('album_art'),
        }

        try:
            downloaded_files = await album_downloader.download_album(album_info)
            if downloaded_files:
                downloaded_albums.append(album_info)
                print(f"  Downloaded successfully")
                update_status_file(download_id, "in_progress",
                                   f"Downloaded {len(downloaded_albums)}/{total}",
                                   "Fresh Releases",
                                   current_track_count=len(downloaded_albums),
                                   total_track_count=total)
            else:
                print(f"  Failed to download")
        except Exception as e:
            print(f"  Error: {e}")

    if downloaded_albums:
        print("\n--- Organizing Files ---")
        navidrome_api.organize_music_files(TEMP_DOWNLOAD_FOLDER, MUSIC_DOWNLOAD_PATH)

    print("\n" + "=" * 60)
    print(f"Fresh Releases Complete: {len(downloaded_albums)}/{total} albums downloaded")
    print("=" * 60)

    update_status_file(download_id, "completed",
                       f"Downloaded {len(downloaded_albums)} of {total} albums",
                       "Download Complete",
                       current_track_count=len(downloaded_albums),
                       total_track_count=total)


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='TrackDrop - Automated music discovery and download',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  trackdrop.py                         Download from all enabled sources
  trackdrop.py --source listenbrainz   Download ListenBrainz recommendations
  trackdrop.py --source lastfm         Download Last.fm recommendations
  trackdrop.py --source llm            Download LLM recommendations
  trackdrop.py --source fresh_releases Download fresh release albums
  trackdrop.py --cleanup               Run cleanup routine
        """
    )
    parser.add_argument(
        '--source', '-s',
        default='all',
        choices=['all', 'listenbrainz', 'lastfm', 'llm', 'fresh_releases'],
        help='Source for recommendations (default: all)'
    )
    parser.add_argument(
        '--cleanup', '-c',
        action='store_true',
        help='Run cleanup routine (delete unrated tracks)'
    )
    parser.add_argument(
        '--user', '-u',
        help='Username for per-user history tracking'
    )
    parser.add_argument(
        '--bypass-playlist-check',
        action='store_true',
        help='Download even if playlist has not changed'
    )
    parser.add_argument(
        '--download-id',
        help='Unique ID for status tracking (used by web UI)'
    )

    args = parser.parse_args()

    # Validate configuration
    if not ROOT_ND or not USER_ND or not PASSWORD_ND:
        print("Error: Navidrome configuration is missing")
        print("Set ROOT_ND, USER_ND, and PASSWORD_ND in config.py or environment")
        sys.exit(1)

    # Initialize streamrip database
    try:
        initialize_streamrip_db()
    except Exception as e:
        print(f"Warning: Could not initialize streamrip database: {e}")

    # Initial status
    update_status_file(args.download_id, "in_progress", "Starting...")

    try:
        if args.cleanup:
            asyncio.run(process_cleanup(username=args.user))
            update_status_file(args.download_id, "completed", "Cleanup finished", "Cleanup Complete")
        elif args.source == 'fresh_releases':
            asyncio.run(process_fresh_releases(download_id=args.download_id))
        else:
            asyncio.run(process_recommendations(
                source=args.source,
                bypass_playlist_check=args.bypass_playlist_check,
                download_id=args.download_id,
                username=args.user,
            ))
    except KeyboardInterrupt:
        print("\nInterrupted by user")
        update_status_file(args.download_id, "failed", "Interrupted by user", "Cancelled")
        sys.exit(130)
    except Exception as e:
        print(f"\nError: {e}")
        update_status_file(args.download_id, "failed", str(e), "Error")
        raise


if __name__ == '__main__':
    main()
