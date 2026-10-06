"""Fetching lyrics from LRCLIB (https://lrclib.net): free, no key, crowd-sourced."""

import json
import re
import time
from difflib import SequenceMatcher
import urllib.parse
import urllib.request
from urllib.error import HTTPError, URLError

from .dictionary import USER_AGENT

LRCLIB_URL = "https://lrclib.net/api"
MAX_RESULTS = 10
ALT_VERSION = re.compile(r"\b(inst|instrumental|off vocal|karaoke|live|mv|the first take|remix)\b", re.I)
TITLE_JUNK = re.compile(r"\s*[(\[](official[^)\]]*|lyrics?|lyric video|audio|video|клип|премьера[^)\]]*|"
                        r"текст[^)\]]*|remaster[^)\]]*|(?:4k|hd|hq)\b[^)\]]*)[)\]]", re.I)

RETRY_FOR = 10        # seconds: LRCLIB often answers 503 "busy" for a moment, so keep trying this long
ATTEMPT_TIMEOUT = 5
RETRY_CODES = {429, 502, 503, 504}


class LrclibBusy(Exception):
    def __init__(self, status: int | None):
        self.status = status
        super().__init__(f"LRCLIB is busy right now (tried for {RETRY_FOR} s)")


def _retry_wait(e: Exception) -> float | None:
    """Seconds to wait before trying again, or None when retrying won't help (e.g. no connection)."""
    if isinstance(e, HTTPError):
        if e.code not in RETRY_CODES:
            return None
        try:
            return min(2.0, max(0.0, float(e.headers.get("Retry-After") or 1)))
        except ValueError:
            return 1.0
    if isinstance(e, TimeoutError) or isinstance(getattr(e, "reason", None), TimeoutError):
        return 0.0
    return None


def _get(path: str, **params) -> object:
    url = f"{LRCLIB_URL}/{path}" + (f"?{urllib.parse.urlencode(params)}" if params else "")
    deadline = time.monotonic() + RETRY_FOR
    while True:
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": USER_AGENT}),
                                        timeout=ATTEMPT_TIMEOUT) as r:
                return json.load(r)
        except (URLError, TimeoutError) as e:
            wait = _retry_wait(e)
            if wait is None:
                raise
            if time.monotonic() + wait >= deadline:
                raise LrclibBusy(getattr(e, "code", None)) from e
            time.sleep(wait)


def clean_title(title: str, artist: str = "") -> str:
    """Drop '(Official Video)'-style junk, a leading track number and a repeated 'Artist - ' prefix."""
    title = re.sub(r"^\d{1,3}(?:[.)]|\s+-)\s+", "", TITLE_JUNK.sub("", title or "").strip())
    if artist and re.match(rf"{re.escape(artist)}\s*[-–—]\s*", title, re.I):
        title = re.sub(rf"^{re.escape(artist)}\s*[-–—]\s*", "", title, flags=re.I)
    return title


