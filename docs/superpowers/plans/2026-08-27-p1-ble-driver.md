# P1 — BLE Driver Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the observed protocol in `PROTOCOL.md` into a working, MIT-licensed driver — and replace every `replayed: no` in that document with a verified `yes` by changing the physical panel.

**Architecture:** Three layers with a hard boundary between purity and I/O. `protocol.py` builds frames and knows nothing about Bluetooth, so it is testable byte-for-byte against real captured traffic with no hardware. `transport.py` owns the radio and the MTU splitting. `client.py` composes them into the API the rest of the project uses.

**Tech Stack:** Python 3.11+, `bleak`, Pillow, pytest, pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-08-27-architecture-design.md`
**Protocol:** `PROTOCOL.md` — every frame in this plan traces to an entry there.

## Global Constraints

- **Nothing identifying goes into this repo.** No MAC addresses, device serials, or capture files. The panel address comes from `DOTDISPLAY_MAC` at runtime. This is the same discipline that caught three leaks before the P0 push; do not relax it for convenience.
- **The clean-room rule stays binding.** Do not open GPL iDotMatrix source at any point. Everything needed is in `PROTOCOL.md`.
- **Runtime dependencies remain exactly `bleak`, `pillow`, `requests`.** `tests/test_licensing.py` enforces this and will fail the build otherwise. `pytest-asyncio` goes in the `dev` extra, not `dependencies`.
- **Only one process may own the radio.** `sensmonlight-idotmatrix-agent.service` is currently active and must be stopped before any hardware step (Task 5).
- Work inside `.venv`; this Ubuntu is PEP 668 externally managed.
- Test command: `cd ~/Documents/gitlab/claude_dot_display && .venv/bin/python -m pytest -q`. **Baseline 11 tests.**
- Lint: `.venv/bin/ruff check .`
- No real Bluetooth in tests. No test above ~1s.
- **A passing test is not evidence the panel changed.** Task 5 is the only place correctness against hardware is established.

## File Structure

| File | Responsibility |
| --- | --- |
| `src/dotdisplay/ble/__init__.py` | Public surface of the driver |
| `src/dotdisplay/ble/protocol.py` | Pure frame builders. No I/O, no async, no bleak |
| `src/dotdisplay/ble/transport.py` | Radio ownership, MTU splitting, pacing. `Transport` protocol + real and fake implementations |
| `src/dotdisplay/ble/client.py` | High-level async API composing the two |
| `tests/test_ble_protocol.py` | Byte-exact tests against captured traffic |
| `tests/test_ble_transport.py` | Splitting and pacing, against the fake |
| `tests/test_ble_client.py` | Composition, against the fake |

The purity of `protocol.py` is the point. Because it is pure, the captured
bytes from the vendor application become a test oracle: our encoder must
produce what the panel was actually seen to accept.

---

### Task 1: protocol.py — short command frames

**Files:**
- Create: `src/dotdisplay/ble/__init__.py`, `src/dotdisplay/ble/protocol.py`
- Test: `tests/test_ble_protocol.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `set_brightness(percent: int) -> bytes`, `set_power(on: bool) -> bytes`, `draw_pixel(x: int, y: int, rgb: tuple[int, int, int]) -> bytes`, `set_image_mode(on: bool = True) -> bytes`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_ble_protocol.py`:

```python
import pytest

from dotdisplay.ble import protocol as p


# Frames observed from the vendor application against a physical panel.
# See PROTOCOL.md. These are the oracle: our encoder must reproduce them.
def test_brightness_matches_captured_frame():
    assert p.set_brightness(75) == bytes.fromhex("05 00 04 80 4b")
    assert p.set_brightness(15) == bytes.fromhex("05 00 04 80 0f")


def test_power_matches_captured_frames():
    assert p.set_power(False) == bytes.fromhex("05 00 07 01 00")
    assert p.set_power(True) == bytes.fromhex("05 00 07 01 01")


def test_image_mode_matches_captured_frame():
    assert p.set_image_mode(True) == bytes.fromhex("05 00 04 01 01")
    assert p.set_image_mode(False) == bytes.fromhex("05 00 04 01 00")


