"""
Provider adapters for Anthropic, OpenAI, Groq, Gemini, and Ollama. Every
provider is called directly over REST via `requests` -- never through the
official SDKs, which pull in compiled extensions (jiter, grpcio) that
have no prebuilt wheels for Android/Termux.

Design ported from ShadowChat's providers.py (same retry/error-handling
philosophy: identical, safe error messages across providers; only 5xx
and connection failures are retried; a key never leaks into an error
message since it's only ever sent in a header, never a URL). Extended
here for ShadowOS's tool-calling: each provider exposes both a buffered
`call()` (used for context-compaction summaries, which are never shown
to the user) and a streaming `call_stream()` (used for normal chat turns,
rendered live). Both can return tool calls; streaming providers assemble
them from incremental deltas as the response arrives.

Anthropic, OpenAI, and Groq stream token-by-token (SSE). Gemini and
Ollama don't get real incremental streaming here -- their tool-calling
stream shapes are less uniform to parse safely without live testing
against the real API, so `call_stream()` for those two just performs the
buffered call and yields the whole reply as one chunk. Every provider
still exposes the same StreamResult interface either way, so the calling
code (memory.py, core.py) never needs to know which is which.
"""

import json
import os
import time

import requests

from . import config

SYSTEM_PROMPT = config.SYSTEM_PROMPT


class ProviderError(Exception):
    """A provider call failed in a well-understood way (bad key, rate
    limit, missing model, provider outage, or no connection). The message
    is always safe to print as-is -- never built from headers or the key."""

    def __init__(self, provider, message):
        self.provider = provider
        super().__init__(message)


class StreamResult:
    """Wraps a provider's raw chunk generator. Iterating yields text
    chunks as they arrive (for live rendering); `.content` and
    `.tool_calls` are fully populated once the generator is exhausted."""

    def __init__(self, generator):
        self._generator = generator
        self.content = ""
        self.tool_calls = []

    def __iter__(self):
        for chunk in self._generator:
            if chunk:
                self.content += chunk
                yield chunk

    def feed_tool_call(self, tool_call: dict) -> None:
        self.tool_calls.append(tool_call)


# ---------------------------------------------------------------------------
# Shared HTTP plumbing (retries, error classification, safe JSON access)
# ---------------------------------------------------------------------------

def _get_key(provider: str) -> str:
    return config.resolve_api_key(provider)


def _describe_status(provider: str, model: str, status_code: int) -> str:
    if status_code == 401:
        return (
            f"{provider}: invalid API key (401). Double-check the "
            f"{provider.upper()}_API_KEY value -- it's missing, wrong, or expired. Try /env."
        )
    if status_code == 403:
        return (
            f"{provider}: permission denied (403). Your API key doesn't have "
            f"access to this model or endpoint (e.g. no billing set up, or the "
            f"model needs a different access tier)."
        )
    if status_code == 404:
        return (
            f"{provider}: model or endpoint not found (404). \"{model}\" may not "
            f"exist, be renamed/retired, or the API URL is wrong. Check /models."
        )
    if status_code == 429:
        return f"{provider}: rate limited (429). Wait a bit before trying again."
    if status_code >= 500:
        return f"{provider}: server error ({status_code}). This is on {provider}'s end, not yours."
    return f"{provider}: request failed ({status_code})."


def _is_retryable_status(status_code: int) -> bool:
    """Only 5xx is treated as a temporary, worth-retrying failure. Every
    4xx (bad key, no access, unknown model, rate limit, malformed
    request) means repeating the same request won't help -- and for 429
    specifically, retrying with our own fixed backoff (rather than
    honoring a Retry-After header) is more likely to make it worse."""
    return status_code >= 500


def _attempt_post(provider: str, url: str, **kwargs):
    try:
        return requests.post(url, **kwargs)
    except requests.exceptions.Timeout as e:
        raise ProviderError(provider, f"{provider}: connection failure -- request timed out. Check your network.") from e
    except requests.exceptions.ConnectionError as e:
        raise ProviderError(provider, f"{provider}: connection failure -- couldn't reach the server. Check your network.") from e
    except requests.exceptions.RequestException as e:
        raise ProviderError(provider, f"{provider}: connection failure -- {type(e).__name__}. Check your network.") from e


