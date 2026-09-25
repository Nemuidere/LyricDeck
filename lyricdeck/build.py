"""Build → review → export."""

import io
import re
from html import escape

from urllib.error import URLError

from flask import Blueprint, flash, jsonify, redirect, render_template, request, send_file, url_for

from . import cards, claude, deck, translate
from .db import get_db, set_setting
from .dictionary import Dictionary, is_ready
from .nlp import clean_lyrics
from .settings import claude_config, mymemory_status, setting

bp = Blueprint("build", __name__)

UNITS = {"word": "Words", "phrase": "Phrases", "line": "Lines"}


@bp.get("/build")
def build_form():
    songs = get_db().execute("SELECT * FROM songs ORDER BY added_at DESC, id DESC").fetchall()
    defaults = {"units": setting("units").split(","), "min_count": setting("min_count"),
                "count_repeats": setting("count_repeats") == "1", "grammar": setting("grammar") == "1"}
    return render_template("build.html", songs=songs, units=UNITS, ready=is_ready(get_db()), d=defaults)


@bp.post("/review")
def review():
    ids = [int(i) for i in request.form.getlist("song")]
    if not ids:
        flash("Pick at least one song.")
        return redirect(url_for("build.build_form"))
    db = get_db()
    by_id = {s["id"]: s for s in db.execute(
        f"SELECT * FROM songs WHERE id IN ({','.join('?' * len(ids))})", ids)}
    songs = [by_id[i] for i in ids if i in by_id]
    known = {r["key"] for r in db.execute("SELECT key FROM known")}
    candidates = cards.build(
        songs, Dictionary(db), known,
        units=set(request.form.getlist("unit")) or {"word"},
        min_count=max(1, request.form.get("min_count", 2, type=int)),
        count_repeats="count_repeats" in request.form,
        grammar="grammar" in request.form,
    )
    cc = claude_config()
    provider = "claude" if cc["lines"] else setting("line_translator")
    with_context = setting("context_english") == "1"
    if provider == "mymemory":
        texts = [c["plain"] for c in candidates] + [l for c in candidates for l in c["context_plain"]]
        found = translate.cached(db, provider, texts)
        for c in candidates:
            c["english"] = c["english"] or found.get(c["plain"], "")
            if with_context and c["context_plain"] and all(l in found for l in c["context_plain"]):
                c["context_english"] = "\n".join(found[l] for l in c["context_plain"])
    return render_template("review.html", cards=candidates, songs=songs, deck_name=setting("deck_name"),
                           translator=provider, with_context=with_context,
                           claude={k: cc[k] for k in ("backend", "model", "words", "lines")})


@bp.post("/export")
def export():
    form = request.form
    deck_name = form.get("deck_name", "").strip() or "LyricDeck"
    set_setting("deck_name", deck_name)
    fields = ["key", "kind", "russian", "sung", "grammar", "english", "extra", "context", "context_english",
              "source", "tags"]
    selected = [{f: form.get(f"{f}-{i}", "") for f in fields} for i in form.getlist("sel")]
    if not selected and form.get("target") == "anki":
        return jsonify(message="No cards selected.")
    if not selected:
        flash("No cards selected.")
        return redirect(url_for("build.build_form"))
    for card in selected:
        card["tags"] = card["tags"].split()
        card["english"] = escape(card["english"].strip())
        card["context_english"] = "<br>".join(escape(l) for l in card["context_english"].split("\n") if l.strip())
    if form.get("target") == "anki":
        try:
            done = deck.send_to_anki(selected, deck_name)
        except deck.AnkiConnectError as e:
            return jsonify(message=str(e))
        return jsonify(message=f'Sent to Anki: {done["added"]} new and {done["updated"]} updated cards in "{deck_name}".')
    filename = re.sub(r"[^\w-]+", "_", deck_name).strip("_") + ".apkg"
    return send_file(io.BytesIO(deck.export(selected, deck_name)), as_attachment=True, download_name=filename,
                     mimetype="application/octet-stream")


@bp.post("/translate")
def translate_texts():
    """Translate texts for the review page. Returns what was translated, plus an error if it stopped early."""
    texts = [t for t in request.get_json().get("texts", []) if isinstance(t, str)][:200]
    if setting("line_translator") != "mymemory" or not texts:
        return jsonify(translations={}, error=None)
    status = mymemory_status()
    if status["blocked_until"]:
        return jsonify(translations=translate.cached(get_db(), "mymemory", texts),
                       error=f"MyMemory's free quota is used up until {status['blocked_until']:%H:%M} UTC.")
    try:
        return jsonify(translations=translate.mymemory(get_db(), texts, setting("mymemory_email")), error=None)
    except translate.QuotaExceeded as e:
        if e.until:
            set_setting("mymemory_blocked_until", e.until.isoformat())
        return jsonify(translations=translate.cached(get_db(), "mymemory", texts), error=str(e) + ".")
    except (URLError, TimeoutError, RuntimeError, ValueError, KeyError) as e:
        return jsonify(translations=translate.cached(get_db(), "mymemory", texts),
                       error=f"Translation failed: {e}. Check your internet connection and try again.")


@bp.post("/translate/claude")
def translate_claude():
    """Translate review items with Claude, using the whole songs as context. Cached per model."""
    data = request.get_json()
    cc = claude_config()
    if cc["backend"] == "off":
        return jsonify(translations={}, error="Claude is off. Turn it on in Settings → Claude.")
    items = [i for i in data.get("items", []) if isinstance(i, dict) and isinstance(i.get("id"), int)][:400]
    provider = f"claude:{cc['model']}"
    key = lambda i: f"{i.get('kind')}|{i.get('text')}|{i.get('line', '')}"
    db = get_db()
    found = translate.cached(db, provider, [key(i) for i in items])
    result = {i["id"]: found[key(i)] for i in items if key(i) in found}
    todo = [i for i in items if key(i) not in found]
    if not todo:
        return jsonify(translations=result, error=None, cost=0)
    ids = [int(s) for s in data.get("songs", [])][:20]
    songs = db.execute(f"SELECT lyrics FROM songs WHERE id IN ({','.join('?' * len(ids))})", ids).fetchall() if ids else []
    lyrics = ["\n".join(clean_lyrics(s["lyrics"], count_repeats=False)) for s in songs]
    try:
        if cc["backend"] == "code":
            done, usage = claude.translate_code(todo, lyrics, cc["model"])
        else:
            done, usage = claude.translate_api(todo, lyrics, cc["model"], cc["api_key"])
    except claude.ClaudeError as e:
        return jsonify(translations=result, error=str(e))
    by_id = {i["id"]: i for i in todo}
    translate.store(db, provider, {key(by_id[i]): english for i, english in done.items()})
    db.execute("INSERT INTO claude_usage VALUES (?, ?, ?, ?, ?, ?)", (translate.today(), cc["backend"], cc["model"],
               usage["input_tokens"], usage["output_tokens"], usage["cost_usd"]))
    db.commit()
    missing = len(todo) - len(done)
    return jsonify(translations=result | done, cost=usage["cost_usd"],
                   error=f"Claude skipped {missing} item(s); type those yourself." if missing else None)


@bp.post("/known")
def known():
    data = request.get_json()
    db = get_db()
    if data.get("known"):
        db.execute("INSERT OR IGNORE INTO known VALUES (?)", (data["key"],))
    else:
        db.execute("DELETE FROM known WHERE key = ?", (data["key"],))
    db.commit()
    return jsonify(ok=True)
