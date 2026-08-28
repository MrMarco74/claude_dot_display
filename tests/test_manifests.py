import json
import pathlib
import tomllib

ROOT = pathlib.Path(__file__).resolve().parent.parent
PLUGIN = ROOT / ".claude-plugin" / "plugin.json"
MARKET = ROOT / ".claude-plugin" / "marketplace.json"


def test_plugin_manifest_is_valid_json_with_required_fields():
    d = json.loads(PLUGIN.read_text())
    assert d["name"] == "claude-dot-display"
    assert d["description"]
    assert d["author"]["name"]


def test_marketplace_lists_this_plugin_from_this_repo():
    d = json.loads(MARKET.read_text())
    entry = next(p for p in d["plugins"] if p["name"] == "claude-dot-display")
    # "./" means the plugin lives in this same repository, which is what makes
    # the repo self-hosting as a marketplace.
    assert entry["source"] == "./"


def test_plugin_version_matches_the_package_version():
    """Two version numbers that can disagree eventually will."""
    pkg = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    assert json.loads(PLUGIN.read_text())["version"] == pkg


def test_package_dunder_version_matches_the_package_version():
    """`dotdisplay --version` reads __version__, not pyproject. A release
    that bumps only one of them ships a binary that misreports itself, and
    the misreport is what you check first when a fix seems not to have
    landed."""
    import dotdisplay
    pkg = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    assert dotdisplay.__version__ == pkg
