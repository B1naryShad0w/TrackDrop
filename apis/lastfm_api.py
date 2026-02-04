import requests
import asyncio
from apis.deezer_api import DeezerAPI
from utils.http import make_request_with_retries


class LastFmAPI:
    """Last.fm API client for fetching recommendations.

    Only requires a username - the recommendations endpoint is public.
    """

    def __init__(self, username, lastfm_enabled):
        self._username = username
        self._lastfm_enabled = lastfm_enabled

    def _make_request(self, method, url, headers=None, params=None, json=None, data=None, max_retries=5, retry_delay=5):
        """Make an HTTP request using the shared retry utility."""
        return make_request_with_retries(
            method=method,
            url=url,
            headers=headers,
            params=params,
            json=json,
            data=data,
            max_retries=max_retries,
            retry_delay=retry_delay,
            service_name="Last.fm API",
        )

    def get_recommended_tracks(self, limit=100):
        """
        Fetches recommended tracks from Last.fm using the undocumented /recommended endpoint.
        This endpoint is public and only requires a valid username.
        """
        username = self._username
        if not username:
            print("Last.fm username not configured.")
            return []

        recommendations = []
        url = f"https://www.last.fm/player/station/user/{username}/recommended"
        headers = {
            'Referer': 'https://www.last.fm/',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3'
        }

        try:
            response = self._make_request(
                method="GET",
                url=url,
                headers=headers
            )
            if response is None:
                print("Failed to get response from Last.fm API after retries.")
                return []
            data = response.json()

            for track_data in data["playlist"]:
                artist = track_data["artists"][0]["name"]
                title = track_data["name"]
                recommendations.append({
                    "artist": artist,
                    "title": title,
                    "album": "Unknown Album",
                    "release_date": None
                })

                if len(recommendations) >= limit:
                    break
            return recommendations
        except requests.exceptions.RequestException as e:
            print(f"Error fetching Last.fm recommendations: {e}")
            return []
        except KeyError as e:
            print(f"Unexpected Last.fm API response structure for recommendations: missing key {e}")
            return []
        except Exception as e:
            print(f"Unexpected error in Last.fm API: {e}")
            return []

    async def get_lastfm_recommendations(self):
        """Fetches recommended tracks from Last.fm and returns them as a list."""
        if not self._lastfm_enabled:
            return []

        print("\nChecking for new Last.fm recommendations...")
        print("\n\033[31m")
        print("###                                   #####              ")
        print("#%#                      ###         ##%#                ")
        print("#%#    #####     #####  ##%####     ##%%##### ####  #### ")
        print("#%#  #### ####  ### #####%%####     ##%#####%############")
        print("#%#  #%#    #%% ####     %%#         #%#   #%#   %%#  #%#")
        print("#%# ##%#    #%%#  #####  #%#         #%#   #%#   %%#  #%#")
        print("#%#  ####  ######   #### ###  # #### #%#   #%#   %%#  #%#")
        print(" ####  ######  #######    ##### ###  ###   ###   ###  ###")
        print("\033[0m")

        recommended_tracks = self.get_recommended_tracks()

        if not recommended_tracks:
            print("No recommendations found from Last.fm.")
            return []

        # Asynchronously fetch album art in parallel
        deezer_api = DeezerAPI()
        tasks = [
            deezer_api.get_deezer_track_details_from_artist_title(
                track["artist"], track["title"]
            )
            for track in recommended_tracks
        ]
        album_details = await asyncio.gather(*tasks)
        songs = []
        for i, track in enumerate(recommended_tracks):
            song = {
                "artist": track["artist"],
                "title": track["title"],
                "album": track["album"],
                "release_date": track["release_date"],
                "album_art": None,
                "recording_mbid": None,
                "source": "Last.fm"
            }
            details = album_details[i]
            if details:
                song["album_art"] = details.get("album_art")
                song["album"] = details.get("album", song["album"])
            songs.append(song)
        return songs
