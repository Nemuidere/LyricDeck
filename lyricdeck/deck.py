"""Anki export (.apkg or AnkiConnect) with a fixed note type per language and stable note IDs,
so re-imports update cards instead of duplicating them."""

import hashlib
import json
import re
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import genanki

ATTRIBUTION = ("Dictionary data: OpenRussian.org (CC BY-SA 4.0). "
               "Frequency data: Lyashevskaya & Sharoff, Russian National Corpus frequency dictionary (2009).")
JA_ATTRIBUTION = ("Dictionary data: JMdict, Electronic Dictionary Research and Development Group (EDRDG), "
                  "CC BY-SA 4.0 (https://www.edrdg.org/edrdg/licence.html). Readings: UniDic.")

CSS = """.card { font-family: system-ui, sans-serif; font-size: 20px; text-align: center; color: #0b1a33; background: #f5f8fd; }
.nightMode.card, .night_mode .card { color: #eef3fb; background: #0b1a33; }
.ru { font-size: 1.6em; font-weight: 600; }
.sung { font-size: 0.85em; opacity: 0.7; margin-top: 0.2em; }
.en { font-size: 1.15em; margin: 0.4em 0; }
.note { font-size: 0.75em; opacity: 0.7; }
.ctx { margin-top: 1em; font-size: 0.9em; line-height: 1.5; }
.ctx-en { font-size: 0.8em; opacity: 0.7; font-style: italic; }
.src { margin-top: 1em; font-size: 0.65em; opacity: 0.5; }"""

RU_FRONT = """<div class="ru">{{Russian}}</div>
{{#SungForm}}<div class="sung">{{SungForm}}</div>{{/SungForm}}"""
RU_BACK = """{{FrontSide}}
<hr id="answer">
<div class="en">{{English}}</div>
{{#Grammar}}<div class="note">{{SungForm}}: {{Grammar}}</div>{{/Grammar}}
{{#Extra}}<div class="note">{{Extra}}</div>{{/Extra}}
{{#Context}}<div class="ctx">{{Context}}</div>{{/Context}}
{{#ContextEnglish}}<div class="ctx-en">{{ContextEnglish}}</div>{{/ContextEnglish}}
<div class="src">{{Source}}</div>"""

# Furigana fields use Anki's 漢字[かんじ] syntax; the front hides readings ({{kanji:}}), the back shows them.
JA_FRONT = """<div class="ru jp">{{kanji:WordFurigana}}</div>
{{#SungForm}}<div class="sung jp">{{kanji:SungForm}}</div>{{/SungForm}}"""
JA_BACK = """{{FrontSide}}
<hr id="answer">
{{#Reading}}<div class="reading jp">{{furigana:WordFurigana}}{{#Pitch}} <span class="pitch">[{{Pitch}}]</span>{{/Pitch}}</div>{{/Reading}}
<div class="en">{{English}}</div>
{{#Grammar}}<div class="note jp">{{furigana:SungForm}}: {{Grammar}}</div>{{/Grammar}}
{{#Extra}}<div class="note">{{Extra}}</div>{{/Extra}}
{{#Context}}<div class="ctx jp">{{furigana:Context}}</div>{{/Context}}
{{#ContextEnglish}}<div class="ctx-en">{{ContextEnglish}}</div>{{/ContextEnglish}}
<div class="src">{{Source}}</div>"""
JA_CSS = CSS + """
.jp { font-family: "Noto Sans JP", "Hiragino Sans", "Yu Gothic", system-ui, sans-serif; }
.reading { font-size: 1.3em; }
.pitch { font-size: 0.6em; opacity: 0.6; }
ruby rt { font-size: 0.55em; opacity: 0.8; }"""


def _word_only(card: dict) -> str:
    return re.sub(r" ?([^ >\[\]<]+?)\[[^\]]*\]", r"\1", card.get("front", "")).strip()


@dataclass(frozen=True)
class NoteType:
    model_id: int                 # fixed forever: changing it (or the fields) stops Anki updating imported notes
    name: str
    fields: tuple[str, ...]
    sources: tuple                # card dict key (or function) for each field
    front: str
    back: str
    css: str
    attribution: str

    def values(self, card: dict) -> list[str]:
        return [src(card) if callable(src) else card.get(src, "") for src in self.sources]

    @property
    def model(self) -> genanki.Model:
        return genanki.Model(self.model_id, self.name, fields=[{"name": f} for f in self.fields],
                             templates=[{"name": self.name.split("(")[1].rstrip(")"), "qfmt": self.front, "afmt": self.back}],
                             css=self.css)


