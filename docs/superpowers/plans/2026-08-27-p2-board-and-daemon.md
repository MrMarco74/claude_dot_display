# P2 — Board and Daemon Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Put Claude Code sessions on the panel. A daemon watches local session state, renders one of two screens, and drives the panel through the P1 driver — while also serving hwmon's existing command queue, so it can replace `sensmonlight-idotmatrix-agent` outright.

**Architecture:** Pure rendering (`render.py`) is separated from where state comes from (`sources.py`) and from the loop that ties them together (`daemon.py`). The daemon is the sole owner of the radio and holds one BLE connection open, reconnecting on failure.

**Tech Stack:** Python 3.11+, Pillow, `requests`, `bleak` (via P1's `dotdisplay.ble`), pytest, systemd user units.

**Spec:** `docs/superpowers/specs/2026-08-27-architecture-design.md`
**Driver:** `PROTOCOL.md` and `src/dotdisplay/ble/` — hardware-verified in P1.

## What P1 changed about this plan

The architecture spec deferred P2's design until refresh speed was known. It is now **0.77 s** per full frame, measured. Consequences, all folded in below:

- **Send-on-change is an optimisation, not a constraint.** It stays because re-sending an identical board wastes an exclusive radio for no reason, but no part of the design has to bend around it.
- **The daemon drives the panel directly.** The spec's original sketch had the renderer queueing `send_image` through hwmon-server; that detour existed only because `worker` has no Bluetooth. This runs on the workstation with the radio.
- **One connection, held open.** Reconnecting per operation would dominate a five-second loop.

## Global Constraints

- **Layout values were measured on the physical panel. Do not "improve" them.** Font **8px** (DejaVuSansMono-Bold), rows **9px** apart, left margin **x=2**, right edge **x=62**, divider under the header.
- **`ImageDraw.fontmode = "1"` is mandatory** on every draw context. Pillow antialiases by default and the blended pixels turn to mush on an LED matrix.
- **Never render a `%` glyph.** Verified unresolvable at 8px.
- **Arrows are polygons, never font glyphs.**
- **Status colours:** issue `(255,30,30)`, question `(255,215,0)`, done `(0,230,80)`, running `(60,120,255)`. **Never grey** — it renders as washed-out lavender.
- **Ordering is pure alphabetical by session name.** Not by status, not by stage count: a row must only move when a session appears or disappears.
- **Nothing identifying in the repo.** No MAC addresses, no hostnames, no capture files. Panel address from `DOTDISPLAY_MAC`; hwmon URL from `DOTDISPLAY_HWMON_URL`.
- **Use builtin generics (`list[dict]`, `dict | None`), not `typing.List`/`Optional`.** ruff's `UP` rules are enabled and reject the legacy spelling.
- **Runtime dependencies stay exactly `bleak`, `pillow`, `requests`.** `tests/test_licensing.py` fails the build otherwise.
- **ccusage must be cached.** It parses ~1,160 transcript files; never call it from the poll loop.
- **Only one process may own the radio.** `sensmonlight-idotmatrix-agent.service` must be stopped before the daemon starts (Task 6).
- Work inside `.venv`. Test: `.venv/bin/python -m pytest -q`. **Baseline 44 tests.** Lint: `.venv/bin/ruff check .`
- No real network or Bluetooth in tests. No test above ~1s.
- **A passing test is not evidence the panel changed.** Task 6 verifies with the camera.

## File Structure

| File | Responsibility |
| --- | --- |
| `src/dotdisplay/render.py` | Pure: state to a 64x64 PIL image. No I/O |
| `src/dotdisplay/sources.py` | Where session state comes from: local files, optional HTTP, ccusage, rate limits |
| `src/dotdisplay/daemon.py` | The loop. Owns the radio, renders, sends on change, serves hwmon commands |
| `src/dotdisplay/config.py` | Environment-derived settings |
| `src/dotdisplay/cli.py` | Grows `daemon`, `status`, `send` subcommands |
| `packaging/dotdisplay.service` | systemd user unit |
| `tests/test_render.py`, `tests/test_sources.py`, `tests/test_daemon.py`, `tests/test_config.py` | |

---

### Task 1: render.py — the two screens

**Files:**
- Create: `src/dotdisplay/render.py`
- Test: `tests/test_render.py`

**Interfaces:**
- Consumes: nothing (pure; Pillow only).
- Produces: `render_sessions(sessions: list[dict], header: dict | None) -> Image`, `render_idle(stats: dict, trends: dict, header: dict | None) -> Image`, `header_colour(pct) -> tuple`, `human_tokens(n: int) -> str`, `STATUS_COLOURS`, `BAND_COLOURS`.

Returning a PIL image rather than PNG bytes: the driver wants an image, and the daemon compares `img.tobytes()` for change detection. Encoding to PNG in between would be work for nobody.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_render.py`:

```python
import pytest

from dotdisplay import render as r


def _sessions(n):
    return [{"name": f"sess{i}", "status": "running", "stages_left": i}
            for i in range(n)]


HEADER = {"pct": 50, "reset": "17:10"}


def test_render_is_a_64x64_image():
    img = r.render_sessions(_sessions(2), HEADER)
    assert img.size == (64, 64)
    assert img.mode == "RGB"


def test_same_input_produces_identical_pixels():
    """The daemon only sends when the rendering changes, so rendering MUST be
    deterministic -- any nondeterminism would spam an exclusive radio."""
    assert (r.render_sessions(_sessions(3), HEADER).tobytes()
            == r.render_sessions(_sessions(3), HEADER).tobytes())


def test_changed_status_changes_the_rendering():
    before = r.render_sessions(_sessions(1), HEADER).tobytes()
    changed = _sessions(1)
    changed[0]["status"] = "issue"
    assert r.render_sessions(changed, HEADER).tobytes() != before


def test_input_order_does_not_matter():
    a = [{"name": "aaa", "status": "running", "stages_left": 1},
         {"name": "bbb", "status": "running", "stages_left": 2}]
    assert (r.render_sessions(a, HEADER).tobytes()
            == r.render_sessions(list(reversed(a)), HEADER).tobytes())


def test_overflow_is_indicated_not_silently_dropped():
    """Dropping sessions without saying so would make the board lie."""
    assert (r.render_sessions(_sessions(9), HEADER).tobytes()
            != r.render_sessions(_sessions(4), HEADER).tobytes())


@pytest.mark.parametrize("pct,band", [(10, "green"), (70, "amber"), (95, "red")])
def test_header_colour_bands(pct, band):
    assert r.header_colour(pct) == r.BAND_COLOURS[band]


def test_no_percent_glyph_is_ever_drawn():
    """Verified unreadable at 8px on the physical panel. Checks the whole
    module: a % reaching the panel from any helper is the same bug."""
    import inspect
    assert "%" not in inspect.getsource(r)


def test_status_colours_are_never_grey():
    """Grey renders as washed-out lavender on real LEDs."""
    for rgb in r.STATUS_COLOURS.values():
        assert len(set(rgb)) > 1, f"{rgb} is grey"


def test_idle_screen_differs_from_the_session_screen():
    idle = r.render_idle({"today": 1, "out": 2, "cache": 3, "read": 4, "all": 5},
                         trends={}, header=HEADER)
    assert idle.tobytes() != r.render_sessions(_sessions(1), HEADER).tobytes()


def test_missing_trend_renders_no_arrow():
    """An arrow with no comparison would imply information that does not
    exist."""
    stats = {"today": 10, "out": 2, "cache": 3, "read": 4, "all": 5}
    assert (r.render_idle(stats, {"today": True}, HEADER).tobytes()
            != r.render_idle(stats, {}, HEADER).tobytes())


def test_header_is_optional():
    assert r.render_sessions(_sessions(1), None).size == (64, 64)


@pytest.mark.parametrize("n,expect", [(999, "999"), (1500, "1.5k"),
                                      (472_049_430, "472M"),
                                      (12_245_280_929, "12G"),
                                      (1_352_334, "1.4M")])
def test_human_tokens(n, expect):
    assert r.human_tokens(n) == expect


def test_long_names_are_truncated_to_the_display_budget():
    img = r.render_sessions(
        [{"name": "a" * 40, "status": "running", "stages_left": 1}], HEADER)
    assert img.size == (64, 64)      # must not raise or overflow
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_render.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'dotdisplay.render'`

- [ ] **Step 3: Write the implementation**

Create `src/dotdisplay/render.py`:

```python
"""Draws the two 64x64 screens.

Pure: no I/O, no network. Every constant here was measured on the physical
panel -- see the architecture design. Do not adjust them without
re-photographing the result.
"""

from PIL import Image, ImageDraw, ImageFont

W = H = 64
MARGIN = 2          # x=0 abuts the bezel and reads as clipped; all 64 columns ARE lit
RIGHT = 62
FONT_SIZE = 8       # verified legible; a larger font was tried and rejected
ROW_H = 9
NAME_CHARS = 9      # what fits beside a right-aligned two-digit count

STATUS_COLOURS = {
    "issue":    (255, 30, 30),
    "question": (255, 215, 0),
    "done":     (0, 230, 80),
    "running":  (60, 120, 255),   # never grey: grey renders as washed-out lavender
}
BAND_COLOURS = {"green": (0, 230, 80), "amber": (255, 215, 0), "red": (255, 30, 30)}
UP_COLOUR, DOWN_COLOUR = (255, 170, 0), (0, 220, 90)
LABEL_COLOUR, VALUE_COLOUR, DIVIDER = (150, 150, 150), (255, 255, 255), (45, 45, 45)

_FONT_PATHS = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
)


