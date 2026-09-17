"""
Configuration: constants, .env loading, on-disk paths, personas, the
provider registry, and retry/context settings. Nothing here depends on
any other shadowos_app module -- this is the base of the import graph.
"""

import json
import os
from pathlib import Path

from dotenv import load_dotenv

# --------------------------------------------------------------------- Paths
# This file lives in shadowos_app/; the project root (next to main.py,
# .env, requirements.txt) is one directory up. Anchoring every on-disk
# path there means behavior doesn't depend on the cwd the user happens to
# launch from.
PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = PACKAGE_DIR.parent

ENV_FILE = PROJECT_DIR / ".env"
# override=True: a value in .env always wins over whatever's already
# exported in the shell. A stale test key exported earlier in a session
# would otherwise silently shadow a freshly-edited .env with no obvious
# cause -- that's a real bug we hit once, so this stays deliberate.
load_dotenv(dotenv_path=ENV_FILE, override=True)

# Everything persistent lives under ~/.shadowos/, not the project folder,
# so it survives a `git pull` / reinstall of the code itself.
STATE_DIR = Path.home() / ".shadowos"
STATE_DIR.mkdir(parents=True, exist_ok=True)

STATE_FILE = STATE_DIR / "state.json"
HISTORY_FILE = STATE_DIR / "history"          # prompt_toolkit input history

CHATS_DIR = STATE_DIR / "chats"                 # saved conversations (storage.py)
CHATS_DIR.mkdir(parents=True, exist_ok=True)

DATA_DIR = STATE_DIR / "data"                    # notes/tasks/reminders (productivity.py)
DATA_DIR.mkdir(parents=True, exist_ok=True)

# --------------------------------------------------------------- Providers
# Single source of truth for which env var each provider needs, its
# default model, and its display color. Adding a new provider or changing
# a default model means editing only this dict.
PROVIDER_CONFIG = {
    "anthropic": {"default_model": "claude-sonnet-4-6", "api_key_env": "ANTHROPIC_API_KEY", "color": "sandy_brown"},
    "openai":    {"default_model": "gpt-5",               "api_key_env": "OPENAI_API_KEY",    "color": "spring_green3"},
    "groq":      {"default_model": "llama-3.3-70b-versatile", "api_key_env": "GROQ_API_KEY",  "color": "orange3"},
    "gemini":    {"default_model": "gemini-2.5-pro",       "api_key_env": "GEMINI_API_KEY",    "color": "royal_blue1"},
    "ollama":    {"default_model": "llama3.1",              "api_key_env": None,                 "color": "grey70"},
}
PROVIDERS = list(PROVIDER_CONFIG)
DEFAULT_PROVIDER = "anthropic"

KNOWN_ENV_VARS = sorted({c["api_key_env"] for c in PROVIDER_CONFIG.values() if c["api_key_env"]})

# ------------------------------------------------------------------ Personas
# Persona name -> system prompt. Switching personas only ever picks a
# different value out of this dict for the *next* turn's system prompt --
# it never touches conversation memory, so switching personas mid-chat
# doesn't duplicate or reset anything. "default" is the baseline prompt,
# so a session that never touches /persona behaves as if personas didn't
# exist.
SYSTEM_PROMPT = (
    "You are ShadowOS, a terminal AI assistant running inside Termux on the "
    "user's Android device. You have tools to run shell commands, read/"
    "write files, and manage notes/tasks/reminders. Use tools proactively "
    "to get things done rather than just describing what could be done. "
    "Be concise in your prose responses since this is a phone terminal. "
    "Confirm before anything irreversible if it isn't already obvious."
)
PERSONAS = {
    "default": SYSTEM_PROMPT,
    "developer": (
        "You are ShadowOS in developer mode. Assume a technical audience. "
        "Prioritize correct, runnable code and precise terminology over "
        "hand-holding; default to code blocks for anything code-related; "
        "explain tradeoffs briefly; skip disclaimers a working developer "
        "wouldn't need. Use your shell/file tools freely to inspect and "
        "modify the user's project rather than describing changes in prose."
    ),
    "research": (
        "You are ShadowOS in research mode. Reason carefully and show your "
        "work: weigh evidence, flag uncertainty and open questions, and "
        "distinguish well-established facts from your own inference. Favor "
        "thorough, well-structured answers over brevity when the topic "
        "warrants it."
    ),
    "concise": (
        "You are ShadowOS in concise mode. Answer in as few words as "
        "possible without losing correctness. Prefer a single sentence or "
        "a short list over a paragraph. Skip preamble, caveats, and "
        "disclaimers unless essential. Still use tools rather than just "
        "describing what you'd do."
    ),
}

