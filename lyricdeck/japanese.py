"""Japanese → English (optional: `uv sync --extra ja`, then `flask --app lyricdeck init-data --lang ja`).

Words and readings come from fugashi + UniDic-lite. A verb or adjective is kept together with its endings
(歩いた = 歩く + た) so a card can show the form as sung and a grammar note. English glosses and frequency
ranks come from JMdict (EDRDG, CC BY-SA 4.0), imported into the app database.
"""

import gzip
import io
import re
import sqlite3
import unicodedata
import xml.etree.ElementTree as ET
from functools import cache
from pathlib import Path

from .nlp import Line, Token, clean_lyrics, fold

JMDICT_URL = "https://www.edrdg.org/pub/Nihongo/JMdict_e.gz"

KANJI_CHARS = "一-鿿々〆ヶ"
KANJI = re.compile(f"[{KANJI_CHARS}]")
JAPANESE = re.compile(f"[぀-ヿ{KANJI_CHARS}]")
FURIGANA = re.compile(r" ?([^ >\[\]<]+?)\[([^\]]*)\]")
CREDIT_RE = re.compile(r"^\s*(作词|作曲|编曲|作詞|編曲|制作人|词|曲)\s*[:：]")

SMALL_POS = {"助詞", "助動詞", "接尾辞", "補助記号", "記号", "空白"}
INFLECTING = {"動詞", "形容詞", "形状詞"}
ATTACH_LEMMA = {"て", "ば", "たり", "ながら"}
TE_AUX = {"居る": "-te iru", "行く": "-te iku", "来る": "-te kuru", "仕舞う": "-te shimau", "置く": "-te oku",
          "見る": "-te miru", "呉れる": "-te kureru", "上げる": "-te ageru", "貰う": "-te morau", "有る": "-te aru"}
COMPOUND_AUX = {"出す", "始める", "続ける", "込む", "合う", "切る", "終わる"}
AUX_NOTE = {"た": "past", "ない": "negative", "ぬ": "negative", "ず": "negative", "ん": "negative",
            "ます": "polite", "です": "polite", "たい": "want to", "う": "volitional", "よう": "volitional",
            "れる": "passive/potential", "られる": "passive/potential", "せる": "causative", "させる": "causative",
            "てる": "-te iru", "ちゃう": "-te shimau", "とく": "-te oku", "だ": "copula", "らしい": "seems",
            "そう": "looks like", "まい": "negative volitional"}
FORM_NOTE = {"命令形": "imperative", "意志推量形": "volitional"}
# UniDic part of speech -> words found in JMdict's part-of-speech descriptions.
POS_MATCH = {"動詞": "verb", "名詞": "noun", "形容詞": "adjective (keiyoushi)", "形状詞": "adjectival noun",
             "副詞": "adverb", "代名詞": "pronoun", "感動詞": "interjection", "連体詞": "pre-noun"}
COMMON_TAGS = {"news1", "ichi1", "spec1", "spec2", "gai1"}
COMMON_OPTIONS = [500, 1000, 2000, 5000]

SCHEMA = """
DROP TABLE IF EXISTS ja_entries;
DROP TABLE IF EXISTS ja_index;
CREATE TABLE ja_entries (id INTEGER PRIMARY KEY, word TEXT, reading TEXT, english TEXT, pos TEXT,
                         common INTEGER, rank INTEGER);
CREATE TABLE ja_index (text TEXT, reading TEXT, id INTEGER);
"""


def available() -> bool:
    """True when the optional Japanese packages are installed."""
    try:
        import fugashi  # noqa: F401
        import unidic_lite  # noqa: F401
    except ImportError:
        return False
    return True


def is_ready(conn: sqlite3.Connection) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'ja_entries'").fetchone() is not None


def hira(text: str) -> str:
    return "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in text or "")


def strip_furigana(text: str) -> str:
    return FURIGANA.sub(r"\1", text).strip()


def furigana(surface: str, reading: str) -> str:
    """Anki furigana: 歩[ある]いた. Kana around the kanji stays outside the brackets; each kanji group gets a
    leading space so Anki knows where it starts."""
    reading = hira(reading)
    if not KANJI.search(surface) or not reading or reading == hira(surface):
        return surface
    parts = re.findall(f"[{KANJI_CHARS}]+|[^{KANJI_CHARS}]+", surface)
    pattern = "".join("(.+?)" if KANJI.match(p) else re.escape(hira(p)) for p in parts)
    if not (m := re.fullmatch(pattern, reading)):
        return f" {surface}[{reading}]"
    groups = iter(m.groups())
    return "".join(f" {p}[{next(groups)}]" if KANJI.match(p) else p for p in parts)


def normalize(line: str) -> str:
    return re.sub(r"ー{2,}", "ー", re.sub(r"[~〜～]+", "ー", line))


