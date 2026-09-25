"""Russian lyrics processing: cleaning, tokenizing, lemmatizing, counting words, phrases and lines.

Lemmatization is a hybrid: spaCy tags each word's part of speech from context (on lowercased text,
so line-initial capitals are not mistaken for names), then the pymorphy3 parse matching that part of
speech gives the dictionary form. Near-ties between parses are broken by an optional frequency lookup.
"""

import re
from collections import Counter
from dataclasses import dataclass, field
from functools import cache
from typing import Callable

import pymorphy3
import spacy
from spacy.tokens import Doc

LATIN_TO_CYRILLIC = str.maketrans("aeopcxyAEOPCXYKMHTB", "аеорсхуАЕОРСХУКМНТВ")
CYRILLIC_TO_LATIN = str.maketrans("аеорсхуАЕОРСХУКМНТВ", "aeopcxyAEOPCXYKMHTB")
CYRILLIC = "а-яёА-ЯЁ"
WORD_RE = re.compile(rf"[{CYRILLIC}A-Za-z]+(?:-[{CYRILLIC}A-Za-z]+)*|[^\s{CYRILLIC}A-Za-z]+")
MIXED_WORD_RE = re.compile(rf"[{CYRILLIC}A-Za-z]*[{CYRILLIC}][{CYRILLIC}A-Za-z]*")
STRESS_RE = re.compile(rf"́|(?<=[{CYRILLIC}])['’`´]")
REPEAT_RE = re.compile(r"\s*[(\[]?\s*(?:[xх×]\s*(\d+)|(\d+)\s*[xх×]|(\d+)\s*раза?)\s*[)\]]?\s*$", re.I)
SECTION_WORDS = (r"припев|куплет|бридж|интро|аутро|проигрыш|chorus|verse|bridge|intro|outro|hook|хук|pre-chorus|пре-припев"
                 r"|大サビ|サビ|[ABC]メロ|間奏|イントロ|アウトロ")
SECTION_RE = re.compile(rf"^\s*(\[.*\]|【.*】|\(?\s*({SECTION_WORDS})\b[^)\n]*\)?:?)\s*$", re.I)

SMALL_PYMORPHY = {"PREP", "CONJ", "PRCL", "INTJ", "NPRO"}
# spaCy (Universal Dependencies) part of speech -> compatible pymorphy3 tags.
UD_TO_PYMORPHY = {
    "NOUN": {"NOUN"}, "PROPN": {"NOUN"}, "VERB": {"VERB", "INFN", "PRTF", "PRTS", "GRND"},
    "AUX": {"VERB", "INFN"}, "ADJ": {"ADJF", "ADJS", "COMP"}, "DET": {"ADJF", "NPRO"},
    "PRON": {"NPRO"}, "ADV": {"ADVB", "PRED", "COMP"}, "ADP": {"PREP"}, "CCONJ": {"CONJ"},
    "SCONJ": {"CONJ"}, "PART": {"PRCL"}, "INTJ": {"INTJ"}, "NUM": {"NUMR"},
}
PYMORPHY_TO_UD = {
    "NOUN": "NOUN", "VERB": "VERB", "INFN": "VERB", "PRTF": "VERB", "PRTS": "VERB", "GRND": "VERB",
    "ADJF": "ADJ", "ADJS": "ADJ", "COMP": "ADJ", "NPRO": "PRON", "ADVB": "ADV", "PRED": "ADV",
    "PREP": "ADP", "CONJ": "CCONJ", "PRCL": "PART", "INTJ": "INTJ", "NUMR": "NUM",
}
TIE_MARGIN = 0.1


@dataclass
class Token:
    text: str                      # as written in the lyrics
    start: int = 0                 # offset in the line
    lemma: str = ""                # dictionary form ("" for punctuation / non-Russian)
    pos: str = ""                  # Universal Dependencies part of speech
    tag: str = ""                  # pymorphy3 grammar tag of the chosen parse
    small: bool = False            # conjunction, preposition, particle, pronoun, ...
    known: bool = True             # False when the word is not in the pymorphy3 dictionary
    alternatives: list[str] = field(default_factory=list)  # other possible dictionary forms
    extra: dict = field(default_factory=dict)              # language-specific data (e.g. Japanese readings)

    @property
    def is_word(self) -> bool:
        return bool(self.lemma)