def test_draw_pixel_matches_captured_frame():
    """Captured while drawing red at (4,2) and blue at (34,50)."""
    assert p.draw_pixel(4, 2, (255, 0, 0)) == bytes.fromhex(
        "0a 00 05 01 00 ff 00 00 04 02")
    assert p.draw_pixel(34, 50, (0, 0, 255)) == bytes.fromhex(
        "0a 00 05 01 00 00 00 ff 22 32")


def test_every_frame_declares_its_own_length():
    """The first two bytes are the frame's own total length; a frame whose
    header disagrees with its body would desynchronise the device."""
    for frame in (p.set_brightness(50), p.set_power(True),
                  p.set_image_mode(), p.draw_pixel(1, 1, (1, 2, 3))):
        assert int.from_bytes(frame[:2], "little") == len(frame)


@pytest.mark.parametrize("bad", [-1, 101, 255])
def test_brightness_out_of_range_rejected(bad):
    with pytest.raises(ValueError):
        p.set_brightness(bad)


@pytest.mark.parametrize("x,y", [(-1, 0), (0, -1), (64, 0), (0, 64)])
def test_pixel_outside_the_panel_rejected(x, y):
    """Silently wrapping would corrupt a frame in a way that is very hard to
    diagnose from the outside."""
    with pytest.raises(ValueError):
        p.draw_pixel(x, y, (0, 0, 0))


@pytest.mark.parametrize("rgb", [(-1, 0, 0), (256, 0, 0), (0, 0, 300)])
def test_colour_out_of_range_rejected(rgb):
    with pytest.raises(ValueError):
        p.draw_pixel(0, 0, rgb)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_ble_protocol.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'dotdisplay.ble'`

- [ ] **Step 3: Write the implementation**

Create `src/dotdisplay/ble/__init__.py`:

```python
"""MIT-licensed iDotMatrix BLE driver.

Derived clean-room from observed wire traffic; see PROTOCOL.md.
"""

from dotdisplay.ble import protocol

__all__ = ["protocol"]
```

Create `src/dotdisplay/ble/protocol.py`:

```python
"""Frame builders for the iDotMatrix protocol.

Pure by design: no I/O, no async, no bleak. That is what lets the bytes
captured from the vendor application serve as a test oracle -- see
tests/test_ble_protocol.py and PROTOCOL.md.

Every frame begins with its own total length as a little-endian u16.
"""

WIDTH = HEIGHT = 64
LENGTH_FIELD = 2


def _frame(body: bytes) -> bytes:
    """Prefix a body with the frame's own total length.

    The length counts *itself*: a three-byte body is declared as 5, exactly
    as observed on the wire. Deriving it from the body rather than hard-coding
    a constant per command means a frame can never disagree with its header.
    """
    return (len(body) + LENGTH_FIELD).to_bytes(2, "little") + body


def _check_colour(rgb: tuple[int, int, int]) -> bytes:
    if len(rgb) != 3 or any(not 0 <= c <= 255 for c in rgb):
        raise ValueError(f"colour components must be 0-255: {rgb!r}")
    return bytes(rgb)


def set_brightness(percent: int) -> bytes:
    if not 0 <= percent <= 100:
        raise ValueError(f"brightness must be 0-100, got {percent}")
    return _frame(bytes([0x04, 0x80, percent]))


def set_power(on: bool) -> bytes:
    return _frame(bytes([0x07, 0x01, 1 if on else 0]))


def set_image_mode(on: bool = True) -> bytes:
    """Sent immediately before a bulk image transfer."""
    return _frame(bytes([0x04, 0x01, 1 if on else 0]))


def draw_pixel(x: int, y: int, rgb: tuple[int, int, int]) -> bytes:
    if not 0 <= x < WIDTH or not 0 <= y < HEIGHT:
        raise ValueError(f"pixel ({x},{y}) is outside the {WIDTH}x{HEIGHT} panel")
    return _frame(bytes([0x05, 0x01, 0x00]) + _check_colour(rgb) + bytes([x, y]))
