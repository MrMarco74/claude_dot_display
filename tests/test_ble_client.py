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
