# P3 — Claude Code Plugin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make sessions report themselves. Hooks cover what is mechanical (a session started, a prompt arrived, a session ended); a skill covers what only the assistant knows (this one hit a question, this one is broken, three stages left).

**Architecture:** The plugin ships hooks, a skill, and a setup command. Hooks call a small wrapper that turns the hook payload into a session name and shells out to `dotdisplay status` — the CLI stays free of hook-payload parsing because the assistant also calls it directly with an explicit name.

**Tech Stack:** Python 3.11+, Claude Code plugin manifests, `claude plugin validate`/`eval`.

**Spec:** `docs/superpowers/specs/2026-08-27-architecture-design.md`

## What earlier phases established

- `dotdisplay status --name N --state S [--left K]` and `--clear` already exist and are tested (P2, Task 5). This plan adds callers, not new CLI surface.
- `.claude-plugin/plugin.json` and `marketplace.json` already exist and pass `claude plugin validate --strict` (P0, Task 4). The plugin currently ships **nothing**; this plan gives it contents.
- The daemon reads `~/.local/state/dotdisplay/sessions/*.json` and ages entries out after 15 minutes.

## Global Constraints

- **Never break a session.** Hooks run inline on every prompt. The wrapper must exit non-zero quietly at worst, never block, never print to stdout, and carry a short timeout.
- **Do not touch the user's `settings.json`.** It already has `SessionStart`, `SessionEnd` and `UserPromptSubmit` hooks pointing at other tools. Plugin hooks are additive and live in `hooks/hooks.json`; editing the user's settings would risk clobbering unrelated configuration.
- **The session name is rendered to an LED panel and used as a filename.** It must match `^[A-Za-z0-9._-]{1,32}$` and stay within the **9-character** display budget.
- **The hook payload's exact shape is not assumed.** Read every field with a fallback; Task 5 verifies the derivation against a real session rather than trusting this document.
- Runtime dependencies stay exactly `bleak`, `pillow`, `requests`.
- Work inside `.venv`. Test: `.venv/bin/python -m pytest -q`. **Baseline 99 tests.** Lint: `.venv/bin/ruff check .`
- Nothing identifying in the repo.

## File Structure

| File | Responsibility |
| --- | --- |
| `src/dotdisplay/session_name.py` | Pure: hook payload to a safe, short session name |
| `hooks/hooks.json` | Which events fire which command |
| `hooks/report.py` | Reads the payload on stdin, calls the CLI. Never fails loudly |
| `skills/report-status/SKILL.md` | Teaches the assistant when to report semantic status |
| `commands/dotdisplay-setup.md` | `/dotdisplay-setup` — installs and configures the daemon |
| `tests/test_session_name.py`, `tests/test_hook_report.py` | |

---

### Task 1: session_name.py — deriving a name

The one piece with real logic. Kept pure and separate so it can be tested without hooks, subprocesses, or a panel.

**Files:**
- Create: `src/dotdisplay/session_name.py`
- Test: `tests/test_session_name.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `derive(payload: dict, cwd_fallback: str | None = None) -> str`, `MAX_DISPLAY = 9`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_session_name.py`:

```python
import pytest

from dotdisplay import session_name as sn
from dotdisplay.cli import SAFE_NAME


def test_name_combines_directory_and_session():
    """A directory alone collides across concurrent sessions in the same
    repo; the session id alone is a UUID nobody recognises."""
    name = sn.derive({"cwd": "/home/x/Documents/gitlab/hwmon",
                      "session_id": "50b53b31-3ec5-4886"})
    assert name == "hwmon-50"


def test_long_directory_names_are_truncated_to_the_display_budget():
    name = sn.derive({"cwd": "/home/x/a-very-long-project-name",
                      "session_id": "abcdef"})
    assert len(name) <= sn.MAX_DISPLAY
    assert name.endswith("-ab")


def test_every_derived_name_is_a_safe_filename():
    """The name becomes a filename and is drawn on a panel."""
    for cwd in ("/home/x/has space", "/home/x/../etc", "/home/x/weißbier",
                "/home/x/UPPER_Case.v2", "/"):
        name = sn.derive({"cwd": cwd, "session_id": "ff00"})
        assert SAFE_NAME.match(name), f"{cwd!r} produced {name!r}"


def test_missing_session_id_still_produces_a_name():
    """Never raise inside a hook. A name without a suffix is better than a
    crashed hook."""
    name = sn.derive({"cwd": "/home/x/hwmon"})
    assert SAFE_NAME.match(name)
    assert "hwmon" in name


def test_missing_cwd_falls_back():
    name = sn.derive({"session_id": "ab"}, cwd_fallback="/home/x/fallback")
    assert SAFE_NAME.match(name)


def test_completely_empty_payload_still_produces_a_name():
    assert SAFE_NAME.match(sn.derive({}, cwd_fallback=""))


def test_same_payload_gives_the_same_name():
    """Rows must not move between ticks."""
    payload = {"cwd": "/home/x/hwmon", "session_id": "50b5"}
    assert sn.derive(payload) == sn.derive(payload)


def test_different_sessions_in_one_directory_differ():
    a = sn.derive({"cwd": "/home/x/hwmon", "session_id": "aa11"})
    b = sn.derive({"cwd": "/home/x/hwmon", "session_id": "bb22"})
    assert a != b


@pytest.mark.parametrize("field", ["sessionId", "session"])
def test_unexpected_field_names_do_not_crash(field):
    """The payload shape is not assumed. An unknown spelling loses the
    suffix; it must not lose the hook."""
    assert SAFE_NAME.match(sn.derive({"cwd": "/home/x/hwmon", field: "zz"}))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_session_name.py -q`
