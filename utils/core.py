"""Core utility functions for TrackDrop.

This module contains the original utils.py content, now part of the utils package.
"""

import json
import os
import re
from datetime import datetime

import requests
import imghdr
from mutagen import File, MutagenError
from mutagen.id3 import ID3, APIC, TPE1, TALB, TIT2, TDRC, TXXX, UFID, TPE2
from mutagen.mp3 import MP3
from mutagen.flac import FLAC, Picture
from mutagen.oggvorbis import OggVorbis
from mutagen.mp4 import MP4, MP4Cover
from streamrip.db import Database, Downloads, Failed


def get_user_history_path(username):
    """Get per-user download history path."""
    from config import DOWNLOAD_HISTORY_PATH
    base_dir = os.path.dirname(DOWNLOAD_HISTORY_PATH)
    os.makedirs(base_dir, exist_ok=True)
    safe_user = username.replace('/', '_').replace('\\', '_')
    return os.path.join(base_dir, f'download_history_{safe_user}.json')


def initialize_streamrip_db():
    """Initialize the streamrip database, ensuring tables exist."""
    db_path = "/app/temp_downloads/downloads.db"
    failed_db_path = "/app/temp_downloads/failed_downloads.db"

    os.makedirs(os.path.dirname(db_path), exist_ok=True)

    print(f"Initializing streamrip database at {db_path}...")
    try:
        downloads_db = Downloads(db_path)
        failed_downloads_db = Failed(failed_db_path)
        Database(downloads=downloads_db, failed=failed_downloads_db)
        print("Streamrip database initialization complete.")
    except Exception as e:
        print(f"Error initializing streamrip database: {e}")
        raise


def remove_empty_folders(path):
    """Remove empty folders from a given path recursively."""
    for root, dirs, files in os.walk(path, topdown=False):
        for dir_name in dirs:
            full_path = os.path.join(root, dir_name)
            if not os.listdir(full_path):
                try:
                    os.rmdir(full_path)
                    print(f"Removed empty folder: {full_path}")
                except OSError as e:
                    print(f"Error removing folder {full_path}: {e}")


