"""End-to-end tests of SDRManager's scan (rtl_power) pipeline and the
hot-plug/disconnect monitor, using tiny stand-in shell scripts the same way
test_sdr_manager_pipeline.py stands in for rtl_fm/ffmpeg — this exercises
the real spawn/parse/terminate code path without needing RTL-SDR hardware.
"""

from __future__ import annotations

import asyncio
import stat
import sys

import pytest

from watchtower.scanner.base import ScanParams
from watchtower.sdr.base import DemodMode, ReceiverParams, ReceiverState, SDRDevice
from watchtower.sdr.manager import SDRManager, _build_rtl_fm_cmd, _build_rtl_power_cmd

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell scripts, process groups")


def _make_executable(path, contents: str) -> str:
    path.write_text(contents)
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return str(path)


@pytest.fixture
def fake_rtl_power(tmp_path):
    # Emits a fresh one-hop "sweep" every ~50ms with a signal peak, then
    # (after 100 iterations, well beyond any test's runtime) sleeps so it
    # behaves like a real long-running tool until killed.
    script = _make_executable(
        tmp_path / "fake_rtl_power",
        "#!/bin/sh\n"
        "i=0\n"
        "while [ $i -lt 200 ]; do\n"
        "  printf '2024-01-01, 12:00:%02d, 88000000, 88050000, 25000, 2, -90.0, -60.0\\n' \"$i\"\n"
        "  i=$((i+1))\n"
        "  sleep 0.05\n"
        "done\n"
        "sleep 30\n",
    )
    return script


@pytest.fixture
def fake_rtl_power_dies(tmp_path):
    # Survives the startup health-check delay, emits one line, then exits —
    # simulates the dongle disappearing mid-scan.
    script = _make_executable(
        tmp_path / "fake_rtl_power_dies",
        "#!/bin/sh\nsleep 0.5\nprintf '2024-01-01, 12:00:00, 88000000, 88050000, 25000, 2, -90.0, -60.0\\n'\nexit 1\n",
    )
    return script


@pytest.fixture
def fake_listen_tools_that_die(tmp_path):
    fake_rtl_fm = _make_executable(tmp_path / "fake_rtl_fm_dies", "#!/bin/sh\nsleep 0.5\nexit 1\n")
    fake_ffmpeg = _make_executable(tmp_path / "fake_ffmpeg", "#!/bin/sh\ncat\n")
    return fake_rtl_fm, fake_ffmpeg


async def test_real_scan_pipeline_parses_signals_and_cleans_up(fake_rtl_power, monkeypatch):
    monkeypatch.setattr("watchtower.sdr.manager.detect.find_rtl_power", lambda: fake_rtl_power)
    mgr = SDRManager()
    try:
        ok, error = await mgr.start_scan(ScanParams(start_mhz=88.0, end_mhz=88.05, bin_khz=25.0))
        assert ok, error

        snap = await mgr.snapshot()
        assert snap.state == ReceiverState.SCANNING

        for _ in range(50):
            snap = await mgr.snapshot()
            if snap.scan and snap.scan.signals:
                break
            await asyncio.sleep(0.1)
        else:
            pytest.fail("no signals parsed from fake rtl_power output within timeout")

        assert snap.scan.signals[0].power_db == pytest.approx(-60.0)
        assert snap.scan.noise_floor_db is not None
    finally:
        await mgr.shutdown()

    snap = await mgr.snapshot()
    assert snap.state == ReceiverState.IDLE


async def test_scan_start_fails_cleanly_when_rtl_power_missing(monkeypatch):
    monkeypatch.setattr("watchtower.sdr.manager.detect.find_rtl_power", lambda: None)
    mgr = SDRManager()
    ok, error = await mgr.start_scan(ScanParams(start_mhz=88.0, end_mhz=108.0))
    assert ok is False
    assert "rtl_power" in error
    snap = await mgr.snapshot()
    assert snap.state == ReceiverState.ERROR
    await mgr.shutdown()


async def test_monitor_tick_detects_scan_process_dying_unexpectedly(fake_rtl_power_dies, monkeypatch):
    monkeypatch.setattr("watchtower.sdr.manager.detect.find_rtl_power", lambda: fake_rtl_power_dies)
    mgr = SDRManager()
    try:
        ok, error = await mgr.start_scan(ScanParams(start_mhz=88.0, end_mhz=88.05, bin_khz=25.0))
        assert ok, error

        # Wait for the fake process to actually exit (it sleeps 0.5s then exits).
        for _ in range(50):
            if mgr._scan_proc is not None and mgr._scan_proc.proc.returncode is not None:
                break
            await asyncio.sleep(0.05)
        else:
            pytest.fail("fake rtl_power never exited")

        await mgr._monitor_tick()

        snap = await mgr.snapshot()
        assert snap.state == ReceiverState.DISCONNECTED
        assert snap.error == "SDR disconnected"
        assert snap.scan is None
    finally:
        await mgr.shutdown()


