"""Radio ownership for the iDotMatrix panel.

Splitting frames into ATT-sized writes lives here rather than in protocol.py
because only the transport knows the negotiated MTU. protocol.py stays pure
and hardware-free.
"""

import asyncio
import logging
import time

logger = logging.getLogger(__name__)

# Largest single ATT write observed from the vendor application.
MAX_WRITE = 509

# The panel drops data when written to as fast as the stack allows. The
# vendor app paces roughly 32ms between writes; this default is deliberately
# conservative and gets tuned against hardware.
#
# Measured against the wall clock, not counted per frame: a full image is
# three separate send() calls, and pacing that restarted with each of them
# left the first write of every chunk unpaced. Writes without response fail
# silently, so those dropped chunks reached the panel as a frame with a
# third of it missing and nothing in the log.
DEFAULT_PACING_S = 0.03

# PLACEHOLDER -- the captures record the ATT handle (0x0006), not the UUID,
# and bleak addresses characteristics by UUID. Confirmed against the panel in
# the hardware task; until then this is a hypothesis, not a finding.
WRITE_CHARACTERISTIC = "0000fa02-0000-1000-8000-00805f9b34fb"


class NotConnected(RuntimeError):
    """A write was attempted before connect() or after disconnect()."""


class BaseTransport:
    def __init__(self, pacing_s: float = DEFAULT_PACING_S):
        self.pacing_s = pacing_s
        self.connected = False
        # Narrowed to the negotiated MTU on connect; see BleakTransport.
        self.max_write = MAX_WRITE
        # When the last ATT write went out, so pacing survives the boundary
        # between one send() and the next. None means "nothing written yet".
        self._last_write_at: float | None = None

    async def connect(self) -> None:
        self.connected = True

    async def disconnect(self) -> None:
        self.connected = False

    async def send(self, frame: bytes) -> None:
        """Write one protocol frame, split to fit the ATT write limit."""
        if not self.connected:
            raise NotConnected("connect() first")
        limit = self.max_write
        parts = [frame[i: i + limit] for i in range(0, len(frame), limit)]
        for part in parts:
            await self._pace()
            await self._write(part)

    async def _pace(self) -> None:
        """Wait until the panel has had pacing_s since the last write.

        Deliberately not "sleep between writes": the poll loop leaves seconds
        between frames, and a burst is the only thing the panel objects to.
        """
        if not self.pacing_s:
            return
        now = time.monotonic()
        if self._last_write_at is not None:
            remaining = self.pacing_s - (now - self._last_write_at)
            if remaining > 0:
                await asyncio.sleep(remaining)
        self._last_write_at = now

    async def _write(self, part: bytes) -> None:
        raise NotImplementedError


class FakeTransport(BaseTransport):
    """Records what would have gone on the wire. The whole driver is testable
    through this without a panel, a radio, or real Bluetooth."""

    def __init__(self, pacing_s: float = 0):
        super().__init__(pacing_s=pacing_s)
        self.writes: list[bytes] = []

    async def _write(self, part: bytes) -> None:
        self.writes.append(part)


class BleakTransport(BaseTransport):
    def __init__(self, address: str, pacing_s: float = DEFAULT_PACING_S):
        super().__init__(pacing_s=pacing_s)
        self.address = address
        self._client = None

    async def connect(self) -> None:
        from bleak import BleakClient
        self._client = BleakClient(self.address)
        await self._client.connect()
        self.connected = True
        self.max_write = min(MAX_WRITE, await self._negotiated_mtu() - 3)
        logger.info("connected to %s, writing up to %d bytes at a time",
                    self.address, self.max_write)

    async def _negotiated_mtu(self) -> int:
        """Ask BlueZ what the MTU actually is.

        BlueZ reports the 23-byte default until the MTU is explicitly
        acquired, which would cap writes at 20 bytes and make a full frame
        roughly six times slower than it needs to be. Measured on this
        hardware: 517.

        `_acquire_mtu` is private bleak API and exists only on the BlueZ
        backend, so a missing or failing call degrades to the reported value
        rather than breaking the connection.
        """
        acquire = getattr(getattr(self._client, "_backend", None),
                          "_acquire_mtu", None)
        if acquire is not None:
            try:
                await acquire()
            except Exception as exc:      # noqa: BLE001 - never fail connect
                logger.debug("could not acquire MTU, using default: %s", exc)
        return self._client.mtu_size

    async def disconnect(self) -> None:
        self.connected = False
        if self._client is not None:
            try:
                await self._client.disconnect()
            finally:
                self._client = None

    async def _write(self, part: bytes) -> None:
        await self._client.write_gatt_char(WRITE_CHARACTERISTIC, part,
                                           response=False)