```

Note the length field **counts itself**: a three-byte body is declared as 5.
That is what the captures show, and it is an easy off-by-two to get wrong —
`test_every_frame_declares_its_own_length` and the byte-exact frame tests both
catch it immediately.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest -q && .venv/bin/ruff check .`
Expected: PASS, 11 + 15 = 26 tests. (Five plain tests plus 3 + 4 + 3 parametrised cases.)

- [ ] **Step 5: Commit**

```bash
git add src/dotdisplay/ble tests/test_ble_protocol.py
git commit -m "feat: add iDotMatrix short command frames"
```

---

### Task 2: protocol.py — image encoding

The fast path. This is the part no open library has.

**Files:**
- Modify: `src/dotdisplay/ble/protocol.py`
- Test: `tests/test_ble_protocol.py`

**Interfaces:**
- Consumes: Task 1's `_frame` helper (not used here — bulk chunks carry a different header).
- Produces: `encode_image(rgb: bytes) -> list[bytes]`, `image_to_rgb(img) -> bytes`, constants `CHUNK_DATA = 4096`, `KIND_IMAGE = 0`, `KIND_ANIMATION = 1`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_ble_protocol.py`:

```python
BLACK = bytes(64 * 64 * 3)


def test_image_produces_three_chunks_matching_the_captured_shape():
    chunks = p.encode_image(BLACK)
    assert len(chunks) == 3
    assert [len(c) for c in chunks] == [4105, 4105, 4105]
    assert sum(len(c) for c in chunks) == 12315


def test_chunk_headers_match_the_captured_bytes():
    """First chunk flags 0x00, continuations 0x02; every chunk declares the
    full payload length. Captured from the vendor application."""
    chunks = p.encode_image(BLACK)
    assert chunks[0][:9] == bytes.fromhex("09 10 00 00 00 00 30 00 00")
    assert chunks[1][:9] == bytes.fromhex("09 10 00 00 02 00 30 00 00")
    assert chunks[2][:9] == bytes.fromhex("09 10 00 00 02 00 30 00 00")


def test_payload_is_the_pixels_unchanged():
    """The vendor app resamples what it sends; we must not. A caller asking
    for an exact pixel gets that exact pixel."""
    rgb = bytearray(BLACK)
    rgb[0:3] = b"\xff\xff\xff"
    body = b"".join(c[9:] for c in p.encode_image(bytes(rgb)))
    assert body == bytes(rgb)


def test_pixel_ordering_is_row_major_from_the_top_left():
    """Established by measurement, not inference: three single-pixel images
    at known corners located in the captured payloads. See PROTOCOL.md."""
    from PIL import Image
    for (x, y), expected_index in (((0, 0), 0), ((63, 0), 63), ((0, 63), 4032)):
        img = Image.new("RGB", (64, 64), (0, 0, 0))
        img.putpixel((x, y), (255, 255, 255))
        body = b"".join(c[9:] for c in p.encode_image(p.image_to_rgb(img)))
        lit = [i for i in range(4096) if body[i * 3:i * 3 + 3] != b"\x00\x00\x00"]
        assert lit == [expected_index], f"({x},{y}) landed at {lit}"


def test_wrong_payload_size_rejected():
    with pytest.raises(ValueError):
        p.encode_image(bytes(100))


def test_image_is_converted_and_resized():
    from PIL import Image
    assert len(p.image_to_rgb(Image.new("L", (32, 32), 255))) == 64 * 64 * 3
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_ble_protocol.py -q`
Expected: FAIL — `AttributeError: module ... has no attribute 'encode_image'`

- [ ] **Step 3: Write the implementation**

Append to `src/dotdisplay/ble/protocol.py`:

```python
import struct

CHUNK_DATA = 4096          # payload bytes per chunk, from the captures
CHUNK_HEADER = 9
KIND_IMAGE = 0x0000
KIND_ANIMATION = 0x0001    # envelope known; payload NOT decoded, do not use
FLAG_FIRST = 0x00
FLAG_CONTINUE = 0x02

IMAGE_BYTES = WIDTH * HEIGHT * 3


