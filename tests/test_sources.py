import dataclasses
import json
import os
import time

import pytest
import requests

from dotdisplay import sources as s
from dotdisplay.config import Config


@pytest.fixture
def cfg(tmp_path):
    return dataclasses.replace(Config.from_env(), state_dir=tmp_path,
                               hwmon_url="", setup_key="")


def _write(cfg, name, status="running", stages_left=None, age_s=0):
    body = {"name": name, "status": status}
    if stages_left is not None:
        body["stages_left"] = stages_left
    path = cfg.state_dir / f"{name}.json"
    path.write_text(json.dumps(body))
    if age_s:
        old = time.time() - age_s
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
    cfg = dataclasses.replace(cfg, sessions_url="https://example.invalid")
    _write(cfg, "local")
    mocker.patch("dotdisplay.sources.fetch_remote_sessions",
                 return_value=[{"name": "remote", "status": "running"}])
    assert sorted(x["name"] for x in s.read_sessions(cfg)) == ["local", "remote"]


def test_remote_failure_leaves_local_sessions_intact(cfg, mocker):
    """The board must not go blank because a server is down."""
    cfg = dataclasses.replace(cfg, sessions_url="https://example.invalid")
    _write(cfg, "local")
    mocker.patch("dotdisplay.sources.fetch_remote_sessions",
                 side_effect=requests.exceptions.ConnectionError("down"))
    assert [x["name"] for x in s.read_sessions(cfg)] == ["local"]


def test_no_remote_configured_means_no_request(cfg, mocker):
    get = mocker.patch("dotdisplay.sources.requests.get")
    s.read_sessions(cfg)
    get.assert_not_called()


def test_header_missing_file_is_tolerated(mocker, tmp_path):
    mocker.patch("dotdisplay.sources.RATE_LIMIT_PATH", tmp_path / "nope.json")
    assert s.read_header() is None


def test_header_is_parsed(mocker, tmp_path):
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


def test_a_repeating_remote_failure_is_logged_once(cfg, mocker, caplog):
    """An optional source that is simply absent must not put a line in the
    log on every poll, or the log stops being readable."""
    import logging
    cfg = dataclasses.replace(cfg, sessions_url="https://example.invalid")
    mocker.patch("dotdisplay.sources.fetch_remote_sessions",
                 side_effect=requests.exceptions.ConnectionError("404 nope"))
    s._last_remote_error[0] = ""
    with caplog.at_level(logging.WARNING, logger="dotdisplay.sources"):
        for _ in range(5):
            s.read_sessions(cfg)
    assert len(caplog.records) == 1


def test_hwmon_url_alone_does_not_enable_remote_sessions(cfg, mocker):
    """The command queue and the session registry are separate opt-ins: the
    registry endpoint does not exist on hwmon-server at all, so setting the
    command URL must not switch it on."""
    cfg = dataclasses.replace(cfg, hwmon_url="https://example.invalid")
    fetch = mocker.patch("dotdisplay.sources.fetch_remote_sessions")
    s.read_sessions(cfg)
    fetch.assert_not_called()


# --- pruning -----------------------------------------------------------


@pytest.fixture
def prune_cfg(tmp_path):
    """A state dir with a real parent, so the sibling pointer directory the
    hook writes to exists in the same shape as on disk."""
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    return dataclasses.replace(Config.from_env(), state_dir=sessions,
                               hwmon_url="", setup_key="")


def _pointer(cfg, slug, name, age_s=0):
    directory = cfg.state_dir.parent / "current"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{slug}.name"
    path.write_text(name)
    if age_s:
        old = time.time() - age_s
        os.utime(path, (old, old))
    return path


def test_prune_deletes_long_dead_sessions(prune_cfg):
    dead = _write(prune_cfg, "ghost", age_s=prune_cfg.prune_after_s + 60)
    s.prune_local_sessions(prune_cfg)
    assert not dead.exists()


def test_prune_keeps_a_session_that_is_merely_stale(prune_cfg):
    """Off the board is not gone: a session between the two thresholds keeps
    its file, so its stages_left survives a quiet stretch."""
    quiet = _write(prune_cfg, "quiet", stages_left=3,
                   age_s=prune_cfg.stale_after_s + 60)
    s.prune_local_sessions(prune_cfg)
    assert json.loads(quiet.read_text())["stages_left"] == 3


def test_prune_reports_how_many_it_removed(prune_cfg):
    _write(prune_cfg, "a", age_s=prune_cfg.prune_after_s + 60)
    _write(prune_cfg, "b", age_s=prune_cfg.prune_after_s + 60)
    _write(prune_cfg, "live")
    assert s.prune_local_sessions(prune_cfg) == 2


def test_prune_removes_dead_pointer_files(prune_cfg):
    """A pointer outliving its session would let `status --this` resurrect a
    dead name on the board."""
    pointer = _pointer(prune_cfg, "_home_x", "ghost",
                       age_s=prune_cfg.prune_after_s + 60)
    fresh = _pointer(prune_cfg, "_home_y", "live")
    s.prune_local_sessions(prune_cfg)
    assert not pointer.exists()
    assert fresh.exists()


def test_prune_leaves_files_it_does_not_own(prune_cfg):
    other = prune_cfg.state_dir / "notes.txt"
    other.write_text("keep me")
    old = time.time() - (prune_cfg.prune_after_s + 60)
    os.utime(other, (old, old))
    s.prune_local_sessions(prune_cfg)
    assert other.exists()


def test_prune_missing_state_directory_is_not_an_error(prune_cfg):
    cfg = dataclasses.replace(prune_cfg, state_dir=prune_cfg.state_dir / "nope")
    assert s.prune_local_sessions(cfg) == 0
