"""
Terminal UI: the Rich console, the prompt_toolkit input session, and all
output formatting. This module holds no application/conversation state --
it only renders what it's given and reads what the user types.
"""

import random

from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.history import FileHistory
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.markup import escape

from . import config

console = Console()


def make_session() -> PromptSession:
    """Build the interactive input session: tab-completes commands,
    provider names, and persona names, and persists input history across
    runs (arrow keys)."""
    completer = WordCompleter(
        config.COMMANDS + config.PROVIDERS + list(config.PERSONAS) + config.BANNER_COLOR_NAMES,
        ignore_case=True,
    )
    return PromptSession(
        history=FileHistory(str(config.HISTORY_FILE)),
        auto_suggest=AutoSuggestFromHistory(),
        completer=completer,
        complete_while_typing=True,
    )


# ------------------------------------------------------------------ Banner

def print_banner(color_name: str = "random") -> None:
    """Prints 'ShadowOS' in a colored box. 'random' picks a fresh color
    from BANNER_COLOR_NAMES each launch; any other name pins it."""
    color = random.choice(config.BANNER_COLOR_NAMES) if color_name == "random" else color_name
    if color not in config.BANNER_COLOR_NAMES:
        color = "cyan"
    title = "ShadowOS"
    width = 30
    pad_left = (width - len(title)) // 2
    pad_right = width - len(title) - pad_left
    top = "╔" + "═" * width + "╗"
    mid = "║" + " " * pad_left + title + " " * pad_right + "║"
    bottom = "╚" + "═" * width + "╝"
    for line in (top, mid, bottom):
        console.print(f"[bold {color}]{line}[/bold {color}]")


# ------------------------------------------------------------------- Help

def print_help() -> None:
    console.print(
        "\n[bold]Commands[/bold]\n"
        f"  [cyan]/model[/cyan] <{'|'.join(config.PROVIDERS)}>            switch provider, keep its current model\n"
        "  [cyan]/model[/cyan] <provider> <model>       switch provider and set its model\n"
        "  [cyan]/models[/cyan]                          list providers, active model, and context status\n"
        "  [cyan]/persona[/cyan]                         list personas (default/developer/research/concise)\n"
        "  [cyan]/persona[/cyan] <name>                  switch persona, conversation memory preserved\n"
        "  [cyan]/status[/cyan]                          active provider/model, message count, context, key status\n"
        "  [cyan]/context[/cyan]                         detailed estimated token usage vs. the context limit\n"
        "  [cyan]/new[/cyan]                             start a fresh, unsaved conversation\n"
        "  [cyan]/clear[/cyan]                           wipe conversation memory (asks to confirm)\n"
        "  [cyan]/clear[/cyan] --force                  wipe immediately, no confirmation\n"
        "  [cyan]/save[/cyan] <name>                     save the current conversation to disk\n"
        "  [cyan]/load[/cyan] <name>                     load a saved conversation (replaces current)\n"
        "  [cyan]/chats[/cyan]                            list saved conversations\n"
        "  [cyan]/delete[/cyan] <name>                   delete a saved conversation\n"
        "  [cyan]/rename[/cyan] <name>                   rename the active saved conversation\n"
        "  [cyan]/notes[/cyan] [tag]                     list saved notes\n"
        "  [cyan]/tasks[/cyan] [all]                     list open tasks (all = include done)\n"
        "  [cyan]/reminders[/cyan] [all]                 list upcoming reminders (all = include past)\n"
        "  [cyan]/dashboard[/cyan]                        snapshot: model, disk, battery, notes/tasks/reminders\n"
        "  [cyan]/color[/cyan] <name>                    change banner color, or 'random'\n"
        "  [cyan]/cd[/cyan] <path>                       change working directory for shell/file tools\n"
        "  [cyan]/ai[/cyan]                              switch to AI chat mode (default)\n"
        "  [cyan]/shell[/cyan]                            switch to raw shell mode -- input runs directly, no AI\n"
        "  [cyan]/env[/cyan]                              check which API keys are loaded, without a request\n"
        "  [cyan]/help[/cyan]                            show this message\n"
        "  [cyan]/exit[/cyan]                            quit ShadowOS\n"
        "\n[dim]Tip: arrow keys move the cursor / recall history. Tab completes commands.\n"
        "Prefix a line with ! to run one shell command directly, in any mode.\n"
        "Older messages are auto-summarized as the conversation grows -- see /context.\n"
        "Chat names: letters, numbers, - and _ only.[/dim]\n"
    )


# --------------------------------------------------------------- Chat turn

def thinking_status(provider: str, model: str):
    """Context manager: shows a spinner while a provider call is in
    flight. For a streaming reply this only covers setup (key lookup,
    opening the connection, and a blocking compaction call if
    triggered) -- Rich only supports one Live display at a time, and
    stream_reply's own Live display takes over once tokens start
    arriving."""
    return console.status(f"[dim]{provider} ({model}) is thinking...[/dim]", spinner="dots")


def stream_reply(provider: str, chunks) -> str:
    """Render a streaming reply live as it arrives: prints the provider's
    colored header once, then re-renders the accumulated text as Markdown
    each time a new chunk comes in. Returns the full reply text.

    If nothing streamed (a tool-only turn with no text), prints nothing
    beyond the header and returns "" -- core.py handles the tool-call
    reporting separately."""
    color = config.PROVIDER_CONFIG.get(provider, {}).get("color", "white")
    console.print(f"[bold {color}]{provider}>[/bold {color}]")
    text = ""
    with Live("", console=console, refresh_per_second=12, transient=False) as live:
        for chunk in chunks:
            text += chunk
            live.update(Markdown(text) if text else "")
    console.print()
    return text


def print_tool_call(name: str, args: dict) -> None:
    console.print(f"  [dim]-> {name}({escape(str(args))})[/dim]")


def print_error(message) -> None:
    console.print(f"[red]{escape('[error] ' + str(message))}[/red]")


def print_warning(message) -> None:
    console.print(f"[yellow]\u26a0 {escape(str(message))}[/yellow]")


def print_dim(message) -> None:
    console.print(f"[dim]{escape(str(message))}[/dim]")


def print_success(message) -> None:
    console.print(f"[green]{escape(str(message))}[/green]")
