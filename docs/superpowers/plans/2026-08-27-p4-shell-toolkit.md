# P4 — Shell Toolkit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the panel usable from ordinary shell scripts, with or without Claude Code — and, critically, whether or not the board daemon is running.

**Architecture:** The obstacle is not the command set but the radio: the daemon owns it exclusively, so today every one-shot command fails while the board is up. The daemon already drains hwmon's command queue; this plan adds a **local** queue of the same shape and a heartbeat that tells the CLI which path to take. One executor, three callers.

**Tech Stack:** Python 3.11+, the P1 driver, pytest.

**Spec:** `docs/superpowers/specs/2026-08-27-architecture-design.md`

## The problem this solves

```
today:   dotdisplay check  ->  "Device not found"  ->  stop the daemon first
                                                        (fine for setup,
                                                         useless for scripts)

after:   daemon running    ->  CLI queues, daemon executes
         daemon stopped    ->  CLI connects directly
         either way        ->  the script does not need to know
```

## Global Constraints

- **Only one process may own the radio.** That is the entire reason this phase exists; do not add a second connector.
- **Scripts must not need to know whether the daemon runs.** A command that only works in one of the two states has not solved the problem.
- **Never leave a caller hanging.** Every queued command gets a result or a timeout, never silence.
- Runtime dependencies stay exactly `bleak`, `pillow`, `requests`.
- Work inside `.venv`. Test: `.venv/bin/python -m pytest -q`. **Baseline 131 tests.** Lint: `.venv/bin/ruff check .`
- No real Bluetooth in tests.
- **A passing test is not evidence the panel changed.** Task 5 verifies with the camera.

## File Structure

| File | Responsibility |
| --- | --- |
| `src/dotdisplay/queue.py` | The local request/result queue and the daemon heartbeat |
| `src/dotdisplay/commands.py` | One executor shared by the CLI, the local queue and hwmon |
| `src/dotdisplay/render.py` | Gains `render_text` |
| `src/dotdisplay/cli.py` | Gains `text`, `brightness`, `power`, `pixel`, `fill`, `clear`; `--json` |
| `src/dotdisplay/daemon.py` | Drains the local queue; writes the heartbeat |

---

### Task 1: queue.py — the local queue and heartbeat

**Files:**
- Create: `src/dotdisplay/queue.py`
- Test: `tests/test_queue.py`

**Interfaces:**
- Produces: `submit(config, command) -> str`, `claim(config) -> tuple[str, dict] | None`, `publish(config, request_id, result)`, `await_result(config, request_id, timeout_s) -> dict | None`, `beat(config)`, `daemon_is_alive(config) -> bool`, `HEARTBEAT_STALE_S`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_queue.py`:

```python
import dataclasses
import time

import pytest

from dotdisplay import queue as q
from dotdisplay.config import Config


@pytest.fixture
def cfg(tmp_path):
    return dataclasses.replace(Config.from_env(), state_dir=tmp_path / "sessions")


def test_a_submitted_command_can_be_claimed(cfg):
    request_id = q.submit(cfg, {"type": "power", "on": True})
    claimed = q.claim(cfg)
    assert claimed is not None
    assert claimed[0] == request_id
    assert claimed[1]["type"] == "power"


def test_a_command_is_claimed_only_once(cfg):
    """Two claims of one command would drive the panel twice."""
    q.submit(cfg, {"type": "power", "on": True})
    assert q.claim(cfg) is not None
    assert q.claim(cfg) is None


def test_commands_are_claimed_oldest_first(cfg):
    first = q.submit(cfg, {"type": "power", "on": True})
    time.sleep(0.01)
    q.submit(cfg, {"type": "power", "on": False})
    assert q.claim(cfg)[0] == first


def test_a_result_reaches_the_caller(cfg):
    request_id = q.submit(cfg, {"type": "power", "on": True})
    q.claim(cfg)
    q.publish(cfg, request_id, {"status": "done"})
    assert q.await_result(cfg, request_id, timeout_s=1)["status"] == "done"


def test_waiting_for_a_result_times_out_rather_than_hanging(cfg):
    """A script must never block forever because the daemon died."""
    request_id = q.submit(cfg, {"type": "power", "on": True})
    started = time.monotonic()
    assert q.await_result(cfg, request_id, timeout_s=0.3) is None
    assert time.monotonic() - started < 3


def test_an_empty_queue_claims_nothing(cfg):
    assert q.claim(cfg) is None


