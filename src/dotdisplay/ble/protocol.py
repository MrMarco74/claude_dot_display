"""Frame builders for the iDotMatrix protocol.

Pure by design: no I/O, no async, no bleak. That is what lets the bytes
captured from the vendor application serve as a test oracle -- see
tests/test_ble_protocol.py and PROTOCOL.md.

Every frame begins with its own total length as a little-endian u16.
"""

import struct

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

    Unlike the vendor application, this does not resample: a caller asking
    for an exact pixel gets that exact pixel. The captures show the app
    smearing a single-pixel source across ~33 pixels, which would destroy
    8px text on a status board.
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
