import pytest

from dotdisplay import commands


class FakePanel:
    def __init__(self):
        self.calls = []

    async def set_brightness(self, pct):
        self.calls.append(("brightness", pct))

    async def power(self, on):
        self.calls.append(("power", on))

    async def draw_pixel(self, x, y, rgb):
        self.calls.append(("pixel", x, y, rgb))

    async def send_image(self, img):
        self.calls.append(("image", img.size))


async def test_brightness():
    panel = FakePanel()
    await commands.execute(panel, {"type": "brightness", "percent": 40})
    assert panel.calls == [("brightness", 40)]


async def test_power():
    panel = FakePanel()
    await commands.execute(panel, {"type": "power", "on": False})
    assert panel.calls == [("power", False)]


async def test_pixel_accepts_a_hex_colour():
    """Shell callers have hex, not tuples."""
    panel = FakePanel()
    await commands.execute(panel, {"type": "pixel", "x": 3, "y": 4,
                                   "colour": "ff8000"})
    assert panel.calls == [("pixel", 3, 4, (255, 128, 0))]


async def test_fill_sends_a_full_frame():
    panel = FakePanel()
    await commands.execute(panel, {"type": "fill", "colour": "00ff00"})
    assert panel.calls == [("image", (64, 64))]


async def test_clear_is_a_black_fill():
    panel = FakePanel()
    await commands.execute(panel, {"type": "clear"})
    assert panel.calls == [("image", (64, 64))]


async def test_text_is_rasterised_here():
    """The device has no font -- see PROTOCOL.md. Text is an image."""
    panel = FakePanel()
    await commands.execute(panel, {"type": "text", "text": "HELLO"})
    assert panel.calls == [("image", (64, 64))]


async def test_hwmon_spellings_still_work():
    """hwmon-server is deliberately unchanged, so its wording must keep
    working."""
    panel = FakePanel()
    await commands.execute(panel, {"type": "set_brightness",
                                   "brightness_percent": 55})
    assert panel.calls == [("brightness", 55)]


async def test_an_unknown_type_is_an_error_not_a_silent_no_op():
    panel = FakePanel()
    with pytest.raises(ValueError):
        await commands.execute(panel, {"type": "teleport"})
    assert panel.calls == []


@pytest.mark.parametrize("bad", ["nothex", "#12345", "", "ff00"])
async def test_a_bad_colour_is_rejected(bad):
    with pytest.raises(ValueError):
        await commands.execute(FakePanel(), {"type": "fill", "colour": bad})
