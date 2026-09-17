"""
Shared conversation memory: the raw message history plus a running summary
of everything older than the retained window (context-compaction), so the
full transcript is never sent forever.

`history` is a module-level mutable list -- other modules import this
module (not individual names from it) and mutate `memory.history` in
place. The running summary is exposed via get_summary()/set_summary()
rather than a plain attribute, since plain strings get reassigned (not
mutated in place) and a `from memory import conversation_summary`
elsewhere would go stale the moment this module reassigns it.

Only plain {"role": "user"/"assistant", "content": str} entries live
here -- tool calls/results that happen *within* a turn are handled by
core.py's turn loop using a separate, richer wire-format list, and only
the final clean text is folded back into this history once the turn
ends. That keeps saved chats, compaction, and /context all working from
one simple, portable shape.
"""

from . import providers
from . import ui
from .config import CONTEXT_CONFIG

history = []

_conversation_summary = ""

# Tracks whether we've already warned that the retained verbatim window
# alone exceeds the token budget. Reset by reset() and whenever real
# compaction succeeds, so the warning can fire again if it recurs.
_recent_overflow_warned = False

SUMMARIZER_SYSTEM_PROMPT = (
    "You compress conversation history. You will be given an optional previous "
    "summary and a block of older conversation turns. Produce ONE updated summary "
    "that preserves: (1) important facts and information shared, (2) decisions "
    "made, (3) stated preferences, (4) ongoing or unfinished tasks and their "
    "status, including any tool actions taken (files touched, commands run, "
    "notes/tasks/reminders created). Be concise and factual -- plain prose or "
    "tight bullet points, no commentary, no meta remarks about being a summary."
)


def get_summary() -> str:
    return _conversation_summary


def set_summary(text: str) -> None:
    global _conversation_summary
    _conversation_summary = text or ""


def clear_overflow_warning() -> None:
    global _recent_overflow_warned
    _recent_overflow_warned = False


def reset() -> None:
    """Wipe history and summary state. Used by /clear and /new."""
    global _conversation_summary
    history.clear()
    _conversation_summary = ""
    clear_overflow_warning()


def _estimate_tokens(text: str) -> int:
    """Rough, provider-agnostic token estimate (~4 characters/token). Not
    billed anywhere -- only used to decide *when* to compact."""
    return max(1, len(text or "") // 4)


def _messages_token_estimate(messages: list) -> int:
    return sum(_estimate_tokens(m.get("content", "")) for m in messages)


def history_token_estimate() -> int:
    return _messages_token_estimate(history)


def summary_token_estimate() -> int:
    return _estimate_tokens(_conversation_summary)


def context_token_estimate() -> int:
    return summary_token_estimate() + history_token_estimate()


def compact_if_needed(provider: str, model: str) -> None:
    """Summarize everything except the most recent messages once the
    tracked conversation crosses CONTEXT_CONFIG['max_tokens']. Safe to
    call every turn -- a no-op until the budget is exceeded. If the
    summarization call itself fails, history is left untouched (retried
    next turn rather than losing messages or crashing the chat loop).
    Runs through the same adapter interface as a normal turn, so it needs
    no provider-specific code and never depends on a second key."""
    global _conversation_summary, _recent_overflow_warned

    keep = CONTEXT_CONFIG["keep_recent_messages"]
    if len(history) <= keep:
        if context_token_estimate() >= CONTEXT_CONFIG["max_tokens"]:
            if not _recent_overflow_warned:
                ui.print_warning(
                    f"context: the {len(history)} retained message(s) are "
                    f"~{context_token_estimate()} tokens on their own, at/over the "
                    f"{CONTEXT_CONFIG['max_tokens']}-token compaction threshold. There's "
                    f"nothing older left to summarize -- keep_recent_messages is a hard "
                    f"floor. Consider /clear, or avoid pasting very long blocks."
                )
                _recent_overflow_warned = True
        else:
            _recent_overflow_warned = False
        return
    if context_token_estimate() < CONTEXT_CONFIG["max_tokens"]:
        return

    to_summarize = history[:-keep]
    recent = history[-keep:]

    transcript = "\n".join(f"{m['role']}: {m['content']}" for m in to_summarize)
    prior = f"Previous summary:\n{_conversation_summary}\n\n" if _conversation_summary else ""
    prompt = (
        f"{prior}Older conversation turns to fold into the summary:\n\n{transcript}\n\n"
        f"Produce the updated running summary now."
    )

    try:
        result = providers.call(
            provider, model, [{"role": "user", "content": prompt}],
            system=SUMMARIZER_SYSTEM_PROMPT,
            max_tokens=CONTEXT_CONFIG["summary_max_tokens"],
        )
    except (providers.ProviderError, RuntimeError):
        return  # try again next turn instead of losing or corrupting history

    _conversation_summary = result.content.strip()
    history[:] = recent
    _recent_overflow_warned = False
    ui.print_dim(
        f"\u21ba context compacted: folded {len(to_summarize)} older message(s) "
        f"into the running summary, kept the last {len(recent)} verbatim."
    )
