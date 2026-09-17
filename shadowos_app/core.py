"""
Main application loop. SessionState holds everything that can change
during a run and is worth remembering for next time (active provider,
each provider's current model, persona, banner color, input mode,
working directory, and which saved chat -- if any -- is loaded);
conversation content itself lives in memory.py, not here.

Two input modes: in AI mode (default) everything typed goes to
_handle_chat_turn, which may loop through several tool calls before the
model gives its final reply. In shell mode, everything typed runs
directly as a shell command -- no model call, no tokens. Either mode,
prefixing a line with `!` runs it as a shell command immediately without
changing the mode.
"""

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from rich.markup import escape

from . import commands
from . import config
from . import memory
from . import productivity
from . import providers
from . import tools
from . import ui

__version__ = "1.0.0"


@dataclass
class SessionState:
    provider: str
    models: dict
    persona: str = "default"
    banner_color: str = "random"
    mode: str = "ai"
    cwd: str = field(default_factory=lambda: str(Path.home()))
    active_chat_name: Optional[str] = None


def _state_from_config() -> SessionState:
    raw = config.load_state()
    return SessionState(
        provider=raw["provider"],
        models=raw["models"],
        persona=raw["persona"],
        banner_color=raw["banner_color"],
        mode=raw["mode"],
        cwd=raw["cwd"],
        active_chat_name=raw.get("active_chat_name"),
    )


def _persist(state: SessionState) -> None:
    config.save_state(vars(state))


def _check_due_reminders() -> None:
    due = productivity.due_reminders()
    if not due:
        return
    ui.console.print("[yellow]You have reminders due:[/yellow]")
    for r in due:
        ui.console.print(f"  - {escape(r['text'])}  (was due {escape(r['when'])})")
        productivity.mark_fired(r["id"])
    ui.console.print()


def _truncate(text: str, limit: int = 400) -> str:
    text = text or ""
    return text if len(text) <= limit else text[:limit] + "... [truncated]"


def _confirm(session, prompt_text: str) -> bool:
    try:
        answer = session.prompt(prompt_text).strip().lower()
    except (EOFError, KeyboardInterrupt):
        answer = "n"
    return answer in ("y", "yes")


def _run_shell_direct(cwd: str, command: str, session) -> None:
    """Run a shell command with zero AI involvement -- no request sent,
    no tokens spent. Same danger confirmation as the AI's own tool calls."""
    if not command:
        return
    if tools.is_dangerous(command):
        ui.console.print(f"[yellow][!] This looks destructive: {escape(command)}[/yellow]")
        if not _confirm(session, "    Allow? [y/N] "):
            ui.console.print("[dim]Cancelled.[/dim]")
            return
    result = tools.execute_tool(cwd, "run_command", {"command": command})
    ui.console.print(result, markup=False)


def _execute_tool_call(cwd: str, tool_call: dict, session) -> str:
    name, args = tool_call["name"], tool_call.get("input") or {}
    if name == "run_command" and tools.is_dangerous(args.get("command", "")):
        ui.console.print(f"[yellow][!] Model wants to run: {escape(str(args.get('command')))}[/yellow]")
        if not _confirm(session, "    Allow? [y/N] "):
            return "[user declined to run this command]"
    return tools.execute_tool(cwd, name, args)


def _effective_system_prompt(state: SessionState) -> str:
    persona_prompt = config.PERSONAS.get(state.persona, config.SYSTEM_PROMPT)
    if memory.get_summary():
        return f"{persona_prompt}\n\nSummary of earlier conversation:\n{memory.get_summary()}"
    return persona_prompt


def _handle_chat_turn(user_text: str, state: SessionState, session) -> None:
    memory.history.append({"role": "user", "content": user_text})
    memory.compact_if_needed(state.provider, state.models[state.provider])

    system = _effective_system_prompt(state)
    wire_messages = list(memory.history)
    tool_call_log = []
    final_text = ""
    model = state.models[state.provider]

    for _ in range(config.MAX_TOOL_ITERATIONS):
        try:
            with ui.thinking_status(state.provider, model):
                stream = providers.call_stream(
                    state.provider, model, wire_messages,
                    system=system, tools=tools.TOOL_SCHEMAS,
                )
            text = ui.stream_reply(state.provider, stream)
        except (providers.ProviderError, RuntimeError) as e:
            ui.print_error(e)
            memory.history.append({"role": "assistant", "content": f"[error: {e}]"})
            return

        final_text = text

        if not stream.tool_calls:
            break

        wire_messages.append({"role": "assistant", "content": text, "tool_calls": stream.tool_calls})
        for tc in stream.tool_calls:
            ui.print_tool_call(tc["name"], tc.get("input") or {})
            result = _execute_tool_call(state.cwd, tc, session)
            ui.print_dim(_truncate(result))
            wire_messages.append({
                "role": "tool_result", "tool_call_id": tc["id"], "name": tc["name"], "content": result,
            })
            tool_call_log.append(f"[ran {tc['name']}({tc.get('input') or {}}) -> {_truncate(result, 200)}]")
    else:
        ui.print_warning(f"Stopped after {config.MAX_TOOL_ITERATIONS} tool iterations -- say 'continue' for more.")

    assistant_record = final_text
    if tool_call_log:
        joined = "\n".join(tool_call_log)
        assistant_record = f"{joined}\n\n{final_text}" if final_text else joined
    memory.history.append({"role": "assistant", "content": assistant_record})


def main() -> None:
    if "--version" in sys.argv or "-v" in sys.argv:
        print(f"ShadowOS {__version__}")
        return

    state = _state_from_config()

    ui.print_banner(state.banner_color)
    ui.console.print("[dim]/help for commands, /exit to quit.[/dim]\n")
    ui.console.print(
        f"Provider: {state.provider} ({escape(state.models[state.provider])})   "
        f"persona: {state.persona}   mode: {state.mode}   cwd: {escape(state.cwd)}\n"
    )
    _check_due_reminders()

    session = ui.make_session()

    while True:
        prompt_label = "$ " if state.mode == "shell" else "you> "
        try:
            line = session.prompt(prompt_label).strip()
        except (EOFError, KeyboardInterrupt):
            ui.console.print("\n[dim]Goodbye.[/dim]")
            break
        if not line:
            continue

        if line.startswith("/"):
            parts = line.split()
            keep_going = commands.dispatch(parts[0].lower(), parts, state, session)
            _persist(state)
            if not keep_going:
                break
            continue

        if line.startswith("!"):
            _run_shell_direct(state.cwd, line[1:].strip(), session)
            _persist(state)
            continue

        if state.mode == "shell":
            _run_shell_direct(state.cwd, line, session)
            _persist(state)
            continue

        _handle_chat_turn(line, state, session)
        _persist(state)


if __name__ == "__main__":
    main()
