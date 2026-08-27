# P0 — Repo Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the `claude-dot-display` repo from two design documents into an installable, tested, licensed, documented, CI-checked project published to both GitHub and the internal GitLab.

**Architecture:** P0 builds no product behaviour. It builds the shell the product will live in: a `src/`-layout Python package with a working test cycle, the MIT licence and the licence-consistency test that protects the project's central promise, the logo, the README that tells the GPL story, the plugin and marketplace manifests that make the repo self-hosting, CI, and both remotes.

**Tech Stack:** Python 3.11+, setuptools, pytest, ruff, GitHub Actions, `claude plugin validate`.

**Spec:** `docs/superpowers/specs/2026-08-27-architecture-design.md`

## Global Constraints

Copied verbatim from the spec. Every task's requirements implicitly include these.

- **Python 3.11+.** Local development runs 3.12.3.
- **Runtime dependencies are exactly `bleak`, `pillow`, `requests`.** Nothing else, and **nothing copyleft** — that constraint is the entire reason this project exists.
- **The clean-room rule is binding from day one.** Do not open GPL iDotMatrix source (`derkalle4/python3-idotmatrix-client`, `markusressel/idotmatrix-api-client`, `python3-idotmatrix-library`) at any point, in any task. Observing wire traffic is permitted; reading their code is not.
- **Names:** repo, plugin id, and PyPI package are `claude-dot-display`. The console script and the Python module are `dotdisplay`.
- **Licence is MIT**, and the licence-consistency test in Task 2 must keep passing for the life of the project.
- **Remotes:** `git@github.com:MrMarco74/claude_dot_display.git` (`origin`) and `git@<internal-gitlab>:apps/claude_dot_display.git` (`gitlab`).
- Work inside `.venv` (see Task 1): this Ubuntu is PEP 668 externally managed, so system-wide `pip install` fails.
- Test command: `cd ~/Documents/gitlab/claude_dot_display && .venv/bin/python -m pytest -q`. **Baseline is 0 tests** — this repo has none yet.
- Lint command: `.venv/bin/ruff check .`
- No real network calls in tests. No test above ~1s.

## File Structure

| File | Responsibility |
| --- | --- |
| `pyproject.toml` | Package metadata, dependencies, console script, ruff and pytest config |
| `src/dotdisplay/__init__.py` | Package version, single source of truth |
| `src/dotdisplay/cli.py` | Argument parsing and entry point. Grows subcommands in P2; carries only `--version` now |
| `tests/test_cli.py` | Entry-point behaviour |
| `tests/test_licensing.py` | Guards the MIT promise and the no-copyleft dependency rule |
| `tests/test_manifests.py` | Guards manifest/package metadata agreement |
| `LICENSE` | MIT text |
| `assets/logo.jpg` + derived sizes | Project logo, supplied by the author |
| `README.md` | Badges, the GPL story, install, status |
| `.claude-plugin/plugin.json` | Plugin manifest |
| `.claude-plugin/marketplace.json` | Self-hosted marketplace manifest |
| `.github/workflows/ci.yml` | Lint and test on 3.11–3.13 |

---

### Task 1: Python package skeleton and test cycle

Nothing else can be tested until there is something installable to test. This task ends with `pytest` green and `dotdisplay --version` working.

**Files:**
- Create: `pyproject.toml`, `src/dotdisplay/__init__.py`, `src/dotdisplay/cli.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `dotdisplay.__version__` (str); `dotdisplay.cli.main(argv: list[str] | None = None) -> int`; console script `dotdisplay`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_cli.py`:

```python
import pytest

import dotdisplay
from dotdisplay import cli


def test_version_is_reported(capsys):
    """--version must print the package version and exit 0, so an installed
    copy can always identify itself in a bug report."""
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0
    assert dotdisplay.__version__ in capsys.readouterr().out


def test_no_arguments_prints_help_and_fails(capsys):
    """A bare invocation must not look like success -- there is no default
    action, and a silent exit 0 would read as 'it worked'."""
    assert cli.main([]) == 1
    assert "usage:" in capsys.readouterr().err.lower()


def test_version_string_is_a_release_number():
    parts = dotdisplay.__version__.split(".")
    assert len(parts) == 3
    assert all(p.isdigit() for p in parts)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ~/Documents/gitlab/claude_dot_display && .venv/bin/python -m pytest -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'dotdisplay'`

