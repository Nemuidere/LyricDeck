"""Fetching lyrics from LRCLIB (https://lrclib.net): free, no key, crowd-sourced."""

import json
import re
import urllib.parse
import urllib.request

from .dictionary import USER_AGENT

LRCLIB_URL = "https://lrclib.net/api"
MAX_RESULTS = 10
ALT_VERSION = re.compile(r"\b(inst|instrumental|off vocal|karaoke|live|mv|the first take|remix)\b", re.I)
TITLE_JUNK = re.compile(r"\s*[(\[](official[^)\]]*|lyrics?|lyric video|audio|video|клип|премьера[^)\]]*|"
                        r"текст[^)\]]*|remaster[^)\]]*)[)\]]", re.I)


def _get(path: str, **params) -> object:
    url = f"{LRCLIB_URL}/{path}" + (f"?{urllib.parse.urlencode(params)}" if params else "")
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": USER_AGENT}), timeout=30) as r:
        return json.load(r)


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


def search(query: str, lang: str = "ru") -> list[dict]:
    """Candidates with plain lyrics, duplicates removed. Texts in the language first (LRCLIB also holds
    translations), otherwise in LRCLIB's order."""
    results, seen = [], set()
    for r in _get("search", q=query):
        plain = (r.get("plainLyrics") or "").strip()
        if not plain or r.get("instrumental"):
            continue
        lines = [l for l in plain.splitlines() if l.strip()]
        title = clean_title(r.get("trackName"), r.get("artistName") or "")
        key = ((r.get("artistName") or "").lower(), title.lower(), lines[0].lower())
        if key in seen:
            continue
        seen.add(key)
        results.append({
            "id": r["id"], "artist": r.get("artistName") or "", "title": title,
            "album": r.get("albumName") or "", "duration": int(r.get("duration") or 0),
            "synced": bool(r.get("syncedLyrics")), "lines": len(lines), "preview": lines[:2],
        } | script_check(plain, lang))
    return sorted(results, key=lambda r: (not r["fit"], bool(ALT_VERSION.search(r["title"]))))[:MAX_RESULTS]


def fetch(lrclib_id: int) -> dict:
    r = _get(f"get/{int(lrclib_id)}")
    return {"artist": r.get("artistName") or "", "title": clean_title(r.get("trackName"), r.get("artistName") or ""),
            "lyrics": (r.get("plainLyrics") or "").strip(), "synced_lyrics": r.get("syncedLyrics") or None,
            "lrclib_id": r["id"]}
