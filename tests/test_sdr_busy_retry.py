"""Regression tests for the "device busy right after we ourselves released
it" issue found on real hardware: even after rtl_power/rtl_fm exits cleanly
on SIGTERM, the RTL-SDR's USB interface can stay reported busy for a few
more seconds before the kernel finishes releasing it. start_listening/
start_scan retry a bounded number of times on exactly this error instead of
surfacing it immediately. Retry constants are monkeypatched down so these
tests run fast without changing the retry *logic* under test.
"""

from __future__ import annotations

import stat
import sys

import pytest

from watchtower.scanner.base import ScanParams
from watchtower.sdr.base import DemodMode, ReceiverParams, ReceiverState
from watchtower.sdr.manager import SDRManager

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell scripts, process groups")


def _make_executable(path, contents: str) -> str:
    path.write_text(contents)
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return str(path)


@pytest.fixture
def fake_rtl_fm_busy_once(tmp_path):
    # Fails with a "busy" stderr the first time (marker file absent), then
    # succeeds (long-running, like a real receiver) on every later attempt.
    marker = tmp_path / "attempted"
    script = _make_executable(
        tmp_path / "fake_rtl_fm_busy_once",
        f"#!/bin/sh\n"
        f'if [ ! -f "{marker}" ]; then\n'
        f'  touch "{marker}"\n'
        f'  echo "usb_claim_interface error -6: Device or resource busy" >&2\n'
        f"  exit 1\n"
        f"fi\n"
        f"sleep 30\n",
    )
    return script


@pytest.fixture
def fake_rtl_fm_always_busy(tmp_path):
    script = _make_executable(
        tmp_path / "fake_rtl_fm_always_busy",
        '#!/bin/sh\necho "usb_claim_interface error -6: Device or resource busy" >&2\nexit 1\n',
    )
    return script


@pytest.fixture
def fake_ffmpeg(tmp_path):
    return _make_executable(tmp_path / "fake_ffmpeg", "#!/bin/sh\ncat\n")


@pytest.fixture
def fake_rtl_power_busy_once(tmp_path):
    marker = tmp_path / "attempted"
    script = _make_executable(
        tmp_path / "fake_rtl_power_busy_once",
        f"#!/bin/sh\n"
        f'if [ ! -f "{marker}" ]; then\n'
        f'  touch "{marker}"\n'
        f'  echo "usb_claim_interface error -6: Device or resource busy" >&2\n'
        f"  exit 1\n"
        f"fi\n"
        f"sleep 30\n",
    )
    return script


@pytest.fixture
def fake_rtl_power_always_busy(tmp_path):
    script = _make_executable(
        tmp_path / "fake_rtl_power_always_busy",
        '#!/bin/sh\necho "usb_claim_interface error -6: Device or resource busy" >&2\nexit 1\n',
    )
    return script


def _speed_up_retries(monkeypatch, attempts=3, delay=0.02):
    monkeypatch.setattr("watchtower.sdr.manager.DEVICE_BUSY_RETRY_ATTEMPTS", attempts)
    monkeypatch.setattr("watchtower.sdr.manager.DEVICE_BUSY_RETRY_DELAY", delay)


async def test_start_listening_retries_past_a_transient_busy_error(
    fake_rtl_fm_busy_once, fake_ffmpeg, monkeypatch
):
    _speed_up_retries(monkeypatch)
    monkeypatch.setattr("watchtower.sdr.manager.detect.find_rtl_fm", lambda: fake_rtl_fm_busy_once)
    monkeypatch.setattr("watchtower.sdr.manager.detect.find_ffmpeg", lambda: fake_ffmpeg)
    mgr = SDRManager()
    try:
        ok, error = await mgr.start_listening(ReceiverParams(frequency_mhz=100.0, mode=DemodMode.NFM))
        assert ok is True, error
        snap = await mgr.snapshot()
        assert snap.state == ReceiverState.LISTENING
    finally:
        await mgr.shutdown()


async def test_start_listening_gives_up_after_exhausting_retries(
    fake_rtl_fm_always_busy, fake_ffmpeg, monkeypatch
):
    _speed_up_retries(monkeypatch, attempts=2, delay=0.02)
    monkeypatch.setattr("watchtower.sdr.manager.detect.find_rtl_fm", lambda: fake_rtl_fm_always_busy)
    monkeypatch.setattr("watchtower.sdr.manager.detect.find_ffmpeg", lambda: fake_ffmpeg)
    mgr = SDRManager()
    try:
        ok, error = await mgr.start_listening(ReceiverParams(frequency_mhz=100.0, mode=DemodMode.NFM))
        assert ok is False
        assert "busy" in error.lower()
        snap = await mgr.snapshot()
        assert snap.state == ReceiverState.ERROR
    finally:
        await mgr.shutdown()


async def test_start_scan_retries_past_a_transient_busy_error(fake_rtl_power_busy_once, monkeypatch):
    _speed_up_retries(monkeypatch)
    monkeypatch.setattr("watchtower.sdr.manager.detect.find_rtl_power", lambda: fake_rtl_power_busy_once)
    mgr = SDRManager()
    try:
        ok, error = await mgr.start_scan(ScanParams(start_mhz=88.0, end_mhz=108.0))
        assert ok is True, error
        snap = await mgr.snapshot()
        assert snap.state == ReceiverState.SCANNING
    finally:
        await mgr.shutdown()


async def test_start_scan_gives_up_after_exhausting_retries(fake_rtl_power_always_busy, monkeypatch):
    _speed_up_retries(monkeypatch, attempts=2, delay=0.02)
    monkeypatch.setattr("watchtower.sdr.manager.detect.find_rtl_power", lambda: fake_rtl_power_always_busy)
    mgr = SDRManager()
    try:
        ok, error = await mgr.start_scan(ScanParams(start_mhz=88.0, end_mhz=108.0))
        assert ok is False
        assert "busy" in error.lower()
        snap = await mgr.snapshot()
        assert snap.state == ReceiverState.ERROR
    finally:
        await mgr.shutdown()