def test_a_malformed_request_is_discarded_not_retried(cfg):
    """A request that cannot be parsed would otherwise be claimed forever."""
    q.submit(cfg, {"type": "power", "on": True})
    bad = next((cfg.state_dir.parent / "queue" / "requests").glob("*.json"))
    bad.write_text("{not json")
    assert q.claim(cfg) is None
    assert not bad.exists()


def test_no_heartbeat_means_no_daemon(cfg):
    assert q.daemon_is_alive(cfg) is False


def test_a_fresh_heartbeat_means_a_daemon(cfg):
    q.beat(cfg)
    assert q.daemon_is_alive(cfg) is True


def test_a_stale_heartbeat_does_not_count(cfg):
    """A daemon that died must not make every command queue into the void."""
    import os
    q.beat(cfg)
    path = q._heartbeat_path(cfg)
    old = time.time() - q.HEARTBEAT_STALE_S - 5
    os.utime(path, (old, old))
    assert q.daemon_is_alive(cfg) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_queue.py -q`
Expected: FAIL — no module `dotdisplay.queue`.

- [ ] **Step 3: Write the implementation**

Create `src/dotdisplay/queue.py`:

```python
"""A local command queue, so shell callers work whether or not the daemon runs.

Only one process may own the radio. Rather than making every caller stop the
daemon, a caller submits here and the daemon -- which already holds the
connection -- executes it. The heartbeat tells the caller which path to take.

Deliberately the same shape as hwmon's agent queue: that arrangement has been
in production for weeks, and a second mechanism would be a second thing to
debug at 2am.
"""

import json
import logging
import pathlib
import time
import uuid

logger = logging.getLogger(__name__)

HEARTBEAT_STALE_S = 30.0     # comfortably more than the 5s poll interval
POLL_S = 0.1


def _root(config) -> pathlib.Path:
    return pathlib.Path(config.state_dir).parent / "queue"


def _requests(config) -> pathlib.Path:
    return _root(config) / "requests"


def _results(config) -> pathlib.Path:
    return _root(config) / "results"


def _heartbeat_path(config) -> pathlib.Path:
    return _root(config) / "daemon.alive"


def beat(config) -> None:
    """Record that a daemon is holding the radio right now."""
    path = _heartbeat_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(time.time()))


def daemon_is_alive(config) -> bool:
    try:
        age = time.time() - _heartbeat_path(config).stat().st_mtime
    except OSError:
        return False
    return age < HEARTBEAT_STALE_S


def submit(config, command: dict) -> str:
    request_id = uuid.uuid4().hex
    directory = _requests(config)
    directory.mkdir(parents=True, exist_ok=True)
    # Write beside, then rename: a claimer must never see a half-written file.
    tmp = directory / f".{request_id}.tmp"
    tmp.write_text(json.dumps(command))
    tmp.rename(directory / f"{request_id}.json")
    return request_id


def claim(config):
    """Take the oldest pending command, or None. Claiming removes it, so a
    command cannot be executed twice."""
    directory = _requests(config)
    if not directory.is_dir():
        return None
    for path in sorted(directory.glob("*.json"), key=lambda p: p.stat().st_mtime):
        try:
            body = json.loads(path.read_text())
        except (OSError, ValueError) as exc:
            # Discard rather than leave it to be claimed forever.
            logger.warning("discarding unreadable request %s: %s", path.name, exc)
            path.unlink(missing_ok=True)
            continue
        path.unlink(missing_ok=True)
        return path.stem, body
    return None


def publish(config, request_id: str, result: dict) -> None:
    directory = _results(config)
    directory.mkdir(parents=True, exist_ok=True)
    tmp = directory / f".{request_id}.tmp"
    tmp.write_text(json.dumps(result))
    tmp.rename(directory / f"{request_id}.json")


def await_result(config, request_id: str, timeout_s: float = 30.0):
    """Wait for a result. Returns None on timeout -- never blocks forever."""
    path = _results(config) / f"{request_id}.json"
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            result = json.loads(path.read_text())
        except (OSError, ValueError):
            time.sleep(POLL_S)
            continue
        path.unlink(missing_ok=True)
        return result
    return None
