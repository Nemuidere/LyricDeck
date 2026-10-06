"""Songs from YouTube links: one video, or a whole public playlist.

Playlists need ytmusicapi (`uv sync --extra youtube`), an unofficial YouTube Music client that needs no
account. A single video also works without it, through YouTube's public oEmbed endpoint (no length then).
"""

import json
import re
import urllib.parse
import urllib.request
from urllib.error import HTTPError, URLError

from .dictionary import USER_AGENT
from .lyrics import clean_title

OEMBED_URL = "https://www.youtube.com/oembed"
MAX_TRACKS = 300
VIDEO_ID = re.compile(r"^[\w-]{11}$")
HOST = re.compile(r"^(https?://)?([\w-]+\.)*(youtube\.com|youtube-nocookie\.com|youtu\.be)(/|$)", re.I)
CHANNEL_JUNK = re.compile(r"\s*(-\s*topic|vevo|official|oficial|официальный канал)$", re.I)
DASH = re.compile(r"\s[-–—]\s")
# YouTube Music's own songs and official videos have a clean title and artist; user uploads don't
SONG_TYPES = {"MUSIC_VIDEO_TYPE_ATV", "MUSIC_VIDEO_TYPE_OMV"}


class YouTubeError(Exception):
    pass


def available() -> bool:
    """Is ytmusicapi installed (needed for playlists)?"""
    try:
        import ytmusicapi  # noqa: F401
    except ImportError:
        return False
    return True


def parse_url(text: str) -> tuple[str, str] | None:
    """("playlist", id) or ("video", id) for a YouTube link, None for anything else.

    A link to a video inside a playlist means the playlist, except for Mixes (RD...): YouTube makes those up
    on the fly and they never end, so they mean the video."""
    text = (text or "").strip()
    if not HOST.match(text):
        return None
    url = urllib.parse.urlparse(text if "://" in text else f"https://{text}")
    query = urllib.parse.parse_qs(url.query)
    playlist, video = (query.get("list") or [""])[0], (query.get("v") or [""])[0]
    if url.netloc.lower().endswith("youtu.be"):
        video = url.path.strip("/").split("/")[0]
    elif m := re.match(r"^/(shorts|live|embed)/([\w-]{11})", url.path):
        video = m[2]
    if playlist and not playlist.startswith("RD"):
        return "playlist", playlist
    if VIDEO_ID.match(video):
        return "video", video
    return None


def clean_channel(name: str) -> str:
    """'KinoVEVO' / 'Кино - Topic' -> the artist's name."""
    return CHANNEL_JUNK.sub("", (name or "").strip()).strip()


def track(title: str, artists: list[str], duration: int | None, video_id: str = "", clean: bool = False) -> dict:
    """A song as LyricDeck looks it up. Uploaded videos are often titled 'Artist - Title (Official Video)'."""
    artists = [clean_channel(a) for a in artists if a and clean_channel(a)]
    if not clean and (m := DASH.search(title or "")):
        left, title = title[:m.start()].strip(), title[m.end():]
        artists = [left] + [a for a in artists if a.casefold() != left.casefold()]
    return {"artists": artists, "artist": artists[0] if artists else "",
            "title": clean_title(title, artists[0] if artists else ""), "duration": duration or None,
            "video_id": video_id}


def _ytmusic():
    from ytmusicapi import YTMusic
    return YTMusic()


def playlist(playlist_id: str) -> dict:
    """{title, tracks, truncated} for a public playlist."""
    if not available():
        raise YouTubeError("Playlists need the YouTube extra. Run: uv sync --extra youtube")
    try:
        data = _ytmusic().get_playlist(playlist_id, limit=MAX_TRACKS)
    except Exception as e:  # ytmusicapi has no error types of its own for missing or private playlists
        raise YouTubeError("Could not read that playlist. Is it public, and is the link complete?") from e
    tracks = [track(t["title"], [a["name"] for a in t.get("artists") or []], t.get("duration_seconds"),
                    t.get("videoId") or "", t.get("videoType") in SONG_TYPES)
              for t in data.get("tracks") or [] if t.get("title") and t.get("isAvailable", True)]
    return {"title": data.get("title") or "YouTube playlist", "tracks": tracks[:MAX_TRACKS],
            "truncated": (data.get("trackCount") or 0) > MAX_TRACKS}


def _oembed(video_id: str) -> dict:
    url = f"{OEMBED_URL}?{urllib.parse.urlencode({'url': f'https://www.youtube.com/watch?v={video_id}', 'format': 'json'})}"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": USER_AGENT}), timeout=15) as r:
            data = json.load(r)
    except HTTPError as e:
        raise YouTubeError("YouTube doesn't know that video, or it is private.") from e
    except (URLError, TimeoutError) as e:
        raise YouTubeError(f"Could not reach YouTube ({e}). Check your connection.") from e
    return track(data.get("title") or "", [data.get("author_name") or ""], None, video_id)


def video(video_id: str) -> dict:
    """{title, tracks: [one track]} for a single video."""
    found = None
    if available():
        try:
            details = _ytmusic().get_song(video_id)["videoDetails"]
            found = track(details["title"], [details.get("author") or ""], int(details.get("lengthSeconds") or 0),
                          video_id, details.get("musicVideoType") in SONG_TYPES)
        except Exception:  # unofficial API: fall back to oEmbed for anything it can't read
            found = None
    found = found or _oembed(video_id)
    return {"title": "YouTube video", "tracks": [found], "truncated": False}


def load(url: str) -> dict:
    kind, ident = parse_url(url) or (None, None)
    if kind == "playlist":
        return playlist(ident)
    if kind == "video":
        return video(ident)
    raise YouTubeError("That doesn't look like a YouTube video or playlist link.")
