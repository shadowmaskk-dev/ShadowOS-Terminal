"""
Tools exposed to the model: shell commands, file operations, and the
notes/tasks/reminders productivity store. Shell/file access is confined
to a working directory the user controls with /cd, and destructive-
looking commands require confirmation before running.
"""

import subprocess
from pathlib import Path

from . import productivity

# Generic (provider-agnostic) tool schemas, OpenAI-function-style. Each
# provider adapter (providers.py) translates these into its own tool/
# function-calling shape.
TOOL_SCHEMAS = [
    {
        "name": "run_command",
        "description": "Run a shell command in the current working directory and return stdout/stderr.",
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The shell command to execute."}
            },
            "required": ["command"],
        },
    },
    {
        "name": "read_file",
        "description": "Read the full text contents of a file.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the file, relative to cwd or absolute."}
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "Write (overwrite) text content to a file, creating it if needed.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "list_dir",
        "description": "List files and folders in a directory.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory path, relative to cwd or absolute. Defaults to cwd."}
            },
            "required": [],
        },
    },
    {
        "name": "add_note",
        "description": "Save a note for the user to look back on later.",
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "The note content."},
                "tag": {"type": "string", "description": "Optional short category/tag, e.g. 'work' or 'ideas'."},
            },
            "required": ["text"],
        },
    },
    {
        "name": "list_notes",
        "description": "List saved notes, optionally filtered by tag.",
        "parameters": {
            "type": "object",
            "properties": {"tag": {"type": "string", "description": "Optional tag to filter by."}},
            "required": [],
        },
    },
    {
        "name": "delete_note",
        "description": "Delete a note by its id.",
        "parameters": {
            "type": "object",
            "properties": {"note_id": {"type": "integer"}},
            "required": ["note_id"],
        },
    },
    {
        "name": "add_task",
        "description": "Add a to-do item.",
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "What needs to be done."},
                "priority": {"type": "string", "description": "'low', 'normal', or 'high'. Defaults to normal."},
            },
            "required": ["text"],
        },
    },
    {
        "name": "list_tasks",
        "description": "List to-do items.",
        "parameters": {
            "type": "object",
            "properties": {
                "include_done": {"type": "boolean", "description": "Include already-completed tasks. Defaults to false."}
            },
            "required": [],
        },
    },
    {
        "name": "complete_task",
        "description": "Mark a task as done by its id.",
        "parameters": {
            "type": "object",
            "properties": {"task_id": {"type": "integer"}},
            "required": ["task_id"],
        },
    },
    {
        "name": "delete_task",
        "description": "Delete a task by its id.",
        "parameters": {
            "type": "object",
            "properties": {"task_id": {"type": "integer"}},
            "required": ["task_id"],
        },
    },
    {
        "name": "add_reminder",
        "description": (
            "Schedule a reminder for a specific date/time. If Termux:API is "
            "installed this also fires a real device notification at that time."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "What to be reminded of."},
                "when": {
                    "type": "string",
                    "description": "ISO 8601 datetime, e.g. '2026-09-12T09:00:00'. Resolve relative "
                                    "phrases like 'tomorrow at 9am' to an actual datetime yourself.",
                },
            },
            "required": ["text", "when"],
        },
    },
    {
        "name": "list_reminders",
        "description": "List upcoming (not yet fired) reminders.",
        "parameters": {
            "type": "object",
            "properties": {
                "include_fired": {"type": "boolean", "description": "Include reminders that already fired. Defaults to false."}
            },
            "required": [],
        },
    },
    {
        "name": "delete_reminder",
        "description": "Cancel/delete a reminder by its id.",
        "parameters": {
            "type": "object",
            "properties": {"reminder_id": {"type": "integer"}},
            "required": ["reminder_id"],
        },
    },
]

# Patterns that trigger a confirmation prompt before running.
_DANGEROUS_SNIPPETS = [
    "rm -rf", "rm -r ", "mkfs", "dd if=", "dd of=", ":(){", "> /dev/",
    "git push --force", "git reset --hard", "chmod -R 777", "curl | sh",
    "wget | sh", "shutdown", "reboot", "termux-reboot",
]


def is_dangerous(command: str) -> bool:
    lowered = command.lower()
    return any(snippet in lowered for snippet in _DANGEROUS_SNIPPETS)


def _resolve(cwd: str, path: str) -> Path:
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = Path(cwd) / p
    return p


