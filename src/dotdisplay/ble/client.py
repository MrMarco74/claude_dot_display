"""The driver's public API.

Composes the pure frame builders with a transport. Everything here is
testable against FakeTransport; nothing here talks to Bluetooth directly.
"""

import logging

from dotdisplay.ble import protocol
from dotdisplay.ble.transport import BleakTransport

logger = logging.getLogger(__name__)


class PanelClient:
    def __init__(self, address: str | None = None, transport=None,
                 pacing_s: float | None = None):
        if transport is None:
            if address is None:
                raise ValueError("either address or transport is required")
            kwargs = {} if pacing_s is None else {"pacing_s": pacing_s}
            transport = BleakTransport(address, **kwargs)
        self.transport = transport

    async def __aenter__(self):
        await self.transport.connect()
        return self

    async def __aexit__(self, *exc):
        await self.transport.disconnect()
        return False

    async def set_brightness(self, percent: int) -> None:
        await self.transport.send(protocol.set_brightness(percent))

    async def power(self, on: bool) -> None:
        await self.transport.send(protocol.set_power(on))

    async def draw_pixel(self, x: int, y: int, rgb) -> None:
        await self.transport.send(protocol.draw_pixel(x, y, rgb))

    async def send_image(self, img) -> None:
        """Push a full frame. ~0.9s on the captured hardware."""
        await self.transport.send(protocol.set_image_mode(True))
        for chunk in protocol.encode_image(protocol.image_to_rgb(img)):
            await self.transport.send(chunk)
