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
        if argv[0] == "--clear":
            path.unlink(missing_ok=True)
        elif argv[0] in STATES:
            directory.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"name": name, "status": argv[0]}))
    except Exception as exc:   # noqa: BLE001 - must never break a session
        print(f"dotdisplay hook: {exc}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
