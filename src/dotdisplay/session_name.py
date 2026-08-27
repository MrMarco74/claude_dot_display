"""Turns a hook payload into a session name.

Pure and separate from the hook wrapper so it can be tested without
subprocesses. The name has two jobs at once: it is a filename, and it is
drawn on a 64x64 LED panel where nine characters fit.

Nothing about the payload's shape is assumed. Every field is read with a
fallback, because a hook that raises is a hook that breaks a session.
"""

import os
import re

MAX_DISPLAY = 9        # what fits beside a right-aligned two-digit count
SUFFIX_CHARS = 2       # enough to tell concurrent sessions in one repo apart
UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")
FALLBACK = "session"


def _slug(text: str) -> str:
    """Reduce arbitrary text to characters that are safe in a filename."""
    return UNSAFE.sub("", text.replace(" ", "-"))


def derive(payload: dict, cwd_fallback: str | None = None) -> str:
    cwd = payload.get("cwd") or cwd_fallback or os.getcwd()
    base = _slug(os.path.basename(os.path.normpath(str(cwd)))) or FALLBACK

    session = payload.get("session_id") or ""
    suffix = _slug(str(session))[:SUFFIX_CHARS]

    if not suffix:
        return base[:MAX_DISPLAY]
    # Keep the whole suffix; spend what is left of the budget on the name.
    return f"{base[: MAX_DISPLAY - SUFFIX_CHARS - 1]}-{suffix}"
