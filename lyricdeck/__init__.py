"""LyricDeck: a local Flask app that turns song lyrics into Anki decks."""

from pathlib import Path

from flask import Flask

from . import build, db, dictionary, songs


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
    app.cli.add_command(dictionary.init_data_command)
    return app