def image_to_rgb(img) -> bytes:
    """Flatten a Pillow image to the panel's raw RGB888, row-major from the
    top left. Anything not already 64x64 RGB is converted."""
    if img.mode != "RGB":
        img = img.convert("RGB")
    if img.size != (WIDTH, HEIGHT):
        from PIL import Image
        img = img.resize((WIDTH, HEIGHT), Image.Resampling.LANCZOS)
    return img.tobytes()


def encode_image(rgb: bytes, kind: int = KIND_IMAGE) -> list[bytes]:
    """Split a raw RGB888 payload into the device's bulk-transfer chunks.

    Returns whole chunks. Splitting these into ATT-sized writes is the
    transport's job, because only it knows the negotiated MTU.
    """
    if len(rgb) != IMAGE_BYTES:
        raise ValueError(
            f"expected {IMAGE_BYTES} bytes of RGB888, got {len(rgb)}")

    chunks = []
    for offset in range(0, len(rgb), CHUNK_DATA):
        data = rgb[offset: offset + CHUNK_DATA]
        header = struct.pack(
            "<HHBI",
            CHUNK_HEADER + len(data),                       # this chunk's length
            kind,
            FLAG_FIRST if offset == 0 else FLAG_CONTINUE,
            len(rgb),                                       # total payload
        )
        chunks.append(header + data)
    return chunks
```

`image_to_rgb` deliberately does **not** reproduce the vendor app's
resampling. The capture shows a single-pixel source arriving smeared across
roughly 33 pixels; for a status board that renders exact 8px text, that would
destroy legibility.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest -q && .venv/bin/ruff check .`
Expected: PASS, 26 + 6 = 32 tests.

- [ ] **Step 5: Commit**

```bash
git add src/dotdisplay/ble/protocol.py tests/test_ble_protocol.py
git commit -m "feat: add the fast image encoding path"
```

---

### Task 3: transport.py — radio, MTU splitting, pacing

**Files:**
- Create: `src/dotdisplay/ble/transport.py`
- Test: `tests/test_ble_transport.py`
- Modify: `pyproject.toml` (add `pytest-asyncio` to the `dev` extra only)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `Transport` (protocol), `FakeTransport`, `BleakTransport`, `MAX_WRITE = 509`.

- [ ] **Step 1: Add the test dependency**

In `pyproject.toml`, extend the dev extra and configure asyncio mode:

```toml
[project.optional-dependencies]
dev = ["pytest>=8.0", "pytest-mock>=3.14", "pytest-asyncio>=0.24", "ruff>=0.6"]
```

```toml
[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]
asyncio_mode = "auto"
```

Then `.venv/bin/python -m pip install -e ".[dev]"`.

**Do not add it to `dependencies`** — `tests/test_licensing.py` asserts the
runtime set is exactly `bleak`, `pillow`, `requests` and will fail.

- [ ] **Step 2: Write the failing tests**

Create `tests/test_ble_transport.py`:

```python
import pytest

from dotdisplay.ble import transport as t


async def test_fake_records_writes():
    fake = t.FakeTransport()
    await fake.connect()
    await fake.send(b"hello")
    await fake.disconnect()
    assert fake.writes == [b"hello"]
    assert fake.connected is False


async def test_large_frame_is_split_to_the_write_limit():
    """A 4105-byte chunk cannot go out in one ATT write."""
    fake = t.FakeTransport()
    await fake.connect()
    await fake.send(bytes(4105))
    assert all(len(w) <= t.MAX_WRITE for w in fake.writes)
    assert sum(len(w) for w in fake.writes) == 4105
    assert len(fake.writes) == 9      # 8 x 509 + 1 x 33, as captured


async def test_split_preserves_the_byte_stream_exactly():
    payload = bytes(range(256)) * 20
    fake = t.FakeTransport()
    await fake.connect()
    await fake.send(payload)
    assert b"".join(fake.writes) == payload


async def test_sending_while_disconnected_is_an_error():
    """Silently dropping writes would look like a dead panel and send the
    next person debugging the wrong layer."""
    with pytest.raises(t.NotConnected):
        await t.FakeTransport().send(b"x")


async def test_pacing_delay_is_applied_between_writes(mocker):
    """The panel drops data if written to as fast as the stack allows; the
    delay is configurable because the right value is hardware-dependent."""
    sleep = mocker.patch("dotdisplay.ble.transport.asyncio.sleep")
    fake = t.FakeTransport(pacing_s=0.01)
    await fake.connect()
    await fake.send(bytes(4105))
    assert sleep.await_count == len(fake.writes) - 1


async def test_no_pacing_means_no_sleeping(mocker):
    sleep = mocker.patch("dotdisplay.ble.transport.asyncio.sleep")
    fake = t.FakeTransport(pacing_s=0)
    await fake.connect()
    await fake.send(bytes(4105))
    sleep.assert_not_awaited()
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_ble_transport.py -q`
Expected: FAIL — no module `dotdisplay.ble.transport`.

