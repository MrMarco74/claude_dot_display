"""The board loop.

Owns the radio: holds one BLE connection open and reconnects on failure.
Reconnecting per operation would dominate a five-second loop.
"""

import asyncio
import contextlib
import logging
import signal
from dataclasses import dataclass

from dotdisplay import commands, render, sources
from dotdisplay import queue as _queue
from dotdisplay.ble import PanelClient
from dotdisplay.config import Config
from dotdisplay.sources import CcusageCache

logger = logging.getLogger(__name__)

RECONNECT_DELAY_S = 10.0

# Consecutive failed writes tolerated before the connection is considered
# dead. A single failure is normal -- the panel briefly out of range, a busy
# radio -- and healed by the next tick. A run of them is not: BlueZ can drop
# the resolved services under a live connection, after which every write
# raises and no number of retries against that client will ever succeed.
MAX_SEND_FAILURES = 3


class PanelUnreachable(RuntimeError):
    """The panel stopped accepting writes. Only a reconnect can fix it."""


@dataclass
class Board:
    """What the loop remembers between ticks."""
    last_sent: bytes | None = None
    ccusage: CcusageCache | None = None
    send_failures: int = 0

    def __post_init__(self):
        if self.ccusage is None:
            self.ccusage = CcusageCache()


def render_board(config: Config, board: Board):
    """Pick and draw the screen."""
    sessions = sources.read_sessions(config)
    header = sources.read_header()
    if sessions:
        return render.render_sessions(sessions, header)
    stats = sources.ccusage_stats(config, board.ccusage)
    return render.render_idle(stats, sources.trends(stats) if stats else {},
                              header)


async def _show_splash(panel, board: Board, stop) -> None:
    """Greet with the project mark for a moment after connecting.

    Purely decorative, so every failure is swallowed. last_sent is cleared
    afterwards, or the first real board would be suppressed as 'unchanged'.
    """
    try:
        image = render.splash()
        if image is not None:
            await panel.send_image(image)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), render.SPLASH_SECONDS)
    except Exception as exc:          # noqa: BLE001 - decoration only
        # Loading the asset is inside the try too: a decorative frame must
        # never be able to break a panel connection, however it fails.
        logger.debug("could not show the splash: %s", exc)
    board.last_sent = None


async def serve_local_queue(config: Config, panel) -> int:
    """Execute commands submitted by local shell callers.

    Without this, every one-shot command would fail while the board runs,
    because the daemon owns the radio.
    """
    ran = 0
    while True:
        claimed = _queue.claim(config)
        if not claimed:
            return ran
        request_id, body = claimed
        try:
            result = {"status": "done",
                      "result": await commands.execute(panel, body)}
        except Exception as exc:          # noqa: BLE001 - must always answer
            result = {"status": "error", "message": str(exc)}
        _queue.publish(config, request_id, result)
        ran += 1


async def tick(config: Config, board: Board, panel) -> bool:
    """One pass. Returns True if the panel was updated.

    Raises PanelUnreachable once the writes have failed MAX_SEND_FAILURES
    times in a row, so run() can rebuild the connection. Nothing else
    escapes: this runs unattended.
    """
    # Beat here rather than in run(): tick only runs while a panel connection
    # is held, which is exactly the condition the heartbeat advertises.
    _queue.beat(config)
    try:
        sources.prune_local_sessions(config)
    except Exception as exc:              # noqa: BLE001 - housekeeping only
        # Tidying the state directory is never worth a dark panel.
        logger.debug("could not prune session files: %s", exc)
    await serve_local_queue(config, panel)
    try:
        image = render_board(config, board)
    except Exception as exc:                  # noqa: BLE001 - unattended loop
        logger.exception("could not render the board: %s", exc)
        return False

    pixels = image.tobytes()
    if pixels == board.last_sent:
        return False

    try:
        await panel.send_image(image)
    except Exception as exc:                  # noqa: BLE001 - unattended loop
        # Deliberately do NOT record this as sent: the panel does not show
        # what we rendered, so the next tick must try again.
        board.send_failures += 1
        logger.warning("could not update the panel: %s", exc)
        if board.send_failures >= MAX_SEND_FAILURES:
            # Retrying a dead link forever leaves the panel showing a frame
            # from hours ago while the log fills with the same warning. Hand
            # the connection back instead; run() is the only thing that can
            # replace it.
            raise PanelUnreachable(
                f"{board.send_failures} writes in a row failed: {exc}") from exc
        return False

    board.send_failures = 0
    board.last_sent = pixels
    # Logged at INFO on purpose: this is the only externally visible sign that
    # the board changed, and it is what makes "a quiet board sends nothing"
    # something you can actually check rather than assume.
    logger.info("panel updated")
    return True


