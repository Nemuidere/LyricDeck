"""Offline dictionary: OpenRussian (CC-BY-SA 4.0) for English glosses, stress and aspect pairs,
and the Russian National Corpus frequency list (Lyashevskaya & Sharoff, 2009) for word frequency.

`flask init-data` downloads both into data/raw/ and imports them into the app database.
"""

import csv
import hashlib
import io
import sqlite3
import zipfile
from functools import cached_property
from pathlib import Path
from urllib.request import Request, urlopen

import click
from flask import current_app

from .nlp import Token, _morph, fold

OPENRUSSIAN_URL = "https://raw.githubusercontent.com/Badestrand/russian-dictionary/master/{}.csv"
OPENRUSSIAN_TABLES = {"nouns": "noun", "verbs": "verb", "adjectives": "adj", "others": "other"}
# Only served over plain http (the https certificate is broken), so the file is checked by hash.
RNC_URL = "http://dict.ruslang.ru/Freq2011.zip"
RNC_SHA256 = "1ae2950966c34c52355e4d5cb91f1cc715f1d50774af71cdf4319223130c7c6e"
USER_AGENT = "LyricDeck/0.1 (+https://github.com/Nemuidere/LyricDeck)"

UD_TO_TABLE = {"NOUN": "noun", "PROPN": "noun", "VERB": "verb", "AUX": "verb", "ADJ": "adj", "DET": "adj"}
STRESS = "́"
VOWELS = set("аеёиоуыэюяАЕЁИОУЫЭЮЯ")

# Common words missing from OpenRussian.
EXTRA_GLOSSES = {"наш": "our, ours", "ваш": "your, yours (plural or formal)"}

SCHEMA = """
DROP TABLE IF EXISTS dict_words;
DROP TABLE IF EXISTS dict_forms;
DROP TABLE IF EXISTS dict_freq;
CREATE TABLE dict_words (key TEXT, kind TEXT, accented TEXT, english TEXT, partner TEXT, aspect TEXT);
CREATE TABLE dict_forms (form TEXT, accented TEXT, key TEXT);
CREATE TABLE dict_freq (key TEXT PRIMARY KEY, ipm REAL);
"""
INDEXES = """
CREATE INDEX dict_words_key ON dict_words (key);
CREATE INDEX dict_forms_form ON dict_forms (form);
"""


def accent(marked: str) -> str:
    """OpenRussian marks stress with an apostrophe after the vowel: любо'вь -> любо́вь."""
    return marked.strip().replace("'", STRESS)


def _download(url: str, dest: Path) -> bytes:
    if not dest.exists():
        with urlopen(Request(url, headers={"User-Agent": USER_AGENT}), timeout=60) as r:
            dest.write_bytes(r.read())
    return dest.read_bytes()


def _form_columns(header: list[str]) -> list[str]:
    prefixes = ("sg_", "pl_", "decl_", "short_", "past_", "presfut_", "imperative_")
    return [c for c in header if c.startswith(prefixes) or c in ("comparative", "superlative")]


def import_data(conn: sqlite3.Connection, raw_dir: Path) -> dict[str, int]:
    raw_dir.mkdir(parents=True, exist_ok=True)
    conn.executescript(SCHEMA)
    words, forms = [], []
    for name, kind in OPENRUSSIAN_TABLES.items():
        text = _download(OPENRUSSIAN_URL.format(name), raw_dir / f"openrussian_{name}.csv").decode()
        reader = csv.DictReader(io.StringIO(text), delimiter="\t", quoting=csv.QUOTE_NONE)
        columns = _form_columns(reader.fieldnames)
        for row in reader:
            key = fold(row["bare"])
            words.append((key, kind, accent(row["accented"]), row["translations_en"].strip(),
                          (row.get("partner") or "").strip() if kind == "verb" else "", row.get("aspect") or ""))
            for variant in {v for c in columns for v in (row[c] or "").split(",") if v.strip()}:
                forms.append((fold(variant.replace("'", "").strip()), accent(variant), key))
    words += [(key, "other", key, english, "", "") for key, english in EXTRA_GLOSSES.items()]

    data = _download(RNC_URL, raw_dir / "Freq2011.zip")
    if hashlib.sha256(data).hexdigest() != RNC_SHA256:
        raise click.ClickException("Frequency list download does not match the expected file; delete data/raw/Freq2011.zip and retry.")
    freq: dict[str, float] = {}
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        rows = csv.DictReader(io.TextIOWrapper(z.open("freqrnc2011.csv"), "utf-8"), delimiter="\t")
        for row in rows:
            freq[fold(row["Lemma"])] = freq.get(fold(row["Lemma"]), 0) + float(row["Freq(ipm)"])

    conn.executemany("INSERT INTO dict_words VALUES (?, ?, ?, ?, ?, ?)", words)
    conn.executemany("INSERT INTO dict_forms VALUES (?, ?, ?)", forms)
    conn.executemany("INSERT INTO dict_freq VALUES (?, ?)", freq.items())
    conn.executescript(INDEXES)
    conn.commit()
    return {"words": len(words), "forms": len(forms), "frequencies": len(freq)}