NOTE_TYPES = {
    "ru": NoteType(1745120943, "LyricDeck (Russian → English)",
                   ("Russian", "SungForm", "Grammar", "English", "Extra", "Context", "ContextEnglish", "Source", "Kind"),
                   ("front", "sung", "grammar", "english", "extra", "context", "context_english", "source", "kind"),
                   RU_FRONT, RU_BACK, CSS, ATTRIBUTION),
    "ja": NoteType(1780454219, "LyricDeck (Japanese → English)",
                   ("Word", "Reading", "WordFurigana", "SungForm", "Grammar", "Pitch", "English", "Extra",
                    "Context", "ContextEnglish", "Source", "Kind"),
                   (_word_only, "reading", "front", "sung", "grammar", "pitch", "english", "extra",
                    "context", "context_english", "source", "kind"),
                   JA_FRONT, JA_BACK, JA_CSS, JA_ATTRIBUTION),
}


class LyricNote(genanki.Note):
    """A note whose ID comes from what the card is (its key), not from its (editable) content."""

    def __init__(self, key: str, **kwargs):
        self.key = key
        super().__init__(**kwargs)

    @property
    def guid(self):
        return genanki.guid_for(self.key)

    @guid.setter
    def guid(self, _value):
        pass


def deck_id(name: str) -> int:
    return int(hashlib.sha256(name.encode()).hexdigest()[:8], 16) | 1 << 31


def export(cards: list[dict], deck_name: str, lang: str = "ru") -> bytes:
    """cards: dicts with the card fields plus 'key' and 'tags'. Card order is kept as new-card order."""
    nt = NOTE_TYPES[lang]
    model = nt.model
    deck = genanki.Deck(deck_id(deck_name), deck_name, description=nt.attribution)
    for due, card in enumerate(cards):
        deck.add_note(LyricNote(card["key"], model=model, due=due, tags=[card["kind"], *card.get("tags", [])],
                                fields=nt.values(card)))
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "deck.apkg"
        genanki.Package(deck).write_to_file(path)
        return path.read_bytes()


ANKICONNECT_URL = "http://127.0.0.1:8765"


class AnkiConnectError(Exception):
    pass


def _anki(action: str, **params):
    req = urllib.request.Request(ANKICONNECT_URL, json.dumps({"action": action, "version": 6, "params": params}).encode(),
                                 {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            reply = json.load(r)
    except OSError as e:
        raise AnkiConnectError("Could not reach Anki. Open Anki with the AnkiConnect add-on (code 2055492159) installed.") from e
    if reply.get("error"):
        raise AnkiConnectError(f"AnkiConnect: {reply['error']}")
    return reply["result"]


def _search_value(text: str) -> str:
    """Escape a field value for an Anki search."""
    return "".join("\\" + c if c in '\\"*_:()' else c for c in text)


def send_to_anki(cards: list[dict], deck_name: str, lang: str = "ru") -> dict[str, int]:
    """Add or update notes in the running Anki. Notes are matched on the first field of the language's note type."""
    nt = NOTE_TYPES[lang]
    _anki("createDeck", deck=deck_name)
    if nt.name not in _anki("modelNames"):
        _anki("createModel", modelName=nt.name, inOrderFields=list(nt.fields), css=nt.css, isCloze=False,
              cardTemplates=[{"Name": nt.name.split("(")[1].rstrip(")"), "Front": nt.front, "Back": nt.back}])
    added = updated = 0
    for card in cards:
        fields = dict(zip(nt.fields, nt.values(card), strict=True))
        first = nt.fields[0]
        found = _anki("findNotes", query=f'"note:{nt.name}" "{first}:{_search_value(fields[first])}"')
        if found:
            _anki("updateNoteFields", note={"id": found[0], "fields": fields})
            updated += 1
        else:
            _anki("addNote", note={"deckName": deck_name, "modelName": nt.name, "fields": fields,
                                   "tags": [card["kind"], *card.get("tags", [])], "options": {"allowDuplicate": False}})
            added += 1
    return {"added": added, "updated": updated}
