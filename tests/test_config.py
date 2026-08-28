import os

from dotdisplay.config import Config


def test_defaults_need_no_environment(monkeypatch):
    for key in list(os.environ):
        if key.startswith("DOTDISPLAY_"):
            monkeypatch.delenv(key, raising=False)
    cfg = Config.from_env()
    assert cfg.poll_s > 0
    assert cfg.mac == ""
    assert cfg.hwmon_url == ""
    assert cfg.setup_key == ""


def test_environment_overrides(monkeypatch):
    monkeypatch.setenv("DOTDISPLAY_MAC", "AA:BB:CC:DD:EE:FF")
    monkeypatch.setenv("DOTDISPLAY_POLL_S", "2.5")
    monkeypatch.setenv("DOTDISPLAY_HWMON_URL", "https://example.invalid/")
    monkeypatch.setenv("DOTDISPLAY_HWMON_SETUP_KEY", "s3cret")
    cfg = Config.from_env()
    assert cfg.mac == "AA:BB:CC:DD:EE:FF"
    assert cfg.poll_s == 2.5
    assert cfg.hwmon_url == "https://example.invalid"    # trailing slash stripped
    assert cfg.setup_key == "s3cret"


def test_prune_is_slower_than_staleness_by_default(monkeypatch):
    """Two thresholds, two jobs: stale_after_s takes a session off the board,
    prune_after_s deletes it. Pruning first would drop counts that a returning
    session should still have."""
    for key in list(os.environ):
        if key.startswith("DOTDISPLAY_"):
            monkeypatch.delenv(key, raising=False)
    cfg = Config.from_env()
    assert cfg.prune_after_s > cfg.stale_after_s


def test_prune_after_s_is_configurable(monkeypatch):
    monkeypatch.setenv("DOTDISPLAY_PRUNE_AFTER_S", "60")
    assert Config.from_env().prune_after_s == 60.0