def _connect(provider: str, model: str, url: str, **kwargs):
    """Opens the HTTP connection, retrying a small fixed number of times
    with exponential backoff, but only for failures that are plausibly
    temporary: no response at all, or a 5xx. Never retries a request that
    already started streaming a body -- a connection drop mid-stream is
    the caller's own concern."""
    max_attempts = config.RETRY_CONFIG["max_attempts"]
    base_delay = config.RETRY_CONFIG["base_delay"]

    last_error = None
    for attempt in range(1, max_attempts + 1):
        try:
            resp = _attempt_post(provider, url, **kwargs)
        except ProviderError as e:
            last_error = e
        else:
            if resp.ok:
                return resp
            if not _is_retryable_status(resp.status_code):
                raise ProviderError(provider, _describe_status(provider, model, resp.status_code))
            last_error = ProviderError(provider, _describe_status(provider, model, resp.status_code))

        if attempt < max_attempts:
            time.sleep(base_delay * (2 ** (attempt - 1)))

    raise last_error


def _post(provider: str, model: str, url: str, **kwargs):
    return _connect(provider, model, url, **kwargs)


def _post_stream(provider: str, model: str, url: str, **kwargs):
    kwargs["stream"] = True
    return _connect(provider, model, url, **kwargs)


def _safe_json(provider: str, resp):
    try:
        return resp.json()
    except ValueError as e:
        preview = ""
        try:
            preview = resp.text[:200].replace("\n", " ").strip()
        except Exception:
            pass
        detail = f" Raw response started with: {preview!r}" if preview else ""
        raise ProviderError(
            provider,
            f"{provider}: response wasn't valid JSON despite a successful status.{detail}",
        ) from e


def _json_dumps(obj) -> str:
    return json.dumps(obj)


def _json_loads(s):
    try:
        return json.loads(s) if s else {}
    except json.JSONDecodeError:
        return {}


# ---------------------------------------------------------------------------
# Tool schema translation -- each provider has its own tool-calling shape
# ---------------------------------------------------------------------------

def _anthropic_tools(tool_schemas: list) -> list:
    return [
        {"name": t["name"], "description": t["description"], "input_schema": t["parameters"]}
        for t in (tool_schemas or [])
    ]


def _openai_tools(tool_schemas: list) -> list:
    return [
        {"type": "function", "function": {
            "name": t["name"], "description": t["description"], "parameters": t["parameters"],
        }}
        for t in (tool_schemas or [])
    ]


def _gemini_tools(tool_schemas: list) -> list:
    if not tool_schemas:
        return []
    return [{"function_declarations": [
        {"name": t["name"], "description": t["description"], "parameters": t["parameters"]}
        for t in tool_schemas
    ]}]


# ---------------------------------------------------------------------------
# Message translation -- shared generic wire format used by core.py/memory.py:
#   {"role": "user", "content": "..."}
#   {"role": "assistant", "content": "...", "tool_calls": [{"id","name","input"}]}
#   {"role": "tool_result", "tool_call_id": "...", "name": "...", "content": "..."}
# ---------------------------------------------------------------------------

def _to_anthropic_messages(messages: list) -> list:
    native = []
    for m in messages:
        if m["role"] == "user":
            native.append({"role": "user", "content": m["content"]})
        elif m["role"] == "assistant":
            blocks = []
            if m.get("content"):
                blocks.append({"type": "text", "text": m["content"]})
            for tc in m.get("tool_calls", []):
                blocks.append({"type": "tool_use", "id": tc["id"], "name": tc["name"], "input": tc["input"]})
            native.append({"role": "assistant", "content": blocks})
        elif m["role"] == "tool_result":
            native.append({
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": m["tool_call_id"], "content": m["content"]}],
            })
    return native


def _to_openai_messages(messages: list, system: str) -> list:
    native = [{"role": "system", "content": system}]
    for m in messages:
        if m["role"] == "user":
            native.append({"role": "user", "content": m["content"]})
        elif m["role"] == "assistant":
            msg = {"role": "assistant", "content": m.get("content") or None}
            if m.get("tool_calls"):
                msg["tool_calls"] = [
                    {"id": tc["id"], "type": "function",
                     "function": {"name": tc["name"], "arguments": _json_dumps(tc["input"])}}
                    for tc in m["tool_calls"]
                ]
            native.append(msg)
        elif m["role"] == "tool_result":
            native.append({"role": "tool", "tool_call_id": m["tool_call_id"], "content": m["content"]})
    return native


