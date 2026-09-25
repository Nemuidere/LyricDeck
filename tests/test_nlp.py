"""Text-core tests. All lyrics here are made up."""

from lyricdeck.nlp import (
    analyze, clean_lyrics, count_lines, count_words, find_phrases, fold, normalize, tokenize,
)

SONG = """[Припев]
Я иду домой, и ветер поёт.
Ты не знаешь, что-то стало иначе. (x2)
Мы стали старше, но любовь живёт.
Любви не бывает без боли, без боли!
Chorus:
Я иду домой, и ветер поёт."""


def words(line: str) -> dict[str, str]:
    return {t.text.lower(): t.lemma for t in analyze([line])[0].tokens if t.is_word}


def test_clean_drops_sections_and_expands_repeats():
    lines = clean_lyrics(SONG)
    assert lines[0] == "Я иду домой, и ветер поёт."
    assert lines.count("Ты не знаешь, что-то стало иначе.") == 2
    assert lines.count("Я иду домой, и ветер поёт.") == 2
    assert not any("Припев" in l or "Chorus" in l for l in lines)


def test_clean_count_once():
    lines = clean_lyrics(SONG, count_repeats=False)
    assert lines.count("Я иду домой, и ветер поёт.") == 1
    assert lines.count("Ты не знаешь, что-то стало иначе.") == 1


def test_repeat_marker_variants():
    for marker in ["(x3)", "x3", "х3", "×3", "(3x)", "(3 раза)"]:
        assert clean_lyrics(f"Ветер поёт {marker}") == ["Ветер поёт"] * 3


def test_normalize_stress_and_latin_lookalikes():
    assert normalize("любо́вь ты' умиpай") == "любовь ты умирай"  # the p in умиpай is Latin
    assert normalize("baby, мой") == "baby, мой"
    assert normalize("lооkbооk") == "lookbook"  # Cyrillic о inside a Latin word


def test_tokenize_hyphens():
    assert ("что-то", 0) in tokenize("что-то стало")
    assert [w for w, _ in tokenize("ла-ла-ла")] == ["ла", "-", "ла", "-", "ла"]


def test_lemmas_use_context():
    assert words("Мы стали старше")["стали"] == "стать"
    assert words("Нож из стали")["стали"] == "сталь"
    assert words("Все люди знают")["все"] == "весь"
    assert words("Любовь живёт")["живёт"] == "жить"
    assert words("Любви не бывает")["любви"] == "любовь"  # line-initial capital is not a name
    assert words("Она поёт о любви")["поёт"] == "петь"


def test_frequency_breaks_ties():
    assert words("Я пою")["пою"] == "поить"  # a true tie in pymorphy3
    freq = {"петь": 100.0, "поить": 5.0}.get
    token = next(t for t in analyze(["Я пою"], freq=lambda l: freq(l, 0))[0].tokens if t.text == "пою")
    assert token.lemma == "петь" and "поить" in token.alternatives


def test_small_words():
    tokens = {t.text.lower(): t for t in analyze(["И мой друг без тебя не поёт"])[0].tokens}
    assert all(tokens[w].small for w in ["и", "мой", "без", "тебя", "не"])
    assert not tokens["друг"].small and not tokens["поёт"].small


def test_unknown_and_foreign_words():
    tokens = {t.text: t for t in analyze(["Любооовь, baby"])[0].tokens}
    assert not tokens["Любооовь"].known
    assert not tokens["baby"].is_word


def test_count_words_groups_forms():
    songs = [analyze(clean_lyrics(SONG))]
    counts = count_words(songs)
    assert counts[("любовь", "NOUN")].count == 2  # любовь + любви
    assert counts[("идти", "VERB")].count == 2
    assert {o.text for o in counts[("любовь", "NOUN")].occurrences} == {"любовь", "Любви"}


def test_count_lines():
    counts = count_lines([analyze(clean_lyrics(SONG))])
    assert counts[(fold("Я иду домой, и ветер поёт."),)].count == 2


def test_phrases_are_maximal_and_trimmed():
    phrases = find_phrases([analyze(clean_lyrics(SONG))])
    assert ("иду", "домой") in phrases
    assert ("ветер", "поет") in phrases
    assert ("без", "боли") in phrases  # a preposition may start a phrase
    assert not any(p[0] in {"и", "я"} for p in phrases)  # other small words may not
    assert ("знаешь", "что-то") not in phrases  # small word at the end


def test_phrases_across_songs():
    a = analyze(["Иду домой"])
    b = analyze(["Я иду домой"])
    assert find_phrases([a, b])[("иду", "домой")].count == 2


def test_grammar_notes():
    from lyricdeck.cards import describe
    assert describe("NOUN,inan,femn sing,gent") == "genitive singular"
    assert describe("VERB,perf,intr femn,sing,past,indc") == "past, feminine"
    assert describe("VERB,impf,intr plur,past,indc") == "past, plural"
    assert describe("VERB,impf,tran sing,1per,pres,indc") == "1st person singular, present"
    assert describe("VERB,perf,tran sing,impr,excl") == "imperative singular"
    assert describe("INFN,impf,intr") == "infinitive"
