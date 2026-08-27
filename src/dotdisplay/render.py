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


CODE_FONT_SIZE = 26      # readable across a room; the 8px board font is not
CODE_COLOUR = (255, 255, 255)
CODE_FRAME = (60, 120, 255)


def _code_font():
    for path in _FONT_PATHS:
        try:
            return ImageFont.truetype(path, CODE_FONT_SIZE)
        except OSError:
            continue
    return ImageFont.load_default()


def render_code(code: str) -> Image.Image:
    """Draw a short confirmation code, large enough to read from across the
    room.

    Used by `dotdisplay check`: seeing this code is what proves the address
    points at the panel you are actually looking at, rather than merely at
    something that answered.
    """
    img, draw = _canvas()
    font = _code_font()
    draw.rectangle([0, 0, W - 1, H - 1], outline=CODE_FRAME)

    text = str(code)
    rows = [text[: len(text) // 2 or 1], text[len(text) // 2 or 1:]]
    y = 4
    for row in rows:
        if not row:
            continue
        width = draw.textlength(row, font=font)
        draw.text(((W - width) / 2, y), row, font=font, fill=CODE_COLOUR)
        y += CODE_FONT_SIZE + 4
    return img


TEXT_SIZES = (26, 20, 16, 13, 11, 9, 8)   # largest first; first that fits wins


def render_text(text: str, colour=(255, 255, 255)) -> Image.Image:
    """Rasterise text to a full frame.

    The device has no font -- the vendor application ships glyph bitmaps per
    character (see PROTOCOL.md). Rendering here and sending an image is both
    simpler and the fastest path we have.

    Picks the largest size at which the text still fits, so a short word is
    readable across a room and a long one is merely readable.
    """
    import textwrap

    img, draw = _canvas()
    words = str(text).split()
    for size in TEXT_SIZES:
        try:
            font = ImageFont.truetype(_FONT_PATHS[0], size)
        except OSError:
            font = ImageFont.load_default()
        char_w = max(1.0, draw.textlength("M", font=font))
        per_line = max(1, int((W - 2 * MARGIN) / char_w))
        lines = textwrap.wrap(" ".join(words), width=per_line) or [""]
        line_h = size + 2
        if len(lines) * line_h <= H and all(
                draw.textlength(line, font=font) <= W - 2 * MARGIN
                for line in lines):
            y = (H - len(lines) * line_h) // 2
            for line in lines:
                width = draw.textlength(line, font=font)
                draw.text(((W - width) / 2, y), line, font=font, fill=colour)
                y += line_h
            return img
    return img          # nothing fit; an empty frame beats a garbled one


SPLASH_SECONDS = 3.0


def splash():
    """The project mark, shown briefly when the panel connects.

    Returns None if the asset is missing rather than raising: a decorative
    frame is never worth failing a connection over.
    """
    try:
        from importlib.resources import files
        with files("dotdisplay").joinpath("assets/splash.png").open("rb") as fh:
            return Image.open(fh).convert("RGB")
    except Exception:      # noqa: BLE001 - decoration must never break a connect
        return None