def _to_gemini_contents(messages: list) -> list:
    native = []
    for m in messages:
        if m["role"] == "user":
            native.append({"role": "user", "parts": [{"text": m["content"]}]})
        elif m["role"] == "assistant":
            parts = []
            if m.get("content"):
                parts.append({"text": m["content"]})
            for tc in m.get("tool_calls", []):
                parts.append({"functionCall": {"name": tc["name"], "args": tc["input"]}})
            native.append({"role": "model", "parts": parts})
        elif m["role"] == "tool_result":
            native.append({
                "role": "function",
                "parts": [{"functionResponse": {"name": m["name"], "response": {"content": m["content"]}}}],
            })
    return native


# ---------------------------------------------------------------------------
# Anthropic
# ---------------------------------------------------------------------------

_ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
_ANTHROPIC_VERSION = "2023-06-01"


def call_anthropic(model, messages, system=SYSTEM_PROMPT, tools=None, max_tokens=1024):
    key = _get_key("anthropic")
    payload = {
        "model": model, "max_tokens": max_tokens, "system": system,
        "messages": _to_anthropic_messages(messages),
    }
    if tools:
        payload["tools"] = _anthropic_tools(tools)
    resp = _post(
        "anthropic", model, _ANTHROPIC_URL,
        headers={"x-api-key": key, "anthropic-version": _ANTHROPIC_VERSION, "content-type": "application/json"},
        json=payload, timeout=60,
    )
    data = _safe_json("anthropic", resp)
    text_parts, tool_calls = [], []
    for block in data.get("content", []):
        if block.get("type") == "text":
            text_parts.append(block.get("text", ""))
        elif block.get("type") == "tool_use":
            tool_calls.append({"id": block["id"], "name": block["name"], "input": block.get("input", {})})
    result = StreamResult(iter(()))
    result.content = "\n".join(text_parts)
    result.tool_calls = tool_calls
    return result


def call_anthropic_stream(model, messages, system=SYSTEM_PROMPT, tools=None, max_tokens=1024):
    key = _get_key("anthropic")
    payload = {
        "model": model, "max_tokens": max_tokens, "system": system,
        "messages": _to_anthropic_messages(messages), "stream": True,
    }
    if tools:
        payload["tools"] = _anthropic_tools(tools)
    resp = _post_stream(
        "anthropic", model, _ANTHROPIC_URL,
        headers={"x-api-key": key, "anthropic-version": _ANTHROPIC_VERSION, "content-type": "application/json"},
        json=payload, timeout=60,
    )
    result = StreamResult(None)
    result._generator = _iter_anthropic_sse(resp, result)
    return result


def _iter_anthropic_sse(resp, result: StreamResult):
    """Parses Anthropic's Messages API SSE stream. Text arrives as
    content_block_delta/text_delta events; tool_use input arrives as
    input_json_delta fragments per content-block index, concatenated and
    parsed once that block's content_block_stop fires."""
    blocks = {}  # index -> {"type","id","name","json"}
    try:
        with resp:
            for line in resp.iter_lines(decode_unicode=True):
                if not line or not line.startswith("data:"):
                    continue
                data = line[len("data:"):].strip()
                try:
                    obj = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if not isinstance(obj, dict):
                    continue

                event_type = obj.get("type")
                if event_type == "content_block_start":
                    idx = obj.get("index")
                    block = obj.get("content_block") or {}
                    blocks[idx] = {"type": block.get("type"), "id": block.get("id"), "name": block.get("name"), "json": ""}
                elif event_type == "content_block_delta":
                    idx = obj.get("index")
                    delta = obj.get("delta") or {}
                    if delta.get("type") == "text_delta":
                        text = delta.get("text")
                        if text:
                            yield text
                    elif delta.get("type") == "input_json_delta":
                        b = blocks.setdefault(idx, {"type": "tool_use", "id": None, "name": None, "json": ""})
                        b["json"] += delta.get("partial_json", "")
                elif event_type == "content_block_stop":
                    idx = obj.get("index")
                    b = blocks.get(idx)
                    if b and b.get("type") == "tool_use":
                        result.feed_tool_call({
                            "id": b.get("id") or f"tool_{idx}",
                            "name": b.get("name"),
                            "input": _json_loads(b.get("json", "")),
                        })
                elif event_type == "error":
                    err = obj.get("error")
                    message = err.get("message") if isinstance(err, dict) else None
                    raise ProviderError("anthropic", f"anthropic: {message or 'stream error'}")
    except requests.exceptions.RequestException as e:
        raise ProviderError("anthropic", f"anthropic: connection dropped mid-response -- {type(e).__name__}.") from e


