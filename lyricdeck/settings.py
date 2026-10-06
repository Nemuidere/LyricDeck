"""Settings page: deck name, translation, defaults for new decks, and usage."""

import json
import os
from datetime import datetime, timezone

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for

from . import claude, japanese, translate
from .db import get_db, get_setting, set_setting

bp = Blueprint("settings", __name__)

DEFAULTS = {
    "deck_name": "LyricDeck",
    "deck_name_ja": "LyricDeck Japanese",
    "translator_word": "dictionary",  # dictionary | claude
    "translator_phrase": "mymemory",  # mymemory | claude | none
    "translator_line": "mymemory",    # mymemory | claude | none; also the song lines on word cards
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
    "claude_api_models": "[]",        # models listed by the last successful key check
}
CLAUDE_BACKENDS = {"off": "Off", "api": "Anthropic API key (pay per use)",
                   "code": "My local Claude Code login (Pro/Max plan, personal use)"}
KINDS = {"word": "Words", "phrase": "Phrases", "line": "Lines"}
ONLINE = {"mymemory": "MyMemory (free, online)", "claude": "Claude", "none": "None — I'll type them"}
TRANSLATORS = {"word": {"dictionary": "Offline dictionary", "claude": "Claude (the meaning used in the song)"},
               "phrase": ONLINE, "line": ONLINE}
OLD_KEYS = ("line_translator", "claude_words", "claude_lines")


def setting(key: str) -> str:
    return get_setting(key, DEFAULTS[key])


def translators() -> dict[str, str]:
    """Who translates each card kind. Claude falls back to the free translators while it is off."""
    claude_on = setting("claude_backend") != "off"
    chosen = {}
    for kind, choices in TRANSLATORS.items():
        value = setting(f"translator_{kind}")
        value = value if value in choices else DEFAULTS[f"translator_{kind}"]
        if value == "claude" and not claude_on:
            value = DEFAULTS[f"translator_{kind}"]
        chosen[kind] = value
    return chosen


def migrate(conn) -> None:
    """Bring settings saved by older versions up to date: per-kind translators and Claude Code model aliases."""
    def get(key):
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def put(key, value):
        conn.execute("INSERT OR REPLACE INTO settings VALUES (?, ?)", (key, value))

    if get("translator_word") is None and any(get(k) is not None for k in OLD_KEYS):
        claude_on = (get("claude_backend") or "off") != "off"
        put("translator_word", "claude" if claude_on and (get("claude_words") or "1") == "1" else "dictionary")
        lines = "claude" if claude_on and (get("claude_lines") or "1") == "1" else get("line_translator") or "mymemory"
        put("translator_phrase", lines)
        put("translator_line", lines)
    conn.executemany("DELETE FROM settings WHERE key = ?", [(k,) for k in OLD_KEYS])
    if (model := get("claude_model_code")) and claude.code_alias(model) != model:
        put("claude_model_code", claude.code_alias(model))
    conn.commit()


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
    """Active Claude setup: backend and model."""
    backend = setting("claude_backend")
    return {"backend": backend, "model": setting("claude_model_code" if backend == "code" else "claude_model_api"),
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
        flash("Claude settings saved.")
        return redirect(url_for("settings.claude_page"))
    api_models = [m if isinstance(m, dict) else {"id": m, "line": None, "current": True}  # older versions saved IDs
                  for m in json.loads(setting("claude_api_models"))] or claude.API_MODELS_FALLBACK
    if setting("claude_model_api") not in {m["id"] for m in api_models}:
        api_models = [{"id": setting("claude_model_api"), "line": None, "current": True, "saved": True}, *api_models]
    code_models = dict(claude.CODE_MODELS)
    if setting("claude_model_code") not in code_models:
        code_models = {setting("claude_model_code"): f'{setting("claude_model_code")} (saved)', **code_models}
    return render_template("settings_claude.html", s={k: setting(k) for k in DEFAULTS}, backends=CLAUDE_BACKENDS,
                           api_models=api_models, code_models=code_models, prices=claude.PRICES,
                           has_env_key=bool(os.environ.get("ANTHROPIC_API_KEY")), usage=claude_usage())


@bp.post("/settings/claude/test")
def claude_test():
    """Check the connection without spending anything."""
    backend = request.form.get("backend", setting("claude_backend"))
    try:
        if backend == "api":
            models = claude.list_api_models(claude_config()["api_key"])
            set_setting("claude_api_models", json.dumps(models))
            return jsonify(ok=True, message=f"The API key works. {len(models)} models available; reload to see them.")
        if backend == "code":
            return jsonify(ok=True, message=claude.code_status())
        return jsonify(ok=False, message="Claude is off.")
    except (claude.ClaudeError, OSError) as e:
        return jsonify(ok=False, message=str(e))


@bp.route("/settings", methods=["GET", "POST"])
def page():
    if request.method == "POST":
        form = request.form
        for key in ("deck_name", "deck_name_ja"):
            if key in form:
                set_setting(key, form.get(key, "").strip() or DEFAULTS[key])
        for kind, choices in TRANSLATORS.items():
            value = form.get(f"translator_{kind}")
            set_setting(f"translator_{kind}", value if value in choices else DEFAULTS[f"translator_{kind}"])
        set_setting("mymemory_email", form.get("mymemory_email", "").strip())
        set_setting("units", ",".join(form.getlist("unit")) or "word")
        set_setting("min_count", str(max(1, form.get("min_count", 2, type=int))))
        for key in ("context_english", "count_repeats", "grammar"):
            set_setting(key, "1" if key in form else "0")
        flash("Settings saved.")
        return redirect(url_for("settings.page"))
    values = {key: setting(key) for key in DEFAULTS}
    return render_template("settings.html", s=values, translators=TRANSLATORS, kinds=KINDS, active=translators(),
                           mm=mymemory_status(), cc=claude_config(), japanese=japanese.available(),
                           units={"word": "Words", "phrase": "Phrases", "line": "Lines"})