def _font():
    for path in _FONT_PATHS:
        try:
            return ImageFont.truetype(path, FONT_SIZE)
        except OSError:
            continue
    return ImageFont.load_default()


def _canvas():
    img = Image.new("RGB", (W, H), (0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.fontmode = "1"   # MANDATORY: antialiased text is unreadable on an LED matrix
    return img, draw


def header_colour(pct: float):
    """Warn before the cutoff, not after."""
    if pct < 60:
        return BAND_COLOURS["green"]
    if pct < 85:
        return BAND_COLOURS["amber"]
    return BAND_COLOURS["red"]


def human_tokens(n: int) -> str:
    for unit, div in (("G", 1e9), ("M", 1e6), ("k", 1e3)):
        if n >= div:
            value = n / div
            return f"{value:.0f}{unit}" if value >= 10 else f"{value:.1f}{unit}"
    return str(n)


def _arrow(draw, x, y, up: bool):
    """5x5 filled triangle. Deliberately NOT a font glyph -- dense glyphs do
    not resolve at this size, and this keeps the arrow independent of font
    metrics."""
    points = ([(x + 2, y), (x, y + 4), (x + 4, y + 4)] if up
              else [(x + 2, y + 4), (x, y), (x + 4, y)])
    draw.polygon(points, fill=UP_COLOUR if up else DOWN_COLOUR)


def _right(draw, text, font, y, colour):
    draw.text((RIGHT - draw.textlength(text, font=font), y), text,
              font=font, fill=colour)


def _draw_header(draw, font, header: dict | None) -> int:
    """Shared by BOTH screens: the five-hour window is persistent context and
    must not vanish when the board switches views. Returns the content top."""
    if not header:
        return 0
    colour = header_colour(header["pct"])
    # Position and colour carry the meaning; the percent sign does not resolve.
    draw.text((MARGIN, 0), f"{header['pct']:.0f}", font=font, fill=colour)
    _right(draw, header["reset"], font, 0, colour)
    draw.line([(MARGIN, 9), (RIGHT, 9)], fill=DIVIDER)
    return 12


def render_sessions(sessions: list[dict], header: dict | None) -> Image.Image:
    img, draw = _canvas()
    font = _font()
    y = _draw_header(draw, font, header)

    rows = sorted(sessions, key=lambda s: s["name"])   # pure alphabetical, by decision
    capacity = (H - y) // ROW_H
    overflow = len(rows) - capacity
    if overflow > 0:
        rows, overflow = rows[: capacity - 1], overflow + 1
    else:
        overflow = 0

    for session in rows:
        colour = STATUS_COLOURS.get(session["status"], VALUE_COLOUR)
        draw.text((MARGIN, y), session["name"][:NAME_CHARS], font=font, fill=colour)
        left = session.get("stages_left")
        if left is not None:
            _right(draw, str(left), font, y, VALUE_COLOUR)
        y += ROW_H
    if overflow:
        draw.text((MARGIN, y), f"+{overflow} more", font=font, fill=LABEL_COLOUR)
    return img


def render_idle(stats: dict[str, int], trends: dict[str, bool],
                header: dict | None) -> Image.Image:
    img, draw = _canvas()
    font = _font()
    y = _draw_header(draw, font, header)

    order = [("today", VALUE_COLOUR), ("out", STATUS_COLOURS["done"]),
             ("cache", STATUS_COLOURS["question"]), ("read", (160, 160, 160)),
             ("all", (120, 170, 255))]
    for key, colour in order:
        if key not in stats:
            continue
        draw.text((MARGIN, y), key, font=font, fill=LABEL_COLOUR)
        text = human_tokens(stats[key])
        _right(draw, text, font, y, colour)
        if key in trends:      # absent trend -> no arrow, never a guessed one
            width = draw.textlength(text, font=font)
            _arrow(draw, int(RIGHT - width - 7), y + 2, trends[key])
        y += ROW_H
    return img
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest -q && .venv/bin/ruff check .`
Expected: PASS, 44 + 19 = 63 tests.

- [ ] **Step 5: Commit**

```bash
git add src/dotdisplay/render.py tests/test_render.py
git commit -m "feat: add the session and idle screens"
```

---

### Task 2: sources.py — where state comes from

**Files:**
- Create: `src/dotdisplay/sources.py`, `src/dotdisplay/config.py`
- Test: `tests/test_sources.py`, `tests/test_config.py`

**Interfaces:**
- Consumes: `Config` (this task).
- Produces: `Config.from_env()`; `read_local_sessions(cfg) -> list[dict]`, `fetch_remote_sessions(cfg) -> list[dict]`, `read_sessions(cfg) -> list[dict]`, `read_header() -> dict | None`, `ccusage_stats(cfg, cache) -> dict`, `trends(stats, path) -> dict[str, bool]`, `CcusageCache`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_config.py`:

```python
from dotdisplay.config import Config


def test_defaults_need_no_environment(monkeypatch):
    for key in list(dict(__import__("os").environ)):
        if key.startswith("DOTDISPLAY_"):
            monkeypatch.delenv(key, raising=False)
    cfg = Config.from_env()
    assert cfg.poll_s > 0
    assert cfg.mac == ""
    assert cfg.hwmon_url == ""


def test_environment_overrides(monkeypatch):
    monkeypatch.setenv("DOTDISPLAY_MAC", "AA:BB:CC:DD:EE:FF")
    monkeypatch.setenv("DOTDISPLAY_POLL_S", "2.5")
    monkeypatch.setenv("DOTDISPLAY_HWMON_URL", "https://example.invalid/")
    cfg = Config.from_env()
    assert cfg.mac == "AA:BB:CC:DD:EE:FF"
    assert cfg.poll_s == 2.5
    assert cfg.hwmon_url == "https://example.invalid"    # trailing slash stripped
```

Create `tests/test_sources.py`:

```python
import dataclasses
import json
import time

import pytest
import requests

from dotdisplay import sources as s
from dotdisplay.config import Config


@pytest.fixture
def cfg(tmp_path):
    return dataclasses.replace(Config.from_env(), state_dir=tmp_path)


def _write(cfg, name, status="running", stages_left=None, age_s=0):
    body = {"name": name, "status": status}
    if stages_left is not None:
        body["stages_left"] = stages_left
    path = cfg.state_dir / f"{name}.json"
    path.write_text(json.dumps(body))
    if age_s:
        old = time.time() - age_s
        import os
        os.utime(path, (old, old))
    return path


def test_local_sessions_are_read(cfg):
    _write(cfg, "hwmon-d7", "issue", 2)
    assert s.read_local_sessions(cfg) == [
        {"name": "hwmon-d7", "status": "issue", "stages_left": 2}]


def test_missing_state_directory_is_not_an_error(cfg):
    cfg = dataclasses.replace(cfg, state_dir=cfg.state_dir / "nope")
    assert s.read_local_sessions(cfg) == []


def test_stale_sessions_age_out(cfg):
    """A session killed without its SessionEnd hook must fade off the board,
    not sit there looking alive forever."""
    _write(cfg, "ghost", age_s=cfg.stale_after_s + 60)
    _write(cfg, "alive")
    assert [x["name"] for x in s.read_local_sessions(cfg)] == ["alive"]


def test_malformed_file_is_skipped_not_fatal(cfg):
    (cfg.state_dir / "broken.json").write_text("{not json")
    _write(cfg, "good")
    assert [x["name"] for x in s.read_local_sessions(cfg)] == ["good"]


def test_file_without_a_name_is_skipped(cfg):
    (cfg.state_dir / "x.json").write_text('{"status": "running"}')
    assert s.read_local_sessions(cfg) == []


def test_invalid_status_is_skipped(cfg):
    """A status we cannot colour would render as plain white and quietly
    misreport a session's state."""
    (cfg.state_dir / "x.json").write_text('{"name": "x", "status": "busy"}')
    assert s.read_local_sessions(cfg) == []


def test_remote_sessions_are_merged(cfg, mocker):
    cfg = dataclasses.replace(cfg, hwmon_url="https://example.invalid")
    _write(cfg, "local")
    mocker.patch("dotdisplay.sources.fetch_remote_sessions",
                 return_value=[{"name": "remote", "status": "running"}])
    assert sorted(x["name"] for x in s.read_sessions(cfg)) == ["local", "remote"]


def test_remote_failure_leaves_local_sessions_intact(cfg, mocker):
    """The board must not go blank because a server is down."""
    cfg = dataclasses.replace(cfg, hwmon_url="https://example.invalid")
    _write(cfg, "local")
    mocker.patch("dotdisplay.sources.fetch_remote_sessions",
                 side_effect=requests.exceptions.ConnectionError("down"))
    assert [x["name"] for x in s.read_sessions(cfg)] == ["local"]


def test_no_remote_configured_means_no_request(cfg, mocker):
    get = mocker.patch("dotdisplay.sources.requests.get")
    s.read_sessions(cfg)
    get.assert_not_called()


def test_header_missing_file_is_tolerated(cfg, mocker, tmp_path):
    mocker.patch("dotdisplay.sources.RATE_LIMIT_PATH", tmp_path / "nope.json")
    assert s.read_header() is None


def test_header_is_parsed(cfg, mocker, tmp_path):
    path = tmp_path / "rl.json"
    path.write_text(json.dumps(
        {"five_hour": {"used_percentage": 42, "resets_at": 1787861400}}))
    mocker.patch("dotdisplay.sources.RATE_LIMIT_PATH", path)
    header = s.read_header()
    assert header["pct"] == 42
    assert ":" in header["reset"]


def test_ccusage_is_cached(cfg, mocker):
    """ccusage parses ~1160 transcript files; calling it per poll would make
    the loop unusable."""
    run = mocker.patch("dotdisplay.sources._run_ccusage",
                       return_value={"today": 1, "out": 1, "cache": 1,
                                     "read": 1, "all": 1})
    cache = s.CcusageCache()
    s.ccusage_stats(cfg, cache)
    s.ccusage_stats(cfg, cache)
    assert run.call_count == 1


def test_ccusage_failure_returns_the_last_good_value(cfg, mocker):
    run = mocker.patch("dotdisplay.sources._run_ccusage",
                       return_value={"today": 5, "out": 1, "cache": 1,
                                     "read": 1, "all": 1})
    cache = s.CcusageCache()
    s.ccusage_stats(cfg, cache)
    cache.fetched_at = 0                       # force a refresh
    run.side_effect = OSError("ccusage gone")
    assert s.ccusage_stats(cfg, cache)["today"] == 5


def test_trends_need_a_previous_day(tmp_path):
    """A metric with no baseline gets NO arrow -- a decorative arrow would
    imply information that does not exist."""
    path = tmp_path / "trend.json"
    assert s.trends({"today": 10}, path) == {}          # first ever run
    path.write_text(json.dumps({"date": "2000-01-01", "stats": {"today": 5}}))
    assert s.trends({"today": 10}, path) == {"today": True}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_sources.py tests/test_config.py -q`
Expected: FAIL — modules not found.

- [ ] **Step 3: Write the implementation**

Create `src/dotdisplay/config.py`:

```python
"""Settings, derived from the environment.

Nothing identifying is baked in: the panel address and any server URL come
from the environment so they never reach the repository.
"""

import os
import pathlib
from dataclasses import dataclass, field

DEFAULT_POLL_S = 5.0
DEFAULT_CCUSAGE_REFRESH_S = 300.0
DEFAULT_STALE_AFTER_S = 900.0


def _default_state_dir() -> pathlib.Path:
    return pathlib.Path(
        os.environ.get("XDG_STATE_HOME", pathlib.Path.home() / ".local/state")
    ) / "dotdisplay" / "sessions"


@dataclass
class Config:
    mac: str = ""
    hwmon_url: str = ""
    setup_key: str = ""          # secret; environment only, never the repo
    poll_s: float = DEFAULT_POLL_S
    ccusage_refresh_s: float = DEFAULT_CCUSAGE_REFRESH_S
    stale_after_s: float = DEFAULT_STALE_AFTER_S
    state_dir: pathlib.Path = field(default_factory=_default_state_dir)

    @classmethod
    def from_env(cls) -> "Config":
        env = os.environ.get
        state = env("DOTDISPLAY_STATE_DIR")
        return cls(
            mac=env("DOTDISPLAY_MAC", "").strip(),
            hwmon_url=env("DOTDISPLAY_HWMON_URL", "").strip().rstrip("/"),
            setup_key=env("DOTDISPLAY_HWMON_SETUP_KEY", "").strip(),
            poll_s=float(env("DOTDISPLAY_POLL_S", DEFAULT_POLL_S)),
            ccusage_refresh_s=float(
                env("DOTDISPLAY_CCUSAGE_REFRESH_S", DEFAULT_CCUSAGE_REFRESH_S)),
            stale_after_s=float(
                env("DOTDISPLAY_STALE_AFTER_S", DEFAULT_STALE_AFTER_S)),
            state_dir=pathlib.Path(state) if state else _default_state_dir(),
        )
```

Create `src/dotdisplay/sources.py`:

```python
"""Where the board's contents come from.

Local session files are the primary source and always work offline. A remote
source is optional and must never be able to blank the board.
"""

import datetime as _dt
import json
import logging
import pathlib
import subprocess
import time
from dataclasses import dataclass, field
from typing import Dict, List

import requests

logger = logging.getLogger(__name__)

RATE_LIMIT_PATH = pathlib.Path.home() / ".claude" / "abtop-rate-limits.json"
TREND_PATH = (pathlib.Path.home() / ".cache" / "dotdisplay-trends.json")
VALID_STATUSES = ("running", "question", "issue", "done")
HTTP_TIMEOUT_S = 10
USER_AGENT = "claude-dot-display/1.0"


@dataclass
class CcusageCache:
    stats: Dict[str, int] = field(default_factory=dict)
    fetched_at: float = 0.0


def read_local_sessions(config) -> List[dict]:
    """One JSON file per session. A file that cannot be understood is skipped,
    never fatal: one broken session must not take down the board."""
    directory = pathlib.Path(config.state_dir)
    if not directory.is_dir():
        return []

    cutoff = time.time() - config.stale_after_s
    sessions = []
    for path in sorted(directory.glob("*.json")):
        try:
            if path.stat().st_mtime < cutoff:
                continue          # session died without its SessionEnd hook
            body = json.loads(path.read_text())
        except (OSError, ValueError) as exc:
            logger.debug("skipping %s: %s", path.name, exc)
            continue
        name, status = body.get("name"), body.get("status")
        if not name or status not in VALID_STATUSES:
            logger.debug("skipping %s: name/status missing or unknown", path.name)
            continue
        entry = {"name": str(name), "status": status}
        if body.get("stages_left") is not None:
            entry["stages_left"] = body["stages_left"]
        sessions.append(entry)
    return sessions


def fetch_remote_sessions(config) -> List[dict]:
    response = requests.get(f"{config.hwmon_url}/api/sensmonlight/sessions",
                            headers={"User-Agent": USER_AGENT},
                            timeout=HTTP_TIMEOUT_S)
    response.raise_for_status()
    return response.json()


def read_sessions(config) -> List[dict]:
    """Local first, remote merged in if configured. A remote failure keeps the
    local sessions rather than blanking the board."""
    sessions = read_local_sessions(config)
    if not config.hwmon_url:
        return sessions
    try:
        remote = fetch_remote_sessions(config)
    except (requests.exceptions.RequestException, ValueError) as exc:
        logger.warning("remote sessions unavailable: %s", exc)
        return sessions
    known = {s["name"] for s in sessions}
    sessions.extend(s for s in remote
                    if s.get("name") and s["name"] not in known)
    return sessions


def read_header():
    """Account-wide five-hour window, written continuously by an existing
    StatusLine hook. Deliberately NOT a per-conversation context budget --
    those are different numbers that both sound like 'tokens used'."""
    try:
        window = json.loads(RATE_LIMIT_PATH.read_text())["five_hour"]
        return {
            "pct": float(window["used_percentage"]),
            "reset": _dt.datetime.fromtimestamp(
                window["resets_at"]).strftime("%H:%M"),
        }
    except (OSError, ValueError, KeyError, TypeError) as exc:
        logger.debug("no rate-limit header available: %s", exc)
        return None


def _run_ccusage() -> Dict[str, int]:
    output = subprocess.run(["ccusage", "daily", "--json"], capture_output=True,
                            text=True, timeout=180, check=True).stdout
    data = json.loads(output)
    rows = data[next(k for k in data if isinstance(data[k], list))]
    today, totals = rows[-1], data.get("totals", {})
    return {"today": today["totalTokens"], "out": today["outputTokens"],
            "cache": today["cacheCreationTokens"],
            "read": today["cacheReadTokens"],
            "all": totals.get("totalTokens", today["totalTokens"])}


def ccusage_stats(config, cache: CcusageCache) -> Dict[str, int]:
    """Cached. ccusage parses ~1160 transcript files; calling it per poll
    would make the loop unusable. A failure keeps the last good value."""
    now = time.monotonic()
    if cache.stats and now - cache.fetched_at < config.ccusage_refresh_s:
        return cache.stats
    try:
        cache.stats = _run_ccusage()
        cache.fetched_at = now
    except (OSError, ValueError, KeyError, StopIteration,
            subprocess.SubprocessError) as exc:
        logger.warning("ccusage unavailable, keeping last values: %s", exc)
    return cache.stats


def trends(stats: Dict[str, int], path=TREND_PATH) -> Dict[str, bool]:
    """Compare against yesterday. A metric with no baseline gets NO arrow."""
    today = _dt.date.today().isoformat()
    try:
        previous = json.loads(pathlib.Path(path).read_text())
    except (OSError, ValueError):
        previous = {}

    out = {}
    if previous.get("date") and previous["date"] != today:
        for key, value in stats.items():
            if key in previous.get("stats", {}):
                out[key] = value > previous["stats"][key]
    if previous.get("date") != today:
        try:
            path = pathlib.Path(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"date": today, "stats": stats}))
        except OSError as exc:
            logger.debug("could not persist trend baseline: %s", exc)
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest -q && .venv/bin/ruff check .`
Expected: PASS, 63 + 16 = 79 tests.

- [ ] **Step 5: Commit**

```bash
git add src/dotdisplay/sources.py src/dotdisplay/config.py \
        tests/test_sources.py tests/test_config.py
git commit -m "feat: add session, header and usage sources"
```

---

### Task 3: daemon.py — the loop

**Files:**
- Create: `src/dotdisplay/daemon.py`
- Test: `tests/test_daemon.py`

**Interfaces:**
- Consumes: `render` (Task 1), `sources` (Task 2), `dotdisplay.ble.PanelClient` (P1).
- Produces: `Board` (holds the loop's state), `render_board(config, board) -> Image`, `tick(config, board, panel) -> bool`, `run(config) -> int`.

`tick` returns True when it sent to the panel. Splitting it out of `run` is
what makes the loop testable without a clock or a radio.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_daemon.py`:

```python
import dataclasses

import pytest

from dotdisplay import daemon as d
from dotdisplay.config import Config


@pytest.fixture
def cfg(tmp_path):
    return dataclasses.replace(Config.from_env(), state_dir=tmp_path,
                               mac="AA:BB:CC:DD:EE:FF", poll_s=0.01)


class FakePanel:
    def __init__(self):
        self.images = []

    async def send_image(self, img):
        self.images.append(img)


async def test_first_render_is_sent(cfg, mocker):
    mocker.patch("dotdisplay.daemon.sources.read_sessions",
                 return_value=[{"name": "a", "status": "running"}])
    mocker.patch("dotdisplay.daemon.sources.read_header", return_value=None)
    panel, board = FakePanel(), d.Board()
    assert await d.tick(cfg, board, panel) is True
    assert len(panel.images) == 1


async def test_unchanged_board_is_not_resent(cfg, mocker):
    """Re-sending an identical board holds an exclusive radio for nothing."""
    mocker.patch("dotdisplay.daemon.sources.read_sessions",
                 return_value=[{"name": "a", "status": "running"}])
    mocker.patch("dotdisplay.daemon.sources.read_header", return_value=None)
    panel, board = FakePanel(), d.Board()
    await d.tick(cfg, board, panel)
    assert await d.tick(cfg, board, panel) is False
    assert len(panel.images) == 1


async def test_changed_status_is_sent(cfg, mocker):
    mocker.patch("dotdisplay.daemon.sources.read_header", return_value=None)
    mocker.patch("dotdisplay.daemon.sources.read_sessions", side_effect=[
        [{"name": "a", "status": "running"}],
        [{"name": "a", "status": "issue"}]])
    panel, board = FakePanel(), d.Board()
    await d.tick(cfg, board, panel)
    await d.tick(cfg, board, panel)
    assert len(panel.images) == 2


async def test_no_sessions_renders_the_idle_screen(cfg, mocker):
    mocker.patch("dotdisplay.daemon.sources.read_sessions", return_value=[])
    mocker.patch("dotdisplay.daemon.sources.read_header", return_value=None)
    mocker.patch("dotdisplay.daemon.sources.ccusage_stats",
                 return_value={"today": 1, "out": 1, "cache": 1,
                               "read": 1, "all": 1})
    mocker.patch("dotdisplay.daemon.sources.trends", return_value={})
    idle = mocker.patch("dotdisplay.daemon.render.render_idle")
    await d.tick(cfg, d.Board(), FakePanel())
    idle.assert_called_once()


async def test_a_send_failure_does_not_raise(cfg, mocker):
    """The panel being out of range or asleep is normal, not an error."""
    mocker.patch("dotdisplay.daemon.sources.read_sessions", return_value=[])
    mocker.patch("dotdisplay.daemon.sources.read_header", return_value=None)
    mocker.patch("dotdisplay.daemon.sources.ccusage_stats", return_value={})
    mocker.patch("dotdisplay.daemon.sources.trends", return_value={})

    class Broken(FakePanel):
        async def send_image(self, img):
            raise OSError("panel gone")

    assert await d.tick(cfg, d.Board(), Broken()) is False


async def test_a_failed_send_is_retried_next_tick(cfg, mocker):
    """If the send failed, the board on the panel is NOT what we rendered, so
    the cache must not record it as sent."""
    mocker.patch("dotdisplay.daemon.sources.read_sessions",
                 return_value=[{"name": "a", "status": "running"}])
    mocker.patch("dotdisplay.daemon.sources.read_header", return_value=None)

    class FlakyPanel(FakePanel):
        def __init__(self):
            super().__init__()
            self.fail_next = True

        async def send_image(self, img):
            if self.fail_next:
                self.fail_next = False
                raise OSError("transient")
            await super().send_image(img)

    panel, board = FlakyPanel(), d.Board()
    await d.tick(cfg, board, panel)          # fails
    assert await d.tick(cfg, board, panel) is True
    assert len(panel.images) == 1


async def test_missing_mac_is_refused_early(cfg):
    """Better a clear error at startup than a daemon that silently does
    nothing."""
    with pytest.raises(SystemExit):
        await d.run(dataclasses.replace(cfg, mac=""))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_daemon.py -q`
Expected: FAIL — no module `dotdisplay.daemon`.

- [ ] **Step 3: Write the implementation**

Create `src/dotdisplay/daemon.py`:

```python
"""The board loop.

Owns the radio: holds one BLE connection open and reconnects on failure.
Reconnecting per operation would dominate a five-second loop.
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Optional

from dotdisplay import render, sources
from dotdisplay.ble import PanelClient
from dotdisplay.config import Config
from dotdisplay.sources import CcusageCache

logger = logging.getLogger(__name__)

RECONNECT_DELAY_S = 10.0


@dataclass
class Board:
    """What the loop remembers between ticks."""
    last_sent: Optional[bytes] = None
    ccusage: CcusageCache = None

    def __post_init__(self):
        if self.ccusage is None:
            self.ccusage = CcusageCache()


def render_board(config: Config, board: Board):
    """Pick and draw the screen. Pure apart from reading its sources."""
    sessions = sources.read_sessions(config)
    header = sources.read_header()
    if sessions:
        return render.render_sessions(sessions, header)
    stats = sources.ccusage_stats(config, board.ccusage)
    return render.render_idle(stats, sources.trends(stats) if stats else {},
                              header)


async def tick(config: Config, board: Board, panel) -> bool:
    """One pass. Returns True if the panel was updated. Never raises: this
    runs unattended."""
    try:
        image = render_board(config, board)
    except Exception as exc:                  # noqa: BLE001 - unattended loop
        logger.exception("could not render the board: %s", exc)
        return False

    pixels = image.tobytes()
    if pixels == board.last_sent:
        return False

    try:
        await panel.send_image(image)
    except Exception as exc:                  # noqa: BLE001 - unattended loop
        # Deliberately do NOT record this as sent: the panel does not show
        # what we rendered, so the next tick must try again.
        logger.warning("could not update the panel: %s", exc)
        return False

    board.last_sent = pixels
    return True


async def run(config: Config) -> int:
    if not config.mac:
        raise SystemExit("DOTDISPLAY_MAC is required")

    board = Board()
    logger.info("watching %s every %.1fs", config.state_dir, config.poll_s)
    while True:
        try:
            async with PanelClient(config.mac) as panel:
                logger.info("panel connected")
                while True:
                    await tick(config, board, panel)
                    await asyncio.sleep(config.poll_s)
        except Exception as exc:              # noqa: BLE001 - unattended loop
            # Out of range, powered off, or the radio was taken. All normal.
            logger.warning("panel unavailable (%s); retrying in %.0fs",
                           exc, RECONNECT_DELAY_S)
            board.last_sent = None            # the panel may show anything now
            await asyncio.sleep(RECONNECT_DELAY_S)
```

Note `board.last_sent = None` on reconnect: after losing the connection the
panel's contents are unknown, so the cached "already sent" value would be a
lie and could leave the board permanently stale.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest -q && .venv/bin/ruff check .`
Expected: PASS, 79 + 7 = 86 tests.

- [ ] **Step 5: Commit**

```bash
git add src/dotdisplay/daemon.py tests/test_daemon.py
git commit -m "feat: add the board daemon loop"
```

---

### Task 4: hwmon command queue

Lets the daemon replace `sensmonlight-idotmatrix-agent` outright, so hwmon
keeps working with **no server-side changes at all**.

**Files:**
- Modify: `src/dotdisplay/daemon.py`, `src/dotdisplay/sources.py`
- Test: `tests/test_daemon.py`

**Interfaces:**
- Consumes: hwmon's existing `/api/sensmonlight/idotmatrix/agent/claim` and `/agent/result`, unchanged.
- Produces: `sources.claim_command(config) -> tuple[str, dict] | None`, `sources.report_result(config, request_id, result)`, `daemon.serve_commands(config, panel, board=None) -> int`.

- [ ] **Step 1: The contract, as it actually is**

Read from `sensmonlight/src/sensmonlight/idotmatrix_agent.py` on 2026-08-27.
hwmon-server is **not** being changed, so this must be matched exactly.

| | |
| --- | --- |
| Claim | `GET /api/sensmonlight/idotmatrix/agent/claim` |
| Claim response | `204` = queue empty; `200` = `{"request_id": ..., "body": {...}}` |
| Report | `POST /api/sensmonlight/idotmatrix/agent/result` |
| Report body | `{"request_id": ..., "result": {"status": "done", "result": {...}}}` or `{"status": "error", "message": "..."}` |
| Auth | **`X-Setup-Key` header is required**; without it the server answers 403 |

**Two landmines, both already paid for once:**

1. **The reverse proxy in front of hwmon-server drops requests whose
   User-Agent matches its scraper denylist, and `requests`' default
   `python-requests/x.y` is on that list.** The connection closes with no
   response at all, which looks exactly like the server being down. Always
   send a proper `User-Agent`.
2. **A result that is never reported is not lost, it is stuck.** The command
   stays in the server's inflight directory until a sweep turns it into an
   error. Report failures explicitly rather than dropping them.

Add to `Config`: `setup_key: str = ""`, from `DOTDISPLAY_HWMON_SETUP_KEY`.
It is a secret: environment file only, never the repo.

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_daemon.py`:

```python
async def test_commands_are_executed_and_reported(cfg, mocker):
    cfg = dataclasses.replace(cfg, hwmon_url="https://example.invalid",
                              setup_key="k")
    mocker.patch("dotdisplay.daemon.sources.claim_command",
                 side_effect=[("abc", {"type": "set_brightness",
                                       "brightness_percent": 40}), None])
    report = mocker.patch("dotdisplay.daemon.sources.report_result")

    class BrightPanel(FakePanel):
        def __init__(self):
            super().__init__()
            self.brightness = None

        async def set_brightness(self, pct):
            self.brightness = pct

    panel = BrightPanel()
    assert await d.serve_commands(cfg, panel) == 1
    assert panel.brightness == 40
    assert report.call_args.args[1] == "abc"
    assert report.call_args.args[2]["status"] == "done"


async def test_a_failing_command_is_reported_as_an_error(cfg, mocker):
    """An unreported failure leaves the command stuck in the server's
    inflight directory until a sweep expires it."""
    cfg = dataclasses.replace(cfg, hwmon_url="https://example.invalid",
                              setup_key="k")
    mocker.patch("dotdisplay.daemon.sources.claim_command",
                 side_effect=[("abc", {"type": "nonsense"}), None])
    report = mocker.patch("dotdisplay.daemon.sources.report_result")
    await d.serve_commands(cfg, FakePanel())
    assert report.call_args.args[2]["status"] == "error"


async def test_no_hwmon_url_means_no_polling(cfg, mocker):
    claim = mocker.patch("dotdisplay.daemon.sources.claim_command")
    assert await d.serve_commands(cfg, FakePanel()) == 0
    claim.assert_not_called()


async def test_a_command_invalidates_the_cached_board(cfg, mocker):
    """A command paints over the board. The cache must forget what it sent,
    or the next tick would see 'no change' and leave the command's image up
    until session state happened to move."""
    cfg = dataclasses.replace(cfg, hwmon_url="https://example.invalid",
                              setup_key="k")
    mocker.patch("dotdisplay.daemon.sources.claim_command",
                 side_effect=[("abc", {"type": "power", "on": True}), None])
    mocker.patch("dotdisplay.daemon.sources.report_result")

    class PowerPanel(FakePanel):
        async def power(self, on):
            pass

    board = d.Board(last_sent=b"something")
    await d.serve_commands(cfg, PowerPanel(), board=board)
    assert board.last_sent is None


async def test_a_claim_failure_ends_the_drain_quietly(cfg, mocker):
    """hwmon being unreachable is normal; it must not stop the board."""
    import requests
    cfg = dataclasses.replace(cfg, hwmon_url="https://example.invalid",
                              setup_key="k")
    mocker.patch("dotdisplay.daemon.sources.claim_command",
                 side_effect=requests.exceptions.ConnectionError("down"))
    assert await d.serve_commands(cfg, FakePanel()) == 0
```

- [ ] **Step 3: Write the implementation**

Add to `sources.py`:

```python
CLAIM_PATH = "/api/sensmonlight/idotmatrix/agent/claim"
RESULT_PATH = "/api/sensmonlight/idotmatrix/agent/result"


def _hwmon_headers(config):
    # The User-Agent is not cosmetic: the reverse proxy in front of
    # hwmon-server drops the default "python-requests/x.y" outright, closing
    # the connection with no response -- which looks like the server is down.
    return {"X-Setup-Key": config.setup_key, "User-Agent": USER_AGENT}


def claim_command(config):
    """Claim at most one queued command. Returns (request_id, body) or None."""
    response = requests.get(config.hwmon_url + CLAIM_PATH,
                            headers=_hwmon_headers(config),
                            timeout=HTTP_TIMEOUT_S)
    if response.status_code == 204:          # queue empty
        return None
    response.raise_for_status()
    payload = response.json()
    return payload["request_id"], payload["body"]


def report_result(config, request_id: str, result: dict) -> None:
    """Always report, including failures: an unreported command sits in the
    server's inflight directory until a sweep turns it into an error."""
    response = requests.post(config.hwmon_url + RESULT_PATH,
                             headers=_hwmon_headers(config),
                             json={"request_id": request_id, "result": result},
                             timeout=HTTP_TIMEOUT_S)
    if not 200 <= response.status_code < 300:
        logger.warning("result for %s returned HTTP %s", request_id,
                       response.status_code)
```

Add to `daemon.py`:

```python
async def _execute(panel, command: dict) -> dict:
    kind = command.get("type")
    if kind == "set_brightness":
        await panel.set_brightness(int(command["brightness_percent"]))
    elif kind == "power":
        await panel.power(bool(command["on"]))
    elif kind == "send_image":
        import base64
        import io

        from PIL import Image
        raw = base64.b64decode(command["image_base64"])
        await panel.send_image(Image.open(io.BytesIO(raw)))
    else:
        raise ValueError(f"unsupported command type {kind!r}")
    return {"sent": True}


async def serve_commands(config: Config, panel, board: Board = None) -> int:
    """Drain hwmon's command queue. Returns how many commands ran."""
    if not config.hwmon_url:
        return 0

    ran = 0
    while True:
        try:
            command = sources.claim_command(config)
        except Exception as exc:              # noqa: BLE001 - unattended loop
            logger.warning("could not claim a command: %s", exc)
            return ran
        if not command:
            return ran
        request_id, body = command

        try:
            result = {"status": "done", "result": await _execute(panel, body)}
        except Exception as exc:              # noqa: BLE001 - must always report
            # An unreported failure leaves the dashboard waiting forever.
            result = {"status": "error", "message": str(exc)}
        try:
            sources.report_result(config, request_id, result)
        except Exception as exc:              # noqa: BLE001
            logger.warning("could not report a result: %s", exc)

        ran += 1
        if board is not None:
            # The command painted over the board; forget what we sent so the
            # next tick re-renders rather than seeing "no change".
            board.last_sent = None
```

Then call it from `run`'s inner loop, **before** `tick`, so an explicit human
request takes priority over the ambient board:

```python
                while True:
                    await serve_commands(config, panel, board)
                    await tick(config, board, panel)
                    await asyncio.sleep(config.poll_s)
```

- [ ] **Step 4: Run tests and commit**

Run: `.venv/bin/python -m pytest -q && .venv/bin/ruff check .`
Expected: PASS, 86 + 5 = 91 tests.

```bash
git add src/dotdisplay tests/test_daemon.py
git commit -m "feat: serve hwmon's command queue from the daemon"
```

---

### Task 5: CLI, service unit, install

**Files:**
- Modify: `src/dotdisplay/cli.py`, `README.md`
- Create: `packaging/dotdisplay.service`, `scripts/install.sh`
- Test: `tests/test_cli.py`

**Interfaces:**
- Produces: `dotdisplay daemon`, `dotdisplay status --name N --state S [--left K]`, `dotdisplay status --name N --clear`, `dotdisplay send <image>`.

`dotdisplay status` is what the P3 hooks and the assistant will call. It writes
a local file — no server, no secret, and it works before the daemon is up.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli.py`:

```python
import json


def test_status_writes_a_session_file(tmp_path, monkeypatch):
    monkeypatch.setenv("DOTDISPLAY_STATE_DIR", str(tmp_path))
    assert cli.main(["status", "--name", "hwmon-d7",
                     "--state", "question", "--left", "3"]) == 0
    body = json.loads((tmp_path / "hwmon-d7.json").read_text())
    assert body == {"name": "hwmon-d7", "status": "question", "stages_left": 3}


def test_status_clear_removes_the_file(tmp_path, monkeypatch):
    monkeypatch.setenv("DOTDISPLAY_STATE_DIR", str(tmp_path))
    cli.main(["status", "--name", "x", "--state", "running"])
    assert cli.main(["status", "--name", "x", "--clear"]) == 0
    assert not (tmp_path / "x.json").exists()


def test_clearing_an_unknown_session_is_not_an_error(tmp_path, monkeypatch):
    """Hooks fire on sessions that were never registered; that is normal."""
    monkeypatch.setenv("DOTDISPLAY_STATE_DIR", str(tmp_path))
    assert cli.main(["status", "--name", "ghost", "--clear"]) == 0


def test_status_rejects_a_name_that_is_not_a_safe_filename(tmp_path, monkeypatch):
    """The name becomes a filename and is rendered to an image; it must not
    carry path separators or control bytes."""
    monkeypatch.setenv("DOTDISPLAY_STATE_DIR", str(tmp_path))
    for bad in ("../escape", "has space", "", "a" * 40):
        assert cli.main(["status", "--name", bad, "--state", "running"]) == 1
    assert list(tmp_path.iterdir()) == []


def test_status_rejects_an_unknown_state(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DOTDISPLAY_STATE_DIR", str(tmp_path))
    with pytest.raises(SystemExit):
        cli.main(["status", "--name", "x", "--state", "busy"])
```

- [ ] **Step 2: Write the implementation**

Rewrite `src/dotdisplay/cli.py` around subparsers, keeping `--version`
behaviour and the stderr-usage rule from P0. Add:

```python
import json
import re

SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]{1,32}$")
STATES = ("running", "question", "issue", "done")


def _cmd_status(args) -> int:
    from dotdisplay.config import Config
    if not SAFE_NAME.match(args.name):
        print(f"invalid session name: {args.name!r}", file=sys.stderr)
        return 1
    directory = Config.from_env().state_dir
    path = directory / f"{args.name}.json"
    if args.clear:
        path.unlink(missing_ok=True)      # hooks fire for unknown sessions
        return 0
    if not args.state:
        print("--state is required unless --clear is given", file=sys.stderr)
        return 1
    body = {"name": args.name, "status": args.state}
    if args.left is not None:
        body["stages_left"] = args.left
    try:
        directory.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(body))
    except OSError as exc:
        print(f"dotdisplay: {exc}", file=sys.stderr)
        return 1
    return 0
```

`daemon` runs `asyncio.run(daemon.run(Config.from_env()))`; `send` loads an
image with Pillow and pushes it through `PanelClient`.

**Never let `status` break a session.** It is called from hooks on every
prompt: it must fail quietly with a non-zero exit and never block.

- [ ] **Step 3: Service unit and installer**

Create `packaging/dotdisplay.service`:

```ini
[Unit]
Description=claude-dot-display board
After=bluetooth.target

[Service]
Type=simple
ExecStart=%h/.local/share/dotdisplay/venv/bin/dotdisplay daemon
Restart=always
RestartSec=10
Environment=PYTHONUNBUFFERED=1
EnvironmentFile=%h/.config/dotdisplay/env

[Install]
WantedBy=default.target
```

Create `scripts/install.sh`: build a venv at
`~/.local/share/dotdisplay/venv`, `pip install` this project into it, write
`~/.config/dotdisplay/env` from `DOTDISPLAY_MAC` (prompting if unset) with
`umask 077` and `chmod 600`, install the unit to
`~/.config/systemd/user/`, then `systemctl --user daemon-reload` and
`enable --now`. Print the `loginctl enable-linger` note rather than running it.

**The installer must refuse to start while `sensmonlight-idotmatrix-agent`
is active** — two owners of one radio produce failures that look like
protocol bugs. Detect it and say so.

- [ ] **Step 4: Verify and commit**

```bash
bash -n scripts/install.sh
.venv/bin/python -m pytest -q && .venv/bin/ruff check .
```

Expected: PASS, 91 + 5 = 96 tests.

```bash
git add src/dotdisplay/cli.py tests/test_cli.py packaging scripts/install.sh README.md
git commit -m "feat: add CLI subcommands, service unit and installer"
```

---

### Task 6: Hardware verification and hwmon cleanup

- [ ] **Step 1: Take the radio**

```bash
systemctl --user stop sensmonlight-idotmatrix-agent.service
systemctl --user is-active sensmonlight-idotmatrix-agent.service   # inactive
```

- [ ] **Step 2: Run the daemon in the foreground and watch the panel**

```bash
DOTDISPLAY_MAC=<panel address> .venv/bin/dotdisplay daemon
```

In another shell:

```bash
.venv/bin/dotdisplay status --name hwmon-d7 --state issue --left 2
.venv/bin/dotdisplay status --name storygen --state question --left 12
```

Photograph:

```bash
ffmpeg -hide_banner -loglevel error -f v4l2 -input_format mjpeg \
  -video_size 1920x1080 -i /dev/video2 -vf "select=gte(n\,15)" \
  -frames:v 1 -q:v 2 -y /tmp/board-sessions.jpg
```

Expected: two rows, **alphabetical** (`hwmon-d7` above `storygen`), names
coloured red and amber, counts right-aligned, header showing the five-hour
window. **Read the photograph, not the log.**

- [ ] **Step 3: Verify the idle screen**

```bash
.venv/bin/dotdisplay status --name hwmon-d7 --clear
.venv/bin/dotdisplay status --name storygen --clear
sleep 10
```

Photograph again. Expected: the token statistics screen.

- [ ] **Step 4: Verify quiet means quiet**

Watch the log for a minute with no session changes. Expected: **no sends at
all.** A quiet board must produce no BLE traffic.

- [ ] **Step 5: Verify hwmon still works**

With `DOTDISPLAY_HWMON_URL` set, send an image through hwmon's existing path
(`sensmonlight/scripts/idot-send.sh`) and confirm it appears. This proves the
daemon really can replace the agent.

- [ ] **Step 6: Install as a service**

```bash
bash scripts/install.sh
systemctl --user is-active dotdisplay.service      # active
```

- [ ] **Step 7: Remove the iDotMatrix code from sensmonlight**

**Only after Steps 2-6 have all passed.** In `~/Documents/gitlab/hwmon`:

```bash
systemctl --user disable --now sensmonlight-idotmatrix-agent.service
git rm sensmonlight/src/sensmonlight/idotmatrix_control.py \
       sensmonlight/src/sensmonlight/idotmatrix_agent.py \
       sensmonlight/tests/test_idotmatrix_control.py \
       sensmonlight/tests/test_idotmatrix_agent.py
```

Remove the `idotmatrix-api-client` dependency and the agent console script
from `sensmonlight/pyproject.toml`, then:

```bash
cd ~/Documents/gitlab/hwmon/sensmonlight && python -m pytest -q
```

**This is the moment the GPL dependency leaves the codebase.** Note the new
test count; the suite will shrink.

Commit in the hwmon repo, then deploy per `CLAUDE.md` — commit, push, and run
the `sensmonlight.yml` playbook through LabControl. Never hand-run rsync or
ssh against the hosts.

- [ ] **Step 8: Commit**

```bash
git add -A && git commit -m "feat: verify the board against the physical panel"
git push origin main && git push gitlab main
```

---

## Definition of done

- 96 tests pass locally and in CI on 3.11, 3.12, 3.13; ruff clean.
- The panel shows sessions, alphabetically, in the right colours.
- The idle screen appears when no session is running.
- A quiet board produces no BLE traffic.
- hwmon's `idot-send.sh` still works, through the new daemon.
- `dotdisplay.service` is active; `sensmonlight-idotmatrix-agent` is disabled.
- `sensmonlight` no longer depends on any GPL library.
- No MAC address, hostname, or capture file is committed.

## Deliberately not in P2

- Hooks and the reporting skill — P3.
- Animation and text frames — the protocol entries are not verified.
- Discovery, pairing, multiple panels.
