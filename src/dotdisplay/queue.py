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


def clear_heartbeat(config) -> None:
    """Called on clean shutdown. Without it a stopped daemon still looks
    alive for HEARTBEAT_STALE_S and callers queue into nothing."""
    try:
        _heartbeat_path(config).unlink(missing_ok=True)
    except OSError:
        pass


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
    """Take the oldest pending command, or None.

    Claiming removes the request, so a command cannot be executed twice.
    """
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
