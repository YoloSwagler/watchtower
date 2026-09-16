import asyncio
import shutil

import pytest

from watchtower.sdr.base import DemodMode, ReceiverParams, ReceiverState
from watchtower.sdr.demo import DemoSDRManager


class _FakeStdout:
    """Stands in for an asyncio subprocess stdout pipe without spawning one."""

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = list(chunks)

    async def read(self, _n: int) -> bytes:
        if self._chunks:
            return self._chunks.pop(0)
        return b""


class _FakeProc:
    def __init__(self, stdout: _FakeStdout) -> None:
        self.stdout = stdout


class _FakeManagedProcess:
    def __init__(self, chunks: list[bytes]) -> None:
        self.proc = _FakeProc(_FakeStdout(chunks))


async def test_demo_starts_idle_with_one_fake_device():
    mgr = DemoSDRManager()
    snap = await mgr.snapshot()
    assert snap.state == ReceiverState.IDLE
    assert snap.tools_available is True

    devices = await mgr.refresh_devices()
    assert len(devices) == 1
    assert "simulated" in devices[0].name.lower()


async def test_demo_start_and_stop_listening():
    mgr = DemoSDRManager()
    params = ReceiverParams(frequency_mhz=101.5, mode=DemodMode.WFM)

    ok, error = await mgr.start_listening(params)
    assert ok is True
    assert error is None

    snap = await mgr.snapshot()
    assert snap.state == ReceiverState.LISTENING
    assert snap.params == params

    await mgr.stop_listening()
    snap = await mgr.snapshot()
    assert snap.state == ReceiverState.IDLE
    assert snap.params is None

    await mgr.shutdown()


async def test_demo_restart_tears_down_previous_session():
    mgr = DemoSDRManager()
    await mgr.start_listening(ReceiverParams(frequency_mhz=100.0, mode=DemodMode.NFM))
    ok, _ = await mgr.start_listening(ReceiverParams(frequency_mhz=145.5, mode=DemodMode.USB))
    assert ok is True

    snap = await mgr.snapshot()
    assert snap.params.frequency_mhz == 145.5
    assert snap.params.mode == DemodMode.USB

    await mgr.shutdown()


async def test_hf_advisory_below_threshold():
    mgr = DemoSDRManager()
    await mgr.start_listening(ReceiverParams(frequency_mhz=7.1, mode=DemodMode.LSB))
    snap = await mgr.snapshot()
    assert snap.hf_advisory is True
    await mgr.shutdown()


async def test_hf_advisory_not_set_above_threshold():
    mgr = DemoSDRManager()
    await mgr.start_listening(ReceiverParams(frequency_mhz=145.5, mode=DemodMode.NFM))
    snap = await mgr.snapshot()
    assert snap.hf_advisory is False
    await mgr.shutdown()


async def test_audio_stream_empty_when_not_listening():
    mgr = DemoSDRManager()
    chunks = [chunk async for chunk in mgr.audio_stream()]
    assert chunks == []


async def test_audio_stream_rejects_concurrent_second_consumer():
    """A second concurrent reader of the same underlying pipe would race
    the first for bytes (each read() call steals from a single shared
    stream), silently corrupting both. This was caught by hand — opening
    the real /api/audio/stream endpoint twice while demoing in a browser —
    not from reading the code. The manager must refuse a second consumer
    instead of letting them race.
    """
    mgr = DemoSDRManager()
    mgr._tone_proc = _FakeManagedProcess([b"abc", b"def"])

    gen1 = mgr.audio_stream()
    first_chunk = await gen1.__anext__()
    assert first_chunk == b"abc"

    # A concurrent second consumer, while gen1 is still attached, gets nothing.
    chunks2 = [chunk async for chunk in mgr.audio_stream()]
    assert chunks2 == []

    # Draining gen1 to completion releases the guard...
    rest = [chunk async for chunk in gen1]
    assert rest == [b"def"]

    # ...so a subsequent (non-concurrent) consumer works normally.
    mgr._tone_proc = _FakeManagedProcess([b"ghi"])
    chunks3 = [chunk async for chunk in mgr.audio_stream()]
    assert chunks3 == [b"ghi"]


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="requires real ffmpeg")
async def test_repeated_start_stop_without_audio_consumer_does_not_stall():
    """Regression test for a real hang found by hand: stopping a listen
    session before any client ever reads /api/audio/stream left ffmpeg's
    SIGTERM flush handler blocked on a full, undrained stdout pipe, so it
    only died on the follow-up SIGKILL — and even then confirmation of its
    death was slow. process.drain_discard fixes this (see
    DemoSDRManager._stop_internal). Bounded by wait_for so a regression
    fails this test instead of hanging the whole suite.
    """

    async def cycle() -> None:
        mgr = DemoSDRManager()
        for i in range(4):
            ok, err = await mgr.start_listening(ReceiverParams(frequency_mhz=100.0 + i, mode=DemodMode.NFM))
            assert ok, err
            await asyncio.sleep(0.1)
            await mgr.stop_listening()  # note: audio_stream() is never consumed
        await mgr.shutdown()

    await asyncio.wait_for(cycle(), timeout=10.0)
