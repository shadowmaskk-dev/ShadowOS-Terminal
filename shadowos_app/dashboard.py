"""
Dashboard: a one-screen snapshot of ShadowOS state -- active provider/
model, working directory, disk usage, and a summary of notes/tasks/
reminders. Battery info is included when Termux:API is installed.
"""

import json
import shutil
import subprocess
from datetime import datetime

from . import productivity
from .ui import console
from rich.markup import escape

_PRIORITY_ORDER = {"high": 0, "normal": 1, "low": 2}


def _human_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.0f}{unit}"
        n /= 1024
    return f"{n:.0f}PB"


def _disk_usage(path: str) -> str:
    try:
        total, used, _free = shutil.disk_usage(path)
        pct = (used / total * 100) if total else 0
        return f"{_human_bytes(used)} / {_human_bytes(total)} ({pct:.0f}% used)"
    except OSError:
        return "n/a"


def _battery() -> str:
    try:
        result = subprocess.run(["termux-battery-status"], capture_output=True, text=True, timeout=5)
        data = json.loads(result.stdout)
        return f"{data.get('percentage', '?')}% ({data.get('status', 'unknown')})"
    except Exception:
        return "n/a (install termux-api for this)"


def render(provider: str, model: str, cwd: str) -> None:
    notes = productivity.list_notes()
    tasks_open = productivity.list_tasks(include_done=False)
    tasks_all = productivity.list_tasks(include_done=True)
    reminders = productivity.list_reminders()
    due = productivity.due_reminders()

    console.print("[bold cyan]ShadowOS Dashboard[/bold cyan]")
    console.print("[cyan]" + "─" * 42 + "[/cyan]")
    console.print(f"  Model        {escape(model)}  ({escape(provider)})")
    console.print(f"  Working dir  {escape(cwd)}")
    console.print(f"  Disk         {_disk_usage(cwd)}")
    console.print(f"  Battery      {_battery()}")
    console.print(f"  Time         {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    console.print()
    console.print(f"  Notes        {len(notes)}")
    console.print(f"  Tasks        {len(tasks_open)} open / {len(tasks_all)} total")
    due_note = f", [red]{len(due)} DUE[/red]" if due else ""
    console.print(f"  Reminders    {len(reminders)} upcoming{due_note}")

    if due:
        console.print()
        console.print("  Due now:")
        for r in due[:5]:
            console.print(f"    [red]! {escape(r['text'])}  (was due {escape(r['when'])})[/red]")

    if tasks_open:
        console.print()
        console.print("  Top tasks:")
        ranked = sorted(tasks_open, key=lambda t: _PRIORITY_ORDER.get(t["priority"], 1))
        for t in ranked[:5]:
            console.print(f"    #{t['id']} ({t['priority']}) {escape(t['text'])}")

    console.print("[cyan]" + "─" * 42 + "[/cyan]")
