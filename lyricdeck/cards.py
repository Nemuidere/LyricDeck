"""Turn analyzed songs into candidate flashcards (words, phrases, lines)."""

import re
from dataclasses import asdict, dataclass, field
from html import escape

from .dictionary import Dictionary
from .nlp import Line, Occurrence, analyze, clean_lyrics, count_lines, count_words, find_phrases, fold, most_common_text

KIND_ORDER = {"word": 0, "phrase": 1, "line": 2}
CONTEXT_MAX_LINES = 3
CONTEXT_MAX_CHARS = 110

CASES = {"nomn": "nominative", "gent": "genitive", "gen2": "genitive", "datv": "dative", "accs": "accusative",
         "acc2": "accusative", "ablt": "instrumental", "loct": "prepositional", "loc2": "locative", "voct": "vocative"}
FORMS = {"INFN": "infinitive", "GRND": "gerund", "PRTF": "participle", "PRTS": "short participle",
         "ADJS": "short form", "COMP": "comparative"}
PERSONS = {"1per": "1st person", "2per": "2nd person", "3per": "3rd person"}
GENDERS = {"masc": "masculine", "femn": "feminine", "neut": "neuter"}
TENSES = {"pres": "present", "futr": "future"}


@dataclass
class Card:
    key: str
    kind: str
    russian: str
    english: str = ""
    sung: str = ""
    grammar: str = ""
    extra: str = ""
    context: str = ""
    context_english: str = ""
    plain: str = ""                                          # text to translate (phrases, lines)
    context_plain: list[str] = field(default_factory=list)   # the context lines as plain text
    source: str = ""
    tags: list[str] = field(default_factory=list)
    count: int = 0
    song: int = 0            # index of the first song it appears in
    small: bool = False
    rank: int = 0            # position in the Russian frequency list (0 = not listed)
    known: bool = False
    resolved: bool = True    # False when no dictionary entry was found
    alternatives: list[dict] = field(default_factory=list)


def describe(tag: str) -> str:
    """pymorphy3 tag -> short English grammar note: 'genitive singular', 'past, feminine',
    '1st person singular, present', 'imperative plural', 'infinitive'."""
    g = set(re.split(r"[ ,]", tag))
    pick = lambda table: next((label for key, label in table.items() if key in g), "")
    number = "plural" if "plur" in g else "singular" if "sing" in g else ""
    form = pick(FORMS)
    if "VERB" in g:
        if "past" in g:
            return "past, " + (pick(GENDERS) if number == "singular" else "plural")
        if "impr" in g:
            return f"imperative {number}".strip()
        return ", ".join(p for p in [f"{pick(PERSONS)} {number}".strip(), pick(TENSES)] if p)
    if form in ("infinitive", "gerund", "comparative"):
        return form
    return " ".join(p for p in [form + "," if form else "", pick(CASES), number] if p)


def song_label(song) -> str:
    return f"{song['artist']} – {song['title']}" if song["artist"] else song["title"]


def song_tag(song) -> str:
    return re.sub(r"[^\w]+", "_", song_label(song)).strip("_")


