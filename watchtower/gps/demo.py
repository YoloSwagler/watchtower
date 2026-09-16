"""Simulated GPS manager: a plausible acquisition sequence then a jittering fix.

Implements GPSManagerBase so the web layer and UI can't tell it apart from
the real gpsd-backed manager. Useful for developing/demonstrating the GPS
panel without a u-blox receiver attached.
"""

from __future__ import annotations

import asyncio
import random
from datetime import datetime, timezone

from watchtower.gps.base import GPSFix, GPSManagerBase, GPSSnapshot, GPSStatus
from watchtower.logging_setup import get_logger

logger = get_logger("gps.demo")

# A fixed reference point (Royal Observatory, Greenwich) with small jitter
# applied, so the demo fix looks like a real (if stationary) receiver.
_BASE_LAT = 51.4769
_BASE_LON = -0.0005
_BASE_ALT_M = 45.0


class DemoGPSManager(GPSManagerBase):
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._status = GPSStatus.NO_DEVICE
        self._fix: GPSFix | None = None
        self._sats_used: int | None = None
        self._sats_visible: int | None = None
        self._device = "/dev/ttyACM0 (simulated)"

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="demo-gps-loop")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def snapshot(self) -> GPSSnapshot:
        return GPSSnapshot(
            status=self._status,
            fix=self._fix,
            satellites_used=self._sats_used,
            satellites_visible=self._sats_visible,
            device=self._device,
            error=None,
        )

    async def _run(self) -> None:
        # Simulate a realistic acquisition sequence: device detected, then
        # gpsd "connects", then a fix takes a few seconds to acquire.
        self._status = GPSStatus.DEVICE_NO_GPSD
        await asyncio.sleep(1.5)
        self._status = GPSStatus.NO_FIX
        self._sats_visible = 5
        self._sats_used = 0
        await asyncio.sleep(2.0)
        self._status = GPSStatus.FIX_3D
        logger.info("demo GPS fix acquired")

        while True:
            self._sats_visible = random.randint(8, 12)
            self._sats_used = random.randint(6, self._sats_visible)
            self._fix = GPSFix(
                latitude=_BASE_LAT + random.uniform(-0.00003, 0.00003),
                longitude=_BASE_LON + random.uniform(-0.00004, 0.00004),
                altitude_m=_BASE_ALT_M + random.uniform(-1.5, 1.5),
                track_deg=random.uniform(0, 359),
                speed_mps=random.uniform(0, 0.4),
                time=datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            )
            await asyncio.sleep(1.0)
