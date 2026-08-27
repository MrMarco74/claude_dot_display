# P6 — Display Without Hardware Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the whole product useful without a panel. Today claude-dot-display is worthless to anyone who does not own a 40-euro LED matrix; after this phase the hardware is the flourish, not the entry fee.

**Architecture:** A second presenter of the same model, **not** a mirror of the first. `sources.py` already produces the model — sessions plus header — and `render.py` is merely one way to show it. The terminal gets its own presenter that uses the terminal's strengths instead of importing the panel's limits.

**Spec:** `docs/superpowers/specs/2026-08-27-architecture-design.md`

## Why not mirror the panel

Mirroring 64x64 pixels into the terminal with half-block characters was tried
and measured: **59 KB of ANSI escapes for a single frame.**

Worse than the cost is what it carries along. The nine-character name limit,
the 8px font, the banned `%` glyph, the four-row budget — every one of those
exists *because of the panel*. A terminal has none of them. Mirroring would
spend bandwidth to import constraints that serve nothing.

So the split is one level lower than it first appears: share the **model**,
write a second **presenter**.

```
sources.py  ──►  model (sessions, header, usage)
                   │
                   ├──►  render.py       64x64 image   → panel
                   └──►  presenters/text.py  text      → terminal, statusline
```

## Global Constraints

- **Do not import panel constraints into the terminal.** Full names, spelled
  out states, no nine-character truncation.
- **The presenters must not drift.** Both read the same `sources` model; no
  second source of truth for what a session is.
- **The statusline must be cheap.** It runs on every render of the prompt; it
  may not connect to anything, spawn anything slow, or block.
- **Never require a panel.** Everything in this phase must work with no
  Bluetooth adapter present at all.
- Runtime dependencies stay exactly `bleak`, `pillow`, `requests`.
- Work inside `.venv`. Test: `.venv/bin/python -m pytest -q`. **Baseline 165 tests** (after P4). Lint: `.venv/bin/ruff check .`

## File Structure

| File | Responsibility |
| --- | --- |
| `src/dotdisplay/presenters/text.py` | Pure: model to text. No I/O, no ANSI decisions |
| `src/dotdisplay/cli.py` | Gains `board` and `statusline` |
| `README.md` | A "without hardware" section |

---

### Task 1: the text presenter

**Files:**
- Create: `src/dotdisplay/presenters/__init__.py`, `src/dotdisplay/presenters/text.py`
- Test: `tests/test_presenter_text.py`

**Interfaces:**
- Produces: `board(sessions, header, stats) -> str`, `statusline(sessions) -> str`, `STATE_WORDS`, `STATE_COLOURS`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_presenter_text.py`:

```python
import pytest

from dotdisplay.presenters import text as t

HEADER = {"pct": 32, "reset": "22:10"}
SESSIONS = [
    {"name": "demo-i", "status": "issue", "stages_left": 7},
    {"name": "hwmon-d7", "status": "running", "stages_left": 12},
]


def test_board_lists_every_session():
    out = t.board(SESSIONS, HEADER, {})
    assert "demo-i" in out
    assert "hwmon-d7" in out


def test_board_spells_states_out():
    """The panel has only colour to work with. A terminal has words, and a
    reader should not have to remember what amber means."""
    out = t.board(SESSIONS, HEADER, {})
    assert "issue" in out
    assert "running" in out


def test_board_does_not_truncate_names_to_the_panel_budget():
    """Nine characters is a panel limit. Importing it here would be
    cargo-culting the hardware."""
    long_name = "a-very-long-session-name"
    out = t.board([{"name": long_name, "status": "running"}], HEADER, {})
    assert long_name in out


def test_board_is_sorted_alphabetically():
    out = t.board(list(reversed(SESSIONS)), HEADER, {})
    assert out.index("demo-i") < out.index("hwmon-d7")


def test_board_shows_the_header():
    assert "22:10" in t.board(SESSIONS, HEADER, {})


def test_board_without_sessions_shows_usage():
    out = t.board([], HEADER, {"today": 684_000_000, "all": 12_000_000_000})
    assert "684M" in out
    assert "12G" in out


def test_board_without_a_header_still_renders():
    assert t.board(SESSIONS, None, {})


def test_board_lines_fit_a_narrow_terminal():
    """80 columns is the floor worth supporting."""
    for line in t.board(SESSIONS, HEADER, {}).splitlines():
        assert len(line) <= 80, line


