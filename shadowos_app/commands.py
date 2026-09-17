"""
Slash-command handling: parses each /command and mutates session state
(active provider, per-provider models, persona, mode, cwd, loaded chat
name -- see core.SessionState), conversation memory (memory.py), and
saved chats (storage.py). All command-related user-facing text lives
here.
"""

import os
from pathlib import Path

from rich.markup import escape

from . import config
from . import dashboard
from . import memory
from . import productivity
from . import storage
from . import tools
from . import ui


def dispatch(cmd, parts, state, session) -> bool:
    """Handle one slash command (`cmd` is parts[0].lower()). `state` is
    the running core.SessionState; `session` is the prompt_toolkit
    PromptSession (only /clear's confirmation prompt needs it). Returns
    False if the loop should stop (/exit), True otherwise."""

    if cmd in ("/exit", "/quit"):
        ui.console.print("[dim]Exiting ShadowOS.[/dim]")
        return False
    elif cmd == "/help":
        ui.print_help()
    elif cmd == "/clear":
        _handle_clear(parts, state, session)
    elif cmd == "/new":
        _wipe_conversation(state)
        ui.console.print("[yellow]Started a new conversation.[/yellow]")
    elif cmd == "/save":
        _handle_save(parts, state)
    elif cmd == "/load":
        _handle_load(parts, state)
    elif cmd == "/chats":
        _handle_chats(state)
    elif cmd == "/delete":
        _handle_delete(parts, state)
    elif cmd == "/rename":
        _handle_rename(parts, state)
    elif cmd == "/models":
        _handle_models(state)
    elif cmd == "/persona":
        _handle_persona(parts, state)
    elif cmd == "/status":
        _handle_status(state)
    elif cmd == "/context":
        _handle_context(state)
    elif cmd == "/model":
        _handle_model(parts, state)
    elif cmd == "/notes":
        _handle_notes(parts)
    elif cmd == "/tasks":
        _handle_tasks(parts)
    elif cmd == "/reminders":
        _handle_reminders(parts)
    elif cmd == "/dashboard":
        dashboard.render(state.provider, state.models[state.provider], state.cwd)
    elif cmd == "/color":
        _handle_color(parts, state)
    elif cmd == "/env":
        _handle_env()
    elif cmd == "/ai":
        state.mode = "ai"
        ui.print_success("Mode: AI chat -- your input goes to the model.")
    elif cmd == "/shell":
        state.mode = "shell"
        ui.print_success("Mode: raw shell -- your input runs directly, no AI, no tokens spent. /ai to switch back.")
    elif cmd == "/cd":
        _handle_cd(parts, state)
    else:
        ui.console.print("[red]Unknown command.[/red] Type /help.")
    return True


def _report_storage_error(e) -> None:
    hint = f" {escape(e.hint)}" if e.hint else ""
    ui.console.print(f"[red]{escape(str(e))}[/red]{hint}")


def _wipe_conversation(state) -> None:
    memory.reset()
    state.active_chat_name = None


def _handle_clear(parts, state, session) -> None:
    force = len(parts) >= 2 and parts[1].lower() == "--force"
    if not memory.history and not memory.get_summary():
        ui.console.print("[dim]Conversation memory is already empty.[/dim]")
    elif force:
        _wipe_conversation(state)
        ui.console.print("[yellow]Conversation memory cleared.[/yellow]")
    else:
        try:
            confirm = session.prompt(
                f"Clear {len(memory.history)} message(s)? This can't be undone. [y/N] "
            ).strip().lower()
        except (EOFError, KeyboardInterrupt):
            confirm = "n"
        if confirm in ("y", "yes"):
            _wipe_conversation(state)
            ui.console.print("[yellow]Conversation memory cleared.[/yellow]")
        else:
            ui.console.print("[dim]Cancelled -- memory not cleared.[/dim]")


def _handle_save(parts, state) -> None:
    if len(parts) < 2:
        ui.console.print("[red]Usage:[/red] /save <name>  (letters, numbers, - and _ only)")
        return
    try:
        safe, overwriting = storage.save_chat(
            parts[1],
            provider=state.provider,
            models=state.models,
            persona=state.persona,
            conversation_summary=memory.get_summary(),
            history=memory.history,
        )
    except storage.StorageError as e:
        _report_storage_error(e)
        return
    state.active_chat_name = safe
    verb = "Overwrote" if overwriting else "Saved"
    ui.console.print(f"[green]{verb} '{safe}' ({len(memory.history)} message(s)).[/green]")


