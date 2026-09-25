"""Song library: list, add (paste or find online), edit, delete."""

from urllib.error import URLError

from flask import Blueprint, abort, flash, redirect, render_template, request

from . import lang, lyrics
from .db import get_db
from .lang import code, lurl

bp = Blueprint("songs", __name__)
bp.before_request(lang.require_installed)


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
    results, error = [], None
    if query:
        try:
            results = lyrics.search(query, code())
        except (URLError, TimeoutError, ValueError) as e:
            error = f"Could not reach LRCLIB ({e}). Check your connection, or paste the lyrics instead."
    return render_template("song_find.html", query=query, results=results, error=error)


@bp.post("/songs/import")
def import_song():
    try:
        song = lyrics.fetch(request.form.get("lrclib_id", type=int))
    except (URLError, TimeoutError, ValueError, KeyError, TypeError) as e:
        flash(f"Could not fetch the lyrics from LRCLIB ({e}).")
        return redirect(lurl("songs.find", q=request.form.get("q", "")))
    db = get_db()
    cur = db.execute("INSERT INTO songs (artist, title, lyrics, source, lrclib_id, synced_lyrics, lang) "
                     "VALUES (:artist, :title, :lyrics, 'lrclib', :lrclib_id, :synced_lyrics, :lang)", song | {"lang": code()})
    db.commit()
    flash("Imported from LRCLIB. Titles there are crowd-sourced, so check the artist and title below.")
    return redirect(lurl("songs.edit", song_id=cur.lastrowid))


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
