"""Language of the current page. Russian lives at /, Japanese at /ja/ (the same blueprints registered twice)."""

from flask import request, url_for

NAMES = {"ru": "Russian", "ja": "Japanese"}
DECK_SETTING = {"ru": "deck_name", "ja": "deck_name_ja"}


def code() -> str:
    return "ja" if (request.blueprint or "").startswith("ja_") else "ru"


def lurl(endpoint: str, **values) -> str:
    """url_for within the current language's part of the site."""
    return url_for(("ja_" if code() == "ja" else "") + endpoint, **values)


def require_installed():
    """Before every Japanese page: explain how to install Japanese support when it is missing."""
    if code() == "ja":
        from .japanese import available
        if not available():
            from flask import render_template
            return render_template("ja_missing.html")
    return None


def plugin_class():
    """The card-builder plug-in class for the current language."""
    if code() == "ja":
        from .japanese import Japanese
        return Japanese
    from .cards import Russian
    return Russian


def plugin(conn):
    return plugin_class()(conn)


def ready(conn) -> bool:
    """Is the current language's dictionary installed and imported?"""
    return plugin_class().ready(conn)