async def _execute(panel, command: dict) -> dict:
    """Kept as a name so serve_commands is untouched; the work moved to
    dotdisplay.commands so the CLI and the local queue share it."""
    return await commands.execute(panel, command)


async def serve_commands(config: Config, panel, board: Board | None = None) -> int:
    """Drain hwmon's command queue. Returns how many commands ran.

    Runs before tick() so an explicit human request takes priority over the
    ambient board.
    """
    if not config.hwmon_url:
        return 0

    ran = 0
    while True:
        try:
            command = sources.claim_command(config)
        except Exception as exc:              # noqa: BLE001 - unattended loop
            logger.warning("could not claim a command: %s", exc)
            return ran
        if not command:
            return ran
        request_id, body = command

        try:
            result = {"status": "done", "result": await _execute(panel, body)}
        except Exception as exc:              # noqa: BLE001 - must always report
            # An unreported failure leaves the command in the server's
            # inflight directory until a sweep expires it.
            result = {"status": "error", "message": str(exc)}
        try:
            sources.report_result(config, request_id, result)
        except Exception as exc:              # noqa: BLE001
            logger.warning("could not report a result: %s", exc)

        ran += 1
        # Deliberately do NOT clear board.last_sent here.
        #
        # A command paints over the board, so it is tempting to invalidate the
        # cache "because the panel changed". That is exactly wrong: the next
        # tick would then re-render the unchanged board and wipe the command's
        # image within seconds. Observed on hardware.
        #
        # Because the daemon sends only when its *render* changes -- not when
        # the panel's contents change -- keeping the cache lets an
        # idot-send.sh image stay up until session state actually moves, and
        # the board reclaims the panel naturally at that point.


def _install_stop_handler() -> asyncio.Event:
    """Return an Event set on SIGTERM or SIGINT.

    Without this, systemd's SIGTERM kills the process mid-connection and BlueZ
    keeps holding the link: the panel then reports Connected while no process
    owns it, and the next start cannot find the device at all. Observed on
    hardware, not hypothetical.
    """
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError, ValueError):
            loop.add_signal_handler(sig, stop.set)
    return stop


async def run(config: Config) -> int:
    if not config.mac:
        raise SystemExit("DOTDISPLAY_MAC is required")

    board = Board()
    stop = _install_stop_handler()
    logger.info("watching %s every %.1fs", config.state_dir, config.poll_s)
    while not stop.is_set():
        try:
            async with PanelClient(config.mac) as panel:
                logger.info("panel connected")
                await _show_splash(panel, board, stop)
                while not stop.is_set():
                    await serve_commands(config, panel, board)
                    await tick(config, board, panel)
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(stop.wait(), config.poll_s)
        except Exception as exc:              # noqa: BLE001 - unattended loop
            # Out of range, powered off, or the radio was taken. All normal.
            logger.warning("panel unavailable (%s); retrying in %.0fs",
                           exc, RECONNECT_DELAY_S)
            # After losing the connection the panel's contents are unknown, so
            # a cached "already sent" value would be a lie and could leave the
            # board permanently stale.
            board.last_sent = None
            board.send_failures = 0
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), RECONNECT_DELAY_S)

    # Remove the heartbeat, or shell callers would keep queueing into a void
    # for HEARTBEAT_STALE_S after this process is gone.
    _queue.clear_heartbeat(config)
    logger.info("stopping; releasing the panel")
    return 0