Expected: FAIL — no module `dotdisplay.session_name`.

- [ ] **Step 3: Write the implementation**

Create `src/dotdisplay/session_name.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest -q && .venv/bin/ruff check .`
Expected: PASS, 99 + 10 = 109 tests. (Eight plain tests plus two parametrised cases.)

- [ ] **Step 5: Commit**

```bash
git add src/dotdisplay/session_name.py tests/test_session_name.py
git commit -m "feat: derive a display-safe session name from a hook payload"
```

---

### Task 2: the hook wrapper and manifest

**Files:**
- Create: `hooks/report.py`, `hooks/hooks.json`
- Test: `tests/test_hook_report.py`

**Interfaces:**
- Consumes: `session_name.derive` (Task 1); the `dotdisplay status` CLI (P2).
- Produces: `hooks/report.py <state|--clear>` reading a payload on stdin.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_hook_report.py`:

```python
import json
import subprocess
import sys
from pathlib import Path

REPORT = Path(__file__).resolve().parent.parent / "hooks" / "report.py"


def _run(payload, args, env_extra, expect_rc=0):
    env = {"PATH": "/usr/bin:/bin", "HOME": env_extra.pop("HOME", "/tmp")}
    env.update(env_extra)
    proc = subprocess.run(
        [sys.executable, str(REPORT), *args],
        input=json.dumps(payload), text=True, capture_output=True,
        env=env, timeout=10)
    assert proc.returncode == expect_rc, proc.stderr
    return proc


def test_a_running_state_writes_a_session_file(tmp_path):
    _run({"cwd": "/home/x/hwmon", "session_id": "aa11"}, ["running"],
         {"DOTDISPLAY_STATE_DIR": str(tmp_path), "HOME": str(tmp_path)})
    written = list(tmp_path.glob("*.json"))
    assert len(written) == 1
    assert json.loads(written[0].read_text())["status"] == "running"
    assert written[0].stem.startswith("hwmon-")


def test_clear_removes_the_session_file(tmp_path):
    payload = {"cwd": "/home/x/hwmon", "session_id": "aa11"}
    env = {"DOTDISPLAY_STATE_DIR": str(tmp_path), "HOME": str(tmp_path)}
    _run(payload, ["running"], dict(env))
    _run(payload, ["--clear"], dict(env))
    assert list(tmp_path.glob("*.json")) == []


def test_malformed_payload_never_fails_the_hook(tmp_path):
    """A hook that exits non-zero on garbage would break every session in
    which anything unexpected reaches stdin."""
    proc = subprocess.run(
        [sys.executable, str(REPORT), "running"],
        input="not json at all", text=True, capture_output=True,
        env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path),
             "DOTDISPLAY_STATE_DIR": str(tmp_path)}, timeout=10)
    assert proc.returncode == 0


def test_the_hook_writes_nothing_to_stdout(tmp_path):
    """Hook stdout can be fed back into the session; ours must stay silent."""
    proc = _run({"cwd": "/home/x/hwmon", "session_id": "aa11"}, ["running"],
                {"DOTDISPLAY_STATE_DIR": str(tmp_path), "HOME": str(tmp_path)})
    assert proc.stdout == ""


