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


WORKING = [
    {"name": "hwmon-d7", "status": "running", "stages_left": 3,
     "stages_total": 7, "activity": "Refreshing the panel",
     "tasks": ["Refresh the panel", "Update the docs", "Release"]},
    {"name": "storygen", "status": "question", "stages_left": 1,
     "stages_total": 4, "activity": "Waiting for the key"},
]


def test_board_says_what_each_session_is_doing():
    """A row of names and the word 'running' answers nothing a human asks
    from across the room. What it is doing, and how much is left, does."""
    out = t.board(WORKING, HEADER, {})
    row = [ln for ln in out.splitlines() if "hwmon-d7" in ln][0]
    assert "3/7" in row
    assert "Refreshing the panel" in row


def test_board_omits_the_activity_column_when_nothing_reports_one():
    """Sessions predating the todo hook must not pay for an empty column."""
    out = t.board(SESSIONS, HEADER, {})
    assert max(len(ln) for ln in out.splitlines()) <= t.WIDTH


def test_board_still_shows_a_bare_count_without_a_total():
    """`dotdisplay status --left 3` reports a count and no total."""
    out = t.board([{"name": "a", "status": "running", "stages_left": 3}],
                  HEADER, {})
    assert "3" in [p for p in out.splitlines()[-1].split()][-1]


def test_board_lists_the_open_tasks_on_request():
    out = t.board(WORKING, HEADER, {}, tasks=True)
    assert "Update the docs" in out
    assert "Release" in out
    # The task list belongs to its session, so it follows that row.
    lines = out.splitlines()
    assert lines.index("  · Update the docs") > lines.index(
        [ln for ln in lines if "hwmon-d7" in ln][0])


def test_board_without_tasks_requested_stays_one_line_per_session():
    out = t.board(WORKING, HEADER, {})
    assert "Update the docs" not in out


def test_long_activity_never_widens_the_board_past_a_terminal():
    out = t.board([{"name": "a", "status": "running",
                    "activity": "Doing " + "something " * 20}], HEADER, {})
    assert max(len(ln) for ln in out.splitlines()) <= 80


def test_statusline_carries_the_progress_of_a_working_session():
    """'*9' says nine sessions exist. It does not say that one of them is
    three stages from done, which is the thing worth glancing at."""
    line = t.statusline(WORKING)
    assert "hwmon-d7:3/7" in line
    assert "?storygen:1/4" in line


def test_statusline_still_counts_sessions_with_nothing_to_report():
    """A running session with no plan is not worth a name."""
    line = t.statusline([
        {"name": "a", "status": "running", "stages_left": 2,
         "stages_total": 3},
        {"name": "b", "status": "running"},
        {"name": "c", "status": "running"},
    ])
    assert "a:2/3" in line
    assert "*2" in line              # b and c stay a count
    assert "b" not in line.replace("a:2/3", "")


def test_statusline_with_progress_still_falls_back_to_counts(sample=None):
    many = [{"name": f"long-session-name-{i}", "status": "running",
             "stages_left": 3, "stages_total": 9} for i in range(6)]
    line = t.statusline(many)
    assert len(line) <= t.STATUSLINE_MAX
    assert line == "*6"