async def test_monitor_tick_detects_listen_process_dying_unexpectedly(fake_listen_tools_that_die, monkeypatch):
    fake_rtl_fm, fake_ffmpeg = fake_listen_tools_that_die
    monkeypatch.setattr("watchtower.sdr.manager.detect.find_rtl_fm", lambda: fake_rtl_fm)
    monkeypatch.setattr("watchtower.sdr.manager.detect.find_ffmpeg", lambda: fake_ffmpeg)
    mgr = SDRManager()
    try:
        ok, error = await mgr.start_listening(ReceiverParams(frequency_mhz=100.0, mode=DemodMode.NFM))
        assert ok, error

        for _ in range(50):
            if mgr._rtl_proc is not None and mgr._rtl_proc.proc.returncode is not None:
                break
            await asyncio.sleep(0.05)
        else:
            pytest.fail("fake rtl_fm never exited")

        await mgr._monitor_tick()

        snap = await mgr.snapshot()
        assert snap.state == ReceiverState.DISCONNECTED
        assert snap.params is None
    finally:
        await mgr.shutdown()


async def test_monitor_tick_leaves_a_healthy_listen_session_alone(monkeypatch):
    async def fake_detect_devices():
        pytest.fail("detect_devices should not be called while a pipeline is active")

    monkeypatch.setattr("watchtower.sdr.manager.detect.detect_devices", fake_detect_devices)
    mgr = SDRManager()
    mgr._state = ReceiverState.LISTENING
    # No _rtl_proc/_ffmpeg_proc set (None): the "dead" check treats a None
    # tracked process as not-dead (only a set-and-exited process counts).
    await mgr._monitor_tick()
    assert mgr._state == ReceiverState.LISTENING


async def test_monitor_tick_flags_disconnect_when_idle_device_disappears(monkeypatch):
    async def no_devices():
        return []

    monkeypatch.setattr("watchtower.sdr.manager.detect.detect_devices", no_devices)
    mgr = SDRManager()
    mgr._state = ReceiverState.IDLE
    mgr._device_seen_once = True

    await mgr._monitor_tick()

    assert mgr._state == ReceiverState.DISCONNECTED


async def test_monitor_tick_ignores_absence_when_device_was_never_seen(monkeypatch):
    async def no_devices():
        return []

    monkeypatch.setattr("watchtower.sdr.manager.detect.detect_devices", no_devices)
    mgr = SDRManager()
    mgr._state = ReceiverState.IDLE
    mgr._device_seen_once = False

    await mgr._monitor_tick()

    assert mgr._state == ReceiverState.IDLE  # no false alarm on a dev machine with no hardware


async def test_monitor_tick_reconnect_transitions_disconnected_to_idle(monkeypatch):
    async def one_device():
        return [SDRDevice(index=0, name="Realtek, RTL2838UHIDIR")]

    monkeypatch.setattr("watchtower.sdr.manager.detect.detect_devices", one_device)
    mgr = SDRManager()
    mgr._state = ReceiverState.DISCONNECTED
    mgr._error = "SDR disconnected"

    await mgr._monitor_tick()

    snap = await mgr.snapshot()
    assert snap.state == ReceiverState.IDLE
    assert snap.error is None
    assert len(snap.devices) == 1


def test_gain_flag_omitted_when_auto():
    cmd = _build_rtl_fm_cmd("rtl_fm", ReceiverParams(frequency_mhz=100.0, mode=DemodMode.NFM, gain_db=None))
    assert "-g" not in cmd


def test_gain_flag_present_when_manual():
    cmd = _build_rtl_fm_cmd("rtl_fm", ReceiverParams(frequency_mhz=100.0, mode=DemodMode.NFM, gain_db=25.0))
    assert "-g" in cmd
    assert cmd[cmd.index("-g") + 1] == "25.0"


def test_scan_gain_flag_omitted_when_auto():
    cmd = _build_rtl_power_cmd("rtl_power", ScanParams(start_mhz=88.0, end_mhz=108.0, gain_db=None))
    assert "-g" not in cmd


def test_scan_gain_flag_present_when_manual():
    cmd = _build_rtl_power_cmd("rtl_power", ScanParams(start_mhz=88.0, end_mhz=108.0, gain_db=30.0))
    assert "-g" in cmd
