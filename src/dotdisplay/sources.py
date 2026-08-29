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

import requests

logger = logging.getLogger(__name__)

RATE_LIMIT_PATH = pathlib.Path.home() / ".claude" / "abtop-rate-limits.json"
TREND_PATH = pathlib.Path.home() / ".cache" / "dotdisplay-trends.json"
VALID_STATUSES = ("running", "question", "issue", "done")
MAX_TASKS = 12
MAX_TEXT = 120
HTTP_TIMEOUT_S = 10
USER_AGENT = "claude-dot-display/1.0"

# Remembers the last remote-session failure so the same one is not logged on
# every poll.
_last_remote_error = [""]

CLAIM_PATH = "/api/sensmonlight/idotmatrix/agent/claim"
RESULT_PATH = "/api/sensmonlight/idotmatrix/agent/result"


@dataclass
class CcusageCache:
    stats: dict = field(default_factory=dict)
    fetched_at: float = 0.0


def read_local_sessions(config) -> list[dict]:
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
        entry.update(_progress(body))
        sessions.append(entry)
    return sessions


def _progress(body: dict) -> dict:
    """The optional progress fields, kept only where they are the right shape.

    These are written by hooks/report.py, which ships with the plugin and is
    updated independently of the daemon. A session running an older or newer
    hook must still reach the board -- dropping one field is a cost the
    reader can absorb, dropping the session is not.
    """
    out = {}
    for key in ("stages_left", "stages_total"):
        value = body.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            out[key] = value
    if isinstance(body.get("activity"), str):
        out["activity"] = body["activity"][:MAX_TEXT]
    tasks = body.get("tasks")
    if isinstance(tasks, list):
        kept = [task[:MAX_TEXT] for task in tasks if isinstance(task, str)]
        if kept:
            out["tasks"] = kept[:MAX_TASKS]
    return out


def prune_local_sessions(config) -> int:
    """Delete session files that have been dead for prune_after_s. Returns
    how many were removed.

    Sessions leave the board after stale_after_s but keep their file, so a
    session that goes quiet and comes back still has its stages_left. Nothing
    ever removed those files, though: a SessionEnd hook that does not run --
    a crash, a closed terminal, a machine that sleeps -- leaves one behind
    forever, and the directory grows for as long as the machine is used.

    Called from the daemon loop, never from the read path: `dotdisplay board`
    reads the same directory from another process, and a read that quietly
    deletes files is a trap.

    Only files this project writes are touched, and a file that cannot be
    removed is skipped rather than raised: tidying is never worth stopping
    the board for.
    """
    directory = pathlib.Path(config.state_dir)
    cutoff = time.time() - config.prune_after_s
    removed = 0
    for path in (*directory.glob("*.json"),
                 *(directory.parent / "current").glob("*.name")):
        try:
            if path.stat().st_mtime >= cutoff:
                continue
            path.unlink()
        except OSError as exc:
            logger.debug("could not prune %s: %s", path.name, exc)
            continue
        logger.info("pruned dead session file %s", path.name)
        removed += 1
    return removed


def _hwmon_headers(config):
    # The User-Agent is not cosmetic: the reverse proxy in front of
    # hwmon-server drops the default "python-requests/x.y" outright, closing
    # the connection with no response -- which looks like the server is down.
    return {"X-Setup-Key": config.setup_key, "User-Agent": USER_AGENT}


def fetch_remote_sessions(config) -> list[dict]:
    response = requests.get(f"{config.sessions_url}/api/sensmonlight/sessions",
                            headers=_hwmon_headers(config),
                            timeout=HTTP_TIMEOUT_S)
    response.raise_for_status()
    return response.json()


def read_sessions(config) -> list[dict]:
    """Local first, remote merged in if configured. A remote failure keeps the
    local sessions rather than blanking the board."""
    sessions = read_local_sessions(config)
    if not config.sessions_url:
        return sessions
    try:
        remote = fetch_remote_sessions(config)
    except (requests.exceptions.RequestException, ValueError) as exc:
        # Warn once per distinct failure, then stay quiet: an optional source
        # that is simply absent must not put a line in the log on every poll,
        # or the log stops being readable at all.
        message = str(exc)
        if message != _last_remote_error[0]:
            logger.warning("remote sessions unavailable: %s", message)
            _last_remote_error[0] = message
        return sessions
    _last_remote_error[0] = ""
    known = {item["name"] for item in sessions}
    sessions.extend(item for item in remote
                    if item.get("name") and item["name"] not in known)
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


def _run_ccusage() -> dict:
    output = subprocess.run(["ccusage", "daily", "--json"], capture_output=True,
                            text=True, timeout=180, check=True).stdout
    data = json.loads(output)
    rows = data[next(k for k in data if isinstance(data[k], list))]
    today, totals = rows[-1], data.get("totals", {})
    return {"today": today["totalTokens"], "out": today["outputTokens"],
            "cache": today["cacheCreationTokens"],
            "read": today["cacheReadTokens"],
            "all": totals.get("totalTokens", today["totalTokens"])}


def ccusage_stats(config, cache: CcusageCache) -> dict:
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


def trends(stats: dict, path=TREND_PATH) -> dict:
    """Compare against yesterday. A metric with no baseline gets NO arrow."""
    today = _dt.date.today().isoformat()
    path = pathlib.Path(path)
    try:
        previous = json.loads(path.read_text())
    except (OSError, ValueError):
        previous = {}

    out = {}
    if previous.get("date") and previous["date"] != today:
        for key, value in stats.items():
            if key in previous.get("stats", {}):
                out[key] = value > previous["stats"][key]
    if previous.get("date") != today:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"date": today, "stats": stats}))
        except OSError as exc:
            logger.debug("could not persist trend baseline: %s", exc)
    return out


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
