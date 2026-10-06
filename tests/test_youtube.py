"""YouTube links → LRCLIB matches. ytmusicapi, oEmbed and LRCLIB are replaced (no network)."""

from urllib.error import HTTPError

import pytest

from lyricdeck import lyrics, youtube


@pytest.mark.parametrize("url, expected", [
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", ("video", "dQw4w9WgXcQ")),
    ("youtube.com/watch?v=dQw4w9WgXcQ&t=42", ("video", "dQw4w9WgXcQ")),
    ("https://youtu.be/dQw4w9WgXcQ?si=abc", ("video", "dQw4w9WgXcQ")),
    ("https://www.youtube.com/shorts/dQw4w9WgXcQ", ("video", "dQw4w9WgXcQ")),
    ("https://music.youtube.com/watch?v=dQw4w9WgXcQ", ("video", "dQw4w9WgXcQ")),
    ("https://www.youtube.com/playlist?list=PLabc123", ("playlist", "PLabc123")),
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PLabc123", ("playlist", "PLabc123")),
    ("https://music.youtube.com/playlist?list=OLAK5uy_xyz", ("playlist", "OLAK5uy_xyz")),
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=RDdQw4w9WgXcQ", ("video", "dQw4w9WgXcQ")),  # a Mix
    ("Кино Группа крови", None),
    ("https://notyoutube.com/watch?v=dQw4w9WgXcQ", None),
    ("https://www.youtube.com/@channel", None),
])
def test_parse_url(url, expected):
    assert youtube.parse_url(url) == expected


def test_uploaded_video_titles_are_split():
    t = youtube.track("Кино - Группа крови (Official Video)", ["Some Fan Channel"], 286)
    assert (t["artist"], t["title"], t["artists"]) == ("Кино", "Группа крови", ["Кино", "Some Fan Channel"])
    assert youtube.track("Гуляю", ["Elfass - Topic"], 175, clean=True)["artist"] == "Elfass"
    assert youtube.track("Ода - Remastered", ["Artist"], 100, clean=True)["title"] == "Ода - Remastered"
    assert youtube.clean_channel("KinoVEVO") == "Kino"


class FakeYTMusic:
    def get_playlist(self, playlist_id, limit=None):
        if playlist_id == "PLmissing":
            raise KeyError("contents")
        return {"title": "Мой плейлист", "trackCount": 3, "tracks": [
            {"videoId": "a" * 11, "title": "Гуляю", "artists": [{"name": "Elfass"}], "duration_seconds": 175,
             "videoType": "MUSIC_VIDEO_TYPE_ATV", "isAvailable": True},
            {"videoId": "b" * 11, "title": "Кино - Группа крови", "artists": [{"name": "fan"}],
             "duration_seconds": 286, "videoType": "MUSIC_VIDEO_TYPE_UGC", "isAvailable": True},
            {"videoId": "c" * 11, "title": None, "artists": [], "isAvailable": False},
        ]}

    def get_song(self, video_id):
        return {"videoDetails": {"title": "Гуляю", "author": "Elfass", "lengthSeconds": "175",
                                 "musicVideoType": "MUSIC_VIDEO_TYPE_OMV"}}


@pytest.fixture
def ytmusic(monkeypatch):
    monkeypatch.setattr(youtube, "available", lambda: True)
    monkeypatch.setattr(youtube, "_ytmusic", FakeYTMusic)


def test_playlist(ytmusic):
    data = youtube.playlist("PLx")
    assert data["title"] == "Мой плейлист" and not data["truncated"]
    assert [(t["artist"], t["title"], t["duration"]) for t in data["tracks"]] == [
        ("Elfass", "Гуляю", 175), ("Кино", "Группа крови", 286)]
    with pytest.raises(youtube.YouTubeError, match="public"):
        youtube.playlist("PLmissing")


def test_video_with_and_without_ytmusicapi(ytmusic, monkeypatch):
    assert youtube.video("a" * 11)["tracks"][0]["duration"] == 175
    monkeypatch.setattr(youtube, "available", lambda: False)
    monkeypatch.setattr(youtube, "_oembed", lambda vid: youtube.track("Elfass - Гуляю (Lyric Video)", ["ElfassVEVO"], None))
    t = youtube.video("a" * 11)["tracks"][0]
    assert (t["artist"], t["title"], t["duration"]) == ("Elfass", "Гуляю", None)


