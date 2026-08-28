import pathlib
import subprocess
import sys
import tomllib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "changelog_section.py"

SAMPLE = """# Changelog

## [0.2.0] - 2026-08-28

### Added

- A thing.

## [0.1.0] - 2026-08-27

Initial release.

[0.2.0]: https://example.invalid/compare/v0.1.0...v0.2.0
"""


def _run(version, text, tmp_path, expect_rc=0):
    path = tmp_path / "CHANGELOG.md"
    path.write_text(text)
    proc = subprocess.run([sys.executable, str(SCRIPT), version, str(path)],
                          capture_output=True, text=True, timeout=10)
    assert proc.returncode == expect_rc, proc.stderr
    return proc


def test_a_section_is_extracted_without_its_heading(tmp_path):
    out = _run("0.2.0", SAMPLE, tmp_path).stdout
    assert "### Added" in out
    assert "- A thing." in out
    assert "0.1.0" not in out          # stops at the next release


def test_the_last_section_stops_before_the_link_definitions(tmp_path):
    """The oldest entry has no release heading after it, so the naive 'read
    to the next ##' rule runs on into the link block and puts URLs in the
    release notes."""
    out = _run("0.1.0", SAMPLE, tmp_path).stdout
    assert "Initial release." in out
    assert "example.invalid" not in out


def test_a_missing_version_fails_loudly(tmp_path):
    """Publishing a release with empty notes is worse than not publishing:
    the tag is already public by the time anyone notices."""
    proc = _run("9.9.9", SAMPLE, tmp_path, expect_rc=1)
    assert "9.9.9" in proc.stderr
    assert proc.stdout == ""


def test_the_changelog_documents_the_current_version():
    """A release cannot be described after it ships, so the entry has to
    exist before the tag does."""
    version = tomllib.loads(
        (ROOT / "pyproject.toml").read_text())["project"]["version"]
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), version, str(ROOT / "CHANGELOG.md")],
        capture_output=True, text=True, timeout=10)
    assert proc.returncode == 0, f"CHANGELOG.md has no section for {version}"
    assert proc.stdout.strip()


@pytest.mark.parametrize("version", ["0.1.0", "0.2.0", "0.3.0"])
def test_every_released_version_has_notes(version):
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), version, str(ROOT / "CHANGELOG.md")],
        capture_output=True, text=True, timeout=10)
    assert proc.returncode == 0
    assert proc.stdout.strip()
