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
