"""One executor for panel operations, shared by every caller.

The CLI, the local queue and hwmon's queue all end up here, so a command
behaves identically no matter where it came from.
"""

import re

from dotdisplay import render

KINDS = ("brightness", "power", "pixel", "fill", "clear", "text",
         "send_image", "set_brightness")

_HEX = re.compile(r"^[0-9A-Fa-f]{6}$")


def parse_colour(value) -> tuple[int, int, int]:
    """Accept what a shell caller has: six hex digits."""
    text = str(value).lstrip("#")
    if not _HEX.match(text):
        raise ValueError(f"colour must be six hex digits, got {value!r}")
    return tuple(int(text[i: i + 2], 16) for i in (0, 2, 4))


async def execute(panel, command: dict) -> dict:
    kind = command.get("type")

    # hwmon uses its own spellings; keep accepting them so the server needs
    # no change.
    if kind in ("brightness", "set_brightness"):
        percent = command.get("percent", command.get("brightness_percent"))
        await panel.set_brightness(int(percent))
    elif kind == "power":
        await panel.power(bool(command["on"]))
    elif kind == "pixel":
        await panel.draw_pixel(int(command["x"]), int(command["y"]),
                               parse_colour(command["colour"]))
    elif kind == "fill":
        from PIL import Image
        colour = parse_colour(command["colour"])
        await panel.send_image(Image.new("RGB", (render.W, render.H), colour))
    elif kind == "clear":
        from PIL import Image
        await panel.send_image(Image.new("RGB", (render.W, render.H), (0, 0, 0)))
    elif kind == "text":
        colour = (parse_colour(command["colour"]) if command.get("colour")
                  else (255, 255, 255))
        await panel.send_image(render.render_text(command["text"], colour))
    elif kind == "send_image":
        import base64
        import io

        from PIL import Image
        raw = base64.b64decode(command["image_base64"])
        await panel.send_image(Image.open(io.BytesIO(raw)))
    else:
        raise ValueError(f"unsupported command type {kind!r}")
    return {"sent": True}