- [ ] **Step 4: Write the implementation**

Create `src/dotdisplay/ble/transport.py`:

```python
"""Radio ownership for the iDotMatrix panel.

Splitting frames into ATT-sized writes lives here rather than in protocol.py
because only the transport knows the negotiated MTU. protocol.py stays pure
and hardware-free.
"""

import asyncio
import logging

logger = logging.getLogger(__name__)

# Largest single ATT write observed from the vendor application.
MAX_WRITE = 509

# The panel drops data when written to as fast as the stack allows. The
# vendor app paces roughly 32ms between writes; this default is deliberately
# conservative and is tuned against hardware in Task 5.
DEFAULT_PACING_S = 0.03

WRITE_CHARACTERISTIC = "0000fa02-0000-1000-8000-00805f9b34fb"


class NotConnected(RuntimeError):
    """A write was attempted before connect() or after disconnect()."""


class BaseTransport:
    def __init__(self, pacing_s: float = DEFAULT_PACING_S):
        self.pacing_s = pacing_s
        self.connected = False

    async def connect(self) -> None:
        self.connected = True

    async def disconnect(self) -> None:
        self.connected = False

    async def send(self, frame: bytes) -> None:
        """Write one protocol frame, split to fit the ATT write limit."""
        if not self.connected:
            raise NotConnected("connect() first")
        parts = [frame[i: i + MAX_WRITE] for i in range(0, len(frame), MAX_WRITE)]
        for index, part in enumerate(parts):
            if index and self.pacing_s:
                await asyncio.sleep(self.pacing_s)
            await self._write(part)

    async def _write(self, part: bytes) -> None:
        raise NotImplementedError


class FakeTransport(BaseTransport):
    """Records what would have gone on the wire. The whole driver is testable
    through this without a panel, a radio, or a running event loop."""

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
        logger.info("connected to %s", self.address)

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
```

**`WRITE_CHARACTERISTIC` is a placeholder and Task 5 must confirm it.** The
captures record the ATT *handle* (`0x0006`), not the UUID, and bleak addresses
characteristics by UUID. Task 5 enumerates the panel's services and replaces
this constant with the observed value; the UUID above is the one commonly used
by this class of device and is a starting hypothesis, not a finding.

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest -q && .venv/bin/ruff check .`
Expected: PASS, 32 + 6 = 38 tests.

- [ ] **Step 6: Commit**

```bash
git add src/dotdisplay/ble/transport.py tests/test_ble_transport.py pyproject.toml
git commit -m "feat: add the BLE transport with MTU splitting and pacing"
```

---

### Task 4: client.py — the composed API

**Files:**
- Create: `src/dotdisplay/ble/client.py`
- Modify: `src/dotdisplay/ble/__init__.py`
- Test: `tests/test_ble_client.py`

**Interfaces:**
- Consumes: `protocol` (Tasks 1-2), `transport` (Task 3).
- Produces: `PanelClient` with `connect()`, `disconnect()`, `set_brightness(percent)`, `power(on)`, `draw_pixel(x, y, rgb)`, `send_image(img)`; usable as an async context manager.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_ble_client.py`:

