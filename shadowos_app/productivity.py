"""
Local JSON-backed storage for notes, tasks, and reminders. Separate from
storage.py (which persists chat *conversations*) -- these are ShadowOS's
own productivity tools, not part of ShadowChat's feature set.
Files live in ~/.shadowos/data/*.json.
"""

import json
import shutil
import subprocess
from datetime import datetime

from . import config

NOTES_FILE = config.DATA_DIR / "notes.json"
TASKS_FILE = config.DATA_DIR / "tasks.json"
REMINDERS_FILE = config.DATA_DIR / "reminders.json"


def _load(file) -> list:
    if file.exists():
        try:
            return json.loads(file.read_text())
        except (json.JSONDecodeError, OSError):
            return []
    return []


def _save(file, items: list) -> None:
    file.write_text(json.dumps(items, indent=2))


def _next_id(items: list) -> int:
    return max((i["id"] for i in items), default=0) + 1


# ------------------------------------------------------------------- Notes
def add_note(text: str, tag: str = "") -> dict:
    items = _load(NOTES_FILE)
    note = {
        "id": _next_id(items),
        "text": text,
        "tag": tag,
        "created": datetime.now().isoformat(timespec="seconds"),
    }
    items.append(note)
    _save(NOTES_FILE, items)
    return note


def list_notes(tag: str = "") -> list:
    items = _load(NOTES_FILE)
    if tag:
        items = [n for n in items if n.get("tag", "").lower() == tag.lower()]
    return items


def delete_note(note_id: int) -> bool:
    items = _load(NOTES_FILE)
    remaining = [n for n in items if n["id"] != note_id]
    changed = len(remaining) != len(items)
    if changed:
        _save(NOTES_FILE, remaining)
    return changed


# ------------------------------------------------------------------- Tasks
def add_task(text: str, priority: str = "normal") -> dict:
    items = _load(TASKS_FILE)
    task = {
        "id": _next_id(items),
        "text": text,
        "priority": priority,
        "done": False,
        "created": datetime.now().isoformat(timespec="seconds"),
    }
    items.append(task)
    _save(TASKS_FILE, items)
    return task


def list_tasks(include_done: bool = False) -> list:
    items = _load(TASKS_FILE)
    if not include_done:
        items = [t for t in items if not t["done"]]
    return items


def complete_task(task_id: int) -> bool:
    items = _load(TASKS_FILE)
    for t in items:
        if t["id"] == task_id:
            t["done"] = True
            t["completed"] = datetime.now().isoformat(timespec="seconds")
            _save(TASKS_FILE, items)
            return True
    return False


def delete_task(task_id: int) -> bool:
    items = _load(TASKS_FILE)
    remaining = [t for t in items if t["id"] != task_id]
    changed = len(remaining) != len(items)
    if changed:
        _save(TASKS_FILE, remaining)
    return changed


# --------------------------------------------------------------- Reminders
def add_reminder(text: str, when: str) -> dict:
    """`when` should be an ISO datetime string, e.g. 2026-09-12T09:00:00.

    If Termux:API and `at` are installed, this also schedules a real
    device notification. Otherwise the reminder is stored and will show
    up as "due" next time ShadowOS starts or /reminders is run.
    """
    items = _load(REMINDERS_FILE)
    reminder = {
        "id": _next_id(items),
        "text": text,
        "when": when,
        "created": datetime.now().isoformat(timespec="seconds"),
        "fired": False,
        "scheduled_via_at": False,
    }
    if shutil.which("at") and shutil.which("termux-notification"):
        reminder["scheduled_via_at"] = _schedule_with_at(text, when)
    items.append(reminder)
    _save(REMINDERS_FILE, items)
    return reminder


def _schedule_with_at(text: str, when: str) -> bool:
    try:
        dt = datetime.fromisoformat(when)
        at_time = dt.strftime("%H:%M %m/%d/%Y")
        cmd = f'termux-notification --title "ShadowOS reminder" --content {json.dumps(text)}'
        result = subprocess.run(
            ["at", at_time], input=cmd, text=True,
            capture_output=True, timeout=10,
        )
        return result.returncode == 0
    except Exception:
        return False


def list_reminders(include_fired: bool = False) -> list:
    items = _load(REMINDERS_FILE)
    if not include_fired:
        items = [r for r in items if not r["fired"]]
    return items


def due_reminders() -> list:
    """Reminders whose time has passed and haven't been shown yet."""
    now = datetime.now()
    due = []
    for r in _load(REMINDERS_FILE):
        try:
            when = datetime.fromisoformat(r["when"])
        except ValueError:
            continue
        if not r["fired"] and when <= now:
            due.append(r)
    return due


def mark_fired(reminder_id: int) -> None:
    items = _load(REMINDERS_FILE)
    for r in items:
        if r["id"] == reminder_id:
            r["fired"] = True
    _save(REMINDERS_FILE, items)


def delete_reminder(reminder_id: int) -> bool:
    items = _load(REMINDERS_FILE)
    remaining = [r for r in items if r["id"] != reminder_id]
    changed = len(remaining) != len(items)
    if changed:
        _save(REMINDERS_FILE, remaining)
    return changed
