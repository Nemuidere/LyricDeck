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
    assert lyrics.clean_title("Never Gonna Give You Up (4K Remaster)") == "Never Gonna Give You Up"


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


class FakeClock:
    """Stands in for time.monotonic/time.sleep so retry tests run instantly."""
    def __init__(self):
        self.now = 0.0

    def sleep(self, seconds):
        self.now += seconds


def fake_urlopen(answers, calls):
    import io
    import json
    from email.message import Message
    from urllib.error import HTTPError

    def urlopen(req, timeout):
        calls.append(req.full_url)
        answer = answers.pop(0) if len(answers) > 1 else answers[0]
        if isinstance(answer, int):
            headers = Message()
            headers["Retry-After"] = "1"
            raise HTTPError(req.full_url, answer, "Service Unavailable", headers, None)
        if isinstance(answer, Exception):
            raise answer
        return io.BytesIO(json.dumps(answer).encode())
    return urlopen


def patch_network(monkeypatch, answers):
    clock, calls = FakeClock(), []
    monkeypatch.setattr(lyrics.time, "monotonic", lambda: clock.now)
    monkeypatch.setattr(lyrics.time, "sleep", clock.sleep)
    monkeypatch.setattr(lyrics.urllib.request, "urlopen", fake_urlopen(answers, calls))
    return clock, calls


def test_busy_lrclib_is_retried(client, monkeypatch):
    clock, calls = patch_network(monkeypatch, [503, 503, RESULTS])
    page = client.get("/songs/find?q=тест").get_data(as_text=True)
    assert "Первая строка / Вторая строка" in page and len(calls) == 3 and clock.now == 2


def test_busy_lrclib_gives_up_after_ten_seconds(client, monkeypatch):
    clock, calls = patch_network(monkeypatch, [503])
    page = client.get("/songs/find?q=тест").get_data(as_text=True)
    assert "LRCLIB is busy right now" in page and "Check your connection" not in page
    assert clock.now < lyrics.RETRY_FOR and len(calls) == 10


def test_timeouts_are_retried(monkeypatch):
    patch_network(monkeypatch, [TimeoutError("timed out"), RESULTS])
    assert lyrics.search("x")


def test_offline_fails_at_once(client, monkeypatch):
    from urllib.error import URLError
    _, calls = patch_network(monkeypatch, [URLError("Name or service not known")])
    assert "Check your connection" in client.get("/songs/find?q=x").get_data(as_text=True) and len(calls) == 1


def test_other_http_errors_are_not_retried(client, monkeypatch):
    _, calls = patch_network(monkeypatch, [404])
    assert "HTTP 404" in client.get("/songs/find?q=x").get_data(as_text=True) and len(calls) == 1


def test_import_retries_too(client, monkeypatch):
    patch_network(monkeypatch, [503, RESULTS[0]])
    assert client.post("/songs/import", data={"lrclib_id": "1"}).headers["Location"].endswith("/songs/1/edit")