def test_an_unwritable_state_directory_is_survived(tmp_path):
    """A read-only home must not turn every prompt into an error."""
    blocked = tmp_path / "ro"
    blocked.mkdir()
    blocked.chmod(0o500)
    try:
        proc = subprocess.run(
            [sys.executable, str(REPORT), "running"],
            input=json.dumps({"cwd": "/home/x/hwmon", "session_id": "aa"}),
            text=True, capture_output=True,
            env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path),
                 "DOTDISPLAY_STATE_DIR": str(blocked / "nope")}, timeout=10)
        assert proc.returncode == 0
    finally:
        blocked.chmod(0o700)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_hook_report.py -q`
Expected: FAIL — `hooks/report.py` does not exist.

- [ ] **Step 3: Write the implementation**

Create `hooks/report.py`:

```python
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
    # Kept in step with src/dotdisplay/session_name.py; duplicated on purpose
    # so the hook has no import path dependency on an installed package --
    # it must work before, during and after installation.
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
```

Note the deliberate duplication of `_derive`. The hook runs from the plugin
directory and must work whether or not `claude-dot-display` is installed in
the interpreter that happens to run it. `tests/test_session_name.py` and
`tests/test_hook_report.py` both pin the behaviour, so the two cannot drift
silently.

Create `hooks/hooks.json`:

```json
{
  "description": "Reports this session's state to the claude-dot-display board.",
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 \"${CLAUDE_PLUGIN_ROOT}/hooks/report.py\" running",
            "timeout": 5
          }
        ]
      }
    ],
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 \"${CLAUDE_PLUGIN_ROOT}/hooks/report.py\" running",
            "timeout": 5
          }
        ]
      }
    ],
    "SessionEnd": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 \"${CLAUDE_PLUGIN_ROOT}/hooks/report.py\" --clear",
            "timeout": 5
          }
        ]
      }
    ]
  }
}
```

`UserPromptSubmit` resets the state to `running` on every prompt on purpose:
after the assistant reports `question` or `done`, the next prompt means work
has resumed, and nothing else would ever clear that.

- [ ] **Step 4: Run tests and validate the manifest**

```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check .
claude plugin validate --strict .
```

Expected: PASS, 109 + 5 = 114 tests; validation passes.

- [ ] **Step 5: Commit**

```bash
git add hooks tests/test_hook_report.py
git commit -m "feat: report session state from Claude Code hooks"
```

---

### Task 3: the skill

Hooks cannot know that a session is stuck. Only the assistant does.

**Files:**
- Create: `skills/report-status/SKILL.md`

- [ ] **Step 1: Write the skill**

Create `skills/report-status/SKILL.md`:

```markdown
---
name: report-status
description: Use when you hit a question for the user, hit a blocking problem, or finish the work - reports this session's state to the claude-dot-display LED board so the user can see it from across the room.
---

# Reporting session status to the board

Hooks already report that this session is **running**. They cannot know
anything else. Three states only you can report:

| State | When |
| --- | --- |
| `question` | You are waiting on the user and cannot proceed |
| `issue` | You hit something broken or blocking |
| `done` | The work you were asked for is finished |

Report with:

    dotdisplay status --name "$DOTDISPLAY_SESSION" --state question --left 3

`--left` is the number of stages still to go, if you are working through a
plan with stages. Omit it when there is no meaningful count. Never invent one.

## When to report

- **`question`** — immediately before you stop and ask the user something.
  The point is that they see it without reading the terminal.
- **`issue`** — when you hit a blocker you cannot resolve alone.
- **`done`** — when you finish. The next prompt sets the state back to
  `running` automatically, so you do not need to undo it.

## When not to report

- Do not report progress in the middle of work. `running` is already correct
  and the board is meant to be glanceable, not a progress bar.
- Do not report `done` for a step. Only for the work as a whole.
- Do not report a state you are not sure about. A board that is wrong is
  worse than a board that is stale: the user acts on it from across the room,
  where they cannot see the terminal to correct their impression.

## If the command fails

Ignore it and carry on. The board is a convenience; it is never worth
interrupting the work for. Do not retry, do not report the failure to the
user unless they ask.
```

- [ ] **Step 2: Verify the skill loads**

```bash
claude plugin validate --strict .
```

The skill's `description` is what the model matches against, so it names the
triggering situations rather than describing the mechanism.

- [ ] **Step 3: Commit**

```bash
git add skills
git commit -m "feat: add the report-status skill"
```

---

### Task 4: the setup command

**Files:**
- Create: `commands/dotdisplay-setup.md`

- [ ] **Step 1: Write the command**

Create `commands/dotdisplay-setup.md`:

```markdown
---
description: Install and start the claude-dot-display board daemon
---

Set up the claude-dot-display board on this machine.

