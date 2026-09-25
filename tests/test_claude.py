"""Claude translator tests. No real Claude call is made: both backends are replaced."""

import json
import subprocess

import pytest

from lyricdeck import claude

ITEMS = [{"id": 0, "kind": "word", "text": "стать", "sung": "стали", "line": "Мы стали старше"},
         {"id": 5, "kind": "line", "text": "Я иду домой"}]


def test_prompt_has_songs_and_items():
    prompt = claude.build_prompt(ITEMS, ["Мы стали старше\nЯ иду домой"])
    assert "<song>" in prompt and '0. [word] стать (as sung: "стали") — line: "Мы стали старше"' in prompt
    assert "5. [line] Я иду домой" in prompt


def test_result_ignores_unknown_ids():
    data = {"translations": [{"id": 0, "english": " become "}, {"id": 99, "english": "x"}]}
    assert claude._result(data, ITEMS) == {0: "become"}


def test_claude_code_backend(monkeypatch):
    seen = {}

    def fake_run(args, **kw):
        seen["args"], seen["input"] = args, kw["input"]
        out = {"is_error": False, "structured_output": {"translations": [{"id": 0, "english": "become"}]},
               "total_cost_usd": 0.012, "usage": {"input_tokens": 900, "output_tokens": 40}}
        return subprocess.CompletedProcess(args, 0, json.dumps(out), "")

    monkeypatch.setattr(claude.shutil, "which", lambda _: "/usr/bin/claude")
    monkeypatch.setattr(claude.subprocess, "run", fake_run)
    result, usage = claude.translate_code(ITEMS, ["song"], "claude-opus-5-5")
    assert result == {0: "become"} and usage["cost_usd"] == 0.012
    assert "--json-schema" in seen["args"] and seen["args"][seen["args"].index("--tools") + 1] == ""
    assert "--bare" not in seen["args"]  # bare mode would ignore the user's subscription login


def test_claude_code_error(monkeypatch):
    monkeypatch.setattr(claude.shutil, "which", lambda _: "/usr/bin/claude")
    monkeypatch.setattr(claude.subprocess, "run", lambda args, **kw: subprocess.CompletedProcess(
        args, 1, json.dumps({"is_error": True, "result": "Not logged in"}), ""))
    with pytest.raises(claude.ClaudeError, match="Not logged in"):
        claude.translate_code(ITEMS, [], "m")


def test_endpoint_caches_and_tracks_usage(client, monkeypatch):
    calls = []

    def fake(items, lyrics, model, api_key, system=None):
        calls.append(items)
        return {i["id"]: f"EN {i['text']}" for i in items}, {"input_tokens": 100, "output_tokens": 20, "cost_usd": 0.01}

    monkeypatch.setattr(claude, "translate_api", fake)
    client.post("/songs/new", data={"title": "S", "lyrics": "Мы стали старше\nЯ иду домой"})
    client.post("/settings/claude", data={"claude_backend": "api", "claude_api_key": "sk-test",
                                          "claude_model_api": "claude-sonnet-5", "claude_words": "on", "claude_lines": "on"})
    body = {"songs": [1], "items": ITEMS}
    first = client.post("/translate/claude", json=body).get_json()
    assert first["translations"] == {"0": "EN стать", "5": "EN Я иду домой"} and first["cost"] == 0.01
    second = client.post("/translate/claude", json=body).get_json()
    assert second["translations"] == first["translations"] and len(calls) == 1  # served from the cache
    page = client.get("/settings/claude").get_data(as_text=True)
    assert "1 request " in page and "$0.01" in page and "sk-test" not in page  # key is never shown


def test_endpoint_when_off(client):
    assert "Claude is off" in client.post("/translate/claude", json={"items": ITEMS}).get_json()["error"]
