import dataclasses

import pytest

from dotdisplay import daemon as d
from dotdisplay.config import Config


@pytest.fixture
def cfg(tmp_path):
    return dataclasses.replace(Config.from_env(), state_dir=tmp_path,
                               mac="AA:BB:CC:DD:EE:FF", poll_s=0.01,
                               hwmon_url="", setup_key="")


class FakePanel:
    def __init__(self):
        self.images = []

    async def send_image(self, img):
        self.images.append(img)


async def test_first_render_is_sent(cfg, mocker):
    mocker.patch("dotdisplay.daemon.sources.read_sessions",
                 return_value=[{"name": "a", "status": "running"}])
    mocker.patch("dotdisplay.daemon.sources.read_header", return_value=None)
    panel, board = FakePanel(), d.Board()
    assert await d.tick(cfg, board, panel) is True
    assert len(panel.images) == 1


async def test_unchanged_board_is_not_resent(cfg, mocker):
    """Re-sending an identical board holds an exclusive radio for nothing."""
    mocker.patch("dotdisplay.daemon.sources.read_sessions",
                 return_value=[{"name": "a", "status": "running"}])
    mocker.patch("dotdisplay.daemon.sources.read_header", return_value=None)
    panel, board = FakePanel(), d.Board()
    await d.tick(cfg, board, panel)
    assert await d.tick(cfg, board, panel) is False
    assert len(panel.images) == 1


async def test_changed_status_is_sent(cfg, mocker):
    mocker.patch("dotdisplay.daemon.sources.read_header", return_value=None)
    mocker.patch("dotdisplay.daemon.sources.read_sessions", side_effect=[
        [{"name": "a", "status": "running"}],
        [{"name": "a", "status": "issue"}]])
    panel, board = FakePanel(), d.Board()
    await d.tick(cfg, board, panel)
    await d.tick(cfg, board, panel)
    assert len(panel.images) == 2


async def test_no_sessions_renders_the_idle_screen(cfg, mocker):
    mocker.patch("dotdisplay.daemon.sources.read_sessions", return_value=[])
    mocker.patch("dotdisplay.daemon.sources.read_header", return_value=None)
    mocker.patch("dotdisplay.daemon.sources.ccusage_stats",
                 return_value={"today": 1, "out": 1, "cache": 1,
                               "read": 1, "all": 1})
    mocker.patch("dotdisplay.daemon.sources.trends", return_value={})
    idle = mocker.patch("dotdisplay.daemon.render.render_idle")
    await d.tick(cfg, d.Board(), FakePanel())
    idle.assert_called_once()


async def test_a_send_failure_does_not_raise(cfg, mocker):
    """The panel being out of range or asleep is normal, not an error."""
    mocker.patch("dotdisplay.daemon.sources.read_sessions", return_value=[])
    mocker.patch("dotdisplay.daemon.sources.read_header", return_value=None)
    mocker.patch("dotdisplay.daemon.sources.ccusage_stats", return_value={})
    mocker.patch("dotdisplay.daemon.sources.trends", return_value={})

    class Broken(FakePanel):
        async def send_image(self, img):
            raise OSError("panel gone")

    assert await d.tick(cfg, d.Board(), Broken()) is False


async def test_a_failed_send_is_retried_next_tick(cfg, mocker):
    """If the send failed, the board on the panel is NOT what we rendered, so
    the cache must not record it as sent."""
    mocker.patch("dotdisplay.daemon.sources.read_sessions",
                 return_value=[{"name": "a", "status": "running"}])
    mocker.patch("dotdisplay.daemon.sources.read_header", return_value=None)

    class FlakyPanel(FakePanel):
        def __init__(self):
            super().__init__()
            self.fail_next = True

        async def send_image(self, img):
            if self.fail_next:
                self.fail_next = False
                raise OSError("transient")
            await super().send_image(img)

    panel, board = FlakyPanel(), d.Board()
    await d.tick(cfg, board, panel)          # fails
    assert await d.tick(cfg, board, panel) is True
    assert len(panel.images) == 1


async def test_missing_mac_is_refused_early(cfg):
    """Better a clear error at startup than a daemon that silently does
    nothing."""
    with pytest.raises(SystemExit):
        await d.run(dataclasses.replace(cfg, mac=""))
