import asyncio

from watchtower.gps.base import GPSStatus
from watchtower.gps.demo import DemoGPSManager


async def test_demo_gps_reaches_3d_fix(monkeypatch):
    """The real demo sequence takes ~3.5s (device -> no-fix -> 3D fix) to
    feel plausible to a human watching the UI. Collapse the delays for the
    test so it runs fast without changing that real-world pacing.
    """
    real_sleep = asyncio.sleep

    async def fast_sleep(_seconds):
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", fast_sleep)

    mgr = DemoGPSManager()
    await mgr.start()
    try:
        snap = None
        for _ in range(200):
            snap = await mgr.snapshot()
            if snap.status == GPSStatus.FIX_3D and snap.fix is not None:
                break
            await real_sleep(0)
        assert snap is not None
        assert snap.status == GPSStatus.FIX_3D
        assert snap.fix.latitude is not None
        assert snap.fix.longitude is not None
        assert snap.satellites_used is not None
    finally:
        await mgr.stop()


async def test_demo_gps_starts_in_device_no_gpsd_state():
    mgr = DemoGPSManager()
    await mgr.start()
    try:
        # Yield once so the freshly-created background task gets to run its
        # first line before we inspect it (create_task schedules but doesn't
        # run synchronously).
        await asyncio.sleep(0)
        snap = await mgr.snapshot()
        assert snap.status == GPSStatus.DEVICE_NO_GPSD
        assert snap.fix is None
    finally:
        await mgr.stop()


async def test_stop_cancels_cleanly():
    mgr = DemoGPSManager()
    await mgr.start()
    await mgr.stop()
    # Stopping twice must not raise.
    await mgr.stop()