# ---------------------------------------------------------------------------
# OpenAI-compatible (OpenAI itself, and Groq, which mirrors it exactly)
# ---------------------------------------------------------------------------

def _openai_style_call(provider, base_url, model, messages, system, tools, max_tokens):
    key = _get_key(provider)
    payload = {"model": model, "messages": _to_openai_messages(messages, system)}
    if tools:
        payload["tools"] = _openai_tools(tools)
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    resp = _post(
        provider, model, f"{base_url}/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json=payload, timeout=60,
    )
    data = _safe_json(provider, resp)
    choices = data.get("choices") or []
    if not choices:
        raise ProviderError(provider, f"{provider}: no choices in response.")
    message = choices[0].get("message", {})
    tool_calls = []
    for tc in (message.get("tool_calls") or []):
        tool_calls.append({"id": tc["id"], "name": tc["function"]["name"], "input": _json_loads(tc["function"].get("arguments", "{}"))})
    result = StreamResult(iter(()))
    result.content = message.get("content") or ""
    result.tool_calls = tool_calls
    return result


def _openai_style_call_stream(provider, base_url, model, messages, system, tools, max_tokens):
    key = _get_key(provider)
    payload = {"model": model, "messages": _to_openai_messages(messages, system), "stream": True}
    if tools:
        payload["tools"] = _openai_tools(tools)
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    resp = _post_stream(
        provider, model, f"{base_url}/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json=payload, timeout=60,
    )
    result = StreamResult(None)
    result._generator = _iter_openai_style_sse(resp, provider, result)
    return result


def _iter_openai_style_sse(resp, provider, result: StreamResult):
    """Parses an OpenAI-compatible SSE stream (OpenAI and Groq). Tool
    call arguments arrive incrementally per tool-call index and are
    concatenated across chunks, then parsed once the stream ends."""
    calls = {}  # index -> {"id","name","arguments"}
    try:
        with resp:
            for line in resp.iter_lines(decode_unicode=True):
                if not line or not line.startswith("data:"):
                    continue
                data = line[len("data:"):].strip()
                if data == "[DONE]":
                    break
                try:
                    obj = json.loads(data)
                    delta = obj["choices"][0]["delta"]
                except (json.JSONDecodeError, KeyError, IndexError, TypeError):
                    continue
                content = delta.get("content") if isinstance(delta, dict) else None
                if content:
                    yield content
                for tc_delta in (delta.get("tool_calls") or []):
                    idx = tc_delta.get("index", 0)
                    entry = calls.setdefault(idx, {"id": None, "name": None, "arguments": ""})
                    if tc_delta.get("id"):
                        entry["id"] = tc_delta["id"]
                    fn = tc_delta.get("function") or {}
                    if fn.get("name"):
                        entry["name"] = fn["name"]
                    if fn.get("arguments"):
                        entry["arguments"] += fn["arguments"]
    except requests.exceptions.RequestException as e:
        raise ProviderError(provider, f"{provider}: connection dropped mid-response -- {type(e).__name__}.") from e

    for idx in sorted(calls):
        entry = calls[idx]
        result.feed_tool_call({
            "id": entry["id"] or f"call_{idx}",
            "name": entry["name"],
            "input": _json_loads(entry["arguments"]),
        })


def call_groq(model, messages, system=SYSTEM_PROMPT, tools=None, max_tokens=None):
    return _openai_style_call("groq", "https://api.groq.com/openai/v1", model, messages, system, tools, max_tokens)


def call_groq_stream(model, messages, system=SYSTEM_PROMPT, tools=None, max_tokens=None):
    return _openai_style_call_stream("groq", "https://api.groq.com/openai/v1", model, messages, system, tools, max_tokens)


def call_openai(model, messages, system=SYSTEM_PROMPT, tools=None, max_tokens=None):
    return _openai_style_call("openai", "https://api.openai.com/v1", model, messages, system, tools, max_tokens)


def call_openai_stream(model, messages, system=SYSTEM_PROMPT, tools=None, max_tokens=None):
    return _openai_style_call_stream("openai", "https://api.openai.com/v1", model, messages, system, tools, max_tokens)


# ---------------------------------------------------------------------------
# Gemini
# ---------------------------------------------------------------------------

