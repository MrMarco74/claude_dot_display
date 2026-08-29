import pytest

from dotdisplay.ble import transport as t


async def test_fake_records_writes():
    fake = t.FakeTransport()
    await fake.connect()
    await fake.send(b"hello")
    await fake.disconnect()
    assert fake.writes == [b"hello"]
    assert fake.connected is False


async def test_large_frame_is_split_to_the_write_limit():
    """A 4105-byte chunk cannot go out in one ATT write."""
    fake = t.FakeTransport()
    await fake.connect()
    await fake.send(bytes(4105))
    assert all(len(w) <= t.MAX_WRITE for w in fake.writes)
    assert sum(len(w) for w in fake.writes) == 4105
    assert len(fake.writes) == 9      # 8 x 509 + 1 x 33, as captured


async def test_split_preserves_the_byte_stream_exactly():
    payload = bytes(range(256)) * 20
    fake = t.FakeTransport()
    await fake.connect()
    await fake.send(payload)
    assert b"".join(fake.writes) == payload


async def test_sending_while_disconnected_is_an_error():
    """Silently dropping writes would look like a dead panel and send the
    next person debugging the wrong layer."""
    with pytest.raises(t.NotConnected):
        await t.FakeTransport().send(b"x")


async def test_pacing_delay_is_applied_between_writes(mocker):
    """The panel drops data if written to as fast as the stack allows; the
    delay is configurable because the right value is hardware-dependent."""
    sleep = mocker.patch("dotdisplay.ble.transport.asyncio.sleep")
    fake = t.FakeTransport(pacing_s=0.01)
    await fake.connect()
    await fake.send(bytes(4105))
    assert sleep.await_count == len(fake.writes) - 1


async def test_no_pacing_means_no_sleeping(mocker):
    sleep = mocker.patch("dotdisplay.ble.transport.asyncio.sleep")
    fake = t.FakeTransport(pacing_s=0)
    await fake.connect()
    await fake.send(bytes(4105))
    sleep.assert_not_awaited()


async def test_split_respects_a_narrowed_write_limit():
    """BlueZ reports a 23-byte MTU until it is acquired. If that value is
    what we get, writes must shrink to fit rather than be rejected."""
    fake = t.FakeTransport()
    await fake.connect()
    fake.max_write = 20
    await fake.send(bytes(100))
    assert [len(w) for w in fake.writes] == [20, 20, 20, 20, 20]


async def test_default_write_limit_is_the_captured_size():
    assert t.FakeTransport().max_write == t.MAX_WRITE == 509


async def test_pacing_spans_consecutive_frames(mocker):
    """The gap between two frames is a write boundary like any other.

    send_image calls send() once per chunk, so pacing that resets per frame
    leaves the first write of every chunk unpaced -- three unpaced writes in
    a row at exactly the boundaries where the panel was observed dropping
    data, which shows up as a frame with a third of it missing.
    """
    sleep = mocker.patch("dotdisplay.ble.transport.asyncio.sleep")
    fake = t.FakeTransport(pacing_s=0.01)
    await fake.connect()
    await fake.send(bytes(600))       # 2 writes
    await fake.send(bytes(600))       # 2 writes
    assert sleep.await_count == len(fake.writes) - 1 == 3


async def test_pacing_is_skipped_when_the_panel_has_had_its_rest(mocker):
    """Pacing protects the panel from a burst, not from the poll loop. A
    write arriving long after the last one must not wait for nothing."""
    sleep = mocker.patch("dotdisplay.ble.transport.asyncio.sleep")
    # The whole module object, not time.monotonic: that attribute is shared
    # with the event loop, and patching it there breaks asyncio itself.
    clock = mocker.patch("dotdisplay.ble.transport.time")
    clock.monotonic.side_effect = [0.0, 10.0]
    fake = t.FakeTransport(pacing_s=0.01)
    await fake.connect()
    await fake.send(bytes(100))
    await fake.send(bytes(100))
    sleep.assert_not_awaited()