# Every slash command ShadowOS understands -- used for tab-completion (see
# ui.make_session). Adding a command here doesn't wire it up by itself; it
# still needs a handler in commands.dispatch.
COMMANDS = [
    "/model", "/models", "/persona", "/status", "/context", "/new", "/clear",
    "/save", "/load", "/chats", "/delete", "/rename",
    "/notes", "/tasks", "/reminders", "/dashboard", "/color", "/env",
    "/ai", "/shell", "/cd", "/help", "/exit",
]

BANNER_COLOR_NAMES = ["red", "green", "yellow", "blue", "magenta", "cyan", "white"]

# Context-compaction budget (see memory.compact_if_needed).
CONTEXT_CONFIG = {
    "max_tokens": 6000,           # compact once summary + history estimate exceeds this
    "keep_recent_messages": 12,   # always keep this many most-recent messages verbatim
    "summary_max_tokens": 500,    # cap the length (and cost) of each summarization call
}

# Retry policy for temporary provider failures (see providers._connect).
RETRY_CONFIG = {
    "max_attempts": 3,   # total tries per request: 1 initial + up to 2 retries
    "base_delay": 0.5,   # seconds; doubles each retry (0.5s, then 1.0s)
}

MAX_TOOL_ITERATIONS = 8   # per chat turn, before giving up on a runaway tool loop

# ------------------------------------------------------------------- State
# Persisted across runs: active provider, each provider's last-used model,
# active persona, banner color, input mode (ai/shell), cwd for tool
# execution, and which saved chat (if any) is currently loaded.


def _default_state() -> dict:
    return {
        "provider": DEFAULT_PROVIDER,
        "models": {p: cfg["default_model"] for p, cfg in PROVIDER_CONFIG.items()},
        "persona": "default",
        "banner_color": "random",
        "mode": "ai",
        "cwd": str(Path.home()),
        "active_chat_name": None,
    }


def load_state() -> dict:
    state = _default_state()
    if STATE_FILE.exists():
        try:
            saved = json.loads(STATE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            saved = {}
        state.update(saved)
        # Merge, don't replace, the per-provider model dict -- an older
        # state file (or one saved before a new provider was added) might
        # be missing entries a fresh install would have.
        models = dict(_default_state()["models"])
        models.update(saved.get("models") or {})
        state["models"] = models
        if state.get("provider") not in PROVIDER_CONFIG:
            state["provider"] = DEFAULT_PROVIDER
        if state.get("persona") not in PERSONAS:
            state["persona"] = "default"
    return state


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2))


# --------------------------------------------------------- Key diagnostics


def _looks_like_placeholder(value: str) -> bool:
    """Catches the common mistake of never editing .env.example's sample
    values (e.g. 'sk-ant-...', 'AI...') before copying it to .env."""
    return "..." in value


def mask_key(value: str) -> str:
    """Show just enough of a secret to recognize it, never the whole thing."""
    if not value:
        return "(not set)"
    if len(value) <= 10:
        return "*" * len(value)
    return f"{value[:6]}...{value[-4:]}"


def env_diagnostics() -> dict:
    """Everything needed to debug 'why is my API key not working' without
    making a network call."""
    rows = []
    for var in KNOWN_ENV_VARS:
        raw = os.environ.get(var)
        value = raw.strip() if raw else raw
        rows.append({
            "var": var,
            "set": bool(value),
            "masked": mask_key(value) if value else "(not set)",
            "placeholder": bool(value) and _looks_like_placeholder(value),
        })
    return {
        "env_file": str(ENV_FILE),
        "env_file_exists": ENV_FILE.exists(),
        "vars": rows,
    }


def resolve_api_key(provider: str) -> str:
    """Look up and validate the API key for a provider name. Returns None
    if that provider needs no key (e.g. ollama). Raises RuntimeError if
    the provider is unrecognized, the key is missing, or it still looks
    like the unedited .env.example placeholder."""
    if provider not in PROVIDER_CONFIG:
        raise RuntimeError(f"Unknown provider '{provider}'. Known providers: {', '.join(PROVIDERS)}")
    env_var = PROVIDER_CONFIG[provider]["api_key_env"]
    if not env_var:
        return None

    raw = os.environ.get(env_var)
    api_key = raw.strip() if raw else raw
    if not api_key:
        raise RuntimeError(
            f"{provider} needs {env_var} set. Put it in {ENV_FILE} "
            f"(run /env to check what's currently loaded)."
        )
    if _looks_like_placeholder(api_key):
        raise RuntimeError(
            f"{env_var} in {ENV_FILE} still looks like the example "
            f"placeholder ('{api_key[:15]}...'). Replace it with your real key."
        )
    return api_key