def call_gemini(model, messages, system=SYSTEM_PROMPT, tools=None, max_tokens=None):
    key = _get_key("gemini")
    payload = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": _to_gemini_contents(messages),
    }
    if tools:
        payload["tools"] = _gemini_tools(tools)
    if max_tokens is not None:
        payload["generationConfig"] = {"maxOutputTokens": max_tokens}
    resp = _post(
        "gemini", model, f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        headers={"x-goog-api-key": key, "Content-Type": "application/json"},
        json=payload, timeout=60,
    )
    data = _safe_json("gemini", resp)
    candidates = data.get("candidates") or []
    result = StreamResult(iter(()))
    if not candidates:
        return result
    parts = candidates[0].get("content", {}).get("parts", [])
    text_parts, tool_calls = [], []
    for i, part in enumerate(parts):
        if "text" in part:
            text_parts.append(part["text"])
        fc = part.get("functionCall")
        if fc:
            tool_calls.append({"id": f"gemini-{i}", "name": fc["name"], "input": fc.get("args", {})})
    result.content = "\n".join(text_parts)
    result.tool_calls = tool_calls
    return result


def call_gemini_stream(model, messages, system=SYSTEM_PROMPT, tools=None, max_tokens=None):
    """Gemini's function-calling SSE shape is less uniform to safely
    incrementally parse without live testing against the real API, so
    this performs the buffered call and yields the whole reply as one
    chunk -- the caller sees the same StreamResult interface either way,
    just without token-by-token rendering for this provider."""
    result = call_gemini(model, messages, system=system, tools=tools, max_tokens=max_tokens)
    text = result.content

    def _gen():
        if text:
            yield text

    wrapped = StreamResult(_gen())
    wrapped.tool_calls = result.tool_calls
    return wrapped


# ---------------------------------------------------------------------------
# Ollama (local, no key, no tool-calling)
# ---------------------------------------------------------------------------

def call_ollama(model, messages, system=SYSTEM_PROMPT, tools=None, max_tokens=None):
    prompt_parts = [system, ""]
    for m in messages:
        if m["role"] == "user":
            prompt_parts.append(f"User: {m['content']}")
        elif m["role"] == "assistant":
            prompt_parts.append(f"Assistant: {m.get('content', '')}")
        elif m["role"] == "tool_result":
            prompt_parts.append(f"[tool result for {m['name']}]: {m['content']}")
    prompt = "\n".join(prompt_parts)
    try:
        r = requests.post(
            "http://localhost:11434/api/generate",
            json={"model": model, "prompt": prompt, "stream": False},
            timeout=60,
        )
        r.raise_for_status()
        text = r.json().get("response", "")
    except requests.RequestException as e:
        raise ProviderError("ollama", f"ollama: request failed (is `ollama serve` running?): {e}") from e
    result = StreamResult(iter(()))
    result.content = text
    return result


def call_ollama_stream(model, messages, system=SYSTEM_PROMPT, tools=None, max_tokens=None):
    result = call_ollama(model, messages, system=system, tools=tools, max_tokens=max_tokens)
    text = result.content

    def _gen():
        if text:
            yield text

    return StreamResult(_gen())


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

ADAPTERS = {
    "anthropic": {"call": call_anthropic, "call_stream": call_anthropic_stream, "supports_tools": True},
    "openai":    {"call": call_openai,    "call_stream": call_openai_stream,    "supports_tools": True},
    "groq":      {"call": call_groq,      "call_stream": call_groq_stream,      "supports_tools": True},
    "gemini":    {"call": call_gemini,    "call_stream": call_gemini_stream,    "supports_tools": True},
    "ollama":    {"call": call_ollama,    "call_stream": call_ollama_stream,    "supports_tools": False},
}


def call(provider: str, model: str, messages: list, system: str = SYSTEM_PROMPT, tools=None, max_tokens=None) -> StreamResult:
    """Buffered call -- used for context-compaction summaries, which are
    never shown to the user, so there's nothing to gain from streaming."""
    adapter = ADAPTERS[provider]["call"]
    use_tools = tools if ADAPTERS[provider]["supports_tools"] else None
    return adapter(model, messages, system=system, tools=use_tools, max_tokens=max_tokens)


def call_stream(provider: str, model: str, messages: list, system: str = SYSTEM_PROMPT, tools=None, max_tokens=None) -> StreamResult:
    """Streaming call -- used for normal chat turns."""
    adapter = ADAPTERS[provider]["call_stream"]
    use_tools = tools if ADAPTERS[provider]["supports_tools"] else None
    return adapter(model, messages, system=system, tools=use_tools, max_tokens=max_tokens)