def run_command(cwd: str, command: str) -> str:
    try:
        result = subprocess.run(
            command, shell=True, cwd=cwd,
            capture_output=True, text=True, timeout=120,
        )
        out = result.stdout.strip()
        err = result.stderr.strip()
        pieces = []
        if out:
            pieces.append(out)
        if err:
            pieces.append(f"[stderr]\n{err}")
        pieces.append(f"[exit code: {result.returncode}]")
        return "\n".join(pieces)
    except subprocess.TimeoutExpired:
        return "[error] command timed out after 120s"
    except Exception as e:
        return f"[error] {e}"


def read_file(cwd: str, path: str) -> str:
    try:
        target = _resolve(cwd, path)
        return target.read_text(errors="replace")
    except Exception as e:
        return f"[error] {e}"


def write_file(cwd: str, path: str, content: str) -> str:
    try:
        target = _resolve(cwd, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        return f"Wrote {len(content)} bytes to {target}"
    except Exception as e:
        return f"[error] {e}"


def list_dir(cwd: str, path: str = "") -> str:
    try:
        target = _resolve(cwd, path) if path else Path(cwd)
        entries = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        lines = [f"{'d' if e.is_dir() else 'f'}  {e.name}" for e in entries]
        return "\n".join(lines) if lines else "(empty)"
    except Exception as e:
        return f"[error] {e}"


def _fmt_notes(items: list) -> str:
    if not items:
        return "(no notes)"
    lines = []
    for n in items:
        tag = f" [{n['tag']}]" if n.get("tag") else ""
        lines.append(f"#{n['id']}{tag} {n['text']}  ({n['created']})")
    return "\n".join(lines)


def _fmt_tasks(items: list) -> str:
    if not items:
        return "(no tasks)"
    lines = []
    for t in items:
        mark = "x" if t["done"] else " "
        lines.append(f"[{mark}] #{t['id']} ({t['priority']}) {t['text']}")
    return "\n".join(lines)


def _fmt_reminders(items: list) -> str:
    if not items:
        return "(no reminders)"
    lines = []
    for r in items:
        via = " [device notification scheduled]" if r.get("scheduled_via_at") else ""
        lines.append(f"#{r['id']} {r['when']} — {r['text']}{via}")
    return "\n".join(lines)


def execute_tool(cwd: str, name: str, args: dict) -> str:
    if name == "run_command":
        return run_command(cwd, args.get("command", ""))
    if name == "read_file":
        return read_file(cwd, args.get("path", ""))
    if name == "write_file":
        return write_file(cwd, args.get("path", ""), args.get("content", ""))
    if name == "list_dir":
        return list_dir(cwd, args.get("path", ""))

    if name == "add_note":
        note = productivity.add_note(args.get("text", ""), args.get("tag", ""))
        return f"Saved note #{note['id']}."
    if name == "list_notes":
        return _fmt_notes(productivity.list_notes(args.get("tag", "")))
    if name == "delete_note":
        ok = productivity.delete_note(int(args.get("note_id", -1)))
        return "Deleted." if ok else "No note with that id."

    if name == "add_task":
        task = productivity.add_task(args.get("text", ""), args.get("priority", "normal"))
        return f"Added task #{task['id']}."
    if name == "list_tasks":
        return _fmt_tasks(productivity.list_tasks(bool(args.get("include_done", False))))
    if name == "complete_task":
        ok = productivity.complete_task(int(args.get("task_id", -1)))
        return "Marked done." if ok else "No task with that id."
    if name == "delete_task":
        ok = productivity.delete_task(int(args.get("task_id", -1)))
        return "Deleted." if ok else "No task with that id."

    if name == "add_reminder":
        reminder = productivity.add_reminder(args.get("text", ""), args.get("when", ""))
        via = " (device notification scheduled)" if reminder["scheduled_via_at"] else " (stored; install Termux:API + `at` for real notifications)"
        return f"Set reminder #{reminder['id']} for {reminder['when']}.{via}"
    if name == "list_reminders":
        return _fmt_reminders(productivity.list_reminders(bool(args.get("include_fired", False))))
    if name == "delete_reminder":
        ok = productivity.delete_reminder(int(args.get("reminder_id", -1)))
        return "Deleted." if ok else "No reminder with that id."

    return f"[error] unknown tool: {name}"