```

- [ ] **Step 4: Run tests and commit**

Run: `.venv/bin/python -m pytest -q && .venv/bin/ruff check .`
Expected: PASS, 131 + 10 = 141 tests.

```bash
git add src/dotdisplay/queue.py tests/test_queue.py
git commit -m "feat: add a local command queue and daemon heartbeat"
```

---

### Task 2: commands.py — one executor

Today `daemon._execute` handles hwmon's commands. Three callers will now want the same thing, so it moves out and grows the operations a shell user needs.

**Files:**
- Create: `src/dotdisplay/commands.py`
- Modify: `src/dotdisplay/daemon.py`, `src/dotdisplay/render.py`
- Test: `tests/test_commands.py`, `tests/test_render.py`

**Interfaces:**
- Produces: `execute(panel, command: dict) -> dict`, `KINDS`; `render.render_text(text, colour) -> Image`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_commands.py`:

```python
import pytest

from dotdisplay import commands


class FakePanel:
    def __init__(self):
        self.calls = []

    async def set_brightness(self, pct):
        self.calls.append(("brightness", pct))

    async def power(self, on):
        self.calls.append(("power", on))

    async def draw_pixel(self, x, y, rgb):
        self.calls.append(("pixel", x, y, rgb))

    async def send_image(self, img):
        self.calls.append(("image", img.size))


async def test_brightness():
    panel = FakePanel()
    await commands.execute(panel, {"type": "brightness", "percent": 40})
    assert panel.calls == [("brightness", 40)]


async def test_power():
    panel = FakePanel()
    await commands.execute(panel, {"type": "power", "on": False})
    assert panel.calls == [("power", False)]


async def test_pixel_accepts_a_hex_colour():
    """Shell callers have hex, not tuples."""
    panel = FakePanel()
    await commands.execute(panel, {"type": "pixel", "x": 3, "y": 4,
                                   "colour": "ff8000"})
    assert panel.calls == [("pixel", 3, 4, (255, 128, 0))]


async def test_fill_sends_a_full_frame():
    panel = FakePanel()
    await commands.execute(panel, {"type": "fill", "colour": "00ff00"})
    assert panel.calls == [("image", (64, 64))]


async def test_clear_is_a_black_fill():
    panel = FakePanel()
    await commands.execute(panel, {"type": "clear"})
    assert panel.calls == [("image", (64, 64))]


async def test_text_is_rasterised_here():
    """The device has no font -- see PROTOCOL.md. Text is an image."""
    panel = FakePanel()
    await commands.execute(panel, {"type": "text", "text": "HELLO"})
    assert panel.calls == [("image", (64, 64))]


async def test_an_unknown_type_is_an_error_not_a_silent_no_op():
    panel = FakePanel()
    with pytest.raises(ValueError):
        await commands.execute(panel, {"type": "teleport"})
    assert panel.calls == []


@pytest.mark.parametrize("bad", ["nothex", "#12345", "", "ff00"])
async def test_a_bad_colour_is_rejected(bad):
    with pytest.raises(ValueError):
        await commands.execute(FakePanel(), {"type": "fill", "colour": bad})
```

Append to `tests/test_render.py`:

```python
def test_text_fills_the_panel():
    img = r.render_text("HELLO")
    assert img.size == (64, 64)
    lit = sum(1 for p in img.getdata() if sum(p) > 60)
    assert lit > 100, "text is too small to be worth showing"


def test_long_text_still_fits():
    """Overflowing the panel silently would drop the end of the message."""
    assert r.render_text("the quick brown fox jumps over").size == (64, 64)


def test_text_rendering_is_deterministic():
    assert r.render_text("HELLO").tobytes() == r.render_text("HELLO").tobytes()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_commands.py -q`
Expected: FAIL — no module `dotdisplay.commands`.

- [ ] **Step 3: Write the implementation**

Add to `src/dotdisplay/render.py`:

```python
TEXT_SIZES = (26, 20, 16, 13, 11, 9, 8)   # largest first; first that fits wins


def render_text(text: str, colour=(255, 255, 255)) -> Image.Image:
    """Rasterise text to a full frame.

    The device has no font -- the vendor application ships glyph bitmaps per
    character (see PROTOCOL.md). Rendering here and sending an image is both
    simpler and the fastest path we have.

    Picks the largest size at which the text still fits, so a short word is
    readable across a room and a long one is merely readable.
    """
    import textwrap

    img, draw = _canvas()
    words = str(text).split()
    for size in TEXT_SIZES:
        try:
            font = ImageFont.truetype(_FONT_PATHS[0], size)
        except OSError:
            font = ImageFont.load_default()
        per_line = max(1, int((W - 2 * MARGIN) / max(1, draw.textlength("M", font=font))))
        lines = textwrap.wrap(" ".join(words), width=per_line) or [""]
        line_h = size + 2
        if len(lines) * line_h <= H and all(
                draw.textlength(line, font=font) <= W - 2 * MARGIN for line in lines):
            y = (H - len(lines) * line_h) // 2
            for line in lines:
                width = draw.textlength(line, font=font)
                draw.text(((W - width) / 2, y), line, font=font, fill=colour)
                y += line_h
            return img
    return img          # nothing fit; an empty frame beats a garbled one
```

