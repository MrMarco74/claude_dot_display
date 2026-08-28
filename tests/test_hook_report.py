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


def test_a_pointer_records_the_name_for_this_directory(tmp_path):
    """The assistant never sees the hook payload, so it cannot derive its own
    session name. The pointer is how `dotdisplay status --this` finds it."""
    _run({"cwd": "/home/x/hwmon", "session_id": "aa11"}, ["running"],
         {"DOTDISPLAY_STATE_DIR": str(tmp_path / "sessions"),
          "HOME": str(tmp_path)})
    pointers = list((tmp_path / "current").glob("*.name"))
    assert len(pointers) == 1
    assert pointers[0].read_text().startswith("hwmon-")


def test_clear_removes_the_pointer_too(tmp_path):
    payload = {"cwd": "/home/x/hwmon", "session_id": "aa11"}
    env = {"DOTDISPLAY_STATE_DIR": str(tmp_path / "sessions"),
           "HOME": str(tmp_path)}
    _run(payload, ["running"], dict(env))
    _run(payload, ["--clear"], dict(env))
    assert list((tmp_path / "current").glob("*.name")) == []


def test_a_beat_leaves_an_existing_status_alone(tmp_path):
    """The whole point of a separate mode. A session showing amber
    'question' or red 'issue' is still running tools while it waits or
    retries; a heartbeat that asserted 'running' would wipe the very signal
    the board exists to show."""
    import os
    import time

    payload = {"cwd": "/home/x/hwmon", "session_id": "aa11"}
    env = {"DOTDISPLAY_STATE_DIR": str(tmp_path), "HOME": str(tmp_path)}
    _run(payload, ["issue"], dict(env))
    path = next(iter(tmp_path.glob("*.json")))
    stale = time.time() - 3600
    os.utime(path, (stale, stale))

    _run(payload, ["--beat"], dict(env))
    # Both halves, or "did nothing at all" would pass the status assertion.
    assert path.stat().st_mtime > stale + 60
    assert json.loads(path.read_text())["status"] == "issue"


def test_a_beat_refreshes_a_session_that_had_gone_stale(tmp_path):
    """A session working on one long task never submits a prompt, so its
    file ages past stale_after_s and drops off the board while Claude is
    demonstrably alive. The beat is what stops that."""
    import os
    import time

    payload = {"cwd": "/home/x/hwmon", "session_id": "aa11"}
    env = {"DOTDISPLAY_STATE_DIR": str(tmp_path), "HOME": str(tmp_path)}
    _run(payload, ["running"], dict(env))
    path = next(iter(tmp_path.glob("*.json")))
    stale = time.time() - 3600
    os.utime(path, (stale, stale))

    _run(payload, ["--beat"], dict(env))
    assert path.stat().st_mtime > stale + 60


def test_a_beat_creates_a_missing_session_file(tmp_path):
    """Self-healing: a session started before the plugin was installed, or
    one whose SessionStart hook did not run, must still reach the board as
    soon as it does any work."""
    _run({"cwd": "/home/x/hwmon", "session_id": "aa11"}, ["--beat"],
         {"DOTDISPLAY_STATE_DIR": str(tmp_path), "HOME": str(tmp_path)})
    written = list(tmp_path.glob("*.json"))
    assert len(written) == 1
    assert json.loads(written[0].read_text())["status"] == "running"
