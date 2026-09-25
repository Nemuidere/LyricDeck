"""Settings page: deck name, translation, defaults for new decks, and usage."""

import json
import os
from datetime import datetime, timezone

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for

from . import claude, translate
from .db import get_db, get_setting, set_setting

bp = Blueprint("settings", __name__)

DEFAULTS = {
    "deck_name": "LyricDeck",
    "line_translator": "mymemory",    # mymemory | none
    "mymemory_email": "",
    "context_english": "1",           # show the English of the song lines on word cards
    "units": "word",
    "min_count": "2",
    "count_repeats": "1",
    "grammar": "1",
    "mymemory_blocked_until": "",
    "claude_backend": "off",          # off | api | code
    "claude_api_key": "",
    "claude_model_api": claude.API_MODELS_DEFAULT,
    "claude_model_code": claude.CODE_MODEL_DEFAULT,
    "claude_api_models": "[]",        # model IDs listed by the last successful key check
    "claude_words": "1",
    "claude_lines": "1",
}
CLAUDE_BACKENDS = {"off": "Off", "api": "Anthropic API key (pay per use)",
                   "code": "My local Claude Code login (Pro/Max plan, personal use)"}
TRANSLATORS = {"mymemory": "MyMemory (free, online)", "none": "None — I'll type translations"}


def setting(key: str) -> str:
    return get_setting(key, DEFAULTS[key])


def mymemory_status() -> dict:
    used = translate.usage(get_db(), "mymemory")
    limit = translate.LIMIT_EMAIL if setting("mymemory_email") else translate.LIMIT_ANONYMOUS
    songs = get_db().execute("SELECT lyrics FROM songs").fetchall()
    per_song = sum(len("".join(dict.fromkeys(s["lyrics"].splitlines()))) for s in songs) / len(songs) if songs else 0
    blocked = setting("mymemory_blocked_until")
    until = datetime.fromisoformat(blocked) if blocked else None
    if until and until <= datetime.now(timezone.utc):
        until = None
    left = 0 if until else max(0, limit - used["chars"])
    return {"used": used["chars"], "requests": used["requests"], "limit": limit, "left": left,
            "songs_left": int(left // per_song) if per_song else None, "blocked_until": until}


def claude_config() -> dict:
    """Active Claude setup: backend, model and what it translates."""
    backend = setting("claude_backend")
    return {"backend": backend, "model": setting("claude_model_code" if backend == "code" else "claude_model_api"),
            "words": backend != "off" and setting("claude_words") == "1",
            "lines": backend != "off" and setting("claude_lines") == "1",
            "api_key": setting("claude_api_key") or os.environ.get("ANTHROPIC_API_KEY", "")}


def claude_usage() -> dict:
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    row = get_db().execute("SELECT COUNT(*) n, SUM(input_tokens) i, SUM(output_tokens) o, SUM(cost_usd) c "
                           "FROM claude_usage WHERE day LIKE ?", (month + "%",)).fetchone()
    return {"requests": row["n"], "input": row["i"] or 0, "output": row["o"] or 0, "cost": row["c"] or 0.0}


@bp.route("/settings/claude", methods=["GET", "POST"])
def claude_page():
    if request.method == "POST":
        form = request.form
        set_setting("claude_backend", form.get("claude_backend") if form.get("claude_backend") in CLAUDE_BACKENDS else "off")
        if form.get("claude_api_key", "").strip():
            set_setting("claude_api_key", form["claude_api_key"].strip())
        if "forget_key" in form:
            set_setting("claude_api_key", "")
        for key in ("claude_model_api", "claude_model_code"):
            if form.get(key):
                set_setting(key, form[key])
        for key in ("claude_words", "claude_lines"):
            set_setting(key, "1" if key in form else "0")
        flash("Claude settings saved.")
        return redirect(url_for("settings.claude_page"))
    api_models = json.loads(setting("claude_api_models")) or list(claude.PRICES)
    return render_template("settings_claude.html", s={k: setting(k) for k in DEFAULTS}, backends=CLAUDE_BACKENDS,
                           api_models=api_models, code_models=claude.CODE_MODELS, prices=claude.PRICES,
                           has_env_key=bool(os.environ.get("ANTHROPIC_API_KEY")), usage=claude_usage())


@bp.post("/settings/claude/test")
def claude_test():
    """Check the connection without spending anything."""
    backend = request.form.get("backend", setting("claude_backend"))
    try:
        if backend == "api":
            models = claude.list_api_models(claude_config()["api_key"])
            set_setting("claude_api_models", json.dumps([m for m in models if m.startswith("claude")]))
            return jsonify(ok=True, message=f"The API key works. {len(models)} models available.")
        if backend == "code":
            return jsonify(ok=True, message=claude.code_status())
        return jsonify(ok=False, message="Claude is off.")
    except (claude.ClaudeError, OSError) as e:
        return jsonify(ok=False, message=str(e))


@bp.route("/settings", methods=["GET", "POST"])
def page():
    if request.method == "POST":
        form = request.form
        set_setting("deck_name", form.get("deck_name", "").strip() or DEFAULTS["deck_name"])
        set_setting("line_translator", form.get("line_translator") if form.get("line_translator") in TRANSLATORS else "mymemory")
        set_setting("mymemory_email", form.get("mymemory_email", "").strip())
        set_setting("units", ",".join(form.getlist("unit")) or "word")
        set_setting("min_count", str(max(1, form.get("min_count", 2, type=int))))
        for key in ("context_english", "count_repeats", "grammar"):
            set_setting(key, "1" if key in form else "0")
        flash("Settings saved.")
        return redirect(url_for("settings.page"))
    values = {key: setting(key) for key in DEFAULTS}
    return render_template("settings.html", s=values, translators=TRANSLATORS, mm=mymemory_status(), cc=claude_config(),
                           units={"word": "Words", "phrase": "Phrases", "line": "Lines"})