def clean(text: str, count_repeats: bool = True) -> list[str]:
    text = unicodedata.normalize("NFKC", text)
    text = "\n".join(line for line in text.splitlines() if not CREDIT_RE.match(line))
    return [line for line in clean_lyrics(text, count_repeats, normalize_line=normalize) if JAPANESE.search(line)]


@cache
def _tagger():
    import fugashi
    return fugashi.Tagger()


def _lemma(w) -> str:
    return (w.feature.lemma or w.surface).split("-")[0]


def _chunks(morphemes: list) -> list[list]:
    """Group an inflecting word with its auxiliaries, connecting particles and helper verbs."""
    out, i = [], 0
    while i < len(morphemes):
        group, i = [morphemes[i]], i + 1
        if group[0].feature.pos1 in INFLECTING:
            while i < len(morphemes):
                n, prev = morphemes[i], group[-1]
                f, pf = n.feature, prev.feature
                helper = (f.pos1 == "動詞" and f.pos2 == "非自立可能"
                          and (_lemma(prev) == "て" or (pf.cForm or "").startswith("連用形")))
                if f.pos1 == "助動詞" or (f.pos1 == "助詞" and _lemma(n) in ATTACH_LEMMA) or helper:
                    group.append(n)
                    i += 1
                else:
                    break
        out.append(group)
    return out


def grammar_note(group: list) -> str:
    """Short English note for a word as sung: '言えなかった' -> 'potential, negative, past'."""
    head, notes = group[0], []
    if head.feature.pos1 == "動詞" and (head.feature.cType or "").startswith("下一段") \
            and _lemma(head) != head.feature.orthBase:
        notes.append("potential")
    for k, w in enumerate(group[1:], 1):
        lemma, form = _lemma(w), (w.feature.cForm or "").split("-")[0]
        if _lemma(group[k - 1]) == "て" and lemma in TE_AUX and notes and notes[-1] == "te-form":
            notes[-1] = TE_AUX[lemma]
        elif lemma == "だ" and head.feature.pos1 == "形状詞" and form == "連体形":
            notes.append("attributive (-na)")
        elif lemma in AUX_NOTE:
            notes.append(AUX_NOTE[lemma])
        elif lemma == "て":
            notes.append("te-form")
        elif lemma == "ば":
            notes.append("conditional")
        if form in FORM_NOTE:
            notes.append(FORM_NOTE[form])
    head_form = (head.feature.cForm or "").split("-")[0]
    if head_form in FORM_NOTE:
        notes.insert(0, FORM_NOTE[head_form])
    if head_form == "仮定形" and "conditional" not in notes:
        notes.append("conditional")
    return ", ".join(dict.fromkeys(notes))


def _compound(group: list) -> tuple[str, str] | None:
    """走り + 出す -> (走り出す, はしりだす): compound verbs are one word in the dictionary."""
    if len(group) > 1 and group[0].feature.pos1 == "動詞" and _lemma(group[1]) in COMPOUND_AUX \
            and (group[0].feature.cForm or "").startswith("連用形"):
        return group[0].surface + group[1].feature.orthBase, hira(group[0].feature.kana + group[1].feature.kanaBase)
    return None


def analyze(lines: list[str]) -> list[Line]:
    """Tokens are words as sung: 歩いた is one token with lemma 歩く. Particles are separate small tokens."""
    result = []
    for line in lines:
        morphemes = [w for w in _tagger()(line)]
        tokens, pos = [], 0
        for group in _chunks(morphemes):
            start = line.find(group[0].surface, pos)
            start = pos if start < 0 else start
            end = start
            for w in group:
                found = line.find(w.surface, end)
                end = (found if found >= 0 else end) + len(w.surface)
            text, pos = line[start:end], end
            head = group[0]
            f = head.feature
            if not JAPANESE.search(text) or f.pos1 in {"補助記号", "記号", "空白"}:
                tokens.append(Token(text, start))
                continue
            compound = _compound(group)
            base = compound[0] if compound else f.orthBase or head.surface
            reading = compound[1] if compound else hira(f.kanaBase or f.kana or "")
            tokens.append(Token(
                text=text, start=start, lemma=compound[0] if compound else _lemma(head), pos=f.pos1 or "",
                tag=grammar_note(group), small=f.pos1 in SMALL_POS, known=not head.is_unk,
                extra={"base": base, "reading": reading, "lemma": _lemma(head),
                       "furi": "".join(furigana(w.surface, w.feature.kana or "") for w in group),
                       "pitch": (f.aType or "").split(",")[0] if (f.aType or "*") != "*" else ""},
            ))
        result.append(Line(line, tokens))
    return result


