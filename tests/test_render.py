import pytest

from dotdisplay import render as r


def _sessions(n):
    return [{"name": f"sess{i}", "status": "running", "stages_left": i}
            for i in range(n)]


HEADER = {"pct": 50, "reset": "17:10"}


def test_render_is_a_64x64_image():
    img = r.render_sessions(_sessions(2), HEADER)
    assert img.size == (64, 64)
    assert img.mode == "RGB"


def test_same_input_produces_identical_pixels():
    """The daemon only sends when the rendering changes, so rendering MUST be
    deterministic -- any nondeterminism would spam an exclusive radio."""
    assert (r.render_sessions(_sessions(3), HEADER).tobytes()
            == r.render_sessions(_sessions(3), HEADER).tobytes())


def test_changed_status_changes_the_rendering():
    before = r.render_sessions(_sessions(1), HEADER).tobytes()
    changed = _sessions(1)
    changed[0]["status"] = "issue"
    assert r.render_sessions(changed, HEADER).tobytes() != before


def test_input_order_does_not_matter():
    a = [{"name": "aaa", "status": "running", "stages_left": 1},
         {"name": "bbb", "status": "running", "stages_left": 2}]
    assert (r.render_sessions(a, HEADER).tobytes()
            == r.render_sessions(list(reversed(a)), HEADER).tobytes())


def test_overflow_is_indicated_not_silently_dropped():
    """Dropping sessions without saying so would make the board lie."""
    assert (r.render_sessions(_sessions(9), HEADER).tobytes()
            != r.render_sessions(_sessions(4), HEADER).tobytes())


@pytest.mark.parametrize("pct,band", [(10, "green"), (70, "amber"), (95, "red")])
def test_header_colour_bands(pct, band):
    assert r.header_colour(pct) == r.BAND_COLOURS[band]


def test_no_percent_glyph_is_ever_drawn():
    """Verified unreadable at 8px on the physical panel. Checks the whole
    module: a % reaching the panel from any helper is the same bug."""
    import inspect
    assert "%" not in inspect.getsource(r)


def test_status_colours_are_never_grey():
    """Grey renders as washed-out lavender on real LEDs."""
    for rgb in r.STATUS_COLOURS.values():
        assert len(set(rgb)) > 1, f"{rgb} is grey"


def test_idle_screen_differs_from_the_session_screen():
    idle = r.render_idle({"today": 1, "out": 2, "cache": 3, "read": 4, "all": 5},
                         trends={}, header=HEADER)
    assert idle.tobytes() != r.render_sessions(_sessions(1), HEADER).tobytes()


def test_missing_trend_renders_no_arrow():
    """An arrow with no comparison would imply information that does not
    exist."""
    stats = {"today": 10, "out": 2, "cache": 3, "read": 4, "all": 5}
    assert (r.render_idle(stats, {"today": True}, HEADER).tobytes()
            != r.render_idle(stats, {}, HEADER).tobytes())


def test_header_is_optional():
    assert r.render_sessions(_sessions(1), None).size == (64, 64)


@pytest.mark.parametrize("n,expect", [(999, "999"), (1500, "1.5k"),
                                      (472_049_430, "472M"),
                                      (12_245_280_929, "12G"),
                                      (1_352_334, "1.4M")])
def test_human_tokens(n, expect):
    assert r.human_tokens(n) == expect


def test_long_names_are_truncated_to_the_display_budget():
    img = r.render_sessions(
        [{"name": "a" * 40, "status": "running", "stages_left": 1}], HEADER)
    assert img.size == (64, 64)      # must not raise or overflow


def test_code_screen_is_64x64():
    assert r.render_code("4207").size == (64, 64)


def test_different_codes_render_differently():
    assert r.render_code("4207").tobytes() != r.render_code("1138").tobytes()


def test_code_rendering_is_deterministic():
    assert r.render_code("4207").tobytes() == r.render_code("4207").tobytes()


def test_the_code_uses_a_large_share_of_the_panel():
    """The point is reading it from across the room. A code drawn in the 8px
    board font would defeat the purpose."""
    img = r.render_code("8888")
    lit = sum(1 for p in img.getdata() if sum(p) > 60)
    assert lit > 300, f"only {lit} pixels lit; code is too small to read"


@pytest.mark.parametrize("code", ["1", "12", "123456"])
def test_odd_length_codes_do_not_crash(code):
    assert r.render_code(code).size == (64, 64)
