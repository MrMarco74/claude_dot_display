import asyncio
import contextlib
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


async def test_commands_are_executed_and_reported(cfg, mocker):
    cfg = dataclasses.replace(cfg, hwmon_url="https://example.invalid",
                              setup_key="k")
    mocker.patch("dotdisplay.daemon.sources.claim_command",
                 side_effect=[("abc", {"type": "set_brightness",
                                       "brightness_percent": 40}), None])
    report = mocker.patch("dotdisplay.daemon.sources.report_result")

    class BrightPanel(FakePanel):
        def __init__(self):
            super().__init__()
            self.brightness = None

        async def set_brightness(self, pct):
            self.brightness = pct

    panel = BrightPanel()
    assert await d.serve_commands(cfg, panel) == 1
    assert panel.brightness == 40
    assert report.call_args.args[1] == "abc"
    assert report.call_args.args[2]["status"] == "done"


async def test_a_failing_command_is_reported_as_an_error(cfg, mocker):
    """An unreported failure leaves the command stuck in the server's
    inflight directory until a sweep expires it."""
    cfg = dataclasses.replace(cfg, hwmon_url="https://example.invalid",
                              setup_key="k")
    mocker.patch("dotdisplay.daemon.sources.claim_command",
                 side_effect=[("abc", {"type": "nonsense"}), None])
    report = mocker.patch("dotdisplay.daemon.sources.report_result")
    await d.serve_commands(cfg, FakePanel())
    assert report.call_args.args[2]["status"] == "error"


async def test_no_hwmon_url_means_no_polling(cfg, mocker):
    claim = mocker.patch("dotdisplay.daemon.sources.claim_command")
    assert await d.serve_commands(cfg, FakePanel()) == 0
    claim.assert_not_called()


async def test_a_command_does_not_invalidate_the_cached_board(cfg, mocker):
    """A command paints over the board, but the cache must NOT be cleared.

    Clearing it makes the next tick re-render the unchanged board and wipe
    the command's image within seconds -- observed on hardware. Keeping it
    lets an explicit human request stay on the panel until session state
    actually moves."""
    cfg = dataclasses.replace(cfg, hwmon_url="https://example.invalid",
                              setup_key="k")
    mocker.patch("dotdisplay.daemon.sources.claim_command",
                 side_effect=[("abc", {"type": "power", "on": True}), None])
    mocker.patch("dotdisplay.daemon.sources.report_result")

    class PowerPanel(FakePanel):
        async def power(self, on):
            pass

    board = d.Board(last_sent=b"something")
    await d.serve_commands(cfg, PowerPanel(), board=board)
    assert board.last_sent == b"something"


async def test_a_claim_failure_ends_the_drain_quietly(cfg, mocker):
    """hwmon being unreachable is normal; it must not stop the board."""
    import requests
    cfg = dataclasses.replace(cfg, hwmon_url="https://example.invalid",
                              setup_key="k")
    mocker.patch("dotdisplay.daemon.sources.claim_command",
                 side_effect=requests.exceptions.ConnectionError("down"))
    assert await d.serve_commands(cfg, FakePanel()) == 0


async def test_a_stop_signal_ends_the_loop_cleanly(cfg, mocker):
    """systemd stops the service with SIGTERM. Without handling it the
    process dies mid-connection and BlueZ keeps holding the link -- the panel
    then reports Connected while nothing owns it, and the next start cannot
    find the device. Seen on hardware."""
    import signal

    mocker.patch("dotdisplay.daemon.sources.read_sessions", return_value=[])
    mocker.patch("dotdisplay.daemon.sources.read_header", return_value=None)
    mocker.patch("dotdisplay.daemon.sources.ccusage_stats", return_value={})
    mocker.patch("dotdisplay.daemon.sources.trends", return_value={})

    class Ctx:
        async def __aenter__(self):
            return FakePanel()

        async def __aexit__(self, *exc):
            return False

    mocker.patch("dotdisplay.daemon.PanelClient", return_value=Ctx())

    task = asyncio.create_task(d.run(dataclasses.replace(cfg, poll_s=0.01)))
    await asyncio.sleep(0.05)
    signal.raise_signal(signal.SIGTERM)
    assert await asyncio.wait_for(task, timeout=2) == 0