@click.command("init-data")
@click.option("--lang", type=click.Choice(["ru", "ja"]), default="ru", show_default=True,
              help="Language whose dictionary to download: ru (OpenRussian + frequency list) or ja (JMdict).")
def init_data_command(lang: str) -> None:
    """Download and import the offline dictionary for a language."""
    from .db import connect

    db_path = Path(current_app.config["DATABASE"])
    raw_dir = db_path.parent / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    with connect(str(db_path)) as conn:
        if lang == "ja":
            from . import japanese
            counts = japanese.import_data(conn, raw_dir, _download)
            if not japanese.available():
                click.echo("Note: the Japanese packages are not installed yet. Run: uv sync --extra ja")
        else:
            counts = import_data(conn, raw_dir)
    click.echo("Imported " + ", ".join(f"{n} {what}" for what, n in counts.items()))


def is_ready(conn: sqlite3.Connection) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'dict_freq'").fetchone() is not None


class Dictionary:
    """Lookups against the imported data. Frequencies are loaded into memory once (about 50k entries)."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    @cached_property
    def _freq(self) -> dict[str, float]:
        return dict(self.conn.execute("SELECT key, ipm FROM dict_freq"))

    @cached_property
    def _rank(self) -> dict[str, int]:
        ordered = sorted(self._freq.items(), key=lambda kv: -kv[1])
        return {key: i for i, (key, _) in enumerate(ordered, 1)}

    def rank(self, lemma: str) -> int:
        """Position in the Russian frequency list, 1 = most common, 0 = not listed."""
        return self._rank.get(fold(lemma), 0)

    def frequency(self, lemma: str) -> float:
        """Occurrences per million words in the Russian National Corpus (0 if unknown)."""
        return self._freq.get(fold(lemma), 0.0)

    def lookup(self, lemma: str, pos: str = "") -> sqlite3.Row | None:
        """Best entry for a dictionary form, preferring the table matching the part of speech."""
        rows = self.conn.execute("SELECT * FROM dict_words WHERE key = ?", (fold(lemma),)).fetchall()
        want = UD_TO_TABLE.get(pos, "other")
        return next((r for r in rows if r["kind"] == want), rows[0] if rows else None)

    def entry(self, token: Token) -> tuple[str, sqlite3.Row] | tuple[str, None]:
        """(dictionary form, entry) for a token, trying the chosen form, the other possible forms,
        the word as written and, for nouns, the plural (деньги). Returns (lemma, None) on a miss."""
        if row := self.lookup(token.lemma, token.pos):
            return token.lemma, row
        plural = _morph().parse(token.text.lower())[0].inflect({"plur", "nomn"})
        for form in [*token.alternatives, token.text.lower(), plural and plural.word]:
            if form and (row := self.lookup(form)):
                return form, row
        return token.lemma, None

    def stress(self, word: str, lemma: str = "") -> str | None:
        """The word with its stress mark, or None when unknown or ambiguous (за́мок / замо́к).
        Words with a single vowel or with ё need no mark and come back unchanged."""
        if sum(c in VOWELS for c in word) < 2 or "ё" in word.lower():
            return word
        rows = self.conn.execute("SELECT accented, key FROM dict_forms WHERE form = ?", (fold(word),)).fetchall()
        rows = rows or self.conn.execute("SELECT accented, key FROM dict_words WHERE key = ?", (fold(word),)).fetchall()
        if lemma and any(r["key"] == fold(lemma) for r in rows):
            rows = [r for r in rows if r["key"] == fold(lemma)]
        variants = {r["accented"] for r in rows}
        if len(variants) != 1:
            return None
        return _match_case(variants.pop(), word)


def _match_case(accented: str, original: str) -> str:
    """Copy the stress mark from the dictionary form onto the word as written (keeps capitals and ё)."""
    pos = accented.find(STRESS)
    if pos < 1 or len(accented.replace(STRESS, "")) != len(original):
        return original
    return original[:pos] + STRESS + original[pos:]
