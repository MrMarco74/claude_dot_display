import json

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
    """Hooks fire for sessions that were never registered; that is normal."""
    monkeypatch.setenv("DOTDISPLAY_STATE_DIR", str(tmp_path))
    assert cli.main(["status", "--name", "ghost", "--clear"]) == 0


def test_status_rejects_a_name_that_is_not_a_safe_filename(tmp_path, monkeypatch):
    """The name becomes a filename and is rendered to an image; it must not
    carry path separators or control bytes."""
    monkeypatch.setenv("DOTDISPLAY_STATE_DIR", str(tmp_path))
    for bad in ("../escape", "has space", "", "a" * 40):
        assert cli.main(["status", "--name", bad, "--state", "running"]) == 1
    assert list(tmp_path.iterdir()) == []


def test_status_rejects_an_unknown_state(tmp_path, monkeypatch):
    monkeypatch.setenv("DOTDISPLAY_STATE_DIR", str(tmp_path))
    with pytest.raises(SystemExit):
        cli.main(["status", "--name", "x", "--state", "busy"])


def _pointer(tmp_path, name):
    import os
    import re
    slug = re.sub(r"[^A-Za-z0-9._-]+", "", os.getcwd().replace("/", "_")) or "root"
    d = tmp_path / "current"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{slug}.name").write_text(name)


def test_this_resolves_the_name_the_hooks_recorded(tmp_path, monkeypatch):
    """The assistant cannot derive its own session name; the hooks leave it
    in a pointer keyed by directory."""
    monkeypatch.setenv("DOTDISPLAY_STATE_DIR", str(tmp_path / "sessions"))
    _pointer(tmp_path, "hwmon-aa")
    assert cli.main(["status", "--this", "--state", "issue"]) == 0
    body = json.loads((tmp_path / "sessions" / "hwmon-aa.json").read_text())
    assert body["status"] == "issue"


def test_this_without_a_pointer_fails_clearly(tmp_path, monkeypatch, capsys):
    """Better a clear message than silently writing a wrong row."""
    monkeypatch.setenv("DOTDISPLAY_STATE_DIR", str(tmp_path / "sessions"))
    assert cli.main(["status", "--this", "--state", "done"]) == 1
    assert "no session recorded" in capsys.readouterr().err


def test_name_and_this_are_mutually_exclusive(tmp_path, monkeypatch):
    monkeypatch.setenv("DOTDISPLAY_STATE_DIR", str(tmp_path))
    with pytest.raises(SystemExit):
        cli.main(["status", "--name", "x", "--this", "--state", "done"])