- [ ] **Step 3: Write the implementation**

Create `pyproject.toml`:

```toml
[build-system]
requires = ["setuptools>=77"]
build-backend = "setuptools.build_meta"

[project]
name = "claude-dot-display"
version = "0.1.0"
description = "Show your Claude Code sessions on an iDotMatrix LED panel."
readme = "README.md"
requires-python = ">=3.11"
license = "MIT"
license-files = ["LICENSE"]
authors = [{ name = "MrMarco", email = "MrMarco74@users.noreply.github.com" }]
keywords = ["claude-code", "idotmatrix", "led-matrix", "ble", "status-board"]
classifiers = [
    "Development Status :: 3 - Alpha",
    "Environment :: Console",
    "Intended Audience :: Developers",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
    "Programming Language :: Python :: 3.13",
    "Topic :: System :: Monitoring",
]
dependencies = [
    "bleak>=1.0",
    "pillow>=11.0",
    "requests>=2.31",
]

[project.optional-dependencies]
dev = ["pytest>=8.0", "pytest-mock>=3.14", "ruff>=0.6"]

[project.urls]
Homepage = "https://github.com/MrMarco74/claude_dot_display"
Source = "https://github.com/MrMarco74/claude_dot_display"

[project.scripts]
dotdisplay = "dotdisplay.cli:main"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]

[tool.ruff]
line-length = 100
src = ["src", "tests"]

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B"]
```

Note `license = "MIT"` with `license-files` is the PEP 639 form, which is why the build requires `setuptools>=77`.

The system setuptools on marcohp is **68.1.2**, which does not understand that form. This is fine because pip uses build isolation by default and will fetch a new enough setuptools from `[build-system] requires`. If you ever build with `--no-build-isolation`, upgrade setuptools first or the build fails with a confusing metadata error.

Create `src/dotdisplay/__init__.py`:

```python
"""claude-dot-display -- your Claude Code sessions on an LED matrix."""

__version__ = "0.1.0"

__all__ = ["__version__"]
```

Create `src/dotdisplay/cli.py`:

```python
"""Command line entry point.

Carries only --version today. P2 adds the `daemon`, `status` and `send`
subcommands here; keeping one console script rather than several is a
deliberate choice recorded in the design.
"""

import argparse
import sys

from dotdisplay import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dotdisplay",
        description="Show your Claude Code sessions on an iDotMatrix LED panel.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    del args  # no subcommands yet; P2 dispatches here
    parser.print_usage(sys.stderr)
    return 1
```

`argparse`'s `--version` action raises `SystemExit(0)` itself, which is what the first test asserts.

Note the explicit `sys.stderr`: `print_usage()` defaults to **stdout**, but this is a failure path, so the usage text belongs on stderr — otherwise it would pollute a pipeline that expects real output. The second test asserts stderr precisely to pin that down.

- [ ] **Step 4: Install in editable mode and run the tests**

```bash
cd ~/Documents/gitlab/claude_dot_display
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
.venv/bin/python -m pytest -q
.venv/bin/ruff check .
.venv/bin/dotdisplay --version
```

Expected: 3 tests PASS, ruff clean, `dotdisplay --version` prints `0.1.0`.

**A venv is required, not optional.** This Ubuntu marks its system Python as
externally managed (PEP 668), so `pip install` outside a venv fails with
`error: externally-managed-environment`. `.venv/` is already gitignored.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml src tests
git commit -m "feat: add the dotdisplay package skeleton and entry point"
```

---

### Task 2: MIT licence and the licence-consistency test

The project's whole reason for existing is licence hygiene, so the licence gets a test rather than a hope.

**Files:**
- Create: `LICENSE`, `tests/test_licensing.py`

**Interfaces:**
- Consumes: `pyproject.toml` from Task 1.
- Produces: nothing importable. A guard test.

- [ ] **Step 1: Write the failing test**

Create `tests/test_licensing.py`:

```python
import pathlib
import tomllib

