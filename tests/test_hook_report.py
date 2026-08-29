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


def test_a_prompt_does_not_wipe_a_reported_stage_count(tmp_path):
    """UserPromptSubmit fires 'running' on every prompt. Overwriting is
    right for the status -- a new prompt does mean running again -- but the
    prompt says nothing about how many stages are left, so erasing the count
    threw away the only number the board has."""
    payload = {"cwd": "/home/x/hwmon", "session_id": "aa11"}
    env = {"DOTDISPLAY_STATE_DIR": str(tmp_path), "HOME": str(tmp_path)}
    _run(payload, ["question"], dict(env))
    path = next(iter(tmp_path.glob("*.json")))
    path.write_text(json.dumps({"name": path.stem, "status": "question",
                                "stages_left": 3}))

    _run(payload, ["running"], dict(env))
    body = json.loads(path.read_text())
    assert body["status"] == "running"       # the prompt does move the state
    assert body["stages_left"] == 3          # but not the count


def test_an_unreadable_session_file_still_reports(tmp_path):
    """Merging means reading first. A file that cannot be parsed must not
    stop the session from reaching the board at all."""
    payload = {"cwd": "/home/x/hwmon", "session_id": "aa11"}
    env = {"DOTDISPLAY_STATE_DIR": str(tmp_path), "HOME": str(tmp_path)}
    _run(payload, ["running"], dict(env))
    path = next(iter(tmp_path.glob("*.json")))
    path.write_text("{not json")

    _run(payload, ["running"], dict(env))
    assert json.loads(path.read_text())["status"] == "running"


def _todos(*items):
    """A TodoWrite PostToolUse payload. status is one of pending,
    in_progress, completed; activeForm is the present-tense phrasing."""
    return {"cwd": "/home/x/hwmon", "session_id": "aa11",
            "tool_name": "TodoWrite",
            "tool_input": {"todos": [
                {"content": c, "status": s, "activeForm": a}
                for c, s, a in items]}}


def _body(tmp_path):
    return json.loads((tmp_path / "hwmon-aa.json").read_text())


def test_a_todo_list_becomes_the_stage_count(tmp_path):
    """The count was only ever written when the assistant chose to report it,
    which in practice never happened -- the column stayed empty for every
    session forever. The todo list is the same information, already on the
    wire, and needs nobody to remember anything."""
    env = {"DOTDISPLAY_STATE_DIR": str(tmp_path), "HOME": str(tmp_path)}
    _run({"cwd": "/home/x/hwmon", "session_id": "aa11"}, ["running"], dict(env))
    _run(_todos(("Fix the transport", "completed", "Fixing the transport"),
                ("Refresh the panel", "in_progress", "Refreshing the panel"),
                ("Update the docs", "pending", "Updating the docs")),
         ["--beat"], dict(env))

    body = _body(tmp_path)
    assert body["stages_left"] == 2
    assert body["stages_total"] == 3
    assert body["activity"] == "Refreshing the panel"
    assert body["tasks"] == ["Refresh the panel", "Update the docs"]
    assert body["status"] == "running"      # a beat never states a status


def test_finishing_every_todo_retracts_the_count(tmp_path):
    """An unretracted count outlives its plan and keeps advertising a number
    that is no longer true."""
    env = {"DOTDISPLAY_STATE_DIR": str(tmp_path), "HOME": str(tmp_path)}
    _run({"cwd": "/home/x/hwmon", "session_id": "aa11"}, ["running"], dict(env))
    _run(_todos(("One", "in_progress", "Doing one")), ["--beat"], dict(env))
    _run(_todos(("One", "completed", "Doing one")), ["--beat"], dict(env))

    body = _body(tmp_path)
    assert "stages_left" not in body
    assert "activity" not in body
    assert "tasks" not in body


def test_a_beat_from_another_tool_leaves_the_count_alone(tmp_path):
    """Every tool call beats. Only TodoWrite says anything about stages."""
    env = {"DOTDISPLAY_STATE_DIR": str(tmp_path), "HOME": str(tmp_path)}
    _run({"cwd": "/home/x/hwmon", "session_id": "aa11"}, ["running"], dict(env))
    _run(_todos(("One", "pending", "Doing one")), ["--beat"], dict(env))
    _run({"cwd": "/home/x/hwmon", "session_id": "aa11", "tool_name": "Bash"},
         ["--beat"], dict(env))
    assert _body(tmp_path)["stages_left"] == 1


def test_a_prompt_keeps_the_tasks_it_did_not_mention(tmp_path):
    """UserPromptSubmit fires 'running' on every prompt. A prompt is a
    statement about the state, not about the work still on the list."""
    env = {"DOTDISPLAY_STATE_DIR": str(tmp_path), "HOME": str(tmp_path)}
    _run({"cwd": "/home/x/hwmon", "session_id": "aa11"}, ["running"], dict(env))
    _run(_todos(("One", "in_progress", "Doing one")), ["--beat"], dict(env))
    _run({"cwd": "/home/x/hwmon", "session_id": "aa11"}, ["running"], dict(env))

    body = _body(tmp_path)
    assert body["stages_left"] == 1
    assert body["activity"] == "Doing one"
    assert body["tasks"] == ["One"]


def test_task_text_is_bounded_and_stripped_of_control_bytes(tmp_path):
    """Todo text is model-authored and lands in a terminal. An escape
    sequence in it would be executed by the terminal drawing the board."""
    env = {"DOTDISPLAY_STATE_DIR": str(tmp_path), "HOME": str(tmp_path)}
    _run({"cwd": "/home/x/hwmon", "session_id": "aa11"}, ["running"], dict(env))
    _run(_todos((f"\x1b[31mred\x07 {'x' * 500}", "in_progress", "\x1bDoing")),
         ["--beat"], dict(env))

    body = _body(tmp_path)
    assert "\x1b" not in body["tasks"][0] and "\x07" not in body["tasks"][0]
    assert len(body["tasks"][0]) <= 120
    assert body["activity"] == "Doing"


def test_a_beat_still_only_touches_freshness_without_a_session_file(tmp_path):
    """Self-healing: a session whose SessionStart hook did not run joins the
    board on its first tool call, with whatever the todo list says."""
    env = {"DOTDISPLAY_STATE_DIR": str(tmp_path), "HOME": str(tmp_path)}
    _run(_todos(("One", "pending", "Doing one")), ["--beat"], dict(env))
    body = _body(tmp_path)
    assert body["status"] == "running" and body["stages_left"] == 1
