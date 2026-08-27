import pathlib
import tomllib

ROOT = pathlib.Path(__file__).resolve().parent.parent

# Copyleft markers. A runtime dependency matching any of these would make the
# MIT claim false, which is the exact problem this project was created to fix.
COPYLEFT = ("gpl", "agpl", "lgpl", "idotmatrix-api-client", "python3-idotmatrix")


def _pyproject() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text())


def test_license_file_exists_and_is_mit():
    text = (ROOT / "LICENSE").read_text()
    assert "MIT License" in text
    assert "WITHOUT WARRANTY OF ANY KIND" in text


def test_pyproject_declares_mit():
    assert _pyproject()["project"]["license"] == "MIT"


def test_license_file_is_shipped_in_the_distribution():
    """A wheel without its licence text is not properly licensed."""
    assert _pyproject()["project"]["license-files"] == ["LICENSE"]


def test_no_copyleft_runtime_dependency():
    """The reason this project exists. Every Python iDotMatrix library is
    GPL-3.0; depending on one would make 'MIT' untrue."""
    for dep in _pyproject()["project"]["dependencies"]:
        lowered = dep.lower()
        for marker in COPYLEFT:
            assert marker not in lowered, f"copyleft dependency reintroduced: {dep}"


def test_runtime_dependencies_are_the_agreed_set():
    """Pinned by the design. A new runtime dependency is a design decision,
    not an implementation detail -- this test forces that conversation."""
    names = {d.split(">")[0].split("=")[0].strip().lower()
             for d in _pyproject()["project"]["dependencies"]}
    assert names == {"bleak", "pillow", "requests"}
