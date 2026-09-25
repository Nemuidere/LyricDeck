"""LRCLIB tests with the HTTP call replaced (no network)."""

from lyricdeck import lyrics

RESULTS = [
    {"id": 1, "artistName": "Тест", "trackName": "Песня (Official Video)", "albumName": "A", "duration": 185,
     "plainLyrics": "Первая строка\nВторая строка", "syncedLyrics": "[00:01.00] Первая строка", "instrumental": False},
    {"id": 2, "artistName": "Тест", "trackName": "Песня", "albumName": "B", "duration": 186,
     "plainLyrics": "Первая строка\nВторая строка", "syncedLyrics": None, "instrumental": False},
    {"id": 3, "artistName": "Тест", "trackName": "Инструментал", "plainLyrics": None, "instrumental": True},
]


def fake_get(path, **params):
    if path == "search":
        return RESULTS
    return next(r for r in RESULTS if f"get/{r['id']}" == path)


def test_alternate_versions_rank_lower(monkeypatch):
    inst = dict(RESULTS[0], id=7, trackName="Песня (inst)", plainLyrics="Другая строка\nЕщё")
    monkeypatch.setattr(lyrics, "_get", lambda path, **p: [inst] + RESULTS)
    assert [r["id"] for r in lyrics.search("x")] == [1, 7]


def test_japanese_script_check():
    assert lyrics.script_check("君と歩いた道を、まだ覚えてる", "ja")["fit"]
    assert not lyrics.script_check("kimi to aruita michi wo", "ja")["importable"]
    assert not lyrics.script_check("我爱你中国人民万岁", "ja")["fit"]  # Chinese: kanji without kana


def test_clean_title():
    assert lyrics.clean_title("01. Кино - Группа крови", "Кино") == "Группа крови"
    assert lyrics.clean_title("Хочешь? (Official Video)") == "Хочешь?"
    assert lyrics.clean_title("7 Сорок") == "7 Сорок"
    assert lyrics.clean_title("03 - Песня") == "Песня"


def test_russian_results_first(monkeypatch):
    english = {"id": 9, "artistName": "Тест", "trackName": "Song", "plainLyrics": "It's such a warm place",
               "instrumental": False}
    monkeypatch.setattr(lyrics, "_get", lambda path, **p: [english] + RESULTS)
    found = lyrics.search("x")
    assert [r["id"] for r in found] == [1, 9] and not found[1]["fit"]


def test_search_cleans_and_dedupes(monkeypatch):
    monkeypatch.setattr(lyrics, "_get", fake_get)
    found = lyrics.search("тест песня")
    assert [r["id"] for r in found] == [1]
    assert found[0]["title"] == "Песня" and found[0]["synced"] and found[0]["duration"] == 185


def test_find_and_import(client, monkeypatch):
    monkeypatch.setattr(lyrics, "_get", fake_get)
    page = client.get("/songs/find?q=тест").get_data(as_text=True)
    assert "Первая строка / Вторая строка" in page and "3:05" in page
    res = client.post("/songs/import", data={"lrclib_id": "1", "q": "тест"})
    assert res.headers["Location"].endswith("/songs/1/edit")
    page = client.get("/").get_data(as_text=True)
    assert "Песня" in page and "LRCLIB" in page


def test_find_handles_network_errors(client, monkeypatch):
    from urllib.error import URLError

    def broken(*a, **k):
        raise URLError("offline")
    monkeypatch.setattr(lyrics, "_get", broken)
    assert "Could not reach LRCLIB" in client.get("/songs/find?q=x").get_data(as_text=True)
