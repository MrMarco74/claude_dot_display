"""Shows the board as text.

Deliberately not a pixel mirror of the panel. The nine-character names, the
8px font and the four-row budget are consequences of a 64x64 LED matrix; a
terminal has none of those limits, so importing them would cost readability
for nothing. Mirroring was measured at 59KB of escapes per frame -- but the
cost was never the main argument.

Pure: returns strings. Deciding whether to colour them is the caller's job.
"""

from dotdisplay.render import human_tokens

WIDTH = 46          # comfortable inside an 80-column terminal

STATE_WORDS = {
    "running": "running",
    "question": "waiting on you",
    "issue": "issue",
    "done": "done",
}
# ANSI colours close enough to the panel's palette to read as the same board.
STATE_COLOURS = {
    "running": 33,      # blue
    "question": 220,    # amber
    "issue": 196,       # red
    "done": 46,         # green
}
STATE_ORDER = ("issue", "question", "running", "done")
MARKS = {"issue": "!", "question": "?", "running": "*", "done": "+"}


def _rule() -> str:
    return "─" * WIDTH


NAME_MIN, NAME_MAX = 18, 28


def board(sessions, header, stats) -> str:
    right = f"{header['pct']:.0f}% · reset {header['reset']}" if header else ""
    lines = [f"{'claude-dot-display':<{WIDTH - len(right)}}{right}"]

    # Size the name column to what is actually present rather than truncating
    # to a fixed width -- a name running into the state word is worse than a
    # wider table.
    longest = max((len(s["name"]) for s in sessions), default=0)
    name_w = min(NAME_MAX, max(NAME_MIN, longest + 1))
    width = max(WIDTH, name_w + 27)
    lines[0] = f"{'claude-dot-display':<{width - len(right)}}{right}"
    lines.append("─" * width)

    for session in sorted(sessions, key=lambda s: s["name"]):
        word = STATE_WORDS.get(session["status"], session["status"])
        left = session.get("stages_left")
        count = "" if left is None else str(left)
        name = session["name"]
        if len(name) > name_w - 1:
            name = name[: name_w - 2] + "…"
        lines.append(f"● {name:<{name_w}}{word:<20}{count:>5}")

    if not sessions:
        lines.append("  no sessions running")
        if stats:
            lines.append("─" * width)
            # Only what fits whole: a value cut in half is worse than absent.
            shown = []
            for key, value in stats.items():
                piece = f"{key} {human_tokens(value)}"
                if len("  " + " · ".join(shown + [piece])) > width:
                    break
                shown.append(piece)
            lines.append("  " + " · ".join(shown))

    return "\n".join(lines)


# States that mean a human is needed. These get names; the rest get counts,
# because knowing *which* session is blocked is actionable and knowing which
# one is merely running is not.
ATTENTION = ("issue", "question")
STATUSLINE_MAX = 60


def statusline(sessions, names: bool = True) -> str:
    """One short segment for a prompt.

    Empty when there is nothing to say: a permanent decoration in a prompt is
    just noise.

    Names are shown for the states that need a human and counts for the rest.
    If that would grow past STATUSLINE_MAX, everything falls back to counts --
    a prompt segment that wraps is worse than one that is vague.
    """
    if not sessions:
        return ""

    counts = {}
    for session in sessions:
        counts[session["status"]] = counts.get(session["status"], 0) + 1

    def counts_only() -> str:
        return " ".join(f"{MARKS[state]}{counts[state]}"
                        for state in STATE_ORDER if counts.get(state))

    if not names:
        return counts_only()

    pieces = []
    for state in STATE_ORDER:
        if not counts.get(state):
            continue
        if state in ATTENTION:
            pieces.extend(
                f"{MARKS[state]}{s['name']}"
                for s in sorted(sessions, key=lambda s: s["name"])
                if s["status"] == state)
        else:
            pieces.append(f"{MARKS[state]}{counts[state]}")

    line = " ".join(pieces)
    return line if len(line) <= STATUSLINE_MAX else counts_only()
