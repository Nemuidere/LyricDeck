"""Dictionary tests. Lookup tests need `flask --app lyricdeck init-data` to have been run."""

from pathlib import Path

import pytest

from lyricdeck.db import connect
from lyricdeck.dictionary import Dictionary, _match_case, accent, is_ready
from lyricdeck.nlp import analyze

DB = Path(__file__).parent.parent / "data" / "app.db"


def test_accent_marks():
    assert accent("любо'вь") == "любо́вь"
    assert _match_case("любви́", "Любви") == "Любви́"


@pytest.fixture(scope="module")
def d():
    if not DB.exists() or not is_ready(conn := connect(str(DB))):
        pytest.skip("dictionary not imported")
    return Dictionary(conn)


def test_lookup_by_part_of_speech(d):
    assert "become" in d.lookup("стать", "VERB")["english"]
    assert "figure" in d.lookup("стать", "NOUN")["english"]
    assert d.lookup("сказать", "VERB")["partner"] == "говорить"
    assert d.lookup("наш", "DET")["english"].startswith("our")
    assert d.lookup("ёлка") is not None or d.lookup("елка") is not None
    assert d.lookup("шмыргль") is None


def test_frequency_tie_break(d):
    token = next(t for t in analyze(["Я пою"], freq=d.frequency)[0].tokens if t.text == "пою")
    assert token.lemma == "петь"


def test_entry_fallbacks(d):
    tokens = {t.text: t for t in analyze(["Где деньги, там и денег нет"], freq=d.frequency)[0].tokens}
    assert d.entry(tokens["денег"])[1]["english"].startswith("money")


def test_stress(d):
    assert d.stress("Любви", "любовь") == "Любви́"
    assert d.stress("ветер") == "ве́тер"
    assert d.stress("замке") is None          # за́мок / замо́к: ambiguous
    assert d.stress("мой") == "мой"            # one vowel: no mark needed
    assert d.stress("поёт") == "поёт"          # ё is always stressed