class Tagger:
    """Handle audio file metadata tagging and album art embedding."""

    def __init__(self):
        pass

    def _embed_album_art(self, file_path, album_art_url):
        """Download and embed album art into the audio file."""
        if not album_art_url:
            return

        try:
            response = requests.get(album_art_url, stream=True, timeout=30)
            response.raise_for_status()
            image_data = response.content

            image_type = imghdr.what(None, h=image_data)
            if not image_type:
                print(f"Could not determine image type for album art. Skipping.")
                return

            mime_type = f"image/{image_type}"

            if file_path.lower().endswith('.mp3'):
                audio = MP3(file_path, ID3=ID3)
                audio.tags.add(APIC(encoding=3, mime=mime_type, type=3, desc='Cover', data=image_data))
                audio.save()
            elif file_path.lower().endswith('.flac'):
                audio = FLAC(file_path)
                image = Picture()
                image.data = image_data
                image.type = 3
                image.mime = mime_type
                audio.clear_pictures()
                audio.add_picture(image)
                audio.save()
            elif file_path.lower().endswith(('.ogg', '.oga')):
                audio = OggVorbis(file_path)
                image = Picture()
                image.data = image_data
                image.type = 3
                image.mime = mime_type
                import base64
                encoded = base64.b64encode(image.write()).decode('ascii')
                audio['metadata_block_picture'] = [encoded]
                audio.save()
            elif file_path.lower().endswith('.m4a'):
                audio = MP4(file_path)
                img_format = MP4Cover.FORMAT_JPEG if image_type == 'jpeg' else MP4Cover.FORMAT_PNG
                audio.tags['covr'] = [MP4Cover(image_data, imageformat=img_format)]
                audio.save()
            else:
                print(f"Unsupported file type for album art embedding: {file_path}")
                return

            print(f"Embedded album art into: {os.path.basename(file_path)}")

        except requests.exceptions.RequestException as e:
            print(f"Error downloading album art: {e}")
        except Exception as e:
            print(f"Error embedding album art into {file_path}: {e}")

    def tag_track(self, file_path, artist, title, album, release_date, recording_mbid, source,
                  album_art_url=None, is_album_recommendation=False, album_artist=None,
                  artists=None, artist_mbids=None):
        """Tag a track with metadata using Mutagen and embed album art."""

        # Extract title from filename if not provided
        if not title:
            base_filename = os.path.splitext(os.path.basename(file_path))[0]
            extracted_title = base_filename

            if artist:
                artist_pattern = re.compile(f"^{re.escape(artist)}\\s*-\\s*", re.IGNORECASE)
                extracted_title = artist_pattern.sub("", extracted_title, 1)

            # Remove track number patterns
            extracted_title = re.sub(r"^\d+\s*-\s*", "", extracted_title)
            extracted_title = re.sub(r"^\d+\.\s*", "", extracted_title)
            extracted_title = re.sub(r"^\(\d+\)\s*", "", extracted_title)
            extracted_title = extracted_title.strip(' -').strip()

            title = extracted_title if extracted_title else base_filename

        try:
            audio = File(file_path)
            if audio is None:
                print(f"Could not open audio file: {file_path}")
                return

            if file_path.lower().endswith('.mp3'):
                if audio.tags is None:
                    audio.tags = ID3()

                if artists and len(artists) > 1:
                    audio.tags.add(TXXX(encoding=3, desc='ARTISTS', text=artists))
                elif artist:
                    audio.tags.add(TPE1(encoding=3, text=[artist]))
                if album_artist:
                    audio.tags.add(TPE2(encoding=3, text=[album_artist]))
                audio.tags.add(TIT2(encoding=3, text=[title]))
                audio.tags.add(TALB(encoding=3, text=[album]))
                if release_date:
                    audio.tags.add(TDRC(encoding=3, text=[release_date]))

                if recording_mbid:
                    audio.tags.add(TXXX(encoding=3, desc='MUSICBRAINZ_RECORDINGID', text=[recording_mbid]))
                    audio.tags.add(UFID(owner='http://musicbrainz.org',
                                       data=f'http://musicbrainz.org/recording/{recording_mbid}'.encode('utf-8')))
                if artist_mbids:
                    audio.tags.add(TXXX(encoding=3, desc='MUSICBRAINZ_ARTISTID', text=artist_mbids))

            elif file_path.lower().endswith('.flac'):
                if artists and len(artists) > 1:
                    audio['artists'] = artists
                    if 'artist' in audio:
                        del audio['artist']
                elif artist:
                    audio['artist'] = [artist]
                if album_artist:
                    audio['albumartist'] = [album_artist]
                audio['title'] = [title]
                audio['album'] = [album]
                if release_date:
                    audio['date'] = [release_date]
                if recording_mbid:
                    audio['musicbrainz_recordingid'] = recording_mbid
                if artist_mbids:
                    audio['musicbrainz_artistid'] = artist_mbids

            elif file_path.lower().endswith(('.ogg', '.oga')):
                if artists and len(artists) > 1:
                    audio['artists'] = artists
                    if 'artist' in audio:
                        del audio['artist']
                elif artist:
                    audio['artist'] = [artist]
                if album_artist:
                    audio['albumartist'] = [album_artist]
                audio['title'] = [title]
                audio['album'] = [album]
                if release_date:
                    audio['date'] = [release_date]
                if recording_mbid:
                    audio['musicbrainz_recordingid'] = recording_mbid
                if artist_mbids:
                    audio['musicbrainz_artistid'] = artist_mbids

            elif file_path.lower().endswith('.m4a'):
                if artists and len(artists) > 1:
                    audio['\xa9ART'] = ["; ".join(artists)]
                elif artist:
                    audio['\xa9ART'] = [artist]
                if album_artist:
                    audio['aART'] = [album_artist]
                audio['\xa9nam'] = [title]
                audio['\xa9alb'] = [album]
                if release_date:
                    audio['\xa9day'] = [release_date]
                if recording_mbid:
                    audio['----:com.apple.iTunes:MusicBrainz Recording Id'] = [recording_mbid.encode('utf-8')]
                if artist_mbids:
                    audio['----:com.apple.iTunes:MusicBrainz Artist Id'] = [mbid.encode('utf-8') for mbid in artist_mbids]

            else:
                print(f"Unsupported file type for tagging: {file_path}")
                return

            audio.save()
            print(f"Tagged: {artist} - {title}")

            if album_art_url:
                self._embed_album_art(file_path, album_art_url)

        except MutagenError as e:
            print(f"Error tagging {file_path}: {e}")
        except Exception as e:
            print(f"Unexpected error tagging {file_path}: {e}")


def update_status_file(download_id, status, message=None, title=None,
                       current_track_count=None, total_track_count=None, **kwargs):
    """Update the status file for a download task."""
    if not download_id:
        return

    status_dir = "/tmp/trackdrop_download_status"
    os.makedirs(status_dir, exist_ok=True)
    status_file_path = os.path.join(status_dir, f"{download_id}.json")

    status_data = {
        "status": status,
        "timestamp": datetime.now().isoformat()
    }

    if message:
        status_data["message"] = message

    if title:
        status_data["title"] = title
    else:
        titles = {
            "completed": "Download completed",
            "failed": "Download failed",
            "in_progress": "Download in progress"
        }
        status_data["title"] = titles.get(status, "Processing")

    if current_track_count is not None:
        status_data["current_track_count"] = current_track_count
    if total_track_count is not None:
        status_data["total_track_count"] = total_track_count

    # Pass through extra fields
    for key in ('tracks', 'skipped_count', 'failed_count', 'downloaded_count', 'download_type', 'source_stats'):
        if key in kwargs and kwargs[key] is not None:
            status_data[key] = kwargs[key]

    with open(status_file_path, 'w') as f:
        json.dump(status_data, f)
