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