class Builder:
    def __init__(self, songs: list, d: Dictionary, known: set[str], count_repeats: bool = True, grammar: bool = True):
        self.songs, self.d, self.known, self.grammar = songs, d, known, grammar
        self.analyzed = [analyze(clean_lyrics(s["lyrics"], count_repeats), freq=d.frequency) for s in songs]
        self._stress: dict[tuple[str, str], str] = {}

    def stressed(self, word: str, lemma: str = "") -> str:
        if (word, lemma) not in self._stress:
            self._stress[word, lemma] = self.d.stress(word, lemma) or word
        return self._stress[word, lemma]

    def render(self, line: Line, bold: range = range(0), span: range | None = None) -> str:
        """HTML of a line (or of the tokens in `span`) with stress marks, tokens in `bold` wrapped in <b>."""
        span = span or range(len(line.tokens))
        out, pos = [], line.tokens[span.start].start if span else 0
        for k, t in enumerate(line.tokens):
            if k not in span:
                continue
            out.append(escape(line.text[pos: t.start]))
            text = escape(self.stressed(t.text, t.lemma) if t.is_word else t.text)
            out.append(f"<b>{text}</b>" if k in bold else text)
            pos = t.start + len(t.text)
        if span.stop >= len(line.tokens):
            out.append(escape(line.text[pos:]))
        spans = "".join(out)
        return spans.replace("</b> <b>", " ").replace("</b>-<b>", "-")

    def context(self, occurrences: list[Occurrence]) -> dict:
        """A few distinct lines the item appears in: as many as stay short, always at least one."""
        chosen, plain, seen, chars = [], [], set(), 0
        for o in occurrences:
            line = self.analyzed[o.song][o.line]
            if fold(line.text) in seen:
                continue
            if chosen and (len(chosen) >= CONTEXT_MAX_LINES or chars + len(line.text) > CONTEXT_MAX_CHARS):
                break
            seen.add(fold(line.text))
            chars += len(line.text)
            chosen.append(self.render(line, range(o.token, o.token + o.size) if o.token >= 0 else range(0)))
            plain.append(line.text)
        return {"context": "<br>".join(chosen), "context_plain": plain}

    def _common(self, key: str, occurrences: list[Occurrence]) -> dict:
        songs = list(dict.fromkeys(o.song for o in occurrences))
        return {
            "known": key in self.known, "song": songs[0],
            "source": "; ".join(song_label(self.songs[s]) for s in songs),
            "tags": [song_tag(self.songs[s]) for s in songs],
        }

    def _extra(self, row) -> str:
        if not row or not row["partner"]:
            return ""
        partners = ", ".join(self.stressed(p.strip()) for p in row["partner"].split(";") if p.strip(" -"))
        if not partners:
            return ""
        return f"{row['aspect']} · pair: {partners}" if row["aspect"] else f"pair: {partners}"

    def _entry(self, lemma: str, pos: str) -> dict:
        row = self.d.lookup(lemma, pos)
        return {"lemma": lemma, "russian": row["accented"] if row else lemma, "english": row["english"] if row else "",
                "key": f"ru|word|{fold(lemma)}|{pos}", "extra": self._extra(row)}

    def words(self, min_count: int) -> list[Card]:
        cards = []
        for (_, pos), item in count_words(self.analyzed).items():
            if item.count < min_count:
                continue
            first = item.occurrences[0]
            token = self.analyzed[first.song][first.line].tokens[first.token]
            lemma, row = self.d.entry(token)
            entry = self._entry(lemma, pos)
            sung_text = most_common_text(item)
            sung_occ = next(o for o in item.occurrences if o.text.lower() == sung_text)
            sung_token = self.analyzed[sung_occ.song][sung_occ.line].tokens[sung_occ.token]
            differs = fold(sung_text) != fold(lemma)
            cards.append(Card(
                key=entry["key"], kind="word", russian=entry["russian"], english=entry["english"],
                sung=self.stressed(sung_text, lemma) if differs else "",
                grammar=describe(sung_token.tag) if differs and self.grammar else "",
                extra=entry["extra"], **self.context(item.occurrences), count=item.count,
                small=token.small, rank=self.d.rank(lemma), resolved=row is not None,
                alternatives=[self._entry(a, "") for a in token.alternatives if self.d.lookup(a)][:4],
                **self._common(entry["key"], item.occurrences),
            ))
        return cards

    def phrases(self, min_count: int) -> list[Card]:
        cards = []
        for key, item in find_phrases(self.analyzed).items():
            if item.count < min_count:
                continue
            first = item.occurrences[0]
            line = self.analyzed[first.song][first.line]
            card_key = "ru|phrase|" + " ".join(key)
            text = self.render(line, span=range(first.token, first.token + first.size))
            plain = line.text[line.tokens[first.token].start: line.tokens[first.token + first.size - 1].start
                              + len(line.tokens[first.token + first.size - 1].text)]
            cards.append(Card(key=card_key, kind="phrase", russian=text, count=item.count, plain=plain,
                              **self.context(item.occurrences), **self._common(card_key, item.occurrences)))
        return cards

    def lines(self, min_count: int) -> list[Card]:
        cards = []
        for (text,), item in count_lines(self.analyzed).items():
            if item.count < min_count:
                continue
            first = item.occurrences[0]
            card_key = f"ru|line|{text}"
            line = self.analyzed[first.song][first.line]
            cards.append(Card(key=card_key, kind="line", russian=self.render(line), plain=line.text,
                              count=item.count, **self._common(card_key, item.occurrences)))
        return cards

def build(songs: list, d: Dictionary, known: set[str], units: set[str], min_count: int = 2,
          count_repeats: bool = True, grammar: bool = True) -> list[dict]:
    """Candidate cards as dicts, ordered by kind, song, then frequency."""
    b = Builder(songs, d, known, count_repeats, grammar)
    cards = [c for unit in ("word", "phrase", "line") if unit in units for c in getattr(b, unit + "s")(min_count)]
    cards.sort(key=lambda c: (KIND_ORDER[c.kind], c.song, -c.count))
    return [asdict(c) for c in cards]
