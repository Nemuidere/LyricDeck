"""Song library: list, add (paste, find online or from a YouTube link), edit, delete."""

from urllib.error import HTTPError, URLError

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request

from . import lang, lyrics, youtube
from .db import get_db
from .lang import code, lurl

bp = Blueprint("songs", __name__)
bp.before_request(lang.require_installed)


LRCLIB_ERRORS = (lyrics.LrclibBusy, URLError, TimeoutError, ValueError, KeyError, TypeError)


def lrclib_error(e: Exception) -> str:
    if isinstance(e, lyrics.LrclibBusy):
        return f"{e}. Try again in a moment."
    if isinstance(e, HTTPError):
        return f"LRCLIB answered with an error (HTTP {e.code}). Try again later."
    if isinstance(e, (URLError, TimeoutError)):
        return f"Could not reach LRCLIB ({e}). Check your connection."
    return f"LRCLIB sent an unexpected answer ({e!r})."


def _get_song(song_id: int):
    song = get_db().execute("SELECT * FROM songs WHERE id = ? AND lang = ?", (song_id, code())).fetchone()
    if song is None:
        abort(404)
    return song


def _form_values() -> tuple[str, str, str] | None:
    artist = request.form.get("artist", "").strip()
    title = request.form.get("title", "").strip()
    lyrics = request.form.get("lyrics", "").strip()
    if not title or not lyrics:
        flash("Title and lyrics are required.")
        return None
    return artist, title, lyrics


@bp.get("/")
def index():
    songs = get_db().execute("SELECT * FROM songs WHERE lang = ? ORDER BY added_at DESC, id DESC", (code(),)).fetchall()
    return render_template("songs.html", songs=songs)


@bp.route("/songs/new", methods=["GET", "POST"])
def new():
    if request.method == "POST" and (values := _form_values()):
        db = get_db()
        db.execute("INSERT INTO songs (artist, title, lyrics, lang) VALUES (?, ?, ?, ?)", (*values, code()))
        db.commit()
        return redirect(lurl("songs.index"))
    return render_template("song_form.html", song=request.form)


@bp.get("/songs/find")
def find():
    query = request.args.get("q", "").strip()
    if youtube.parse_url(query):
        return redirect(lurl("songs.from_youtube", url=query))
    results, error = [], None
    if query:
        try:
            results = lyrics.search(query, code())
        except (lyrics.LrclibBusy, URLError, TimeoutError, ValueError) as e:
            error = lrclib_error(e) + " Or paste the lyrics instead."
    return render_template("song_find.html", query=query, results=results, error=error)


def _library() -> tuple[set[int], set[tuple[str, str]]]:
    """LRCLIB ids and (artist, title) pairs of this language's songs, to mark what is already imported."""
    rows = get_db().execute("SELECT artist, title, lrclib_id FROM songs WHERE lang = ?", (code(),)).fetchall()
    return ({r["lrclib_id"] for r in rows if r["lrclib_id"]},
            {(r["artist"].casefold(), r["title"].casefold()) for r in rows})


def _import(lrclib_id: int) -> int:
    song = lyrics.fetch(lrclib_id)
    db = get_db()
    cur = db.execute("INSERT INTO songs (artist, title, lyrics, source, lrclib_id, synced_lyrics, lang) "
                     "VALUES (:artist, :title, :lyrics, 'lrclib', :lrclib_id, :synced_lyrics, :lang)", song | {"lang": code()})
    db.commit()
    return cur.lastrowid


@bp.post("/songs/import")
def import_song():
    """Import one LRCLIB text: a form post from the search page, or JSON from the YouTube page."""
    if request.is_json:
        lrclib_id = request.get_json().get("lrclib_id")
        if not isinstance(lrclib_id, int):
            return jsonify(ok=False, error="No song picked.")
        if row := get_db().execute("SELECT id FROM songs WHERE lrclib_id = ? AND lang = ?",
                                   (lrclib_id, code())).fetchone():
            return jsonify(ok=True, song_id=row["id"], existing=True)
        try:
            return jsonify(ok=True, song_id=_import(lrclib_id), existing=False)
        except LRCLIB_ERRORS as e:
            return jsonify(ok=False, error=lrclib_error(e))
    try:
        song_id = _import(request.form.get("lrclib_id", type=int))
    except LRCLIB_ERRORS as e:
        flash(f"Could not fetch the lyrics from LRCLIB. {lrclib_error(e)}")
        return redirect(lurl("songs.find", q=request.form.get("q", "")))
    flash("Imported from LRCLIB. Titles there are crowd-sourced, so check the artist and title below.")
    return redirect(lurl("songs.edit", song_id=song_id))


@bp.get("/songs/youtube")
def from_youtube():
    """The songs of a YouTube video or playlist; the page then matches each one against LRCLIB."""
    url = request.args.get("url", "").strip()
    source, error = None, None
    kind = (youtube.parse_url(url) or (None,))[0]
    if kind == "playlist" and not youtube.available():
        error = "missing-extra"
    else:
        try:
            source = youtube.load(url)
        except youtube.YouTubeError as e:
            error = str(e)
    return render_template("song_youtube.html", url=url, kind=kind, source=source, error=error)


@bp.get("/songs/match")
def match():
    """LRCLIB candidates for one song from the YouTube page, as JSON."""
    artists = [a for a in request.args.getlist("artist") if a.strip()]
    title = request.args.get("title", "").strip()
    if not title:
        return jsonify(candidates=[], error="No title.")
    try:
        candidates = lyrics.match(artists, title, request.args.get("duration", type=int), code())
    except LRCLIB_ERRORS as e:
        return jsonify(candidates=[], error=lrclib_error(e))
    ids, pairs = _library()
    for c in candidates:
        c.pop("key", None)
        c["in_library"] = c["id"] in ids or (c["artist"].casefold(), c["title"].casefold()) in pairs
    return jsonify(candidates=candidates, error=None)


@bp.route("/songs/<int:song_id>/edit", methods=["GET", "POST"])
def edit(song_id: int):
    song = _get_song(song_id)
    if request.method == "POST":
        if values := _form_values():
            db = get_db()
            db.execute("UPDATE songs SET artist = ?, title = ?, lyrics = ? WHERE id = ?", (*values, song_id))
            db.commit()
            return redirect(lurl("songs.index"))
        song = request.form
    return render_template("song_form.html", song=song, song_id=song_id)


@bp.post("/songs/<int:song_id>/delete")
def delete(song_id: int):
    _get_song(song_id)
    db = get_db()
    db.execute("DELETE FROM songs WHERE id = ?", (song_id,))
    db.commit()
    return redirect(lurl("songs.index"))
