import asyncio

from watchtower.scanner.base import ScanParams
from watchtower.sdr.base import DemodMode, ReceiverParams, ReceiverState
from watchtower.sdr.demo import DEMO_SCAN_TICK_SECONDS, DemoSDRManager


async def test_default_gain_is_auto_not_zero():
    """Regression test for the field-test finding: gain 0 used to mean
    manual 0 dB, which is unusable. The default must be None (auto).
    """
    params = ReceiverParams(frequency_mhz=100.0, mode=DemodMode.NFM)
    assert params.gain_db is None


async def test_start_scan_transitions_to_scanning_and_produces_signals():
    mgr = DemoSDRManager()
    ok, error = await mgr.start_scan(ScanParams(start_mhz=88.0, end_mhz=108.0, bin_khz=100.0))
    assert ok is True, error

    snap = await mgr.snapshot()
    assert snap.state == ReceiverState.SCANNING
    assert snap.scan is not None
    assert snap.scan.start_mhz == 88.0
    assert snap.scan.end_mhz == 108.0

    await asyncio.sleep(DEMO_SCAN_TICK_SECONDS * 1.5)
    snap = await mgr.snapshot()
    assert snap.scan.noise_floor_db is not None
    assert snap.scan.last_sweep_at is not None
    assert len(snap.scan.signals) > 0

    await mgr.shutdown()


async def test_stop_scan_returns_to_idle_and_clears_scan_snapshot():
    mgr = DemoSDRManager()
    await mgr.start_scan(ScanParams(start_mhz=88.0, end_mhz=108.0))
    await asyncio.sleep(DEMO_SCAN_TICK_SECONDS * 1.2)

    await mgr.stop_scan()

    snap = await mgr.snapshot()
    assert snap.state == ReceiverState.IDLE
    assert snap.scan is None
    assert snap.can_resume_scan is True  # remembered for resume_scan()


async def test_invalid_scan_range_is_rejected():
    mgr = DemoSDRManager()
    ok, error = await mgr.start_scan(ScanParams(start_mhz=108.0, end_mhz=88.0))
    assert ok is False
    assert "greater than" in error

    snap = await mgr.snapshot()
    assert snap.state == ReceiverState.ERROR
    await mgr.shutdown()


async def test_repeated_scan_start_stop_cycles_leave_clean_state():
    mgr = DemoSDRManager()
    for i in range(4):
        ok, error = await mgr.start_scan(ScanParams(start_mhz=88.0 + i, end_mhz=108.0 + i))
        assert ok, error
        await asyncio.sleep(0.05)
        await mgr.stop_scan()

    snap = await mgr.snapshot()
    assert snap.state == ReceiverState.IDLE
    await mgr.shutdown()


async def test_starting_to_listen_while_scanning_stops_the_scan():
    mgr = DemoSDRManager()
    await mgr.start_scan(ScanParams(start_mhz=88.0, end_mhz=108.0))

    ok, error = await mgr.start_listening(ReceiverParams(frequency_mhz=95.3, mode=DemodMode.WFM))
    assert ok, error

    snap = await mgr.snapshot()
    assert snap.state == ReceiverState.LISTENING
    assert snap.scan is None
    assert snap.can_resume_scan is True

    await mgr.shutdown()


async def test_resume_scan_restarts_the_remembered_scan():
    mgr = DemoSDRManager()
    await mgr.start_scan(ScanParams(start_mhz=88.0, end_mhz=108.0, bin_khz=50.0))
    await mgr.start_listening(ReceiverParams(frequency_mhz=95.3, mode=DemodMode.WFM))
    await mgr.stop_listening()

    ok, error = await mgr.resume_scan()
    assert ok, error

    snap = await mgr.snapshot()
    assert snap.state == ReceiverState.SCANNING
    assert snap.scan.bin_khz == 50.0

    await mgr.shutdown()


async def test_resume_scan_without_a_previous_scan_fails_cleanly():
    mgr = DemoSDRManager()
    ok, error = await mgr.resume_scan()
    assert ok is False
    assert error


async def test_starting_a_scan_while_listening_stops_listening():
    mgr = DemoSDRManager()
    await mgr.start_listening(ReceiverParams(frequency_mhz=95.3, mode=DemodMode.WFM))

    ok, error = await mgr.start_scan(ScanParams(start_mhz=88.0, end_mhz=108.0))
    assert ok, error

    snap = await mgr.snapshot()
    assert snap.state == ReceiverState.SCANNING
    assert snap.params is None  # listen session is gone

    await mgr.shutdown()


async def test_set_gain_while_listening_changes_gain_and_preserves_other_params():
    mgr = DemoSDRManager()
    await mgr.start_listening(
        ReceiverParams(frequency_mhz=95.3, mode=DemodMode.WFM, squelch=10, ppm=3, device_index=0)
    )

    ok, error = await mgr.set_gain(25.0)
    assert ok, error

    snap = await mgr.snapshot()
    assert snap.state == ReceiverState.LISTENING
    assert snap.params.gain_db == 25.0
    assert snap.params.frequency_mhz == 95.3
    assert snap.params.mode == DemodMode.WFM
    assert snap.params.squelch == 10
    assert snap.params.ppm == 3

    await mgr.shutdown()


async def test_set_gain_back_to_auto():
    mgr = DemoSDRManager()
    await mgr.start_listening(ReceiverParams(frequency_mhz=95.3, mode=DemodMode.WFM, gain_db=25.0))
    ok, _ = await mgr.set_gain(None)
    assert ok
    snap = await mgr.snapshot()
    assert snap.params.gain_db is None
    await mgr.shutdown()


async def test_set_gain_when_not_listening_fails_cleanly():
    mgr = DemoSDRManager()
    ok, error = await mgr.set_gain(25.0)
    assert ok is False
    assert error
