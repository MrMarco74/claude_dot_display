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
