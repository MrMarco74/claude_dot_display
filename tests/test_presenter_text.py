import pytest

from dotdisplay.presenters import text as t

HEADER = {"pct": 32, "reset": "22:10"}
SESSIONS = [
    {"name": "demo-i", "status": "issue", "stages_left": 7},
    {"name": "hwmon-d7", "status": "running", "stages_left": 12},
]


def test_board_lists_every_session():
    out = t.board(SESSIONS, HEADER, {})
    assert "demo-i" in out
    assert "hwmon-d7" in out


def test_board_spells_states_out():
    """The panel has only colour to work with. A terminal has words, and a
    reader should not have to remember what amber means."""
    out = t.board(SESSIONS, HEADER, {})
    assert "issue" in out
    assert "running" in out


def test_board_does_not_truncate_names_to_the_panel_budget():
    """Nine characters is a panel limit. Importing it here would be
    cargo-culting the hardware."""
    long_name = "a-very-long-session"
    out = t.board([{"name": long_name, "status": "running"}], HEADER, {})
    assert long_name in out


def test_board_is_sorted_alphabetically():
    out = t.board(list(reversed(SESSIONS)), HEADER, {})
    assert out.index("demo-i") < out.index("hwmon-d7")


def test_board_shows_the_header():
    assert "22:10" in t.board(SESSIONS, HEADER, {})


def test_board_without_sessions_shows_usage():
    out = t.board([], HEADER, {"today": 684_000_000, "all": 12_000_000_000})
    assert "684M" in out
    assert "12G" in out


def test_board_without_a_header_still_renders():
    assert t.board(SESSIONS, None, {})


def test_board_lines_fit_a_narrow_terminal():
    """80 columns is the floor worth supporting."""
    for line in t.board(SESSIONS, HEADER, {}).splitlines():
        assert len(line) <= 80, line


def test_statusline_is_one_short_line():
    line = t.statusline(SESSIONS)
    assert "\n" not in line
    assert len(line) <= 40


def test_statusline_counts_states_that_do_not_need_you():
    """Running sessions are a number: knowing which one is running is not
    actionable, so the name would only cost width."""
    line = t.statusline([
        {"name": "a", "status": "issue"},
        {"name": "b", "status": "running"},
        {"name": "c", "status": "running"},
    ])
    assert "*2" in line


def test_statusline_is_empty_without_sessions():
    """An empty prompt segment beats a permanent decoration."""
    assert t.statusline([]) == ""


@pytest.mark.parametrize("status", ["running", "question", "issue", "done"])
def test_every_state_has_a_word_and_a_colour(status):
    assert status in t.STATE_WORDS
    assert status in t.STATE_COLOURS


def test_a_long_name_never_runs_into_the_state_word():
    """A name touching the next column makes the row unreadable."""
    out = t.board([{"name": "a-very-long-session", "status": "question"}],
                  HEADER, {})
    row = [ln for ln in out.splitlines() if "a-very-long" in ln][0]
    assert "session waiting" in row or "…" in row
    assert "sessionwaiting" not in row


def test_usage_values_are_never_cut_in_half():
    """A truncated number is worse than an absent one."""
    stats = {"today": 764_000_000, "out": 2_300_000, "cache": 8_200_000,
             "read": 750_000_000, "all": 12_000_000_000}
    line = t.board([], HEADER, stats).splitlines()[-1]
    for piece in line.replace("  ", "").split(" · "):
        assert piece.strip(), line
        assert not piece.rstrip().endswith(("k", "M", "G")) or len(piece.split()) == 2
    assert not line.rstrip().endswith(("7", "75"))  # no dangling digits


def test_statusline_names_the_sessions_that_need_you():
    """A count tells you something is blocked; a name tells you which."""
    line = t.statusline([
        {"name": "hwmon-d7", "status": "issue"},
        {"name": "storygen", "status": "question"},
        {"name": "kolonial", "status": "running"},
        {"name": "marcohp", "status": "running"},
    ])
    assert "hwmon-d7" in line
    assert "storygen" in line
    assert "*2" in line              # running sessions stay a count
    assert "kolonial" not in line


def test_statusline_falls_back_to_counts_when_names_get_too_long():
    """A prompt segment that wraps is worse than one that is vague."""
    many = [{"name": f"long-session-name-{i}", "status": "issue"}
            for i in range(6)]
    line = t.statusline(many)
    assert len(line) <= t.STATUSLINE_MAX
    assert line == "!6"


def test_counts_only_is_available_explicitly():
    line = t.statusline([{"name": "hwmon-d7", "status": "issue"}], names=False)
    assert line == "!1"


def test_named_sessions_are_alphabetical():
    line = t.statusline([
        {"name": "zulu", "status": "issue"},
        {"name": "alpha", "status": "issue"},
    ])
    assert line.index("alpha") < line.index("zulu")
