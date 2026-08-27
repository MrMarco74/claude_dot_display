import pytest

import dotdisplay
from dotdisplay import cli


def test_version_is_reported(capsys):
    """--version must print the package version and exit 0, so an installed
    copy can always identify itself in a bug report."""
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0
    assert dotdisplay.__version__ in capsys.readouterr().out


def test_no_arguments_prints_help_and_fails(capsys):
    """A bare invocation must not look like success -- there is no default
    action, and a silent exit 0 would read as 'it worked'."""
    assert cli.main([]) == 1
    assert "usage:" in capsys.readouterr().err.lower()


def test_version_string_is_a_release_number():
    parts = dotdisplay.__version__.split(".")
    assert len(parts) == 3
    assert all(p.isdigit() for p in parts)
