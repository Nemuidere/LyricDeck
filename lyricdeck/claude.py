"""Optional Claude translator, through an Anthropic API key or the user's own local Claude Code login.

The Claude Code backend runs the `claude` command the user installed and logged into themselves
(`claude -p`); LyricDeck never reads Claude Code's credentials.
"""

import json
import shutil
import subprocess
import tempfile

API_MODELS_DEFAULT = "claude-sonnet-5"
CODE_MODELS = ["claude-opus-5-5", "claude-fable-5-1", "claude-sonnet-5", "claude-haiku-4-5"]
CODE_MODEL_DEFAULT = "claude-opus-5-5"
# USD per million tokens (input, output), from platform.claude.com/docs/en/about-claude/pricing.
PRICES = {"claude-fable-5-1": (10, 50), "claude-opus-5-5": (4, 20), "claude-opus-5": (5, 25),
          "claude-sonnet-5": (2, 10), "claude-haiku-4-5": (1, 5)}
TIMEOUT = 600

SCHEMA = {
    "type": "object",
    "properties": {"translations": {"type": "array", "items": {
        "type": "object",
        "properties": {"id": {"type": "integer"}, "english": {"type": "string"}},
        "required": ["id", "english"], "additionalProperties": False,
    }}},
    "required": ["translations"], "additionalProperties": False,
}

SYSTEM = """You translate Russian song lyrics into English for language-learning flashcards.
You get the lyrics of the songs, then a numbered list of items. Return one translation for every id.
- word: the English meaning of the Russian dictionary form, as a short gloss (1-4 words per sense,
  at most 3 senses, comma-separated). Put the sense used in the given song line first.
- phrase: a natural English translation of the phrase as it is used in its line.
- line: a faithful, natural English translation of the whole line, as one line.
Slang, swearing and poetic word order should be translated by meaning, not word by word."""


class ClaudeError(Exception):
    pass


def build_prompt(items: list[dict], lyrics: list[str]) -> str:
    songs = "\n\n".join(f"<song>\n{text}\n</song>" for text in lyrics)
    lines = []
    for item in items:
        detail = f' (as sung: "{item["sung"]}")' if item.get("sung") else ""
        where = f' — line: "{item["line"]}"' if item.get("line") else ""
        lines.append(f'{item["id"]}. [{item["kind"]}] {item["text"]}{detail}{where}')
    return f"{songs}\n\nItems:\n" + "\n".join(lines)


def _result(data: dict, items: list[dict]) -> dict[int, str]:
    ids = {item["id"] for item in items}
    return {t["id"]: t["english"].strip() for t in data.get("translations", []) if t.get("id") in ids}


def _api_client(api_key: str):
    try:
        import anthropic
    except ImportError as e:
        raise ClaudeError("The Anthropic SDK is not installed. Run: uv sync --extra claude") from e
    return anthropic, anthropic.Anthropic(api_key=api_key or None)


def translate_api(items: list[dict], lyrics: list[str], model: str, api_key: str) -> tuple[dict[int, str], dict]:
    anthropic, client = _api_client(api_key)
    try:
        with client.messages.stream(
            model=model, max_tokens=32000, system=SYSTEM,
            messages=[{"role": "user", "content": build_prompt(items, lyrics)}],
            output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
        ) as stream:
            message = stream.get_final_message()
    except anthropic.AuthenticationError as e:
        raise ClaudeError("The API key was rejected. Check it in Settings → Claude.") from e
    except anthropic.PermissionDeniedError as e:
        raise ClaudeError("This API key has no access to that model.") from e
    except anthropic.NotFoundError as e:
        raise ClaudeError(f"Model {model} was not found. Pick another one in Settings → Claude.") from e
    except anthropic.RateLimitError as e:
        raise ClaudeError("Rate limited by the API. Wait a minute and try again.") from e
    except anthropic.APIStatusError as e:
        raise ClaudeError(f"API error {e.status_code}: {e.message}") from e
    except anthropic.APIConnectionError as e:
        raise ClaudeError("Could not reach the Claude API. Check your internet connection.") from e
    if message.stop_reason == "refusal":
        raise ClaudeError("Claude declined to translate these lyrics.")
    if message.stop_reason == "max_tokens":
        raise ClaudeError("The answer was too long. Tick fewer cards and try again.")
    text = next(b.text for b in message.content if b.type == "text")
    price = PRICES.get(model)
    usage = {"input_tokens": message.usage.input_tokens, "output_tokens": message.usage.output_tokens,
             "cost_usd": (message.usage.input_tokens * price[0] + message.usage.output_tokens * price[1]) / 1e6
             if price else None}
    return _result(json.loads(text), items), usage


def claude_command() -> str:
    path = shutil.which("claude")
    if not path:
        raise ClaudeError("The `claude` command was not found. Install Claude Code and log in first.")
    return path


def _code_args(model: str) -> list[str]:
    return [claude_command(), "-p", "--output-format", "json", "--json-schema", json.dumps(SCHEMA),
            "--model", model, "--tools", "", "--system-prompt", SYSTEM, "--no-session-persistence",
            "--disable-slash-commands", "--strict-mcp-config", "--setting-sources", ""]


def translate_code(items: list[dict], lyrics: list[str], model: str) -> tuple[dict[int, str], dict]:
    with tempfile.TemporaryDirectory() as empty_dir:  # run outside any project so no project settings load
        try:
            proc = subprocess.run(_code_args(model), input=build_prompt(items, lyrics), capture_output=True,
                                  text=True, cwd=empty_dir, timeout=TIMEOUT)
        except subprocess.TimeoutExpired as e:
            raise ClaudeError("Claude Code took too long. Tick fewer cards and try again.") from e
    try:
        out = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise ClaudeError(f"Claude Code failed: {(proc.stderr or proc.stdout).strip()[:300]}") from e
    if out.get("is_error") or proc.returncode != 0:
        raise ClaudeError(f"Claude Code failed: {str(out.get('result') or proc.stderr)[:300]}")
    usage = out.get("usage") or {}
    return _result(out.get("structured_output") or {}, items), {
        "input_tokens": usage.get("input_tokens", 0), "output_tokens": usage.get("output_tokens", 0),
        "cost_usd": out.get("total_cost_usd"),
    }


def list_api_models(api_key: str) -> list[str]:
    """Current model IDs for this key (free call; also checks the key)."""
    anthropic, client = _api_client(api_key)
    try:
        return [m.id for m in client.models.list()]
    except anthropic.AuthenticationError as e:
        raise ClaudeError("The API key was rejected.") from e
    except anthropic.APIConnectionError as e:
        raise ClaudeError("Could not reach the Claude API.") from e
    except anthropic.APIStatusError as e:
        raise ClaudeError(f"API error {e.status_code}: {e.message}") from e


def code_status() -> str:
    """Login status of the local Claude Code (no model call, nothing is spent)."""
    proc = subprocess.run([claude_command(), "auth", "status", "--json"], capture_output=True, text=True, timeout=30)
    try:
        status = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise ClaudeError(f"Could not read Claude Code's status: {proc.stderr.strip()[:200]}") from e
    if not status.get("loggedIn"):
        raise ClaudeError("Claude Code is installed but not logged in. Run `claude` and log in there.")
    plan = status.get("subscriptionType")
    return f"Logged in to Claude Code{f' ({plan} plan)' if plan else ''}."
