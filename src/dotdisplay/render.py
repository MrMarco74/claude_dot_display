"""Draws the two 64x64 screens.

Pure: no I/O, no network. Every constant here was measured on the physical
panel -- see the architecture design. Do not adjust them without
re-photographing the result.
"""


from PIL import Image, ImageDraw, ImageFont

W = H = 64
MARGIN = 2          # x=0 abuts the bezel and reads as clipped; all 64 columns ARE lit
RIGHT = 62
FONT_SIZE = 8       # verified legible; a larger font was tried and rejected
ROW_H = 9
NAME_CHARS = 9      # what fits beside a right-aligned two-digit count

STATUS_COLOURS = {
    "issue":    (255, 30, 30),
    "question": (255, 215, 0),
    "done":     (0, 230, 80),
    "running":  (60, 120, 255),   # never grey: grey renders as washed-out lavender
}
BAND_COLOURS = {"green": (0, 230, 80), "amber": (255, 215, 0), "red": (255, 30, 30)}
UP_COLOUR, DOWN_COLOUR = (255, 170, 0), (0, 220, 90)
LABEL_COLOUR, VALUE_COLOUR, DIVIDER = (150, 150, 150), (255, 255, 255), (45, 45, 45)

_FONT_PATHS = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
)


def _font():
    for path in _FONT_PATHS:
        try:
            return ImageFont.truetype(path, FONT_SIZE)
        except OSError:
            continue
    return ImageFont.load_default()


def _canvas():
    img = Image.new("RGB", (W, H), (0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.fontmode = "1"   # MANDATORY: antialiased text is unreadable on an LED matrix
    return img, draw


def header_colour(pct: float):
    """Warn before the cutoff, not after."""
    if pct < 60:
        return BAND_COLOURS["green"]
    if pct < 85:
        return BAND_COLOURS["amber"]
    return BAND_COLOURS["red"]


def human_tokens(n: int) -> str:
    for unit, div in (("G", 1e9), ("M", 1e6), ("k", 1e3)):
        if n >= div:
            value = n / div
            return f"{value:.0f}{unit}" if value >= 10 else f"{value:.1f}{unit}"
    return str(n)


def _arrow(draw, x, y, up: bool):
    """5x5 filled triangle. Deliberately NOT a font glyph -- dense glyphs do
    not resolve at this size, and this keeps the arrow independent of font
    metrics."""
    points = ([(x + 2, y), (x, y + 4), (x + 4, y + 4)] if up
              else [(x + 2, y + 4), (x, y), (x + 4, y)])
    draw.polygon(points, fill=UP_COLOUR if up else DOWN_COLOUR)


def _right(draw, text, font, y, colour):
    draw.text((RIGHT - draw.textlength(text, font=font), y), text,
              font=font, fill=colour)


def _draw_header(draw, font, header: dict | None) -> int:
    """Shared by BOTH screens: the five-hour window is persistent context and
    must not vanish when the board switches views. Returns the content top."""
    if not header:
        return 0
    colour = header_colour(header["pct"])
    # Position and colour carry the meaning; the percent glyph does not
    # resolve at this size and is deliberately never drawn.
    draw.text((MARGIN, 0), f"{header['pct']:.0f}", font=font, fill=colour)
    _right(draw, header["reset"], font, 0, colour)
    draw.line([(MARGIN, 9), (RIGHT, 9)], fill=DIVIDER)
    return 12


def render_sessions(sessions: list[dict], header: dict | None) -> Image.Image:
    img, draw = _canvas()
    font = _font()
    y = _draw_header(draw, font, header)

    rows = sorted(sessions, key=lambda s: s["name"])   # pure alphabetical, by decision
    capacity = (H - y) // ROW_H
    overflow = len(rows) - capacity
    if overflow > 0:
        rows, overflow = rows[: capacity - 1], overflow + 1
    else:
        overflow = 0

    for session in rows:
        colour = STATUS_COLOURS.get(session["status"], VALUE_COLOUR)
        draw.text((MARGIN, y), session["name"][:NAME_CHARS], font=font, fill=colour)
        left = session.get("stages_left")
        if left is not None:
            _right(draw, str(left), font, y, VALUE_COLOUR)
        y += ROW_H
    if overflow:
        draw.text((MARGIN, y), f"+{overflow} more", font=font, fill=LABEL_COLOUR)
    return img


def render_idle(stats: dict[str, int], trends: dict[str, bool],
                header: dict | None) -> Image.Image:
    img, draw = _canvas()
    font = _font()
    y = _draw_header(draw, font, header)

    order = [("today", VALUE_COLOUR), ("out", STATUS_COLOURS["done"]),
             ("cache", STATUS_COLOURS["question"]), ("read", (160, 160, 160)),
             ("all", (120, 170, 255))]
    for key, colour in order:
        if key not in stats:
            continue
        draw.text((MARGIN, y), key, font=font, fill=LABEL_COLOUR)
        text = human_tokens(stats[key])
        _right(draw, text, font, y, colour)
        if key in trends:      # absent trend -> no arrow, never a guessed one
            width = draw.textlength(text, font=font)
            _arrow(draw, int(RIGHT - width - 7), y + 2, trends[key])
        y += ROW_H
    return img
