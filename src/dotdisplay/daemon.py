"""The board loop.

Owns the radio: holds one BLE connection open and reconnects on failure.
Reconnecting per operation would dominate a five-second loop.
"""

import asyncio
import logging
from dataclasses import dataclass

from dotdisplay import render, sources
from dotdisplay.ble import PanelClient
from dotdisplay.config import Config
from dotdisplay.sources import CcusageCache

logger = logging.getLogger(__name__)

RECONNECT_DELAY_S = 10.0


@dataclass
class Board:
    """What the loop remembers between ticks."""
    last_sent: bytes | None = None
    ccusage: CcusageCache | None = None

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


async def tick(config: Config, board: Board, panel) -> bool:
    """One pass. Returns True if the panel was updated. Never raises: this
    runs unattended."""
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
        logger.warning("could not update the panel: %s", exc)
        return False

    board.last_sent = pixels
    return True


async def _execute(panel, command: dict) -> dict:
    kind = command.get("type")
    if kind == "set_brightness":
        await panel.set_brightness(int(command["brightness_percent"]))
    elif kind == "power":
        await panel.power(bool(command["on"]))
    elif kind == "send_image":
        import base64
        import io

        from PIL import Image
        raw = base64.b64decode(command["image_base64"])
        await panel.send_image(Image.open(io.BytesIO(raw)))
    else:
        raise ValueError(f"unsupported command type {kind!r}")
    return {"sent": True}


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
        if board is not None:
            # The command painted over the board; forget what we sent so the
            # next tick re-renders rather than seeing "no change".
            board.last_sent = None


async def run(config: Config) -> int:
    if not config.mac:
        raise SystemExit("DOTDISPLAY_MAC is required")

    board = Board()
    logger.info("watching %s every %.1fs", config.state_dir, config.poll_s)
    while True:
        try:
            async with PanelClient(config.mac) as panel:
                logger.info("panel connected")
                while True:
                    await serve_commands(config, panel, board)
                    await tick(config, board, panel)
                    await asyncio.sleep(config.poll_s)
        except Exception as exc:              # noqa: BLE001 - unattended loop
            # Out of range, powered off, or the radio was taken. All normal.
            logger.warning("panel unavailable (%s); retrying in %.0fs",
                           exc, RECONNECT_DELAY_S)
            # After losing the connection the panel's contents are unknown, so
            # a cached "already sent" value would be a lie and could leave the
            # board permanently stale.
            board.last_sent = None
            await asyncio.sleep(RECONNECT_DELAY_S)
