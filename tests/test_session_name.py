import pytest

from dotdisplay import session_name as sn
from dotdisplay.cli import SAFE_NAME


def test_name_combines_directory_and_session():
    """A directory alone collides across concurrent sessions in the same
    repo; the session id alone is a UUID nobody recognises."""
    name = sn.derive({"cwd": "/home/x/Documents/gitlab/hwmon",
                      "session_id": "50b53b31-3ec5-4886"})
    assert name == "hwmon-50"


def test_long_directory_names_are_truncated_to_the_display_budget():
    name = sn.derive({"cwd": "/home/x/a-very-long-project-name",
                      "session_id": "abcdef"})
    assert len(name) <= sn.MAX_DISPLAY
    assert name.endswith("-ab")


def test_every_derived_name_is_a_safe_filename():
    """The name becomes a filename and is drawn on a panel."""
    for cwd in ("/home/x/has space", "/home/x/../etc", "/home/x/weißbier",
                "/home/x/UPPER_Case.v2", "/"):
        name = sn.derive({"cwd": cwd, "session_id": "ff00"})
        assert SAFE_NAME.match(name), f"{cwd!r} produced {name!r}"


def test_missing_session_id_still_produces_a_name():
    """Never raise inside a hook. A name without a suffix is better than a
    crashed hook."""
    name = sn.derive({"cwd": "/home/x/hwmon"})
    assert SAFE_NAME.match(name)
    assert "hwmon" in name


def test_missing_cwd_falls_back():
    name = sn.derive({"session_id": "ab"}, cwd_fallback="/home/x/fallback")
    assert SAFE_NAME.match(name)


def test_completely_empty_payload_still_produces_a_name():
    assert SAFE_NAME.match(sn.derive({}, cwd_fallback=""))


def test_same_payload_gives_the_same_name():
    """Rows must not move between ticks."""
    payload = {"cwd": "/home/x/hwmon", "session_id": "50b5"}
    assert sn.derive(payload) == sn.derive(payload)


def test_different_sessions_in_one_directory_differ():
    a = sn.derive({"cwd": "/home/x/hwmon", "session_id": "aa11"})
    b = sn.derive({"cwd": "/home/x/hwmon", "session_id": "bb22"})
    assert a != b


@pytest.mark.parametrize("field", ["sessionId", "session"])
def test_unexpected_field_names_do_not_crash(field):
    """The payload shape is not assumed. An unknown spelling loses the
    suffix; it must not lose the hook."""
    assert SAFE_NAME.match(sn.derive({"cwd": "/home/x/hwmon", field: "zz"}))
