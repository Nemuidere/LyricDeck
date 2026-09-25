"""Translation of phrases and lines, with a cache and usage tracking.

MyMemory (https://mymemory.translated.net) is free without a key: 5,000 characters a day, or
50,000 when an email address is sent with each request.
"""

import html
import json
import re
import sqlite3
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from .dictionary import USER_AGENT

MYMEMORY_URL = "https://api.mymemory.translated.net/get"
MYMEMORY_MAX_BYTES = 500
LIMIT_ANONYMOUS, LIMIT_EMAIL = 5_000, 50_000
REQUEST_GAP = 0.3  # seconds between requests, to stay polite
QUOTA_RE = re.compile(r"NEXT AVAILABLE IN\s+(\d+)\s+HOURS?\s+(\d+)\s+MINUTES?\s+(\d+)\s+SECONDS?", re.I)


class QuotaExceeded(Exception):
    def __init__(self, until: datetime | None):
        self.until = until
        super().__init__("MyMemory's free daily quota is used up" +
                         (f" until {until:%H:%M} UTC" if until else ""))


def today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def cached(conn: sqlite3.Connection, provider: str, texts: list[str]) -> dict[str, str]:
    found = {}
    for text in dict.fromkeys(texts):
        if row := conn.execute("SELECT english FROM translations WHERE provider = ? AND text = ?",
                               (provider, text)).fetchone():
            found[text] = row["english"]
    return found


def store(conn: sqlite3.Connection, provider: str, results: dict[str, str]) -> None:
    conn.executemany("INSERT OR REPLACE INTO translations VALUES (?, ?, ?)",
                     [(provider, t, e) for t, e in results.items()])
    conn.commit()


def _store(conn: sqlite3.Connection, provider: str, results: dict[str, str], chars: int, requests: int) -> None:
    store(conn, provider, results)
    conn.execute("INSERT INTO usage (provider, day, chars, requests) VALUES (?, ?, ?, ?) "
                 "ON CONFLICT (provider, day) DO UPDATE SET chars = chars + excluded.chars, "
                 "requests = requests + excluded.requests", (provider, today(), chars, requests))
    conn.commit()


def _batches(texts: list[str]) -> list[list[str]]:
    """Group texts into newline-joined requests under MyMemory's size limit."""
    batches, current = [], []
    for text in texts:
        if current and len("\n".join(current + [text]).encode()) > MYMEMORY_MAX_BYTES:
            batches.append(current)
            current = []
        current.append(text)
    return batches + [current] if current else batches


def _mymemory_request(text: str, email: str) -> str:
    params = {"q": text, "langpair": "ru|en"} | ({"de": email} if email else {})
    req = urllib.request.Request(f"{MYMEMORY_URL}?{urllib.parse.urlencode(params)}", headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.load(r)
    translated = html.unescape(data["responseData"]["translatedText"] or "")
    if data.get("quotaFinished") or data.get("responseStatus") in (429, "429") or "MYMEMORY WARNING" in translated:
        m = QUOTA_RE.search(translated)
        until = datetime.now(timezone.utc) + timedelta(hours=int(m[1]), minutes=int(m[2]), seconds=int(m[3])) if m else None
        raise QuotaExceeded(until)
    if str(data.get("responseStatus")) != "200":
        raise RuntimeError(data.get("responseDetails") or f"MyMemory error {data.get('responseStatus')}")
    return translated.strip()


def mymemory(conn: sqlite3.Connection, texts: list[str], email: str = "") -> dict[str, str]:
    """Translate texts (cached ones are free). Raises QuotaExceeded after saving what was done."""
    results = cached(conn, "mymemory", texts)
    todo = [t for t in dict.fromkeys(texts) if t not in results and t.strip()]
    done, chars, requests = {}, 0, 0
    try:
        for batch in _batches(todo):
            joined = "\n".join(batch)
            lines = _mymemory_request(joined, email).split("\n")
            chars, requests = chars + len(joined), requests + 1
            if len(lines) != len(batch):  # line breaks were not preserved: one request per text
                lines = []
                for text in batch:
                    time.sleep(REQUEST_GAP)
                    lines.append(_mymemory_request(text, email))
                    chars, requests = chars + len(text), requests + 1
            done |= dict(zip(batch, lines, strict=True))
            time.sleep(REQUEST_GAP)
    finally:
        _store(conn, "mymemory", done, chars, requests)
    return results | done


def usage(conn: sqlite3.Connection, provider: str) -> dict:
    row = conn.execute("SELECT chars, requests FROM usage WHERE provider = ? AND day = ?", (provider, today())).fetchone()
    return {"chars": row["chars"] if row else 0, "requests": row["requests"] if row else 0}
