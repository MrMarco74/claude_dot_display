"""Runs scripts/install.sh in a sandbox.

The installer's job is not only to build a venv: it must leave a
`dotdisplay` that a shell -- and therefore a Claude Code session running the
report-status skill -- can actually find. That end was missing for four
releases and failed silently, because the skill is told to swallow errors.
So this exercises the real script and asserts on the filesystem it leaves
behind, rather than on the text of the script.

python3 and systemctl are stubbed: creating a real venv and touching the
user's systemd would make the test slow and destructive, and neither is what
is under test here.
"""

import os
import pathlib
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
INSTALL = ROOT / "scripts" / "install.sh"

# Builds a venv-shaped directory and swallows the pip call.
FAKE_PYTHON3 = """#!/usr/bin/env bash
if [[ "${1:-}" == "-m" && "${2:-}" == "venv" ]]; then
    mkdir -p "$3/bin"
    for exe in python dotdisplay; do
        printf '#!/bin/sh\\nexit 0\\n' > "$3/bin/$exe"
        chmod +x "$3/bin/$exe"
    done
fi
exit 0
"""

# `is-active` must answer "no" or the installer refuses to run: it will not
# install while another process may own the radio.
FAKE_SYSTEMCTL = """#!/usr/bin/env bash
[[ "${2:-}" == "is-active" ]] && exit 3
exit 0
"""


@pytest.fixture
def run_installer(tmp_path):
    stub_bin = tmp_path / "stub-bin"
    stub_bin.mkdir()
    for name, body in (("python3", FAKE_PYTHON3), ("systemctl", FAKE_SYSTEMCTL)):
        path = stub_bin / name
        path.write_text(body)
        path.chmod(0o755)

    home = tmp_path / "home"
    home.mkdir()

    def run():
        env = dict(os.environ,
                   HOME=str(home),
                   PATH=f"{stub_bin}:{os.environ['PATH']}",
                   DOTDISPLAY_MAC="AA:BB:CC:DD:EE:FF")
        return subprocess.run(["bash", str(INSTALL)], env=env,
                              capture_output=True, text=True, timeout=60)

    return home, run


def test_installer_puts_dotdisplay_on_the_path(run_installer):
    """The daemon runs by absolute path and never needed this. Everything
    else -- the skill, the status line, the user's own shell -- does."""
    home, run = run_installer
    result = run()
    assert result.returncode == 0, result.stderr

    link = home / ".local/bin/dotdisplay"
    assert link.exists(), "installer left no dotdisplay on PATH"
    assert link.resolve() == (home / ".local/share/dotdisplay/venv/bin/dotdisplay")


def test_installing_twice_is_not_an_error(run_installer):
    """Re-running the installer is the normal way to upgrade."""
    home, run = run_installer
    assert run().returncode == 0
    second = run()
    assert second.returncode == 0, second.stderr
    assert (home / ".local/bin/dotdisplay").exists()