```python
from PIL import Image

from dotdisplay.ble import protocol as p
from dotdisplay.ble.client import PanelClient
from dotdisplay.ble.transport import FakeTransport


async def test_brightness_goes_out_as_the_protocol_frame():
    fake = FakeTransport()
    async with PanelClient(transport=fake) as panel:
        await panel.set_brightness(75)
    assert b"".join(fake.writes) == p.set_brightness(75)


async def test_send_image_sets_mode_then_streams_three_chunks():
    """Order matters: the captures always show the mode command first."""
    fake = FakeTransport()
    async with PanelClient(transport=fake) as panel:
        await panel.send_image(Image.new("RGB", (64, 64), (0, 0, 0)))

    stream = b"".join(fake.writes)
    assert stream.startswith(p.set_image_mode(True))
    assert len(stream) == len(p.set_image_mode(True)) + 12315


async def test_context_manager_disconnects_even_on_error():
    """An exception must not leave the radio held; the next run would find
    the panel busy and the cause would be invisible."""
    fake = FakeTransport()
    try:
        async with PanelClient(transport=fake):
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert fake.connected is False


async def test_pixels_reach_the_wire_in_row_major_order():
    fake = FakeTransport()
    img = Image.new("RGB", (64, 64), (0, 0, 0))
    img.putpixel((63, 0), (255, 255, 255))
    async with PanelClient(transport=fake) as panel:
        await panel.send_image(img)
    body = b"".join(fake.writes)[len(p.set_image_mode(True)):]
    payload = b"".join(
        body[i + 9: i + 4105] for i in range(0, len(body), 4105))
    assert payload[63 * 3: 63 * 3 + 3] == b"\xff\xff\xff"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_ble_client.py -q`
Expected: FAIL — no module `dotdisplay.ble.client`.

- [ ] **Step 3: Write the implementation**

Create `src/dotdisplay/ble/client.py`:

```python
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
```

Update `src/dotdisplay/ble/__init__.py`:

```python
"""MIT-licensed iDotMatrix BLE driver.

Derived clean-room from observed wire traffic; see PROTOCOL.md.
"""

from dotdisplay.ble import protocol
from dotdisplay.ble.client import PanelClient
from dotdisplay.ble.transport import BleakTransport, FakeTransport, NotConnected

__all__ = ["protocol", "PanelClient", "BleakTransport", "FakeTransport",
           "NotConnected"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest -q && .venv/bin/ruff check .`
Expected: PASS, 38 + 4 = 42 tests.

- [ ] **Step 5: Commit**

```bash
git add src/dotdisplay/ble tests/test_ble_client.py
git commit -m "feat: add the panel client API"
```

---

### Task 5: Hardware verification

**This is the only task that establishes correctness.** Everything before it
proves we emit the bytes we intended, not that the panel accepts them.

**Files:**
- Modify: `PROTOCOL.md` (replayed markers), `src/dotdisplay/ble/transport.py` (real characteristic UUID)
- Create: `tools/panel_smoke.py`

- [ ] **Step 1: Take the radio**

```bash
systemctl --user stop sensmonlight-idotmatrix-agent.service
systemctl --user is-active sensmonlight-idotmatrix-agent.service   # expect: inactive
```

Two owners of one radio produce failures that look like protocol bugs. Restart
it at the end of this task.

- [ ] **Step 2: Find the real write characteristic**

```bash
export DOTDISPLAY_MAC=<panel address>       # never commit this
.venv/bin/python - <<'EOF'
import asyncio, os
from bleak import BleakClient

async def main():
    async with BleakClient(os.environ["DOTDISPLAY_MAC"]) as c:
        for service in c.services:
            for ch in service.characteristics:
                print(f"{ch.handle:#06x}  {ch.uuid}  {ch.properties}")

asyncio.run(main())
EOF
```

The captures wrote to handle `0x0006`. Find the characteristic whose handle
matches, or failing that the writable one this class of device uses, and set
`WRITE_CHARACTERISTIC` in `transport.py` to its UUID. **Record the actual
value observed** — do not keep the placeholder if it turns out to be wrong.

- [ ] **Step 3: Write the smoke tool**

Create `tools/panel_smoke.py`:

