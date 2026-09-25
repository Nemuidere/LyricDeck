"""SQLite access. The schema is created on startup; tables are added with CREATE TABLE IF NOT EXISTS."""

import sqlite3
from pathlib import Path

from flask import Flask, current_app, g

SCHEMA = """
CREATE TABLE IF NOT EXISTS songs (
    id INTEGER PRIMARY KEY,
    artist TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL,
    lyrics TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'paste',
    lrclib_id INTEGER,
    synced_lyrics TEXT,
    added_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS known (key TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def get_setting(key: str, default: str = "") -> str:
    row = get_db().execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    get_db().execute("INSERT OR REPLACE INTO settings VALUES (?, ?)", (key, value))
    get_db().commit()


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = connect(current_app.config["DATABASE"])
    return g.db


def close_db(_exc=None) -> None:
    if (conn := g.pop("db", None)) is not None:
        conn.close()


def init_app(app: Flask) -> None:
    Path(app.config["DATABASE"]).parent.mkdir(parents=True, exist_ok=True)
    with connect(app.config["DATABASE"]) as conn:
        conn.executescript(SCHEMA)
    app.teardown_appcontext(close_db)
