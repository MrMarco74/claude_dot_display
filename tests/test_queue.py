import dataclasses
import os
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
    q.beat(cfg)
    path = q._heartbeat_path(cfg)
    old = time.time() - q.HEARTBEAT_STALE_S - 5
    os.utime(path, (old, old))
    assert q.daemon_is_alive(cfg) is False