```python
#!/usr/bin/env python3
"""Drive the panel through every implemented command, for hardware checks.

Not a test: it needs a real panel and a human looking at it. Reads the panel
address from DOTDISPLAY_MAC so no address is ever committed.
"""

import asyncio
import os
import sys

from PIL import Image

from dotdisplay.ble import PanelClient


async def main() -> int:
    address = os.environ.get("DOTDISPLAY_MAC")
    if not address:
        print("set DOTDISPLAY_MAC to the panel's address", file=sys.stderr)
        return 1

    async with PanelClient(address) as panel:
        print("brightness 20"); await panel.set_brightness(20)
        await asyncio.sleep(2)
        print("brightness 90"); await panel.set_brightness(90)
        await asyncio.sleep(2)

        print("power off"); await panel.power(False)
        await asyncio.sleep(2)
        print("power on"); await panel.power(True)
        await asyncio.sleep(2)

        print("red pixel at 0,0"); await panel.draw_pixel(0, 0, (255, 0, 0))
        await asyncio.sleep(2)

        print("solid blue image")
        await panel.send_image(Image.new("RGB", (64, 64), (0, 0, 255)))
        await asyncio.sleep(2)

        print("corner marker: white at top-right only")
        img = Image.new("RGB", (64, 64), (0, 0, 0))
        img.putpixel((63, 0), (255, 255, 255))
        await panel.send_image(img)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
```

- [ ] **Step 4: Run it and watch the panel**

```bash
DOTDISPLAY_MAC=<panel address> .venv/bin/python tools/panel_smoke.py
```

Check each step **on the panel**, not in the log:

| Step | Expected on the panel |
| --- | --- |
| brightness 20 / 90 | visibly dims, then brightens |
| power off / on | goes dark, then returns |
| red pixel | a single red dot in one corner |
| solid blue | the whole panel blue |
| corner marker | one white dot, **top-right** |

The corner marker is the real test of the whole stack: it confirms row-major
ordering and top-left origin end to end, from Pillow through the encoder and
the transport to the glass. If it lands top-left, the ordering is transposed.

- [ ] **Step 5: Photograph the result**

```bash
ffmpeg -hide_banner -loglevel error -f v4l2 -input_format mjpeg \
  -video_size 1920x1080 -i /dev/video2 -vf "select=gte(n\,15)" \
  -frames:v 1 -q:v 2 -y /tmp/panel-verify.jpg
```

- [ ] **Step 6: Tune pacing if anything was corrupted**

If images arrive torn or partial, raise `DEFAULT_PACING_S` until they are
clean, then lower it to find the real floor. Record the working value and the
measured full-frame duration in `PROTOCOL.md`. A frame that takes materially
longer than the ~0.9s the vendor achieved means the pacing is too conservative.

- [ ] **Step 7: Update PROTOCOL.md**

For every command that visibly changed the panel, change `replayed: **no**`
to `replayed: yes, <date>`. Leave `no` on anything not exercised. Remove the
status banner's claim that nothing has been replayed, and record the real
characteristic UUID under Transport.

**Do not mark anything replayed on the strength of the script exiting 0.**

- [ ] **Step 8: Return the radio and commit**

```bash
systemctl --user start sensmonlight-idotmatrix-agent.service
.venv/bin/python -m pytest -q && .venv/bin/ruff check .
git add PROTOCOL.md src/dotdisplay/ble/transport.py tools/panel_smoke.py
git commit -m "feat: verify the driver against the physical panel"
git push origin main && git push gitlab main
```

---

## Definition of done

- 42 tests pass locally and in CI on 3.11, 3.12, 3.13; ruff clean.
- The panel visibly responds to brightness, power, a single pixel, and a full image.
- The top-right corner marker appears **top-right**.
- `PROTOCOL.md` marks the exercised commands replayed, with the real characteristic UUID.
- No MAC address, serial, or capture file is committed.
- `sensmonlight-idotmatrix-agent.service` is running again.

## Deliberately not in P1

- Animation. The envelope is known but the payload is not; `PROTOCOL.md` records a 160-byte discrepancy. Implementing it now would be guessing.
- Text frames. We rasterise with Pillow and use the image path; the device has no font.
- Reconnection, retries, device discovery. P2 adds what the daemon actually needs.
- Removing the iDotMatrix code from `sensmonlight` — that is P2, once the daemon replaces the agent.
