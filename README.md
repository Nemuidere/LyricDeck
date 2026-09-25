# LyricDeck

Turn Russian song lyrics into Anki flashcards (Russian → English). It runs locally in your browser and is free by default.

## What it does

- **Get lyrics.** Search [LRCLIB](https://lrclib.net) (free, no account) or paste them.
- **Find cards.** You get words, repeated phrases and whole lines. Word forms are grouped (любви → любовь) using word types taken from the surrounding words.
- **Review before exporting.**
  - A slider picks the words you need to understand X% of the song.
  - Small words (и, в, не, мой…) and very common Russian words can be hidden.
  - Words you mark as known are remembered.
  - Where a word could be read two ways, you pick the right one.
- **Rich cards.**
  - Front: the stressed dictionary form (любо́вь) with the form as sung.
  - Back: the English, a grammar note ("genitive singular"), the verb's aspect pair, up to three song lines with the word in bold, and optionally their English.
- **Export.** Download an `.apkg` file, or send the cards straight to a running Anki with AnkiConnect. Re-exporting updates existing cards instead of duplicating them.
- **Translation.**
  - Single words come from an offline dictionary.
  - Phrases and lines use MyMemory (free).
  - Claude is optional, through an API key or your own Claude Code login.

## Setup

Needs Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/Nemuidere/LyricDeck.git
cd LyricDeck
uv sync
uv run flask --app lyricdeck init-data   # one-time: downloads the dictionary (~25 MB)
uv run flask --app lyricdeck run
```

Open http://127.0.0.1:5000.

### Free translation limits

MyMemory allows 5,000 characters a day, or 50,000 if you add your email under **Settings**. The usage box on that page shows how much is left. Translations are cached, so nothing is translated twice.

### Claude (optional)

Set it up under **Settings → Claude**. Nothing is sent to Claude until you press "Translate with Claude" on the review page.

- **API key:** run `uv sync --extra claude` and paste a key from [platform.claude.com](https://platform.claude.com/settings/keys). Usage is billed per token, roughly 3–15 cents per song.
- **Your Claude Code login:** for personal use with a Pro or Max plan. Install [Claude Code](https://code.claude.com), run `claude` once and log in. LyricDeck then runs your local `claude -p` command, which Anthropic covers with the plan's monthly Agent SDK credit. LyricDeck never reads your Claude credentials.

### Send to Anki (optional)

Install the [AnkiConnect](https://ankiweb.net/shared/info/2055492159) add-on and keep Anki open, then use **Send to Anki** on the review page. Exporting an `.apkg` file works without it.

## Limitations

- **Dictionary forms are guessed.** The guess is usually right; the review screen is where you catch the misses.
- **The dictionary data is from 2021.** It covered about 96% of words in a 15-song test. Slang and names may need Claude or a manual translation.
- **The free services are best-effort.** MyMemory and LRCLIB can be slow or rate-limited.
- **Lyrics are copyrighted.** They are stored only in your local `data/` folder, which is not committed. Keep the decks you make for personal use.

## Credits

- **Dictionary:** [OpenRussian.org](https://en.openrussian.org) data (CC BY-SA 4.0).
- **Word frequencies:** Lyashevskaya & Sharoff, *Russian National Corpus frequency dictionary* (2009).
- **Word forms:** [pymorphy3](https://github.com/no-plagiarism/pymorphy3) and [spaCy](https://spacy.io) `ru_core_news_sm`.
- **Other services and libraries:** lyrics from [LRCLIB](https://lrclib.net), translation by [MyMemory](https://mymemory.translated.net), Anki packages by [genanki](https://github.com/kerrickstaley/genanki).

## License

GPL-3.0-or-later. See [LICENSE](LICENSE).