def _handle_load(parts, state) -> None:
    if len(parts) < 2:
        ui.console.print("[red]Usage:[/red] /load <name>")
        return
    try:
        data = storage.load_chat(parts[1])
    except storage.StorageError as e:
        _report_storage_error(e)
        return

    loaded_history = data.get("history", [])
    if not isinstance(loaded_history, list):
        loaded_history = []
    memory.history[:] = loaded_history
    memory.set_summary(data.get("conversation_summary", ""))
    memory.clear_overflow_warning()

    loaded_provider = data.get("provider")
    if loaded_provider in config.PROVIDER_CONFIG:
        state.provider = loaded_provider
    else:
        ui.console.print(
            f"[yellow]Saved provider '{escape(str(loaded_provider))}' isn't available anymore "
            f"-- staying on {state.provider}.[/yellow]"
        )

    loaded_persona = data.get("persona", "default")
    if loaded_persona in config.PERSONAS:
        state.persona = loaded_persona
    else:
        ui.console.print(
            f"[yellow]Saved persona '{escape(str(loaded_persona))}' isn't available anymore "
            f"-- staying on '{state.persona}'.[/yellow]"
        )

    for p, m in (data.get("models") or {}).items():
        if p in state.models and isinstance(m, str) and m:
            state.models[p] = m

    safe = storage.sanitize_name(parts[1])
    state.active_chat_name = safe
    ui.console.print(
        f"[green]Loaded '{safe}' -- {len(memory.history)} message(s), "
        f"provider {state.provider} ({escape(state.models[state.provider])}), "
        f"saved {escape(str(data.get('saved_at', '?')))}.[/green]"
    )


def _handle_chats(state) -> None:
    rows = storage.list_chats()
    if not rows:
        ui.console.print("[dim]No saved chats yet. Use /save <name>.[/dim]")
        return
    lines = ["\n[bold]Saved chats[/bold]"]
    for name, saved_at, provider, msg_count in rows:
        marker = "[bold]*[/bold]" if name == state.active_chat_name else " "
        lines.append(
            f" {marker} [cyan]{name}[/cyan]  {escape(str(saved_at))}  {escape(str(provider))}  {msg_count} msg(s)"
        )
    ui.console.print("\n".join(lines) + "\n")


def _handle_delete(parts, state) -> None:
    if len(parts) < 2:
        ui.console.print("[red]Usage:[/red] /delete <name>")
        return
    try:
        safe = storage.delete_chat(parts[1])
    except storage.StorageError as e:
        _report_storage_error(e)
        return
    if state.active_chat_name == safe:
        state.active_chat_name = None
    ui.console.print(f"[yellow]Deleted '{safe}'.[/yellow]")


def _handle_rename(parts, state) -> None:
    if len(parts) < 2:
        ui.console.print("[red]Usage:[/red] /rename <new-name>  (renames the active saved chat)")
        return
    if not state.active_chat_name:
        ui.console.print("[red]No active saved chat to rename.[/red] /save or /load one first, then /rename.")
        return
    try:
        new_safe = storage.rename_chat(state.active_chat_name, parts[1])
    except storage.StorageError as e:
        _report_storage_error(e)
        return
    state.active_chat_name = new_safe
    ui.console.print(f"[green]Renamed to '{new_safe}'.[/green]")


def _handle_context(state) -> None:
    keep = config.CONTEXT_CONFIG["keep_recent_messages"]
    limit = config.CONTEXT_CONFIG["max_tokens"]
    history_tokens = memory.history_token_estimate()
    summary_tokens = memory.summary_token_estimate() if memory.get_summary() else 0
    total = history_tokens + summary_tokens
    pct = (total / limit * 100) if limit else 0

    lines = ["\n[bold]Context usage[/bold] [dim](estimated, ~4 chars/token -- not billed anywhere)[/dim]"]
    lines.append(f" Messages:  {len(memory.history)} raw message(s) kept verbatim (floor: {keep})")
    lines.append(f" History:   ~{history_tokens} tokens")
    lines.append(f" Summary:   {'~' + str(summary_tokens) + ' tokens' if memory.get_summary() else 'empty'}")
    lines.append(f" Total:     ~{total} / {limit} tokens (~{pct:.0f}%)")

    if total >= limit:
        lines.append(
            "\n[yellow]At/over the compaction limit -- older messages will be summarized "
            "on the next turn (unless the retained window itself is already at the "
            "limit; /new or /clear help there).[/yellow]"
        )
    ui.console.print("\n".join(lines) + "\n")


def _handle_persona(parts, state) -> None:
    if len(parts) < 2:
        lines = ["\n[bold]Personas[/bold]"]
        for name, prompt in config.PERSONAS.items():
            marker = "[bold]*[/bold]" if name == state.persona else " "
            preview = prompt if len(prompt) <= 70 else prompt[:67] + "..."
            lines.append(f" {marker} [cyan]{name}[/cyan]: [dim]{preview}[/dim]")
        lines.append("\n[dim]/persona <name>    switch persona, conversation memory is preserved[/dim]\n")
        ui.console.print("\n".join(lines))
        return

    name = parts[1]
    if name not in config.PERSONAS:
        ui.console.print(f"[red]Unknown persona '{name}'.[/red] Available: {', '.join(config.PERSONAS)}")
        return
    if name == state.persona:
        ui.console.print(f"[dim]Already using '{name}'.[/dim]")
        return

    state.persona = name
    ui.console.print(f"[green]Switched to '{name}' persona. Conversation memory preserved.[/green]")


