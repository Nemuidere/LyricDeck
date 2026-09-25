"""LyricDeck: a local Flask app that turns song lyrics into Anki decks."""

from pathlib import Path

import re

from flask import Flask
from markupsafe import Markup

from . import build, db, dictionary, lang, settings, songs


def ruby(html: str) -> Markup:
    """Show Anki furigana (漢字[かんじ]) as HTML ruby text on the review page."""
    return Markup(re.sub(r" ?([^ >\[\]<]+?)\[([^\]]*)\]", r"<ruby>\1<rt>\2</rt></ruby>", str(html)))


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__)
    app.config.from_mapping(
        SECRET_KEY="local-only",
        MAX_FORM_MEMORY_SIZE=16 * 1024 * 1024,
        DATABASE=str(Path(app.root_path).parent / "data" / "app.db"),
    )
    if test_config:
        app.config.update(test_config)

    db.init_app(app)
    app.register_blueprint(songs.bp)
    app.register_blueprint(build.bp)
    app.register_blueprint(songs.bp, name="ja_songs", url_prefix="/ja")
    app.register_blueprint(build.bp, name="ja_build", url_prefix="/ja")
    app.register_blueprint(settings.bp)
    app.add_template_global(lang.lurl, "lurl")
    app.context_processor(lambda: {"lang": lang.code(), "lang_name": lang.NAMES[lang.code()]})
    app.add_template_filter(ruby)
    app.cli.add_command(dictionary.init_data_command)
    return app