async def test_the_daemon_beats_while_it_holds_the_radio(cfg, mocker):
    """The heartbeat is how a shell caller knows to queue instead of
    connecting, so it must be written whenever the radio is held."""
    from dotdisplay import queue as q
    mocker.patch("dotdisplay.daemon.sources.read_sessions", return_value=[])
    mocker.patch("dotdisplay.daemon.sources.read_header", return_value=None)
    mocker.patch("dotdisplay.daemon.sources.ccusage_stats", return_value={})
    mocker.patch("dotdisplay.daemon.sources.trends", return_value={})
    await d.tick(cfg, d.Board(), FakePanel())
    assert q.daemon_is_alive(cfg) is True


async def test_local_queue_commands_are_executed_and_answered(cfg):
    from dotdisplay import queue as q
    request_id = q.submit(cfg, {"type": "power", "on": True})

    class PowerPanel(FakePanel):
        def __init__(self):
            super().__init__()
            self.on = None

        async def power(self, on):
            self.on = on

    panel = PowerPanel()
    assert await d.serve_local_queue(cfg, panel) == 1
    assert panel.on is True
    assert q.await_result(cfg, request_id, timeout_s=1)["status"] == "done"


async def test_a_failing_local_command_is_answered_with_an_error(cfg):
    """Silence would leave the caller waiting for its full timeout."""
    from dotdisplay import queue as q
    request_id = q.submit(cfg, {"type": "nonsense"})
    await d.serve_local_queue(cfg, FakePanel())
    assert q.await_result(cfg, request_id, timeout_s=1)["status"] == "error"


async def test_the_splash_does_not_suppress_the_first_board(cfg, mocker):
    """After the splash, last_sent must be cleared or the first real board
    would look 'unchanged' and never be sent."""
    board = d.Board(last_sent=b"stale")
    panel = FakePanel()
    stop = asyncio.Event()
    stop.set()                       # skip the 3s wait
    await d._show_splash(panel, board, stop)
    assert board.last_sent is None
    assert len(panel.images) == 1


async def test_a_failing_splash_is_swallowed(cfg, mocker):
    mocker.patch("dotdisplay.daemon.render.splash", side_effect=OSError("nope"))
    board = d.Board()
    stop = asyncio.Event()
    stop.set()
    try:
        await d._show_splash(FakePanel(), board, stop)
    except OSError:
        raise AssertionError("splash failure escaped") from None


async def test_repeated_send_failures_force_a_reconnect(cfg, mocker):
    """A dead GATT link never heals on its own.

    Observed on hardware: BlueZ dropped the resolved services under a live
    connection and every write raised "Service Discovery has not been
    performed yet" for three hours. The panel kept a frame that was hours
    old while the loop logged a warning every five seconds. Tolerating a
    write failure forever is the same as not reconnecting at all, so after
    MAX_SEND_FAILURES consecutive failures tick must give the connection
    back to run(), which is the only thing that can rebuild it.
    """
    mocker.patch("dotdisplay.daemon.sources.read_sessions",
                 return_value=[{"name": "a", "status": "running"}])
    mocker.patch("dotdisplay.daemon.sources.read_header", return_value=None)

    class Dead(FakePanel):
        async def send_image(self, img):
            raise OSError("Service Discovery has not been performed yet")

    panel, board = Dead(), d.Board()
    for _ in range(d.MAX_SEND_FAILURES - 1):
        assert await d.tick(cfg, board, panel) is False
    with pytest.raises(d.PanelUnreachable):
        await d.tick(cfg, board, panel)


async def test_a_recovered_send_clears_the_failure_count(cfg, mocker):
    """Only *consecutive* failures mean the link is gone. An occasional
    failed write between good ones must never accumulate into a reconnect."""
    mocker.patch("dotdisplay.daemon.sources.read_header", return_value=None)
    sessions = mocker.patch("dotdisplay.daemon.sources.read_sessions")

    class Flaky(FakePanel):
        def __init__(self):
            super().__init__()
            self.fail = False

        async def send_image(self, img):
            if self.fail:
                raise OSError("transient")
            await super().send_image(img)

    panel, board = Flaky(), d.Board()
    for index in range(d.MAX_SEND_FAILURES * 3):
        # A distinct board each tick, or the cache would skip the send.
        sessions.return_value = [{"name": f"s{index}", "status": "running"}]
        panel.fail = index % 2 == 0
        await d.tick(cfg, board, panel)
    assert len(panel.images) == d.MAX_SEND_FAILURES * 3 // 2