Create `src/dotdisplay/commands.py`:

```python
"""One executor for panel operations, shared by every caller.

The CLI, the local queue and hwmon's queue all end up here, so a command
behaves identically no matter where it came from.
"""

import re

from dotdisplay import render

KINDS = ("brightness", "power", "pixel", "fill", "clear", "text",
         "send_image", "set_brightness")

_HEX = re.compile(r"^[0-9A-Fa-f]{6}$")


def parse_colour(value) -> tuple[int, int, int]:
    """Accept what a shell caller has: six hex digits."""
    text = str(value).lstrip("#")
    if not _HEX.match(text):
        raise ValueError(f"colour must be six hex digits, got {value!r}")
    return tuple(int(text[i: i + 2], 16) for i in (0, 2, 4))


async def execute(panel, command: dict) -> dict:
    kind = command.get("type")

    # hwmon uses its own spellings; keep accepting them so the server needs
    # no change.
    if kind in ("brightness", "set_brightness"):
        percent = command.get("percent", command.get("brightness_percent"))
        await panel.set_brightness(int(percent))
    elif kind == "power":
        await panel.power(bool(command["on"]))
    elif kind == "pixel":
        await panel.draw_pixel(int(command["x"]), int(command["y"]),
                               parse_colour(command["colour"]))
    elif kind == "fill":
        colour = parse_colour(command["colour"])
        from PIL import Image
        await panel.send_image(Image.new("RGB", (render.W, render.H), colour))
    elif kind == "clear":
        from PIL import Image
        await panel.send_image(Image.new("RGB", (render.W, render.H), (0, 0, 0)))
    elif kind == "text":
        colour = (parse_colour(command["colour"]) if command.get("colour")
                  else (255, 255, 255))
        await panel.send_image(render.render_text(command["text"], colour))
    elif kind == "send_image":
        import base64
        import io

        from PIL import Image
        raw = base64.b64decode(command["image_base64"])
        await panel.send_image(Image.open(io.BytesIO(raw)))
    else:
        raise ValueError(f"unsupported command type {kind!r}")
    return {"sent": True}
```

In `daemon.py`, replace the body of `_execute` with a call to
`commands.execute`, keeping the name so `serve_commands` is untouched:

```python
from dotdisplay import commands


async def _execute(panel, command: dict) -> dict:
    return await commands.execute(panel, command)
```

- [ ] **Step 4: Run tests and commit**

Expected: PASS, 141 + 12 = 153 tests.

```bash
git add src/dotdisplay tests/test_commands.py tests/test_render.py
git commit -m "feat: share one panel executor across every caller"
```

---

### Task 3: the daemon serves the local queue

**Files:**
- Modify: `src/dotdisplay/daemon.py`
- Test: `tests/test_daemon.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_daemon.py`:

```python
async def test_the_daemon_beats_while_it_holds_the_radio(cfg, mocker):
    """The heartbeat is how a shell caller knows to queue instead of
    connecting, so it must be written whenever the radio is held."""
    from dotdisplay import queue as q
    mocker.patch("dotdisplay.daemon.sources.read_sessions", return_value=[])
    mocker.patch("dotdisplay.daemon.sources.read_header", return_value=None)
    mocker.patch("dotdisplay.daemon.sources.ccusage_stats", return_value={})
    mocker.patch("dotdisplay.daemon.sources.trends", return_value={})
    await d.tick(cfg, d.Board(), FakePanel())
    assert q.daemon_is_alive(cfg) is True


async def test_local_queue_commands_are_executed_and_answered(cfg, mocker):
    from dotdisplay import queue as q
    request_id = q.submit(cfg, {"type": "power", "on": True})

    class PowerPanel(FakePanel):
        def __init__(self):
            super().__init__()
            self.on = None

        async def power(self, on):
            self.on = on

    panel = PowerPanel()
    assert await d.serve_local_queue(cfg, panel) == 1
    assert panel.on is True
    assert q.await_result(cfg, request_id, timeout_s=1)["status"] == "done"


async def test_a_failing_local_command_is_answered_with_an_error(cfg):
    """Silence would leave the caller waiting for its full timeout."""
    from dotdisplay import queue as q
    request_id = q.submit(cfg, {"type": "nonsense"})
    await d.serve_local_queue(cfg, FakePanel())
    assert q.await_result(cfg, request_id, timeout_s=1)["status"] == "error"
```

