"""Japanese tests. All lyrics are made up. Skipped when the optional Japanese packages are missing;
dictionary tests also need `flask --app lyricdeck init-data --lang ja`."""

import io
import shutil
import sqlite3
import tempfile
import zipfile
from pathlib import Path

import pytest

from lyricdeck import japanese as ja

pytestmark = pytest.mark.skipif(not ja.available(), reason="Japanese packages not installed (uv sync --extra ja)")

REAL_DB = Path(__file__).parent.parent / "data" / "app.db"
LYRICS = """【サビ】
君と歩いた道を、まだ覚えてる
夜空に星が消えていく （ｘ２）
行かないで、ここにいて
「さよなら」なんて言えなかった
作詞：テスト
君と歩いた道を、まだ覚えてる"""


def words(line: str) -> dict:
    return {t.text: t for t in ja.analyze([line])[0].tokens if t.is_word}


def test_clean():
    lines = ja.clean(LYRICS)
    assert lines[0] == "君と歩いた道を、まだ覚えてる"
    assert lines.count("夜空に星が消えていく") == 2          # (ｘ２) after NFKC
    assert not any("サビ" in l or "作詞" in l for l in lines)
    assert ja.clean("ねーーー、すごー〜い") == ["ねー、すごーい"]


def test_chunks_keep_endings_with_the_word():
    w = words("君と歩いた道を、まだ覚えてる")
    assert w["歩いた"].lemma == "歩く" and w["歩いた"].tag == "past"
    assert w["覚えてる"].tag == "-te iru"
    assert w["と"].small and w["を"].small and not w["君"].small


def test_grammar_notes():
    assert words("言えなかった")["言えなかった"].tag == "potential, negative, past"
    assert words("行かないで")["行かないで"].tag == "negative, te-form"
    assert words("消えていく")["消えていく"].tag == "-te iku"
    assert words("夢を見たい")["見たい"].tag == "want to"
    assert words("綺麗な花")["綺麗な"].tag == "attributive (-na)"


def test_compound_verbs_and_furigana():
    t = words("僕らは走り出す")["走り出す"]
    assert t.lemma == "走り出す" and t.extra["furi"].strip() == "走[はし]り 出[だ]す"
    assert ja.furigana("歩いた", "あるいた") == " 歩[ある]いた"
    assert ja.furigana("世の中", "よのなか") == " 世[よ]の 中[なか]"
    assert ja.furigana("キミ", "きみ") == "キミ"
    assert ja.strip_furigana(" 歩[ある]いた 道[みち]") == "歩いた道"


def test_english_and_symbols_are_not_words():
    assert set(words("Oh baby 夢を見たい")) == {"夢", "を", "見たい"}


@pytest.fixture(scope="module")
def jdict():
    from lyricdeck.db import connect
    if not REAL_DB.exists() or not ja.is_ready(conn := connect(str(REAL_DB))):
        pytest.skip("JMdict not imported")
    return ja.Japanese(conn)


def test_dictionary_lookup(jdict):
    w = words("ここにいて")
    here, be = jdict.word(w["ここ"], w["ここ"], True), jdict.word(w["いて"], w["いて"], True)
    assert here["front"] == "ここ" and here["english"].startswith("here")      # not the rare spelling 此処
    assert be["english"].startswith("to be")                                  # 居る, not 射る (to shoot)
    walk = jdict.word(*[words("君と歩いた")["歩いた"]] * 2, True)
    assert walk["front"] == "歩[ある]く" and walk["reading"] == "あるく" and walk["sung"] == "歩[ある]いた"
    assert walk["grammar"] == "past" and walk["pitch"] == "2" and walk["key"].startswith("ja|word|")


@pytest.fixture(scope="module")
def db_copy(tmp_path_factory):
    path = tmp_path_factory.mktemp("ja") / "app.db"
    if REAL_DB.exists():
        shutil.copy(REAL_DB, path)
    return path


@pytest.fixture
def client(db_copy):
    from lyricdeck import create_app
    from lyricdeck.db import SCHEMA, connect
    with connect(str(db_copy)) as c:
        c.executescript(SCHEMA + "DELETE FROM songs; DELETE FROM known; DELETE FROM settings;")
    return create_app({"TESTING": True, "DATABASE": str(db_copy)}).test_client()


def test_languages_are_separate(client):
    client.post("/songs/new", data={"title": "Русская", "lyrics": "Я иду домой"})
    client.post("/ja/songs/new", data={"title": "日本の歌", "lyrics": "君と歩いた道"})
    ru, jp = client.get("/").get_data(as_text=True), client.get("/ja/").get_data(as_text=True)
    assert "Русская" in ru and "日本の歌" not in ru
    assert "日本の歌" in jp and "Русская" not in jp
    assert client.get("/ja/songs/1/edit").status_code == 404                 # a Russian song is not reachable from /ja


def test_japanese_review_and_export(client, jdict):
    client.post("/ja/songs/new", data={"artist": "テスト", "title": "歌", "lyrics": LYRICS})
    page = client.post("/ja/review", data={"song": "1", "unit": ["word", "phrase", "line"], "min_count": "2",
                                           "count_repeats": "on", "grammar": "on"}).get_data(as_text=True)
    assert "<ruby>歩<rt>ある</rt></ruby>" in page and "to walk" in page and "top 500" in page
    data = {"sel": ["0"], "deck_name": "JP", "key-0": "ja|word|1", "kind-0": "word", "front-0": "歩[ある]く",
            "reading-0": "あるく", "pitch-0": "2", "english-0": "to walk", "sung-0": "歩[ある]いた"}
    res = client.post("/ja/export", data=data)
    with zipfile.ZipFile(io.BytesIO(res.data)) as z, tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "c.anki2").write_bytes(z.read("collection.anki2"))
        with sqlite3.connect(Path(tmp) / "c.anki2") as c:
            fields = c.execute("SELECT flds FROM notes").fetchone()[0].split("\x1f")
            models = c.execute("SELECT models FROM col").fetchone()[0]
    assert fields[:3] == ["歩く", "あるく", "歩[ある]く"] and "LyricDeck (Japanese" in models and "Russian" not in models