def russian_share(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    return sum("а" <= c.lower() <= "я" or c.lower() == "ё" for c in letters) / len(letters) if letters else 0.0


def japanese_share(text: str) -> float:
    """Share of letters that are kana or kanji; 0 when there is too little kana to be Japanese (Chinese)."""
    letters = [c for c in text if c.isalpha()]
    kana = sum("぀" <= c <= "ヿ" for c in letters)
    japanese = kana + sum("一" <= c <= "鿿" or c == "々" for c in letters)
    return japanese / len(letters) if letters and kana >= 0.1 * japanese else 0.0


def script_check(text: str, lang: str) -> dict:
    """Does the text look like lyrics in the language? LRCLIB also holds translations and romaji."""
    if lang == "ja":
        share = japanese_share(text)
        if share >= 0.8:
            return {"fit": True, "note": "", "importable": True}
        if share >= 0.2:
            return {"fit": False, "note": "mixed / translation", "importable": True}
        return {"fit": False, "note": "romaji or not Japanese — can't be analysed", "importable": False}
    fit = russian_share(text) >= 0.5
    return {"fit": fit, "note": "" if fit else "not in Russian", "importable": True}


def _candidate(r: dict, lang: str) -> dict | None:
    """An LRCLIB record as a search result, or None when it has no plain lyrics."""
    plain = (r.get("plainLyrics") or "").strip()
    if not plain or r.get("instrumental"):
        return None
    lines = [l for l in plain.splitlines() if l.strip()]
    title = clean_title(r.get("trackName"), r.get("artistName") or "")
    return {
        "id": r["id"], "artist": r.get("artistName") or "", "title": title,
        "album": r.get("albumName") or "", "duration": int(r.get("duration") or 0),
        "synced": bool(r.get("syncedLyrics")), "lines": len(lines), "preview": lines[:2],
        "key": ((r.get("artistName") or "").lower(), title.lower(), lines[0].lower()),
    } | script_check(plain, lang)


def _unique(candidates: list[dict]) -> list[dict]:
    """Drop repeated uploads of the same text (same artist, title and first line), keeping the first."""
    seen = set()
    return [c for c in candidates if not (c["key"] in seen or seen.add(c["key"]))]


def search(query: str, lang: str = "ru") -> list[dict]:
    """Candidates with plain lyrics, duplicates removed. Texts in the language first (LRCLIB also holds
    translations), otherwise in LRCLIB's order."""
    results = _unique([c for r in _get("search", q=query) if (c := _candidate(r, lang))])
    return sorted(results, key=lambda r: (not r["fit"], bool(ALT_VERSION.search(r["title"]))))[:MAX_RESULTS]


FEAT = re.compile(r"\s*[(\[]?\b(feat|ft|featuring|при уч)\b\.?.*$", re.I)


def _norm(text: str) -> str:
    text = FEAT.sub("", clean_title(text or "")).casefold().replace("ё", "е")
    return " ".join(re.sub(r"[^\w\s]", " ", text).split())


def similarity(a: str, b: str) -> float:
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return 0.0
    ratio = SequenceMatcher(None, a, b).ratio()
    return max(ratio, 0.9) if min(len(a), len(b)) >= 3 and (a in b or b in a) else ratio


def score(c: dict, artists: list[str], title: str, duration: int | None) -> float:
    """How well an LRCLIB candidate fits a song known by artist, title and length (0-1)."""
    title_fit = similarity(title, c["title"])
    artist_fit = max((similarity(a, c["artist"]) for a in artists if a), default=0.5)
    if duration and c["duration"]:
        length_fit = max(0.0, 1 - max(0, abs(c["duration"] - duration) - 2) / 28)
    else:
        length_fit = 0.5
    value = 0.45 * title_fit + 0.3 * artist_fit + 0.25 * length_fit
    if not c["fit"]:
        value *= 0.5
    if ALT_VERSION.search(c["title"]) and not ALT_VERSION.search(title):
        value *= 0.7
    return round(value, 3)


GOOD_MATCH = 0.75


def match(artists: list[str], title: str, duration: int | None = None, lang: str = "ru") -> list[dict]:
    """Best LRCLIB candidates for a song from elsewhere (a YouTube video), best first.

    The exact lookup by artist, title and length (LRCLIB allows ±2 s) comes first, then a search by
    fields and, when that finds little, a free-text search."""
    artist = artists[0] if artists else ""
    found, exact = [], set()
    if artist and duration:
        try:
            if c := _candidate(_get("get", track_name=title, artist_name=artist, duration=duration), lang):
                found.append(c)
                exact.add(c["id"])
        except HTTPError as e:
            if e.code != 404:  # 404: no exact match
                raise
    if artist:
        found += [c for r in _get("search", track_name=title, artist_name=artist) if (c := _candidate(r, lang))]
    if sum(score(c, artists, title, duration) >= GOOD_MATCH for c in found) < 1 or len(found) < 3:
        found += [c for r in _get("search", q=f"{artist} {title}".strip()) if (c := _candidate(r, lang))]
    by_id = {}
    for c in found:
        by_id.setdefault(c["id"], c | {"score": score(c, artists, title, duration), "exact": c["id"] in exact,
                                       "duration_diff": c["duration"] - duration if duration and c["duration"] else None})
    ranked = sorted(by_id.values(), key=lambda c: (not c["exact"], -c["score"]))
    for c in ranked:
        c["good"] = c["exact"] or (c["score"] >= GOOD_MATCH and c["fit"])
    return _unique(ranked)[:MAX_RESULTS]


def fetch(lrclib_id: int) -> dict:
    r = _get(f"get/{int(lrclib_id)}")
    return {"artist": r.get("artistName") or "", "title": clean_title(r.get("trackName"), r.get("artistName") or ""),
            "lyrics": (r.get("plainLyrics") or "").strip(), "synced_lyrics": r.get("syncedLyrics") or None,
            "lrclib_id": r["id"]}