def test_statusline_is_one_short_line():
    line = t.statusline(SESSIONS)
    assert "\n" not in line
    assert len(line) <= 40


def test_statusline_counts_by_state():
    line = t.statusline([
        {"name": "a", "status": "issue"},
        {"name": "b", "status": "issue"},
        {"name": "c", "status": "running"},
    ])
    assert "2" in line


def test_statusline_is_empty_without_sessions():
    """An empty prompt segment beats a permanent decoration."""
    assert t.statusline([]) == ""


@pytest.mark.parametrize("status", ["running", "question", "issue", "done"])
def test_every_state_has_a_word_and_a_colour(status):
    assert status in t.STATE_WORDS
    assert status in t.STATE_COLOURS
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_presenter_text.py -q`
Expected: FAIL — no module `dotdisplay.presenters`.

- [ ] **Step 3: Write the implementation**

Create `src/dotdisplay/presenters/__init__.py`:

```python
"""Ways of showing the board.

The panel renderer in dotdisplay.render is one presenter; this package holds
the others. They share the model from dotdisplay.sources and nothing else.
"""
```

Create `src/dotdisplay/presenters/text.py`:

```python
"""Shows the board as text.

Deliberately not a pixel mirror of the panel. The nine-character names, the
8px font and the four-row budget are consequences of a 64x64 LED matrix; a
terminal has none of those limits, so importing them would cost readability
for nothing.

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
# ANSI colour numbers matching the panel's palette closely enough to be
# recognisable as the same board.
STATE_COLOURS = {
    "running": 33,      # blue
    "question": 220,    # amber
    "issue": 196,       # red
    "done": 46,         # green
}
STATE_ORDER = ("issue", "question", "running", "done")


def _rule() -> str:
    return "─" * WIDTH


def board(sessions, header, stats) -> str:
    lines = ["claude-dot-display".ljust(WIDTH - 18) +
             (f"{header['pct']:.0f}% · reset {header['reset']}" if header else "")]
    lines.append(_rule())

    for session in sorted(sessions, key=lambda s: s["name"]):
        word = STATE_WORDS.get(session["status"], session["status"])
        left = session.get("stages_left")
        count = "" if left is None else str(left)
        lines.append(f"● {session['name']:<18}{word:<20}{count:>5}"[:WIDTH])

    if not sessions:
        lines.append("  no sessions running")
        if stats:
            lines.append(_rule())
            lines.append("  " + " · ".join(
                f"{key} {human_tokens(value)}"
                for key, value in stats.items())[:WIDTH - 2])

    return "\n".join(lines)


def statusline(sessions) -> str:
    """One short segment for a prompt. Empty when there is nothing to say --
    a permanent decoration would just be noise."""
    if not sessions:
        return ""
    counts = {}
    for session in sessions:
        counts[session["status"]] = counts.get(session["status"], 0) + 1
    marks = {"issue": "!", "question": "?", "running": "*", "done": "+"}
    return " ".join(f"{marks[state]}{counts[state]}"
                    for state in STATE_ORDER if counts.get(state))
```

- [ ] **Step 4: Run tests and commit**

Expected: PASS, 165 + 12 = 177 tests.

```bash
git add src/dotdisplay/presenters tests/test_presenter_text.py
git commit -m "feat: add a text presenter for terminals"
```

---

### Task 2: the board and statusline commands

**Files:**
- Modify: `src/dotdisplay/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Produces: `dotdisplay board [--watch] [--no-colour]`, `dotdisplay statusline`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli.py`:

```python
def test_board_prints_sessions_without_any_panel(tmp_path, monkeypatch, capsys):
    """No Bluetooth, no MAC, no daemon. This must still work -- that is the
    entire point of the phase."""
    monkeypatch.setenv("DOTDISPLAY_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("DOTDISPLAY_MAC", raising=False)
    (tmp_path / "demo.json").write_text(
        json.dumps({"name": "demo", "status": "issue", "stages_left": 4}))
    assert cli.main(["board"]) == 0
    out = capsys.readouterr().out
    assert "demo" in out and "issue" in out


def test_board_without_colour_emits_no_escapes(tmp_path, monkeypatch, capsys):
    """Piping the board into a file should not fill it with escape codes."""
    monkeypatch.setenv("DOTDISPLAY_STATE_DIR", str(tmp_path))
    (tmp_path / "demo.json").write_text(
        json.dumps({"name": "demo", "status": "issue"}))
    cli.main(["board", "--no-colour"])
    assert "\033[" not in capsys.readouterr().out


def test_statusline_prints_one_line(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DOTDISPLAY_STATE_DIR", str(tmp_path))
    (tmp_path / "demo.json").write_text(
        json.dumps({"name": "demo", "status": "issue"}))
    assert cli.main(["statusline"]) == 0
    assert capsys.readouterr().out.count("\n") <= 1


def test_statusline_is_silent_without_sessions(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DOTDISPLAY_STATE_DIR", str(tmp_path))
    assert cli.main(["statusline"]) == 0
    assert capsys.readouterr().out.strip() == ""


def test_statusline_never_touches_the_radio(tmp_path, monkeypatch, mocker):
    """It runs on every prompt render. Connecting would be unforgivable."""
    monkeypatch.setenv("DOTDISPLAY_STATE_DIR", str(tmp_path))
    panel = mocker.patch("dotdisplay.ble.PanelClient")
    cli.main(["statusline"])
    panel.assert_not_called()
```

- [ ] **Step 2: Write the implementation**

```python
def _cmd_board(args) -> int:
    import time

    from dotdisplay import sources
    from dotdisplay.config import Config
    from dotdisplay.presenters import text as presenter

    config = Config.from_env()
    colour = not args.no_colour and sys.stdout.isatty()

    def once():
        sessions = sources.read_local_sessions(config)
        stats = sources.ccusage_stats(config, sources.CcusageCache()) \
            if not sessions else {}
        out = presenter.board(sessions, sources.read_header(), stats)
        if colour:
            for state, code in presenter.STATE_COLOURS.items():
                word = presenter.STATE_WORDS[state]
                out = out.replace(word, f"\033[38;5;{code}m{word}\033[0m")
        print(out)

    if not args.watch:
        once()
        return 0

    try:
        while True:
            print("\033[2J\033[H", end="")
            once()
            time.sleep(config.poll_s)
    except KeyboardInterrupt:
        return 0


def _cmd_statusline() -> int:
    from dotdisplay import sources
    from dotdisplay.config import Config
    from dotdisplay.presenters import text as presenter

    line = presenter.statusline(sources.read_local_sessions(Config.from_env()))
    if line:
        print(line)
    return 0
```

`statusline` reads files and nothing else. It must never import `bleak`
lazily or otherwise, because it runs on every prompt render.

- [ ] **Step 3: Run tests and commit**

Expected: PASS, 177 + 5 = 182 tests.

```bash
git add src/dotdisplay/cli.py tests/test_cli.py
git commit -m "feat: add the board and statusline commands"
```

---

### Task 3: wire the statusline into Claude Code, and document

**Files:**
- Modify: `README.md`, `commands/dotdisplay-setup.md`

- [ ] **Step 1: Document the statusline**

Claude Code's statusline is configured in `settings.json` under
`statusLine`. Document the snippet in the README rather than editing the
user's settings:

```json
{
  "statusLine": {
    "type": "command",
    "command": "dotdisplay statusline"
  }
}
```

**Do not modify the user's `settings.json` from the installer.** They may
already have a statusline; replacing it would be exactly the kind of
clobbering the plugin's own hooks were designed to avoid.

- [ ] **Step 2: Rewrite the README's opening claim**

The README currently reads as though a panel is required. It is not, after
this phase. Add near the top:

> **No panel? It still works.** `dotdisplay board` shows the same board in
> your terminal, and `dotdisplay statusline` puts a summary in Claude Code's
> status line. The LED matrix is the fun part, not the price of entry.

- [ ] **Step 3: Verify by using it**

```bash
dotdisplay board
dotdisplay board --no-colour | cat        # no escapes when piped
dotdisplay statusline
```

Then start a real Claude Code session and confirm a row appears in
`dotdisplay board` **while the daemon is stopped** — proving the terminal
path is independent of the panel entirely.

- [ ] **Step 4: Commit**

```bash
.venv/bin/python -m pytest -q && .venv/bin/ruff check .
git add -A && git commit -m "docs: describe using the board without a panel"
git push origin main && git push gitlab main
```

---

## Definition of done

- 182 tests pass locally and in CI; ruff clean.
- `dotdisplay board` and `statusline` work with **no Bluetooth adapter, no
  MAC and no daemon**.
- Piped output contains no escape codes.
- The statusline never touches the radio.
- The README no longer implies a panel is required.

## Deliberately not in P6

- A web view. The terminal covers the case; a served page is a different
  product with its own security surface.
- Editing the user's `settings.json`. The snippet is documented; wiring it up
  is theirs.
