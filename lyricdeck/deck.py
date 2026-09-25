"""Anki export (.apkg) with a fixed note type and stable note IDs, so re-imports update cards."""

import hashlib
import tempfile
from pathlib import Path

import genanki

# Fixed forever: changing the note type's ID or fields stops Anki from updating already imported notes.
MODEL_ID = 1745120943
FIELDS = ["Russian", "SungForm", "Grammar", "English", "Extra", "Context", "ContextEnglish", "Source", "Kind"]
ATTRIBUTION = ("Dictionary data: OpenRussian.org (CC BY-SA 4.0). "
               "Frequency data: Lyashevskaya & Sharoff, Russian National Corpus frequency dictionary (2009).")

FRONT = """<div class="ru">{{Russian}}</div>
{{#SungForm}}<div class="sung">{{SungForm}}</div>{{/SungForm}}"""
BACK = """{{FrontSide}}
<hr id="answer">
<div class="en">{{English}}</div>
{{#Grammar}}<div class="note">{{SungForm}}: {{Grammar}}</div>{{/Grammar}}
{{#Extra}}<div class="note">{{Extra}}</div>{{/Extra}}
{{#Context}}<div class="ctx">{{Context}}</div>{{/Context}}
{{#ContextEnglish}}<div class="ctx-en">{{ContextEnglish}}</div>{{/ContextEnglish}}
<div class="src">{{Source}}</div>"""
CSS = """.card { font-family: system-ui, sans-serif; font-size: 20px; text-align: center; color: #0b1a33; background: #f5f8fd; }
.nightMode.card, .night_mode .card { color: #eef3fb; background: #0b1a33; }
.ru { font-size: 1.6em; font-weight: 600; }
.sung { font-size: 0.85em; opacity: 0.7; margin-top: 0.2em; }
.en { font-size: 1.15em; margin: 0.4em 0; }
.note { font-size: 0.75em; opacity: 0.7; }
.ctx { margin-top: 1em; font-size: 0.9em; line-height: 1.5; }
.ctx-en { font-size: 0.8em; opacity: 0.7; font-style: italic; }
.src { margin-top: 1em; font-size: 0.65em; opacity: 0.5; }"""

MODEL = genanki.Model(
    MODEL_ID, "LyricDeck (Russian → English)",
    fields=[{"name": f} for f in FIELDS],
    templates=[{"name": "Russian → English", "qfmt": FRONT, "afmt": BACK}],
    css=CSS,
)


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


def export(cards: list[dict], deck_name: str) -> bytes:
    """cards: dicts with the lowercase FIELDS names plus 'key' and 'tags'. Card order is kept as new-card order."""
    deck = genanki.Deck(deck_id(deck_name), deck_name, description=ATTRIBUTION)
    for due, card in enumerate(cards):
        note = LyricNote(card["key"], model=MODEL, due=due, tags=[card["kind"], *card.get("tags", [])], fields=[
            card.get(name, "") for name in ("russian", "sung", "grammar", "english", "extra",
                                            "context", "context_english", "source", "kind")
        ])
        deck.add_note(note)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "deck.apkg"
        genanki.Package(deck).write_to_file(path)
        return path.read_bytes()
