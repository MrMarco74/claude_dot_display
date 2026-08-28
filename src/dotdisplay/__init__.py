"""claude-dot-display -- your Claude Code sessions on an LED matrix."""

# Kept in step with pyproject.toml and .claude-plugin/plugin.json by
# tests/test_manifests.py. It cannot be derived from either at runtime: an
# installed wheel has no pyproject.toml, and installed metadata goes stale
# under an editable install, which is exactly how this drifted to 0.1.0
# while the package shipped 0.5.x.
__version__ = "0.5.0"

__all__ = ["__version__"]
