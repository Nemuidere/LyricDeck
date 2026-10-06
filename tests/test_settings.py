"""Per-kind translator settings and the upgrade from older settings."""

from lyricdeck import create_app, translate
from lyricdeck.db import connect


def old_settings(tmp_path, values: dict):
    path = str(tmp_path / "old.db")
    create_app({"TESTING": True, "DATABASE": path})
    with connect(path) as conn:
        conn.execute("DELETE FROM settings")
        conn.executemany("INSERT INTO settings VALUES (?, ?)", values.items())
    conn.close()
    app = create_app({"TESTING": True, "DATABASE": path})
    with connect(path) as conn:
        saved = dict(conn.execute("SELECT key, value FROM settings").fetchall())
    conn.close()
    return app, saved


def test_migrates_claude_setup(tmp_path):
    _, saved = old_settings(tmp_path, {"line_translator": "mymemory", "claude_backend": "code", "claude_words": "1",
                                       "claude_lines": "1", "claude_model_code": "claude-sonnet-5"})
    assert (saved["translator_word"], saved["translator_phrase"], saved["translator_line"]) == ("claude",) * 3
    assert saved["claude_model_code"] == "sonnet"
    assert not {"line_translator", "claude_words", "claude_lines"} & set(saved)


def test_migrates_free_setup(tmp_path):
    _, saved = old_settings(tmp_path, {"line_translator": "none", "claude_backend": "off", "claude_lines": "1"})
    assert (saved["translator_word"], saved["translator_phrase"], saved["translator_line"]) == \
        ("dictionary", "none", "none")


def test_new_settings_are_kept(tmp_path):
    _, saved = old_settings(tmp_path, {"translator_word": "dictionary", "translator_phrase": "claude",
                                       "translator_line": "mymemory", "claude_backend": "api"})
    assert (saved["translator_phrase"], saved["translator_line"]) == ("claude", "mymemory")


def test_claude_choice_falls_back_while_off(client):
    client.post("/settings", data={"translator_word": "claude", "translator_phrase": "claude",
                                   "translator_line": "none"})
    page = client.get("/settings").get_data(as_text=True)
    assert "Claude is off, so Offline dictionary is used for now" in page and "(set up first)" in page
    with client.application.app_context():
        from lyricdeck.settings import translators
        assert translators() == {"word": "dictionary", "phrase": "mymemory", "line": "none"}
        client.post("/settings/claude", data={"claude_backend": "code"})
        assert translators() == {"word": "claude", "phrase": "claude", "line": "none"}


def test_translate_endpoint_runs_when_one_kind_uses_mymemory(client, monkeypatch):
    monkeypatch.setattr(translate, "_mymemory_request", lambda text, email, langpair="ru|en": text.upper())
    monkeypatch.setattr(translate, "REQUEST_GAP", 0)
    client.post("/settings", data={"translator_phrase": "none", "translator_line": "mymemory"})
    assert client.post("/translate", json={"texts": ["да"]}).get_json()["translations"] == {"да": "ДА"}
    client.post("/settings", data={"translator_phrase": "none", "translator_line": "none"})
    assert client.post("/translate", json={"texts": ["нет"]}).get_json()["translations"] == {}
