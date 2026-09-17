import asyncio

import pytest

from watchtower.scanner.base import ScanParams
from watchtower.sdr.base import DemodMode, ReceiverParams, ReceiverState
from watchtower.sdr.demo import DEMO_HOP_TICK_SECONDS, DemoSDRManager


async def test_default_gain_is_auto_not_zero():
    """Regression test for the field-test finding: gain 0 used to mean
    manual 0 dB, which is unusable. The default must be None (auto).
    """
    params = ReceiverParams(frequency_mhz=100.0, mode=DemodMode.NFM)
    assert params.gain_db is None


async def test_start_scan_transitions_to_scanning_and_produces_signals():
    mgr = DemoSDRManager()
    # Narrower than one demo hop, so a single hop is a complete pass —
    # signals and noise floor should both show up quickly.
    ok, error = await mgr.start_scan(ScanParams(start_mhz=88.0, end_mhz=90.0, bin_khz=100.0))
    assert ok is True, error

    snap = await mgr.snapshot()
    assert snap.state == ReceiverState.SCANNING
    assert snap.scan is not None
    assert snap.scan.start_mhz == 88.0
    assert snap.scan.end_mhz == 90.0

    for _ in range(100):
        snap = await mgr.snapshot()
        if snap.scan.signals and snap.scan.noise_floor_db is not None:
            break
        await asyncio.sleep(0.05)
    else:
        pytest.fail("no signals/noise floor produced within timeout")

    assert snap.scan.last_sweep_at is not None
    assert snap.scan.current_freq_mhz is not None

    await mgr.shutdown()


async def test_signals_appear_incrementally_before_a_full_pass_completes():
    mgr = DemoSDRManager()
    # Wide enough to span several demo hops (default hop is 2.4 MHz).
    ok, error = await mgr.start_scan(ScanParams(start_mhz=88.0, end_mhz=100.0, bin_khz=100.0))
    assert ok, error

    # Right after the first hop or two, we should have a live current-
    # frequency cursor but the full-range noise floor shouldn't be set yet
    # (that only refreshes once a whole pass completes).
    for _ in range(100):
        snap = await mgr.snapshot()
        if snap.scan.current_freq_mhz is not None:
            break
        await asyncio.sleep(0.05)
    else:
        pytest.fail("current_freq_mhz never appeared")
    assert snap.scan.current_freq_mhz < 92.0  # still early in the range
    assert snap.scan.last_sweep_at is None  # first pass not complete yet

    # Eventually a full pass completes and the noise floor/last_sweep_at
    # get filled in too.
    for _ in range(200):
        snap = await mgr.snapshot()
        if snap.scan.last_sweep_at is not None:
            break
        await asyncio.sleep(0.05)
    else:
        pytest.fail("full sweep never completed")
    assert snap.scan.noise_floor_db is not None

    await mgr.shutdown()


async def test_stop_scan_persists_the_last_results():
    mgr = DemoSDRManager()
    await mgr.start_scan(ScanParams(start_mhz=88.0, end_mhz=90.0, bin_khz=100.0))

    for _ in range(100):
        snap = await mgr.snapshot()
        if snap.scan.signals:
            break
        await asyncio.sleep(0.05)
    else:
        pytest.fail("no signals produced within timeout")
    signals_before_stop = snap.scan.signals

    await mgr.stop_scan()

    snap = await mgr.snapshot()
    assert snap.state == ReceiverState.IDLE
    assert snap.scan is not None  # results stick around, not cleared
    assert snap.scan.signals == signals_before_stop
    assert snap.scan.current_freq_mhz is None  # "currently scanning" cursor only meaningful while live
    assert snap.has_scan_results is True

    await mgr.shutdown()


async def test_starting_a_new_scan_replaces_the_previous_results():
    mgr = DemoSDRManager()
    await mgr.start_scan(ScanParams(start_mhz=88.0, end_mhz=90.0))
    await mgr.stop_scan()

    ok, error = await mgr.start_scan(ScanParams(start_mhz=150.0, end_mhz=152.0))
    assert ok, error

    snap = await mgr.snapshot()
    assert snap.scan.start_mhz == 150.0
    assert snap.scan.end_mhz == 152.0

    await mgr.shutdown()


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


async def test_starting_to_listen_while_scanning_stops_the_scan_but_keeps_results():
    mgr = DemoSDRManager()
    await mgr.start_scan(ScanParams(start_mhz=88.0, end_mhz=90.0))

    ok, error = await mgr.start_listening(ReceiverParams(frequency_mhz=95.3, mode=DemodMode.WFM))
    assert ok, error

    snap = await mgr.snapshot()
    assert snap.state == ReceiverState.LISTENING
    assert snap.scan is not None  # last scan's results still visible
    assert snap.has_scan_results is True

    await mgr.shutdown()


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


async def test_demo_hop_tick_constant_is_reasonable():
    # Not really a functional test — just guards against someone dropping
    # this so low that demo scanning burns CPU pointlessly, or so high
    # that it feels unresponsive.
    assert 0.1 <= DEMO_HOP_TICK_SECONDS <= 2.0
