#!/usr/bin/env python3
"""Regenerate the capture test images.

These are inputs to the protocol captures, so they are tracked rather than
ignored: PROTOCOL.md cites them by filename as the source of each observation.

The one-pixel trio is the important part. Three captures that each differ from
the black baseline by a single pixel, at three known corners, pin down pixel
ordering (row- vs column-major), the origin corner, and the bit packing --
by comparison rather than inference.
"""

import pathlib

from PIL import Image

OUT = pathlib.Path(__file__).parent / "testimages"
SIZE = 64

SINGLE_PIXELS = {
    "02-one-pixel-topleft": (0, 0),
    "03-one-pixel-topright": (SIZE - 1, 0),
    "04-one-pixel-bottomleft": (0, SIZE - 1),
}


def main() -> int:
    OUT.mkdir(exist_ok=True)
    Image.new("RGB", (SIZE, SIZE), (0, 0, 0)).save(OUT / "01-black.png")
    for name, xy in SINGLE_PIXELS.items():
        img = Image.new("RGB", (SIZE, SIZE), (0, 0, 0))
        img.putpixel(xy, (255, 255, 255))
        img.save(OUT / f"{name}.png")
    Image.new("RGB", (SIZE, SIZE), (255, 0, 0)).save(OUT / "05-red.png")
    print(f"wrote {len(SINGLE_PIXELS) + 2} images to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
