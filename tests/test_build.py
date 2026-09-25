"""Build → review → export, using the imported dictionary (skipped when it is missing)."""

import io
import re
import shutil
import sqlite3
import zipfile
from pathlib import Path

import pytest

from lyricdeck import create_app
from lyricdeck.db import SCHEMA, connect
from lyricdeck.dictionary import is_ready

REAL_DB = Path(__file__).parent.parent / "data" / "app.db"
LYRICS = """Я иду домой, и ветер поёт.
Ты не знаешь, что-то стало иначе. (x2)
Любви не бывает без боли, без боли!
Я иду домой, и ветер поёт."""


@pytest.fixture(scope="module")
def dict_db(tmp_path_factory):
    if not REAL_DB.exists() or not is_ready(connect(str(REAL_DB))):
        pytest.skip("dictionary not imported")
    path = tmp_path_factory.mktemp("db") / "app.db"
    shutil.copy(REAL_DB, path)
    with connect(str(path)) as conn:
        conn.executescript(SCHEMA + "DELETE FROM songs; DELETE FROM known; DELETE FROM settings;")
    return path


@pytest.fixture
def client(dict_db, tmp_path):
    path = tmp_path / "app.db"
    shutil.copy(dict_db, path)
    c = create_app({"TESTING": True, "DATABASE": str(path)}).test_client()
    c.post("/songs/new", data={"artist": "Tester", "title": "Made Up", "lyrics": LYRICS})
    return c


def review(client, **extra):
    data = {"song": "1", "unit": ["word", "phrase", "line"], "min_count": "2", "count_repeats": "on", "grammar": "on"}
    return client.post("/review", data=data | extra).get_data(as_text=True)


def test_review_lists_cards(client):
    page = review(client)
    assert "ве́тер" in page and "wind" in page
    assert "иду́ домо́й" in page                 # a phrase
    assert "genitive singular" in page          # grammar hint for боли
    assert "любо́вь" not in page                 # appears once, below the minimum count


def test_review_needs_a_song(client):
    assert client.post("/review", data={}, follow_redirects=True).status_code == 200


def test_known_words_persist(client):
    client.post("/known", json={"key": "ru|word|ветер|NOUN", "known": True})
    rows = re.findall(r'<tr class="card-row.*?</tr>', review(client), re.S)
    wind = next(r for r in rows if "ве́тер</div>" in r)
    assert 'data-known="1"' in wind


def test_export_apkg(client):
    fields = {"sel": ["0", "1"], "deck_name": "Test deck"}
    for i, (key, russian, english) in enumerate([("ru|word|любовь|NOUN", "любо́вь", "love"),
                                                   ("ru|line|x", "Я иду́ домо́й", "I'm going home")]):
        fields |= {f"key-{i}": key, f"kind-{i}": "word" if i == 0 else "line", f"russian-{i}": russian,
                   f"english-{i}": english, f"source-{i}": "Tester – Made Up", f"tags-{i}": "Tester_Made_Up"}
    res = client.post("/export", data=fields)
    assert res.status_code == 200 and res.headers["Content-Disposition"].endswith("Test_deck.apkg")
    with zipfile.ZipFile(io.BytesIO(res.data)) as z, open_collection(z) as conn:
        notes = conn.execute("SELECT guid, flds, tags FROM notes").fetchall()
    assert len(notes) == 2 and "love" in notes[0][1] and "Tester_Made_Up" in notes[0][2]


class open_collection:
    def __init__(self, z):
        self.z = z

    def __enter__(self):
        import tempfile
        self.dir = tempfile.TemporaryDirectory()
        path = Path(self.dir.name) / "c.anki2"
        path.write_bytes(self.z.read("collection.anki2"))
        self.conn = sqlite3.connect(path)
        return self.conn

    def __exit__(self, *exc):
        self.conn.close()
        self.dir.cleanup()


def test_stable_guid_across_exports(client):
    def guid(english):
        data = {"sel": ["0"], "deck_name": "D", "key-0": "ru|word|любовь|NOUN", "kind-0": "word",
                "russian-0": "любо́вь", "english-0": english}
        with zipfile.ZipFile(io.BytesIO(client.post("/export", data=data).data)) as z, open_collection(z) as conn:
            return conn.execute("SELECT guid FROM notes").fetchone()[0]
    assert guid("love") == guid("love, affection")


def test_send_to_anki(client, monkeypatch):
    from lyricdeck import deck
    calls, notes = [], {}

    def fake_anki(action, **params):
        calls.append(action)
        if action == "modelNames":
            return []
        if action == "findNotes":
            return [1] if "любо" in params["query"] and notes else []
        if action == "addNote":
            notes[len(notes) + 1] = params["note"]
        return None

    monkeypatch.setattr(deck, "_anki", fake_anki)
    data = {"sel": ["0"], "deck_name": "D", "target": "anki", "key-0": "ru|word|любовь|NOUN", "kind-0": "word",
            "russian-0": "любо́вь", "english-0": "love"}
    assert "1 new and 0 updated" in client.post("/export", data=data).get_json()["message"]
    assert "0 new and 1 updated" in client.post("/export", data=data).get_json()["message"]
    assert calls.count("createModel") == 2 and "updateNoteFields" in calls


def test_send_to_anki_offline(client):
    data = {"sel": ["0"], "deck_name": "D", "target": "anki", "key-0": "k", "kind-0": "word", "russian-0": "x"}
    assert "Could not reach Anki" in client.post("/export", data=data).get_json()["message"]


def test_anki_search_escaping():
    from lyricdeck.deck import _search_value
    assert _search_value('a "b" c:d *') == 'a \\"b\\" c\\:d \\*'
