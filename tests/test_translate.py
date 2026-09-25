"""Translation tests. The MyMemory HTTP call is replaced, so no network is used."""

import json

import pytest

from lyricdeck import translate
from lyricdeck.db import SCHEMA, connect


@pytest.fixture
def conn(tmp_path):
    c = connect(str(tmp_path / "t.db"))
    c.executescript(SCHEMA)
    return c


class FakeMyMemory:
    def __init__(self, keep_newlines=True, quota_after=None):
        self.calls, self.keep_newlines, self.quota_after = [], keep_newlines, quota_after

    def __call__(self, text, email, langpair="ru|en"):
        self.langpair = langpair
        self.calls.append((text, email))
        if self.quota_after is not None and len(self.calls) > self.quota_after:
            raise translate.QuotaExceeded(None)
        lines = [f"EN({t})" for t in text.split("\n")]
        return "\n".join(lines) if self.keep_newlines else " ".join(lines)


def test_batches_respect_size_limit():
    texts = ["строка номер " + str(i) * 20 for i in range(30)]
    batches = translate._batches(texts)
    assert [t for b in batches for t in b] == texts
    assert all(len("\n".join(b).encode()) <= translate.MYMEMORY_MAX_BYTES for b in batches)


def test_translates_in_batches_and_caches(conn, monkeypatch):
    fake = FakeMyMemory()
    monkeypatch.setattr(translate, "_mymemory_request", fake)
    monkeypatch.setattr(translate, "REQUEST_GAP", 0)
    result = translate.mymemory(conn, ["Ветер поёт", "Я иду домой", "Ветер поёт"], "me@example.com")
    assert result == {"Ветер поёт": "EN(Ветер поёт)", "Я иду домой": "EN(Я иду домой)"}
    assert len(fake.calls) == 1 and fake.calls[0][1] == "me@example.com"
    assert translate.usage(conn, "mymemory") == {"chars": len("Ветер поёт\nЯ иду домой"), "requests": 1}
    translate.mymemory(conn, ["Ветер поёт"])
    assert len(fake.calls) == 1  # served from the cache


def test_falls_back_when_newlines_are_lost(conn, monkeypatch):
    fake = FakeMyMemory(keep_newlines=False)
    monkeypatch.setattr(translate, "_mymemory_request", fake)
    monkeypatch.setattr(translate, "REQUEST_GAP", 0)
    result = translate.mymemory(conn, ["один", "два"])
    assert result["один"].startswith("EN(один)") and len(fake.calls) == 3


def test_quota_keeps_partial_results(conn, monkeypatch):
    monkeypatch.setattr(translate, "_mymemory_request", FakeMyMemory(quota_after=1))
    monkeypatch.setattr(translate, "REQUEST_GAP", 0)
    long = ["слово " * 40 + str(i) for i in range(3)]  # one text per request
    with pytest.raises(translate.QuotaExceeded):
        translate.mymemory(conn, long)
    assert len(translate.cached(conn, "mymemory", long)) == 1


def test_quota_message_parsing(monkeypatch):
    body = {"responseData": {"translatedText": "MYMEMORY WARNING: YOU USED ALL AVAILABLE FREE TRANSLATIONS FOR TODAY. "
                                               "NEXT AVAILABLE IN  13 HOURS 31 MINUTES 45 SECONDS"},
            "quotaFinished": True, "responseStatus": 429}

    class Response:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self): return json.dumps(body).encode()

    monkeypatch.setattr(translate.urllib.request, "urlopen", lambda *a, **k: Response())
    with pytest.raises(translate.QuotaExceeded) as e:
        translate._mymemory_request("тест", "")
    assert e.value.until is not None


def test_translate_endpoint_and_settings(client, monkeypatch):
    monkeypatch.setattr(translate, "_mymemory_request", FakeMyMemory())
    monkeypatch.setattr(translate, "REQUEST_GAP", 0)
    data = client.post("/translate", json={"texts": ["Ветер поёт"]}).get_json()
    assert data == {"translations": {"Ветер поёт": "EN(Ветер поёт)"}, "error": None}

    client.post("/settings", data={"deck_name": "Mine", "line_translator": "none", "min_count": "3", "unit": ["word"]})
    assert client.post("/translate", json={"texts": ["Ещё"]}).get_json()["translations"] == {}
    page = client.get("/settings").get_data(as_text=True)
    assert 'value="Mine"' in page and "of 5,000 characters used" in page