1. Check whether the daemon is already installed and running:

       systemctl --user is-active dotdisplay.service

   If it is active, report that and stop — do not reinstall over a working
   service.

2. Find the panel's Bluetooth address if the user has not given one:

       bluetoothctl devices | grep -i IDM

   The panel advertises as `IDM-<last six hex digits of its address>`. If
   nothing is found, ask the user rather than guessing.

3. Confirm no other process owns the radio. `sensmonlight-idotmatrix-agent`
   is the known conflict:

       systemctl --user is-active sensmonlight-idotmatrix-agent.service

   Two owners of one radio produce failures that look like protocol bugs. If
   it is active, stop and tell the user before going further.

4. Run the installer from a checkout of the repository:

       DOTDISPLAY_MAC=<address> bash scripts/install.sh

5. Verify by looking, not by exit code:

       systemctl --user is-active dotdisplay.service
       journalctl --user -u dotdisplay.service -n 20 --no-pager

   Expect `panel connected` followed by `panel updated`. Then confirm with
   the user that the panel actually changed — a clean log is not evidence
   that anything lit up.
```

- [ ] **Step 2: Validate and commit**

```bash
claude plugin validate --strict .
git add commands
git commit -m "feat: add the /dotdisplay-setup command"
```

---

### Task 5: install and verify against a real session

**This is where the hook payload's actual shape gets checked**, rather than
assumed from this document.

- [ ] **Step 1: Confirm the daemon is running**

```bash
systemctl --user is-active dotdisplay.service
```

- [ ] **Step 2: Install the plugin from the local checkout**

```bash
claude plugin marketplace add ~/Documents/gitlab/claude_dot_display
claude plugin install claude-dot-display
claude plugin details claude-dot-display
```

Expect the inventory to list 3 hooks, 1 skill and 1 command.

- [ ] **Step 3: Capture a real hook payload**

Before trusting the derivation, look at what actually arrives:

```bash
cat > /tmp/dd-probe.sh <<'EOF'
#!/usr/bin/env bash
cat > /tmp/dd-payload.json
EOF
chmod +x /tmp/dd-probe.sh
```

Temporarily point one hook at the probe, start a session, then:

```bash
python3 -c "import json;d=json.load(open('/tmp/dd-payload.json'));print(sorted(d))"
```

**Record the real field names.** If `cwd` or `session_id` are spelled
differently, fix `hooks/report.py` and `src/dotdisplay/session_name.py`
together, and update their tests. Remove the probe afterwards.

- [ ] **Step 4: Verify end to end**

Start a fresh Claude Code session in a project directory and confirm:

- a row appears on the panel within about five seconds
- the name reads as `<dir>-<xx>` and is legible
- ending that session makes the row disappear

Photograph the panel:

```bash
ffmpeg -hide_banner -loglevel error -f v4l2 -input_format mjpeg \
  -video_size 1920x1080 -i /dev/video2 -vf "select=gte(n\,15)" \
  -frames:v 1 -q:v 2 -y /tmp/plugin-verify.jpg
```

**A session file on disk is not evidence. The panel must show the row.**

- [ ] **Step 5: Verify two concurrent sessions**

Open two sessions in the **same** directory. Both rows must appear with
different names — this is what the session-id suffix exists for. If they
collide, the suffix is not doing its job.

- [ ] **Step 6: Verify the skill**

In a session, ask something that makes the assistant stop with a question and
confirm the row turns amber. Then send any prompt and confirm it returns to
blue.

- [ ] **Step 7: Commit and push**

```bash
.venv/bin/python -m pytest -q && .venv/bin/ruff check .
git add -A && git commit -m "feat: verify the plugin against real sessions"
git push origin main && git push gitlab main
```

---

## Definition of done

- 114 tests pass locally and in CI on 3.11, 3.12, 3.13; ruff clean.
- `claude plugin validate --strict .` passes.
- `claude plugin details` lists 3 hooks, 1 skill, 1 command.
- A new session appears on the panel by itself; ending it removes the row.
- Two sessions in one directory get distinct names.
- The assistant can turn a row amber through the skill.
- The user's `settings.json` is unchanged.

## Deliberately not in P3

- `claude plugin eval` in CI. The eval harness needs API credentials in
  repository secrets, which is a decision about spending and secret handling
  rather than a technical step. Run evals locally; revisit if the plugin
  grows behaviour worth guarding that way.
- Reporting stage counts automatically from a plan's ledger. The skill takes
  `--left` by hand for now.
- Publishing to `anthropics/claude-plugins-official`.
