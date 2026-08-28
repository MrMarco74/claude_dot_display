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


def test_check_without_a_mac_says_how_to_find_one(monkeypatch, capsys):
    """A first-time user has no address yet; the error must point at the way
    to get one rather than just refusing."""
    monkeypatch.delenv("DOTDISPLAY_MAC", raising=False)
    assert cli.main(["check"]) == 1
    assert "discover" in capsys.readouterr().err


def test_check_sends_the_code_and_prints_it(monkeypatch, capsys, mocker):
    monkeypatch.setenv("DOTDISPLAY_MAC", "AA:BB:CC:DD:EE:FF")
    sent = {}

    class FakePanel:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def send_image(self, img):
            sent["img"] = img

    mocker.patch("dotdisplay.ble.PanelClient", return_value=FakePanel())
    assert cli.main(["check", "--code", "4207"]) == 0
    assert sent["img"].size == (64, 64)
    assert "4207" in capsys.readouterr().out


def test_check_reports_the_daemon_as_a_likely_cause(monkeypatch, capsys, mocker):
    """Only one process can hold the radio. A raw BLE error would send the
    next person debugging the wrong layer."""
    monkeypatch.setenv("DOTDISPLAY_MAC", "AA:BB:CC:DD:EE:FF")
    mocker.patch("dotdisplay.ble.PanelClient", side_effect=OSError("busy"))
    assert cli.main(["check"]) == 1
    assert "dotdisplay.service" in capsys.readouterr().err


def test_discover_reports_when_nothing_is_found(capsys, mocker):
    mocker.patch("bleak.BleakScanner.discover", return_value=[])
    assert cli.main(["discover"]) == 1
    assert "IDM-" in capsys.readouterr().err


def test_discover_lists_only_panels(capsys, mocker):
    """Every Bluetooth device in the room would be noise."""
    class Dev:
        def __init__(self, address, name):
            self.address, self.name = address, name

    mocker.patch("bleak.BleakScanner.discover",
                 return_value=[Dev("11:22", "Headphones"),
                               Dev("9C:F6", "IDM-234849")])
    assert cli.main(["discover"]) == 0
    out = capsys.readouterr().out
    assert "IDM-234849" in out
    assert "Headphones" not in out


def _panel_env(tmp_path, monkeypatch):
    monkeypatch.setenv("DOTDISPLAY_STATE_DIR", str(tmp_path / "sessions"))
    monkeypatch.setenv("DOTDISPLAY_MAC", "AA:BB:CC:DD:EE:FF")


def test_a_command_is_queued_when_the_daemon_holds_the_radio(
        tmp_path, monkeypatch, mocker):
    """The whole point: a script must not have to stop the daemon."""
    _panel_env(tmp_path, monkeypatch)
    mocker.patch("dotdisplay.queue.daemon_is_alive", return_value=True)
    mocker.patch("dotdisplay.queue.await_result", return_value={"status": "done"})
    connect = mocker.patch("dotdisplay.ble.PanelClient")
    assert cli.main(["power", "on"]) == 0
    connect.assert_not_called()          # queued, not connected


def test_a_command_connects_directly_without_a_daemon(
        tmp_path, monkeypatch, mocker):
    _panel_env(tmp_path, monkeypatch)
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


def test_a_queue_timeout_falls_back_and_reports_the_real_failure(
        tmp_path, monkeypatch, mocker, capsys):
    """When the queue goes unanswered AND the radio is unreachable, the
    caller must learn the actual reason, not 'timed out'."""
    _panel_env(tmp_path, monkeypatch)
    mocker.patch("dotdisplay.queue.daemon_is_alive", return_value=True)
    mocker.patch("dotdisplay.queue.await_result", return_value=None)
    mocker.patch("dotdisplay.ble.PanelClient", side_effect=OSError("no radio"))
    assert cli.main(["power", "on"]) == 1
    assert "no radio" in capsys.readouterr().err


def test_json_output_is_machine_readable(tmp_path, monkeypatch, mocker, capsys):
    _panel_env(tmp_path, monkeypatch)
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
    _panel_env(tmp_path, monkeypatch)
    mocker.patch("dotdisplay.queue.daemon_is_alive", return_value=True)
    mocker.patch("dotdisplay.queue.await_result", return_value={"status": "done"})
    assert cli.main(args) == 0


def test_a_stale_heartbeat_falls_back_to_a_direct_connection(
        tmp_path, monkeypatch, mocker, capsys):
    """A crashed daemon leaves a heartbeat behind. Failing outright would
    make every command unusable until it expires."""
    _panel_env(tmp_path, monkeypatch)
    mocker.patch("dotdisplay.queue.daemon_is_alive", return_value=True)
    mocker.patch("dotdisplay.queue.await_result", return_value=None)

    class FakePanel:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def power(self, on):
            pass

    mocker.patch("dotdisplay.ble.PanelClient", return_value=FakePanel())
    assert cli.main(["power", "on"]) == 0
    assert "connecting directly" in capsys.readouterr().err


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


def test_status_without_left_keeps_the_stage_count(tmp_path, monkeypatch):
    """Reporting a new state says nothing about how many stages remain, so
    it must not silently discard a count that is still true."""
    monkeypatch.setenv("DOTDISPLAY_STATE_DIR", str(tmp_path))
    cli.main(["status", "--name", "hwmon-d7", "--state", "running",
              "--left", "4"])
    cli.main(["status", "--name", "hwmon-d7", "--state", "question"])
    body = json.loads((tmp_path / "hwmon-d7.json").read_text())
    assert body == {"name": "hwmon-d7", "status": "question", "stages_left": 4}


def test_left_zero_retracts_the_stage_count(tmp_path, monkeypatch):
    """Once a count is preserved across writes there has to be a way to stop
    advertising it, or a finished plan's number sits on the panel for ever.
    Nothing left to do and no count are the same thing to a reader."""
    monkeypatch.setenv("DOTDISPLAY_STATE_DIR", str(tmp_path))
    cli.main(["status", "--name", "hwmon-d7", "--state", "running",
              "--left", "4"])
    cli.main(["status", "--name", "hwmon-d7", "--state", "done", "--left", "0"])
    body = json.loads((tmp_path / "hwmon-d7.json").read_text())
    assert body == {"name": "hwmon-d7", "status": "done"}