async def test_a_dead_panel_is_reconnected_by_the_loop(cfg, mocker):
    """The whole point of PanelUnreachable: run() must build a *new*
    connection, not keep writing to the one that stopped answering."""
    mocker.patch("dotdisplay.daemon.sources.read_sessions",
                 return_value=[{"name": "a", "status": "running"}])
    mocker.patch("dotdisplay.daemon.sources.read_header", return_value=None)
    mocker.patch("dotdisplay.daemon.RECONNECT_DELAY_S", 0.01)

    connections = []

    class Dead(FakePanel):
        async def send_image(self, img):
            raise OSError("Service Discovery has not been performed yet")

    class Ctx:
        async def __aenter__(self):
            connections.append(Dead())
            return connections[-1]

        async def __aexit__(self, *exc):
            return False

    mocker.patch("dotdisplay.daemon.PanelClient", side_effect=lambda mac: Ctx())

    task = asyncio.create_task(d.run(dataclasses.replace(cfg, poll_s=0.001)))
    await asyncio.sleep(0.2)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    assert len(connections) > 1


async def test_tick_prunes_dead_session_files(cfg, mocker):
    """The daemon is the only process that owns the state directory's
    lifecycle, so it is the one that tidies it."""
    import json
    import os
    import time
    dead = cfg.state_dir / "ghost.json"
    dead.write_text(json.dumps({"name": "ghost", "status": "running"}))
    old = time.time() - (cfg.prune_after_s + 60)
    os.utime(dead, (old, old))
    mocker.patch("dotdisplay.daemon.sources.read_header", return_value=None)

    await d.tick(cfg, d.Board(), FakePanel())

    assert not dead.exists()


async def test_a_failing_prune_does_not_stop_the_board(cfg, mocker):
    mocker.patch("dotdisplay.daemon.sources.prune_local_sessions",
                 side_effect=OSError("read-only state dir"))
    mocker.patch("dotdisplay.daemon.sources.read_sessions",
                 return_value=[{"name": "a", "status": "running"}])
    mocker.patch("dotdisplay.daemon.sources.read_header", return_value=None)
    panel = FakePanel()
    assert await d.tick(cfg, d.Board(), panel) is True
    assert len(panel.images) == 1


async def test_an_unchanged_board_is_refreshed_eventually(cfg, mocker):
    """A silently corrupted panel must heal itself.

    Image writes go out without response, so a dropped chunk raises nothing:
    the panel keeps a frame with a third of it missing while the daemon
    believes it is showing the current board. Because 'unchanged' meant
    'never send again', that state survived until session state happened to
    move -- observed as a panel stuck on its bottom rows.
    """
    mocker.patch("dotdisplay.daemon.sources.read_sessions",
                 return_value=[{"name": "a", "status": "running"}])
    mocker.patch("dotdisplay.daemon.sources.read_header", return_value=None)
    clock = mocker.patch("dotdisplay.daemon.time")
    clock.monotonic.side_effect = [0.0, 10.0, 10.0 + cfg.refresh_after_s]
    panel, board = FakePanel(), d.Board()

    assert await d.tick(cfg, board, panel) is True     # t=0, first frame
    assert await d.tick(cfg, board, panel) is False    # t=10, still fresh
    assert await d.tick(cfg, board, panel) is True     # refresh window passed
    assert len(panel.images) == 2


async def test_a_refresh_is_not_logged_as_a_change(cfg, mocker, caplog):
    """'panel updated' is how a human checks that the board moved. A periodic
    resend of the same pixels has not moved it and must not say so."""
    import logging
    mocker.patch("dotdisplay.daemon.sources.read_sessions",
                 return_value=[{"name": "a", "status": "running"}])
    mocker.patch("dotdisplay.daemon.sources.read_header", return_value=None)
    clock = mocker.patch("dotdisplay.daemon.time")
    clock.monotonic.side_effect = [0.0, cfg.refresh_after_s]
    panel, board = FakePanel(), d.Board()
    await d.tick(cfg, board, panel)
    with caplog.at_level(logging.INFO, logger="dotdisplay.daemon"):
        await d.tick(cfg, board, panel)
    assert "panel updated" not in caplog.text
    assert "refreshed" in caplog.text