@dataclass
class Line:
    text: str
    tokens: list[Token]


def fold(text: str) -> str:
    """Matching key: lowercase with ё folded to е."""
    return text.lower().replace("ё", "е")


LOOKALIKES = set("aeopcxyAEOPCXYKMHTBаеорсхуАЕОРСХУКМНТВ")


def _fix_script(word: str) -> str:
    """Letters without a look-alike decide the word's alphabet; genuinely mixed words stay as they are."""
    latin = any("a" <= c.lower() <= "z" and c not in LOOKALIKES for c in word)
    cyrillic = any(c.isalpha() and not "a" <= c.lower() <= "z" and c not in LOOKALIKES for c in word)
    if latin == cyrillic:
        return word if latin else word.translate(LATIN_TO_CYRILLIC)
    return word.translate(CYRILLIC_TO_LATIN if latin else LATIN_TO_CYRILLIC)


def normalize(line: str) -> str:
    """Strip stress marks and fix look-alike letters from the other alphabet inside words."""
    return MIXED_WORD_RE.sub(lambda m: _fix_script(m.group()), STRESS_RE.sub("", line))


def clean_lyrics(text: str, count_repeats: bool = True, normalize_line: Callable[[str], str] | None = None) -> list[str]:
    """Lyrics text -> lines to analyze.

    Section labels ([Припев], Chorus:, ...) and blank lines are dropped. Repeat markers such as
    "(x2)" or "2 раза" repeat the line when count_repeats is set; otherwise identical lines are kept once.
    """
    lines, seen = [], set()
    for raw in text.splitlines():
        if not raw.strip() or SECTION_RE.match(raw):
            continue
        times = 1
        if m := REPEAT_RE.search(raw):
            times, raw = int(next(g for g in m.groups() if g)), raw[: m.start()]
        line = (normalize_line or normalize)(raw).strip().strip("()").strip()
        if not line:
            continue
        if count_repeats:
            lines += [line] * max(times, 1)
        elif (key := fold(line)) not in seen:
            seen.add(key)
            lines.append(line)
    return lines


@cache
def _morph() -> pymorphy3.MorphAnalyzer:
    return pymorphy3.MorphAnalyzer()


@cache
def _spacy():
    return spacy.load("ru_core_news_sm", exclude=["parser", "ner", "lemmatizer"])


def tokenize(line: str) -> list[tuple[str, int]]:
    """Split into (piece, start offset) for words and punctuation.
    Hyphenated words are kept whole if the dictionary knows them."""
    out = []
    for m in WORD_RE.finditer(line):
        piece, start = m.group(), m.start()
        if "-" in piece.strip("-") and piece[0].isalpha() and not _morph().word_is_known(piece.lower()):
            for part in re.finditer(r"[^-]+|-", piece):
                out.append((part.group(), start + part.start()))
        else:
            out.append((piece, start))
    return out


def _choose(word: str, ud_pos: str, freq: Callable[[str], float] | None):
    parses = _morph().parse(word)
    allowed = UD_TO_PYMORPHY.get(ud_pos, set())
    candidates = [p for p in parses if p.tag.POS in allowed]
    if not candidates:  # spaCy's guess fits no dictionary reading: trust pymorphy3
        candidates, ud_pos = parses, PYMORPHY_TO_UD.get(parses[0].tag.POS, ud_pos)
    best = candidates[0]
    if freq:
        close = [p for p in candidates if best.score - p.score < TIE_MARGIN]
        best = max(close, key=lambda p: freq(p.normal_form))
    alternatives = list(dict.fromkeys(p.normal_form for p in parses if p.normal_form != best.normal_form))
    return best, ud_pos, alternatives


