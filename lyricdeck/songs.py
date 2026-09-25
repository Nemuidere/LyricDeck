"""Song library: list, add (paste), edit, delete."""

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from .db import get_db

bp = Blueprint("songs", __name__)


def _get_song(song_id: int):
    song = get_db().execute("SELECT * FROM songs WHERE id = ?", (song_id,)).fetchone()
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
    songs = get_db().execute("SELECT * FROM songs ORDER BY added_at DESC, id DESC").fetchall()
    return render_template("songs.html", songs=songs)


@bp.route("/songs/new", methods=["GET", "POST"])
def new():
    if request.method == "POST" and (values := _form_values()):
        db = get_db()
        db.execute("INSERT INTO songs (artist, title, lyrics) VALUES (?, ?, ?)", values)
        db.commit()
        return redirect(url_for("songs.index"))
    return render_template("song_form.html", song=request.form)


@bp.route("/songs/<int:song_id>/edit", methods=["GET", "POST"])
def edit(song_id: int):
    song = _get_song(song_id)
    if request.method == "POST":
        if values := _form_values():
            db = get_db()
            db.execute("UPDATE songs SET artist = ?, title = ?, lyrics = ? WHERE id = ?", (*values, song_id))
            db.commit()
            return redirect(url_for("songs.index"))
        song = request.form
    return render_template("song_form.html", song=song, song_id=song_id)


@bp.post("/songs/<int:song_id>/delete")
def delete(song_id: int):
    _get_song(song_id)
    db = get_db()
    db.execute("DELETE FROM songs WHERE id = ?", (song_id,))
    db.commit()
    return redirect(url_for("songs.index"))