ROOT = pathlib.Path(__file__).resolve().parent.parent

# Copyleft markers. A runtime dependency matching any of these would make the
# MIT claim false, which is the exact problem this project was created to fix.
COPYLEFT = ("gpl", "agpl", "lgpl", "idotmatrix-api-client", "python3-idotmatrix")


def _pyproject() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text())


def test_license_file_exists_and_is_mit():
    text = (ROOT / "LICENSE").read_text()
    assert "MIT License" in text
    assert "WITHOUT WARRANTY OF ANY KIND" in text


def test_pyproject_declares_mit():
    assert _pyproject()["project"]["license"] == "MIT"


def test_license_file_is_shipped_in_the_distribution():
    """A wheel without its licence text is not properly licensed."""
    assert _pyproject()["project"]["license-files"] == ["LICENSE"]


def test_no_copyleft_runtime_dependency():
    """The reason this project exists. Every Python iDotMatrix library is
    GPL-3.0; depending on one would make 'MIT' untrue."""
    deps = _pyproject()["project"]["dependencies"]
    for dep in deps:
        lowered = dep.lower()
        for marker in COPYLEFT:
            assert marker not in lowered, f"copyleft dependency reintroduced: {dep}"


def test_runtime_dependencies_are_the_agreed_set():
    """Pinned by the design. A new runtime dependency is a design decision,
    not an implementation detail -- this test forces that conversation."""
    names = {d.split(">")[0].split("=")[0].strip().lower()
             for d in _pyproject()["project"]["dependencies"]}
    assert names == {"bleak", "pillow", "requests"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_licensing.py -q`
Expected: FAIL — `FileNotFoundError` for `LICENSE`.

- [ ] **Step 3: Write the implementation**

Create `LICENSE` with the standard MIT text:

```
MIT License

Copyright (c) 2026 MrMarco

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS, 3 + 5 = 8 tests.

- [ ] **Step 5: Commit**

```bash
git add LICENSE tests/test_licensing.py
git commit -m "feat: add the MIT licence and its consistency tests"
```

---

### Task 3: Logo and README

The README carries the GPL story, which is a stated requirement rather than decoration. The logo ships with it because the README embeds it.

**Files:**
- Copy in: `assets/logo.jpg`, `assets/logo-512.png`, `assets/icon-128.png`, `assets/splash-64.png`
- Create: `README.md`

**Interfaces:**
- Consumes: nothing.
- Produces: `assets/splash-64.png`, reused in P2 as the daemon's splash screen on the panel.

- [ ] **Step 1: Confirm the logo assets are present**

The logo was supplied by the author as a 2048x2048 JPEG. The derived sizes are
already generated and committed. Do not restyle or regenerate them.

```bash
ls -l assets/
```

Expected: `logo.jpg` (1024, README hero), `logo-512.png`, `icon-128.png`, `splash-64.png`.

`splash-64.png` is a tight crop of the logo's glowing star, **not** a downscale
of the whole image: the full logo at 64x64 was rendered and confirmed
unreadable. It is quantized to 24 colours, which is what the panel renders
cleanly.

- [ ] **Step 2: Verify the assets**

```bash
python3 -c "
from PIL import Image
for n in ('logo.jpg', 'logo-512.png', 'icon-128.png', 'splash-64.png'):
    im = Image.open('assets/' + n); print(n, im.size, im.mode)"
```

Expected: `(1024, 1024)`, `(512, 512)`, `(128, 128)`, `(64, 64)`.

**Unresolved before publishing — the Octocat.** The supplied logo includes
GitHub's Octocat mascot. GitHub's brand guidelines restrict derivative uses of
it, and this project's entire premise is getting licensing right, so shipping
it publicly is a call to make deliberately rather than by default. This is
listed again in Task 6's pre-push checklist and must be resolved there.

- [ ] **Step 3: Write the README**

Create `README.md`:

````markdown
<div align="center">

<img src="assets/logo.jpg" width="320" alt="claude-dot-display">

# claude-dot-display

**Your Claude Code sessions, on an LED matrix.**

[![CI](https://github.com/MrMarco74/claude_dot_display/actions/workflows/ci.yml/badge.svg)](https://github.com/MrMarco74/claude_dot_display/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![Status](https://img.shields.io/badge/status-alpha-orange.svg)](#status)

</div>

---

`claude-dot-display` turns a 64x64 iDotMatrix LED panel into an ambient status
board for your Claude Code sessions. Each running session gets a row: its name
in a colour that tells you its state, and how many stages it has left.

- **blue** — running
- **amber** — waiting on a question
- **red** — hit an issue
- **green** — done

When nothing is running, the panel switches to a summary of your token usage.

Underneath it is an original, MIT-licensed implementation of the iDotMatrix
BLE protocol, usable on its own.

## Status

Alpha, and honest about it. The repository is being built in phases:

| Phase | What | State |
| --- | --- | --- |
| P0 | Repo foundation | in progress |
| P1 | BLE driver and protocol documentation | not started |
| P2 | Board, renderer, daemon | not started |
| P3 | Claude Code plugin | not started |

## Why we wrote our own protocol layer

Every Python library for these panels is **GPL-3.0**:

| Project | Licence |
| --- | --- |
| [`derkalle4/python3-idotmatrix-client`](https://github.com/derkalle4/python3-idotmatrix-client) | GPL-3.0 |
| [`markusressel/idotmatrix-api-client`](https://github.com/markusressel/idotmatrix-api-client) | GPL-3.0 |
| `python3-idotmatrix-library` (upstream of both) | GPL-3.0 |

Those projects did the original work of making these panels usable at all, and
this one would not exist without the path they cut. But their licence is
viral: anything that links them inherits it. A project cannot honestly call
itself MIT while requiring a GPL-3.0 library at runtime.

We wanted this to be **really** free — usable in commercial work, in
proprietary tools, in anything at all, with no obligations flowing back. So we
wrote the protocol layer ourselves.

**Clean-room, and we mean it.** No GPL source was read. The protocol was
derived from bytes observed on the wire against real hardware: the panel's own
traffic, captured and decoded. Protocol facts are not copyrightable, but
provenance is what makes that defensible, so [`PROTOCOL.md`](PROTOCOL.md)
records for every opcode what was observed, where it was captured, and that it
was replayed successfully against a physical unit.

If you only want a permissively licensed way to talk to one of these panels,
take `dotdisplay.ble` and ignore the rest.

## Requirements

- Python 3.11 or newer
- A Bluetooth LE adapter within range of the panel
- A 64x64 iDotMatrix panel

Other iDotMatrix sizes are untested. We only claim what we have verified
against hardware.

## Install

```bash
pipx install claude-dot-display
```

## Licence

MIT — see [LICENSE](LICENSE).
````

- [ ] **Step 4: Verify**

```bash
python -m pytest -q
ruff check .
```

Expected: 8 tests PASS.

The README links `PROTOCOL.md`, which P1 creates. That link is dead until then; it is listed as a known gap in Task 6's push checklist so it is closed deliberately rather than forgotten.

- [ ] **Step 5: Commit**

```bash
git add assets README.md
git commit -m "docs: add the logo and the README with the licensing rationale"
```

---

### Task 4: Plugin and marketplace manifests

Making the repo its own marketplace is what lets anyone install with two commands on day one, from GitHub or from the internal GitLab, with nobody's approval required.

**Files:**
- Create: `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`, `tests/test_manifests.py`

**Interfaces:**
- Consumes: `pyproject.toml` version from Task 1.
- Produces: a `claude-dot-display` plugin installable via `/plugin marketplace add MrMarco74/claude_dot_display`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_manifests.py`:

```python
import json
import pathlib
import tomllib

ROOT = pathlib.Path(__file__).resolve().parent.parent
PLUGIN = ROOT / ".claude-plugin" / "plugin.json"
MARKET = ROOT / ".claude-plugin" / "marketplace.json"


def test_plugin_manifest_is_valid_json_with_required_fields():
    d = json.loads(PLUGIN.read_text())
    assert d["name"] == "claude-dot-display"
    assert d["description"]
    assert d["author"]["name"]


def test_marketplace_lists_this_plugin_from_this_repo():
    d = json.loads(MARKET.read_text())
    entry = next(p for p in d["plugins"] if p["name"] == "claude-dot-display")
    # "./" means the plugin lives in this same repository, which is what makes
    # the repo self-hosting as a marketplace.
    assert entry["source"] == "./"


def test_plugin_version_matches_the_package_version():
    """Two version numbers that can disagree eventually will."""
    pkg = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    assert json.loads(PLUGIN.read_text())["version"] == pkg
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_manifests.py -q`
Expected: FAIL — `FileNotFoundError` for `.claude-plugin/plugin.json`.

- [ ] **Step 3: Write the implementation**

Create `.claude-plugin/plugin.json`:

```json
{
  "name": "claude-dot-display",
  "description": "Show your Claude Code sessions on an iDotMatrix LED panel.",
  "version": "0.1.0",
  "author": {
    "name": "MrMarco"
  },
  "homepage": "https://github.com/MrMarco74/claude_dot_display"
}
```

Create `.claude-plugin/marketplace.json`:

```json
{
  "$schema": "https://anthropic.com/claude-code/marketplace.schema.json",
  "name": "claude-dot-display",
  "description": "Ambient status board for Claude Code sessions on an LED matrix.",
  "owner": {
    "name": "MrMarco"
  },
  "plugins": [
    {
      "name": "claude-dot-display",
      "description": "Show your Claude Code sessions on an iDotMatrix LED panel.",
      "author": {
        "name": "MrMarco"
      },
      "category": "productivity",
      "source": "./",
      "homepage": "https://github.com/MrMarco74/claude_dot_display"
    }
  ]
}
```

The plugin carries no hooks, skills, or commands yet — P3 adds those. A manifest-only plugin is valid, and shipping it now means the install path exists and can be tested before there is anything to install.

- [ ] **Step 4: Validate against the real validator and run the tests**

```bash
claude plugin validate --strict .
python -m pytest -q
```

Expected: `✔ Validation passed`, and 11 tests PASS.

`claude plugin validate` is the authoritative check — the `$schema` URL is an identifier, not a fetchable document, so do not try to validate against it over HTTP.

- [ ] **Step 5: Commit**

```bash
git add .claude-plugin tests/test_manifests.py
git commit -m "feat: add the plugin and self-hosted marketplace manifests"
```

---

### Task 5: Continuous integration

**Files:**
- Create: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: the `dev` extra from Task 1.
- Produces: the CI badge target used by the README.

- [ ] **Step 1: Write the workflow**

Create `.github/workflows/ci.yml`:

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:

jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      fail-fast: false
      matrix:
        python-version: ["3.11", "3.12", "3.13"]
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}
          cache: pip

      - name: Install
        run: python -m pip install -e ".[dev]"

      - name: Lint
        run: ruff check .

      - name: Test
        run: python -m pytest -q
```

`claude plugin validate` is deliberately **not** in CI: the `claude` CLI is not available on GitHub runners. It stays a local pre-push check, listed in Task 6. `claude plugin eval` is deferred to P3, where the plugin gains behaviour worth evaluating — it needs API credentials in repository secrets, which is a decision to make then, not now.

- [ ] **Step 2: Verify the workflow parses**

```bash
python3 -c "
import yaml, pathlib
d = yaml.safe_load(pathlib.Path('.github/workflows/ci.yml').read_text())
print(sorted(d['jobs']['test']['strategy']['matrix']['python-version']))"
```

Expected: `['3.11', '3.12', '3.13']`

If `yaml` is missing: `python -m pip install pyyaml`.

- [ ] **Step 3: Run the same checks CI will run**

```bash
ruff check .
python -m pytest -q
```

Expected: clean, 11 tests PASS. CI must never be the first place a failure is discovered.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: lint and test on Python 3.11 through 3.13"
```

---

### Task 6: Publish to GitHub and the internal GitLab

**Files:**
- Modify: git remotes only. No source changes.

**STOP — this task publishes a public repository.** Creating a public GitHub repo is outward-facing and irreversible in practice (the name is claimed, and anything pushed may be indexed). Confirm with the user before Step 2, even though the overall plan is approved.

- [ ] **Step 1: Pre-push checklist**

```bash
cd ~/Documents/gitlab/claude_dot_display
ruff check .
python -m pytest -q
claude plugin validate --strict .
git status --short          # must be clean
git log --oneline
```

Confirm all of:
- 11 tests pass, ruff clean, plugin validation passes.
- No captured Bluetooth traffic is staged — `.gitignore` excludes `captures/`, `*.btsnoop`, `*.log`. Captures can contain identifiers from other devices in range.
- The README's link to `PROTOCOL.md` is still dead. That is expected at P0 and closes in P1.
- **The Octocat question is resolved.** The logo currently includes GitHub's
  mascot; either the author has decided to ship it as-is, or the logo has been
  amended. Do not publish while this is still open.

- [ ] **Step 2: Create the GitHub repository and push**

`gh` is already authenticated as `MrMarco74`.

```bash
gh repo create MrMarco74/claude_dot_display \
  --public \
  --description "Your Claude Code sessions, on an LED matrix. MIT, with a clean-room iDotMatrix BLE driver." \
  --source . --remote origin --push
```

If the remote was added over HTTPS, switch it to SSH as specified:

```bash
git remote set-url origin git@github.com:MrMarco74/claude_dot_display.git
git remote -v
```

- [ ] **Step 3: Add the GitLab remote and push**

```bash
git remote add gitlab git@<internal-gitlab>:apps/claude_dot_display.git
git push -u gitlab main
```

If GitLab rejects the push because the project does not exist, create it in the GitLab UI under the `apps` group first, then repeat the push. Do not enable push-to-create just for this.

- [ ] **Step 4: Verify both remotes actually received the code**

```bash
git ls-remote --heads origin main
git ls-remote --heads gitlab main
git log --oneline -1
```

Both must report the same SHA as the local `main`. A successful `git push` message is not sufficient evidence.

- [ ] **Step 5: Verify CI ran and the install path works**

```bash
gh run list --limit 3
gh run watch
```

Expected: the CI run passes on all three Python versions. If the badge in the README still shows `no status`, the workflow file was not on `main` when the run was triggered — push again and recheck.

Then confirm the marketplace install path end to end:

```bash
claude plugin marketplace add MrMarco74/claude_dot_display
claude plugin list
```

Expected: `claude-dot-display` appears as an available plugin. This is the real proof the manifests are right — the tests only check their shape.

- [ ] **Step 6: Commit any fixes and close out**

If Steps 2–5 required manifest or workflow corrections:

```bash
git add -A
git commit -m "fix: correct <what> found during first publish"
git push origin main && git push gitlab main
```

---

## Definition of done

P0 is complete when all of these are true:

- `pipx install` is not yet possible (no PyPI release — that is P2/P3), but `pip install -e .` works and `dotdisplay --version` prints `0.1.0`.
- 11 tests pass locally and in CI on Python 3.11, 3.12, and 3.13.
- `ruff check .` is clean.
- `claude plugin validate --strict .` passes.
- `claude plugin marketplace add MrMarco74/claude_dot_display` succeeds from a clean machine's perspective.
- `main` is identical on GitHub and on the internal GitLab.
- The README states the GPL rationale and the clean-room method.

## Deliberately not in P0

- `PROTOCOL.md` — P1 writes it, from real captures.
- Any BLE, rendering, or daemon code.
- Hooks, skills, or plugin commands — P3.
- A PyPI release, and therefore the PyPI badge. Added at first publish so the badge is never broken.
- `claude plugin eval` in CI — P3, and it needs a credentials decision.