def _entry_fields(el) -> tuple:
    kebs = [(k.find("keb").text, [p.text for p in k.findall("ke_pri")]) for k in el.findall("k_ele")]
    rebs = [(r.find("reb").text, [p.text for p in r.findall("re_pri")], [x.text for x in r.findall("re_restr")])
            for r in el.findall("r_ele")]
    pris = {p for _, ps in kebs for p in ps} | {p for _, ps, _ in rebs for p in ps}
    nf = [int(p[2:]) for p in pris if p.startswith("nf")]
    senses = el.findall("sense")
    english = "; ".join(", ".join(g.text for g in s.findall("gloss")[:3]) for s in senses[:3] if s.findall("gloss"))
    pos = next((p.text for s in senses for p in s.findall("pos")), "")
    return kebs, rebs, english, pos, bool(pris & COMMON_TAGS), min(nf) * 500 if nf else 0


def import_data(conn: sqlite3.Connection, raw_dir: Path, download) -> dict[str, int]:
    """Import JMdict (English glosses) into ja_entries / ja_index."""
    data = download(JMDICT_URL, raw_dir / "JMdict_e.gz")
    conn.executescript(SCHEMA)
    entries, index = [], []
    for _, el in ET.iterparse(io.BytesIO(gzip.decompress(data)), events=("end",)):
        if el.tag != "entry":
            continue
        entry_id = int(el.find("ent_seq").text)
        kebs, rebs, english, pos, common, rank = _entry_fields(el)
        word = kebs[0][0] if kebs else rebs[0][0]
        entries.append((entry_id, word, rebs[0][0], english, pos, int(common), rank))
        for reb, _, restr in rebs:
            index.append((reb, hira(reb), entry_id))
            index += [(keb, hira(reb), entry_id) for keb, _ in kebs if not restr or keb in restr]
        el.clear()
    conn.executemany("INSERT INTO ja_entries VALUES (?, ?, ?, ?, ?, ?, ?)", entries)
    conn.executemany("INSERT INTO ja_index VALUES (?, ?, ?)", index)
    conn.execute("CREATE INDEX ja_index_text ON ja_index (text)")
    conn.commit()
    return {"entries": len(entries), "spellings": len(index)}


class Japanese:
    """Language plug-in for the card builder."""

    code, name, langpair = "ja", "Japanese", "ja|en"
    common_options = COMMON_OPTIONS
    clean = staticmethod(clean)

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    @staticmethod
    def ready(conn: sqlite3.Connection) -> bool:
        return available() and is_ready(conn)

    def lines(self, lyrics: str, count_repeats: bool) -> list[Line]:
        return analyze(clean(lyrics, count_repeats))

    def decorate(self, token: Token) -> str:
        return token.extra.get("furi", token.text) if token.is_word else token.text

    def fold(self, text: str) -> str:
        return fold(text)

    def entries(self, token: Token) -> tuple[list[sqlite3.Row], str]:
        """Matching JMdict entries, best first (same reading, same part of speech, common, frequent),
        and the spelling that matched."""
        extra = token.extra
        # The standardised form (居る) pins down the entry better than a kana spelling (いる: 射る, 要る, 居る...).
        for text in dict.fromkeys([token.lemma, extra["base"], extra["lemma"], token.text, extra["reading"]]):
            rows = self.conn.execute("SELECT e.*, i.reading AS matched FROM ja_index i JOIN ja_entries e ON e.id = i.id "
                                     "WHERE i.text = ?", (text,)).fetchall()
            if rows:
                break
        else:
            return [], ""
        same = [r for r in rows if r["matched"] == extra["reading"]] or rows
        want = POS_MATCH.get(token.pos, "~")
        same.sort(key=lambda r: (want not in (r["pos"] or "").lower(), not r["common"], r["rank"] or 10**6, r["id"]))
        return list({r["id"]: r for r in same}.values()), text

    def _card_entry(self, row, token: Token, matched: str = "") -> dict:
        """The song's own spelling when it is a dictionary spelling (ここ, not 此処), else JMdict's headword."""
        extra = token.extra
        own = row is None or (matched in (extra["base"], token.lemma, extra["lemma"]) and row["matched"] == extra["reading"])
        word = extra["base"] if row is None or own else row["word"]
        reading = extra["reading"] if own else hira(row["reading"])
        return {"front": furigana(word, reading).strip(), "english": row["english"] if row else "",
                "extra": re.sub(r"\s*\([^)]*\)", "", row["pos"] or "") if row else "",
                "reading": reading if KANJI.search(word) else "",
                "key": f"ja|word|{row['id']}" if row else f"ja|word|{token.lemma}|{reading}"}

    def word(self, token: Token, sung: Token, grammar: bool) -> dict:
        rows, matched = self.entries(token)
        entry = self._card_entry(rows[0] if rows else None, token, matched)
        differs = sung.text != strip_furigana(entry["front"])
        return entry | {
            "sung": sung.extra["furi"].strip() if differs else "", "grammar": sung.tag if differs and grammar else "",
            "pitch": token.extra.get("pitch", ""), "rank": rows[0]["rank"] if rows else 0, "resolved": bool(rows),
            "alternatives": [self._card_entry(r, token, matched) for r in rows[1:5]],
        }
