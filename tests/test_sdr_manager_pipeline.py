"""End-to-end test of SDRManager's real rtl_fm -> ffmpeg subprocess chaining.

This exists specifically because that chaining once broke in a way none of
the mocked unit tests could catch: passing one asyncio subprocess's
`.stdout` (an asyncio.StreamReader) directly as another's `stdin=` raises
`AttributeError: 'StreamReader' object has no attribute 'fileno'` at
spawn time — asyncio subprocess pipes aren't real file descriptors the way
`subprocess.Popen`'s are, so a StreamReader can't be handed to Popen's
stdin plumbing. It only surfaced by actually running SDRManager against
real (stand-in) processes, not by mocking rtl_fm/ffmpeg away. This test
substitutes tiny shell scripts for rtl_fm/ffmpeg so the real spawn/pipe/
terminate code path runs, without needing real RTL-SDR hardware.
"""

from __future__ import annotations

import asyncio
import stat
import sys

import pytest

from watchtower.sdr.base import DemodMode, ReceiverParams, ReceiverState
from watchtower.sdr.manager import SDRManager

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell scripts, process groups")


def _make_executable(path, contents: str):
    path.write_text(contents)
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return str(path)


@pytest.fixture
def fake_tools(tmp_path, monkeypatch):
    # Stands in for rtl_fm: emits one recognizable chunk, then stays alive
    # (like a real receiver would) until killed.
    fake_rtl_fm = _make_executable(
        tmp_path / "fake_rtl_fm",
        "#!/bin/sh\nprintf 'PCMDATA'\nsleep 30\n",
    )
    # Stands in for ffmpeg: ignores its (ffmpeg-specific) argv entirely and
    # just relays stdin to stdout, like `cat` would.
    fake_ffmpeg = _make_executable(
        tmp_path / "fake_ffmpeg",
        "#!/bin/sh\ncat\n",
    )
    monkeypatch.setattr("watchtower.sdr.manager.detect.find_rtl_fm", lambda: fake_rtl_fm)
    monkeypatch.setattr("watchtower.sdr.manager.detect.find_ffmpeg", lambda: fake_ffmpeg)
    return fake_rtl_fm, fake_ffmpeg


async def test_real_pipeline_chains_processes_and_streams_bytes(fake_tools):
    mgr = SDRManager()
    try:
        ok, error = await mgr.start_listening(ReceiverParams(frequency_mhz=100.0, mode=DemodMode.NFM))
        assert ok is True, error

        snap = await mgr.snapshot()
        assert snap.state == ReceiverState.LISTENING

        gen = mgr.audio_stream()
        collected = b""
        while b"PCMDATA" not in collected:
            chunk = await asyncio.wait_for(gen.__anext__(), timeout=5)
            collected += chunk
        await gen.aclose()

        assert b"PCMDATA" in collected
    finally:
        await mgr.shutdown()

    snap = await mgr.snapshot()
    assert snap.state == ReceiverState.IDLE