def analyze(lines: list[str], freq: Callable[[str], float] | None = None) -> list[Line]:
    """Tag every word of every line. freq(lemma) breaks near-ties between possible dictionary forms."""
    pieces = [tokenize(line) for line in lines]
    docs = _spacy().pipe(Doc(_spacy().vocab, words=[w.lower() for w, _ in ps]) for ps in pieces)
    result = []
    for line, ps, doc in zip(lines, pieces, docs, strict=True):
        tokens = []
        for (w, start), t in zip(ps, doc, strict=True):
            if not w[0].isalpha() or not re.search(f"[{CYRILLIC}]", w):
                tokens.append(Token(w, start))
                continue
            parse, pos, alternatives = _choose(w.lower(), t.pos_, freq)
            small = parse.tag.POS in SMALL_PYMORPHY or "Apro" in parse.tag
            tokens.append(Token(
                text=w, start=start, lemma=parse.normal_form, pos=pos, tag=str(parse.tag),
                small=small, known=_morph().word_is_known(w.lower()), alternatives=alternatives,
            ))
        result.append(Line(line, tokens))
    return result


@dataclass
class Occurrence:
    song: int   # index of the song in the input list
    line: int   # index of the line within the song
    text: str   # word / phrase / line as written
    token: int = -1  # index of the (first) token in the line, -1 for whole lines
    size: int = 1    # number of tokens (phrases)


@dataclass
class Item:
    key: tuple
    count: int = 0
    occurrences: list[Occurrence] = field(default_factory=list)


def _add(items: dict, key: tuple, occ: Occurrence) -> None:
    item = items.setdefault(key, Item(key))
    item.count += 1
    item.occurrences.append(occ)


def count_words(songs: list[list[Line]]) -> dict[tuple, Item]:
    """(lemma, pos) -> Item, over all analyzed songs."""
    items: dict[tuple, Item] = {}
    for s, lines in enumerate(songs):
        for i, line in enumerate(lines):
            for k, t in enumerate(line.tokens):
                if t.is_word:
                    _add(items, (t.lemma, t.pos), Occurrence(s, i, t.text, k))
    return items


def count_lines(songs: list[list[Line]]) -> dict[tuple, Item]:
    """(folded line text,) -> Item."""
    items: dict[tuple, Item] = {}
    for s, lines in enumerate(songs):
        for i, line in enumerate(lines):
            if any(t.is_word for t in line.tokens):
                _add(items, (fold(line.text),), Occurrence(s, i, line.text))
    return items


def _segments(line: Line) -> list[list[tuple[int, Token]]]:
    """Runs of consecutive Russian words (with token indices), split at punctuation and non-Russian words."""
    segments, current = [], []
    for k, t in enumerate(line.tokens):
        if t.is_word:
            current.append((k, t))
        elif t.text != "-":
            segments.append(current)
            current = []
    return [s for s in segments + [current] if len(s) > 1]


def find_phrases(songs: list[list[Line]], sizes: range = range(2, 5)) -> dict[tuple, Item]:
    """Repeated word groups: 2-4 words within a line, not starting with a small word (prepositions
    excepted) and not ending with one. A group is dropped when a longer group containing it repeats
    just as often."""
    items: dict[tuple, Item] = {}
    for s, lines in enumerate(songs):
        for i, line in enumerate(lines):
            for seg in _segments(line):
                for n in sizes:
                    for j in range(len(seg) - n + 1):
                        idx, gram = zip(*seg[j: j + n])
                        if (gram[0].small and gram[0].pos != "ADP") or gram[-1].small:
                            continue
                        key = tuple(fold(t.text) for t in gram)
                        text = line.text[gram[0].start: gram[-1].start + len(gram[-1].text)]
                        _add(items, key, Occurrence(s, i, text, idx[0], len(gram)))
    repeated = {k: v for k, v in items.items() if v.count > 1}

    def contained(short: tuple, long: tuple) -> bool:
        return any(long[j: j + len(short)] == short for j in range(len(long) - len(short) + 1))

    return {
        k: v for k, v in repeated.items()
        if not any(len(o) > len(k) and ov.count == v.count and contained(k, o) for o, ov in repeated.items())
    }


def most_common_text(item: Item) -> str:
    return Counter(o.text.lower() for o in item.occurrences).most_common(1)[0][0]
