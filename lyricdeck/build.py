"""Build → review → export."""

import io
import re
from html import escape

from flask import Blueprint, flash, jsonify, redirect, render_template, request, send_file, url_for

from . import cards, deck
from .db import get_db, get_setting, set_setting
from .dictionary import Dictionary, is_ready

bp = Blueprint("build", __name__)

UNITS = {"word": "Words", "phrase": "Phrases", "line": "Lines"}


@bp.get("/build")
def build_form():
    songs = get_db().execute("SELECT * FROM songs ORDER BY added_at DESC, id DESC").fetchall()
    return render_template("build.html", songs=songs, units=UNITS, ready=is_ready(get_db()))


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
    candidates, total = cards.build(
        songs, Dictionary(db), known,
        units=set(request.form.getlist("unit")) or {"word"},
        min_count=max(1, request.form.get("min_count", 2, type=int)),
        count_repeats="count_repeats" in request.form,
        grammar="grammar" in request.form,
    )
    return render_template("review.html", cards=candidates, total_words=total, songs=songs,
                           deck_name=get_setting("deck_name", "LyricDeck"))


@bp.post("/export")
def export():
    form = request.form
    deck_name = form.get("deck_name", "").strip() or "LyricDeck"
    set_setting("deck_name", deck_name)
    fields = ["key", "kind", "russian", "sung", "grammar", "english", "extra", "context", "source", "tags"]
    selected = [{f: form.get(f"{f}-{i}", "") for f in fields} for i in form.getlist("sel")]
    if not selected:
        flash("No cards selected.")
        return redirect(url_for("build.build_form"))
    for card in selected:
        card["tags"] = card["tags"].split()
        card["english"] = escape(card["english"].strip())
    filename = re.sub(r"[^\w-]+", "_", deck_name).strip("_") + ".apkg"
    return send_file(io.BytesIO(deck.export(selected, deck_name)), as_attachment=True, download_name=filename,
                     mimetype="application/octet-stream")


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
