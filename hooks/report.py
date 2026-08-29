#!/usr/bin/env python3
"""Reports the current session's state, called from Claude Code hooks.

Reads the hook payload on stdin and writes a session file. Deliberately does
NOT live in the CLI: the CLI is also called directly by the assistant with an
explicit name, and it should not grow hook-payload parsing for that.

**This must never break a session.** It runs inline on every prompt, so it
always exits 0, never writes to stdout, and swallows every error.
"""

import json
import os
import pathlib
import re
import sys

MAX_DISPLAY = 9
SUFFIX_CHARS = 2
UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")
FALLBACK = "session"
STATES = ("running", "question", "issue", "done")

# What a todo list contributes to a session file. Kept as one list so the
# carry-over rules cannot drift from what the writer sets.
PROGRESS_KEYS = ("stages_left", "stages_total", "activity", "tasks")
# Todo text is model-authored and ends up in a terminal. A stray escape
# sequence would be executed by whatever draws the board.
CONTROL = re.compile(r"[\x00-\x1f\x7f]")
MAX_TEXT = 120
MAX_TASKS = 12


def _pointer_path(cwd: str) -> pathlib.Path:
    """Where the current session name for a directory is recorded.

    The assistant cannot know its own session name -- it is derived here from
    the hook payload, which the assistant never sees. This pointer is how
    `dotdisplay status --this` finds it.
    """
    slug = UNSAFE.sub("", str(cwd).replace("/", "_")) or "root"
    return _state_dir().parent / "current" / f"{slug}.name"


def _state_dir() -> pathlib.Path:
    override = os.environ.get("DOTDISPLAY_STATE_DIR")
    if override:
        return pathlib.Path(override)
    base = os.environ.get("XDG_STATE_HOME") or (
        pathlib.Path(os.path.expanduser("~")) / ".local/state")
    return pathlib.Path(base) / "dotdisplay" / "sessions"


def _derive(payload: dict) -> str:
    """Kept in step with src/dotdisplay/session_name.py.

    Duplicated on purpose: the hook must work whether or not
    claude-dot-display is installed in whichever interpreter runs it, so it
    cannot import the package. Both copies are pinned by tests, so they
    cannot drift silently.
    """
    cwd = payload.get("cwd") or os.getcwd()
    base = UNSAFE.sub("", os.path.basename(os.path.normpath(str(cwd)))
                      .replace(" ", "-")) or FALLBACK
    suffix = UNSAFE.sub("", str(payload.get("session_id") or ""))[:SUFFIX_CHARS]
    if not suffix:
        return base[:MAX_DISPLAY]
    return f"{base[: MAX_DISPLAY - SUFFIX_CHARS - 1]}-{suffix}"


def _carried(path: pathlib.Path) -> dict:
    """What survives a state change.

    A writer that says nothing about the progress must not erase it:
    UserPromptSubmit fires "running" on every prompt, and a prompt is a
    statement about the state, not about how much work is left. Returns
    an empty dict for anything unreadable -- a damaged file is a reason to
    lose the count, never a reason to leave the session off the board.
    """
    try:
        body = json.loads(path.read_text())
        if not isinstance(body, dict):
            return {}
        return {key: body[key] for key in PROGRESS_KEYS
                if body.get(key) is not None}
    except (OSError, ValueError):
        return {}


def _status_on_file(path: pathlib.Path) -> str:
    """The status already recorded, or "running" for a session with no file.

    A beat must never state a status of its own -- it fires on every tool
    call, and a session that asked a question is still asking it while it
    works on something else.
    """
    try:
        body = json.loads(path.read_text())
        status = body.get("status") if isinstance(body, dict) else None
    except (OSError, ValueError):
        status = None
    return status if status in STATES else "running"


def _clean(text) -> str:
    return CONTROL.sub("", str(text)).strip()[:MAX_TEXT]


def _progress(payload: dict) -> dict | None:
    """Read the session's progress off a TodoWrite call.

    The stage count used to exist only if the assistant remembered to report
    one, which meant it never existed. The todo list is the same information
    -- an enumerated plan the assistant already committed to -- and it
    arrives here on its own, in the PostToolUse payload, on every change.

    Returns None when this payload says nothing about todos, which must be
    distinguished from an empty list: only the latter retracts a count.
    """
    if payload.get("tool_name") != "TodoWrite":
        return None
    todos = (payload.get("tool_input") or {}).get("todos")
    if not isinstance(todos, list):
        return None

    open_todos = [t for t in todos
                  if isinstance(t, dict) and t.get("status") != "completed"]
    if not open_todos:
        # Every stage done. Dropping the keys is the retraction: a count that
        # outlives its plan is read from across the room and acted on.
        return {}

    active = next((t for t in open_todos if t.get("status") == "in_progress"),
                  None)
    progress = {"stages_left": len(open_todos), "stages_total": len(todos),
                "tasks": [_clean(t.get("content")) for t in
                          open_todos[:MAX_TASKS] if t.get("content")]}
    if active and active.get("activeForm"):
        progress["activity"] = _clean(active["activeForm"])
    return progress


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        return 0

    try:
        payload = json.loads(sys.stdin.read() or "{}")
        if not isinstance(payload, dict):
            payload = {}
    except (ValueError, OSError):
        payload = {}          # garbage on stdin is not a reason to fail

    try:
        name = _derive(payload)
        directory = _state_dir()
        path = directory / f"{name}.json"
        pointer = _pointer_path(payload.get("cwd") or os.getcwd())
        if argv[0] == "--beat":
            # A heartbeat says "this session still exists", NOT what state it
            # is in -- so it must never write a status. The other modes fire
            # only when you type or the session ends, which leaves a session
            # working on one long task looking dead after stale_after_s. This
            # is the only event that fires while Claude is actually working.
            directory.mkdir(parents=True, exist_ok=True)
            progress = _progress(payload)
            if progress is not None:
                # The one thing a beat may say beyond "still here": how much
                # of the plan is left. Still never a status -- that is read
                # off the existing file, or defaults for a new one.
                # A todo list replaces the progress outright rather than
                # merging: it is the whole plan, so anything it omits is
                # finished or gone.
                status = _status_on_file(path)
                path.write_text(json.dumps(
                    {"name": name, "status": status, **progress}))
            elif path.exists():
                os.utime(path, None)      # freshness only; status untouched
            else:
                # Self-healing: a session that predates the plugin, or whose
                # SessionStart hook did not run, joins the board on its first
                # tool call rather than staying invisible for its whole life.
                path.write_text(json.dumps({"name": name,
                                            "status": "running"}))
            pointer.parent.mkdir(parents=True, exist_ok=True)
            pointer.write_text(name)
        elif argv[0] == "--clear":
            path.unlink(missing_ok=True)
            pointer.unlink(missing_ok=True)
        elif argv[0] in STATES:
            directory.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"name": name, "status": argv[0],
                                        **_carried(path)}))
            pointer.parent.mkdir(parents=True, exist_ok=True)
            pointer.write_text(name)
    except Exception as exc:   # noqa: BLE001 - must never break a session
        print(f"dotdisplay hook: {exc}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