LRCLIB = {
    1: {"id": 1, "artistName": "Кино", "trackName": "Группа крови", "duration": 284,
        "plainLyrics": "Тёплое место, но улицы ждут\nОтпечатков наших ног", "instrumental": False},
    2: {"id": 2, "artistName": "Кино", "trackName": "Группа крови (Live)", "duration": 301,
        "plainLyrics": "Тёплое место\nНо улицы ждут", "instrumental": False},
    3: {"id": 3, "artistName": "Kino", "trackName": "Gruppa krovi (English translation)", "duration": 284,
        "plainLyrics": "A warm place, but the streets are waiting", "instrumental": False},
    4: {"id": 4, "artistName": "Другой", "trackName": "Группа крови", "duration": 200,
        "plainLyrics": "Совсем другая песня\nИ другие слова", "instrumental": False},
}


def fake_lrclib(path, **params):
    if path == "get":
        if params["artist_name"] == "Кино" and abs(params["duration"] - 284) <= 2:
            return LRCLIB[1]
        raise HTTPError("u", 404, "Not Found", None, None)
    return [LRCLIB[i] for i in (4, 2, 3, 1)]


def test_match_ranks_exact_then_close(monkeypatch):
    monkeypatch.setattr(lyrics, "_get", fake_lrclib)
    found = lyrics.match(["Кино"], "Группа крови", 286)
    assert [c["id"] for c in found][:2] == [1, 2] and found[0]["exact"] and found[0]["good"]
    assert found[0]["duration_diff"] == -2
    assert {c["id"]: c["good"] for c in found}[3] is False  # translation: wrong language


def test_match_without_exact_hit(monkeypatch):
    monkeypatch.setattr(lyrics, "_get", fake_lrclib)
    found = lyrics.match(["Кино"], "Группа крови", None)
    assert found[0]["id"] == 1 and not found[0]["exact"] and found[0]["good"]
    live = next(c for c in found if c["id"] == 2)
    assert live["score"] < found[0]["score"]  # a live version ranks below the studio one


def test_youtube_link_in_search_redirects(client):
    res = client.get("/songs/find?q=https://youtu.be/dQw4w9WgXcQ")
    assert "/songs/youtube?url=" in res.headers["Location"]


def test_playlist_page_needs_the_extra(client, monkeypatch):
    monkeypatch.setattr(youtube, "available", lambda: False)
    page = client.get("/songs/youtube?url=https://www.youtube.com/playlist?list=PLx").get_data(as_text=True)
    assert "uv sync --extra youtube" in page


def test_playlist_page_lists_tracks(client, ytmusic):
    page = client.get("/songs/youtube?url=https://www.youtube.com/playlist?list=PLx").get_data(as_text=True)
    assert "Мой плейлист" in page and '"duration": 286' in page and "Import picked" in page


def test_bad_link_shows_error(client):
    page = client.get("/songs/youtube?url=https://www.youtube.com/@channel").get_data(as_text=True)
    assert "doesn&#39;t look like a YouTube" in page


def test_match_endpoint_and_json_import(client, monkeypatch):
    def lrclib(path, **params):
        return LRCLIB[int(path.split("/")[1])] if path.startswith("get/") else fake_lrclib(path, **params)
    monkeypatch.setattr(lyrics, "_get", lrclib)
    url = "/songs/match?artist=Кино&title=Группа крови&duration=286"
    data = client.get(url).get_json()
    assert data["error"] is None and data["candidates"][0]["id"] == 1 and not data["candidates"][0]["in_library"]
    assert client.post("/songs/import", json={"lrclib_id": 1}).get_json() == {"ok": True, "song_id": 1, "existing": False}
    assert client.post("/songs/import", json={"lrclib_id": 1}).get_json()["existing"]
    assert client.get(url).get_json()["candidates"][0]["in_library"]
    assert not client.post("/songs/import", json={}).get_json()["ok"]


def test_match_endpoint_reports_busy_lrclib(client, monkeypatch):
    def busy(*a, **k):
        raise lyrics.LrclibBusy(503)
    monkeypatch.setattr(lyrics, "_get", busy)
    data = client.get("/songs/match?artist=a&title=b").get_json()
    assert data["candidates"] == [] and "busy" in data["error"]