def _handle_status(state) -> None:
    lines = ["\n[bold]Status[/bold]"]
    lines.append(f" Active provider: [cyan]{state.provider}[/cyan]")
    lines.append(f" Active model:    {escape(state.models[state.provider])}")
    lines.append(f" Active persona:  [cyan]{state.persona}[/cyan]")
    lines.append(f" Mode:            {state.mode}   Working dir: {escape(state.cwd)}")
    lines.append(f" Messages:        {len(memory.history)} raw message(s) kept verbatim")
    summary_state = "present" if memory.get_summary() else "empty"
    lines.append(
        f" Context:         ~{memory.context_token_estimate()} tokens tracked "
        f"(compacts at ~{config.CONTEXT_CONFIG['max_tokens']}), summary {summary_state}"
    )

    lines.append("\n[bold]Providers[/bold]")
    for p in config.PROVIDERS:
        env_var = config.PROVIDER_CONFIG[p]["api_key_env"]
        marker = "[bold]*[/bold]" if p == state.provider else " "
        if not env_var:
            status_text = "[green]no key needed[/green]"
        elif os.environ.get(env_var):
            status_text = "[green]configured[/green]"
        else:
            status_text = f"[red]missing[/red] ({env_var})"
        lines.append(f" {marker} [cyan]{p}[/cyan]: {status_text}")
    lines.append("\n[dim]Run /env for a deeper key check (masked values, placeholder detection).[/dim]")

    ui.console.print("\n".join(lines) + "\n")


def _handle_models(state) -> None:
    lines = ["\n[bold]Models[/bold]"]
    for p in config.PROVIDERS:
        marker = "[bold]*[/bold]" if p == state.provider else " "
        default = config.PROVIDER_CONFIG[p]["default_model"]
        note = "" if state.models[p] == default else f"  [dim](default: {default})[/dim]"
        lines.append(f" {marker} [cyan]{p}[/cyan]: {escape(state.models[p])}{note}")
    lines.append(
        "\n[dim]/model <provider>            switch, keep its model\n"
        "/model <provider> <model>    switch and set its model[/dim]\n"
    )
    summary_state = "present" if memory.get_summary() else "empty"
    lines.append(
        f"[dim]Context: {len(memory.history)} raw message(s) kept verbatim, "
        f"summary {summary_state} (~{memory.context_token_estimate()} tokens tracked, "
        f"compacts at ~{config.CONTEXT_CONFIG['max_tokens']})[/dim]\n"
    )
    ui.console.print("\n".join(lines))


def _handle_model(parts, state) -> None:
    if len(parts) < 2 or parts[1] not in config.PROVIDERS:
        ui.console.print(f"[red]Usage:[/red] /model <{'|'.join(config.PROVIDERS)}> [model]")
        return
    state.provider = parts[1]
    if len(parts) >= 3:
        state.models[state.provider] = " ".join(parts[2:])
    ui.console.print(
        f"[green]Switched to {state.provider} ({escape(state.models[state.provider])}). Memory preserved.[/green]"
    )


def _handle_notes(parts) -> None:
    tag = parts[1] if len(parts) >= 2 else ""
    ui.console.print(tools._fmt_notes(productivity.list_notes(tag)), markup=False)


def _handle_tasks(parts) -> None:
    include_done = len(parts) >= 2 and parts[1].lower() == "all"
    ui.console.print(tools._fmt_tasks(productivity.list_tasks(include_done=include_done)), markup=False)


def _handle_reminders(parts) -> None:
    include_fired = len(parts) >= 2 and parts[1].lower() == "all"
    ui.console.print(tools._fmt_reminders(productivity.list_reminders(include_fired=include_fired)), markup=False)


def _handle_color(parts, state) -> None:
    name = parts[1].lower() if len(parts) >= 2 else "random"
    if name != "random" and name not in config.BANNER_COLOR_NAMES:
        ui.console.print(
            f"[red]Unknown color '{escape(name)}'.[/red] Options: {', '.join(config.BANNER_COLOR_NAMES)}, random"
        )
        return
    state.banner_color = name
    ui.print_banner(name)
    ui.console.print(f"Banner color set to {name}.")


def _handle_env() -> None:
    diag = config.env_diagnostics()
    lines = [
        f"  .env file:  {diag['env_file']}",
        f"  exists:     {'yes' if diag['env_file_exists'] else 'no — copy .env.example to .env here'}",
    ]
    for row in diag["vars"]:
        if row["placeholder"]:
            status = "[yellow]SET, but still looks like the example placeholder[/yellow]"
        elif row["set"]:
            status = f"[green]set[/green] ({escape(row['masked'])})"
        else:
            status = "[dim]not set[/dim]"
        lines.append(f"  {row['var']:20s} {status}")
    ui.console.print("\n".join(lines))


def _handle_cd(parts, state) -> None:
    target = Path(parts[1] if len(parts) >= 2 else "~").expanduser()
    if not target.is_absolute():
        target = Path(state.cwd) / target
    if target.is_dir():
        state.cwd = str(target.resolve())
        ui.console.print(f"cwd: {escape(state.cwd)}")
    else:
        ui.console.print(f"[red]No such directory:[/red] {escape(str(target))}")