- [ ] **Step 2: Write the implementation**

Add to `daemon.py`:

```python
from dotdisplay import queue as _queue


async def serve_local_queue(config: Config, panel) -> int:
    """Execute commands submitted by local shell callers."""
    ran = 0
    while True:
        claimed = _queue.claim(config)
        if not claimed:
            return ran
        request_id, body = claimed
        try:
            result = {"status": "done", "result": await commands.execute(panel, body)}
        except Exception as exc:          # noqa: BLE001 - must always answer
            result = {"status": "error", "message": str(exc)}
        _queue.publish(config, request_id, result)
        ran += 1
```

Call it from `tick` (so it runs on every pass) and beat the heartbeat there
too:

```python
async def tick(config: Config, board: Board, panel) -> bool:
    _queue.beat(config)
    await serve_local_queue(config, panel)
    ...
```

Beating inside `tick` rather than in `run` is deliberate: `tick` only runs
while a panel connection is held, which is exactly the condition the
heartbeat is meant to advertise.

- [ ] **Step 3: Run tests and commit**

Expected: PASS, 153 + 3 = 156 tests.

```bash
git add src/dotdisplay/daemon.py tests/test_daemon.py
git commit -m "feat: serve the local command queue from the daemon"
```

---

### Task 4: the CLI commands

**Files:**
- Modify: `src/dotdisplay/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Produces: `dotdisplay text|brightness|power|pixel|fill|clear`, each with `--json`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli.py`:

```python
def test_a_command_is_queued_when_the_daemon_holds_the_radio(tmp_path, monkeypatch, mocker):
    """The whole point: a script must not have to stop the daemon."""
    from dotdisplay import queue as q
    monkeypatch.setenv("DOTDISPLAY_STATE_DIR", str(tmp_path / "sessions"))
    monkeypatch.setenv("DOTDISPLAY_MAC", "AA:BB:CC:DD:EE:FF")
    mocker.patch("dotdisplay.queue.daemon_is_alive", return_value=True)
    mocker.patch("dotdisplay.queue.await_result", return_value={"status": "done"})
    connect = mocker.patch("dotdisplay.ble.PanelClient")
    assert cli.main(["power", "on"]) == 0
    connect.assert_not_called()          # queued, not connected


def test_a_command_connects_directly_without_a_daemon(tmp_path, monkeypatch, mocker):
    monkeypatch.setenv("DOTDISPLAY_STATE_DIR", str(tmp_path / "sessions"))
    monkeypatch.setenv("DOTDISPLAY_MAC", "AA:BB:CC:DD:EE:FF")
    mocker.patch("dotdisplay.queue.daemon_is_alive", return_value=False)

    class FakePanel:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def power(self, on):
            pass

    mocker.patch("dotdisplay.ble.PanelClient", return_value=FakePanel())
    assert cli.main(["power", "on"]) == 0


def test_a_queue_timeout_is_reported_not_swallowed(tmp_path, monkeypatch, mocker, capsys):
    monkeypatch.setenv("DOTDISPLAY_STATE_DIR", str(tmp_path / "sessions"))
    monkeypatch.setenv("DOTDISPLAY_MAC", "AA:BB:CC:DD:EE:FF")
    mocker.patch("dotdisplay.queue.daemon_is_alive", return_value=True)
    mocker.patch("dotdisplay.queue.await_result", return_value=None)
    assert cli.main(["power", "on"]) == 1
    assert "timed out" in capsys.readouterr().err


def test_json_output_is_machine_readable(tmp_path, monkeypatch, mocker, capsys):
    monkeypatch.setenv("DOTDISPLAY_STATE_DIR", str(tmp_path / "sessions"))
    monkeypatch.setenv("DOTDISPLAY_MAC", "AA:BB:CC:DD:EE:FF")
    mocker.patch("dotdisplay.queue.daemon_is_alive", return_value=True)
    mocker.patch("dotdisplay.queue.await_result", return_value={"status": "done"})
    assert cli.main(["--json", "power", "on"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "done"


@pytest.mark.parametrize("args", [
    ["text", "HELLO"],
    ["brightness", "40"],
    ["pixel", "1", "2", "ff0000"],
    ["fill", "00ff00"],
    ["clear"],
])
def test_every_command_reaches_the_queue(args, tmp_path, monkeypatch, mocker):
    monkeypatch.setenv("DOTDISPLAY_STATE_DIR", str(tmp_path / "sessions"))
    monkeypatch.setenv("DOTDISPLAY_MAC", "AA:BB:CC:DD:EE:FF")
    mocker.patch("dotdisplay.queue.daemon_is_alive", return_value=True)
    mocker.patch("dotdisplay.queue.await_result", return_value={"status": "done"})
    assert cli.main(args) == 0
```

- [ ] **Step 2: Write the implementation**

Add a `--json` flag to the top-level parser, subparsers for the five
commands, and one dispatcher:

```python
def _run_command(command: dict, as_json: bool) -> int:
    """Queue it if the daemon holds the radio, otherwise connect directly.

    A script should not have to know which of those is the case.
    """
    import asyncio

    from dotdisplay import commands, queue
    from dotdisplay.ble import PanelClient
    from dotdisplay.config import Config

    config = Config.from_env()
    if not config.mac:
        print("DOTDISPLAY_MAC is not set. Run 'dotdisplay discover'.",
              file=sys.stderr)
        return 1

    if queue.daemon_is_alive(config):
        request_id = queue.submit(config, command)
        result = queue.await_result(config, request_id, timeout_s=60)
        if result is None:
            print("timed out waiting for the board daemon", file=sys.stderr)
            return 1
    else:
        async def go():
            async with PanelClient(config.mac) as panel:
                return await commands.execute(panel, command)

        try:
            result = {"status": "done", "result": asyncio.run(go())}
        except Exception as exc:      # noqa: BLE001 - report, no traceback
            result = {"status": "error", "message": str(exc)}

    if as_json:
        print(json.dumps(result))
    elif result.get("status") == "error":
        print(f"dotdisplay: {result.get('message')}", file=sys.stderr)
    return 0 if result.get("status") == "done" else 1
```

- [ ] **Step 3: Run tests and commit**

Expected: PASS, 156 + 9 = 165 tests.

```bash
git add src/dotdisplay/cli.py tests/test_cli.py
git commit -m "feat: add shell commands that work with or without the daemon"
```

---

### Task 5: hardware verification and documentation

- [ ] **Step 1: Verify with the daemon running**

```bash
systemctl --user is-active dotdisplay.service      # active
dotdisplay text "HALLO"
```

Photograph. The text must appear **without stopping the daemon** — that is
the whole point of this phase.

```bash
dotdisplay fill 00ff00 && sleep 3
dotdisplay pixel 0 0 ff0000
dotdisplay brightness 20 && sleep 2 && dotdisplay brightness 90
dotdisplay clear
```

Check each on the panel, not in the log.

- [ ] **Step 2: Verify without the daemon**

```bash
systemctl --user stop dotdisplay.service
dotdisplay text "OHNE DAEMON"
systemctl --user start dotdisplay.service
```

Same result by a different route. **If this only works in one of the two
states, the phase has not achieved its goal.**

- [ ] **Step 3: Verify a script does not need to know**

```bash
for state in stop start; do
  systemctl --user $state dotdisplay.service; sleep 3
  dotdisplay --json fill 0000ff | tee /dev/stderr | grep -q '"status": "done"'
done
```

Both iterations must report `done`.

- [ ] **Step 4: Document and commit**

Add a **Shell usage** section to the README listing every command with one
example each, and state plainly that they work with or without the daemon.

```bash
.venv/bin/python -m pytest -q && .venv/bin/ruff check .
git add -A && git commit -m "feat: verify the shell toolkit against the panel"
git push origin main && git push gitlab main
```

---

## Definition of done

- 165 tests pass locally and in CI; ruff clean.
- Every command works **both** with the daemon running and stopped.
- `--json` emits parseable output and exit codes match the status.
- A queued command that is never answered times out rather than hanging.
- The README documents shell usage.

## Deliberately not in P4

- Animation. The protocol entry is unverified.
- A daemon control socket. The file queue matches hwmon's proven arrangement,
  and a second IPC mechanism would be a second thing to debug.
