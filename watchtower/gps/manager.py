"""Real GPSManager: a gpsd client with reconnect + device presence detection.

Watchtower does not launch gpsd itself in this phase (see ARCHITECTURE.md,
"GPS lifecycle"). This manager only ever connects to an already-running
gpsd, and otherwise reports whether a plausible serial device exists so the
UI can tell "no hardware" apart from "hardware present, gpsd not started."
"""

from __future__ import annotations

import asyncio
import glob

from watchtower.gps.base import GPSFix, GPSManagerBase, GPSSnapshot, GPSStatus
from watchtower.gps.gpsd_client import GpsdClient, SkyMessage, TPVMessage
from watchtower.logging_setup import get_logger

logger = get_logger("gps.manager")

RECONNECT_INTERVAL = 5.0
DEVICE_GLOBS = ("/dev/ttyUSB*", "/dev/ttyACM*")


def find_candidate_device() -> str | None:
    for pattern in DEVICE_GLOBS:
        matches = sorted(glob.glob(pattern))
        if matches:
            return matches[0]
    return None


class GPSManager(GPSManagerBase):
    def __init__(self, host: str = "127.0.0.1", port: int = 2947) -> None:
        self._host = host
        self._port = port
        self._task: asyncio.Task | None = None
        self._stopping = False

        self._status = GPSStatus.NO_DEVICE
        self._fix: GPSFix | None = None
        self._sats_used: int | None = None
        self._sats_visible: int | None = None
        self._device: str | None = None
        self._error: str | None = None

    async def start(self) -> None:
        self._stopping = False
        self._task = asyncio.create_task(self._run_loop(), name="gpsd-client-loop")

    async def stop(self) -> None:
        self._stopping = True
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
            error=self._error,
        )

    async def _run_loop(self) -> None:
        while not self._stopping:
            client = GpsdClient(self._host, self._port, self._on_tpv, self._on_sky, self._on_device)
            try:
                await client.connect()
            except (OSError, asyncio.TimeoutError):
                self._mark_disconnected()
                await asyncio.sleep(RECONNECT_INTERVAL)
                continue

            logger.info("connected to gpsd at %s:%s", self._host, self._port)
            self._status = GPSStatus.NO_FIX
            self._error = None
            try:
                await client.run_forever()
            except asyncio.CancelledError:
                await client.close()
                raise
            except (OSError, asyncio.TimeoutError) as e:
                self._error = str(e)
                logger.warning("gpsd connection lost: %s", e)
            finally:
                await client.close()

            self._mark_disconnected()
            await asyncio.sleep(RECONNECT_INTERVAL)

    def _mark_disconnected(self) -> None:
        self._fix = None
        self._sats_used = None
        self._sats_visible = None
        device = find_candidate_device()
        self._status = GPSStatus.DEVICE_NO_GPSD if device else GPSStatus.NO_DEVICE
        if not self._device:
            self._device = device

    async def _on_tpv(self, tpv: TPVMessage) -> None:
        self._fix = GPSFix(
            latitude=tpv.lat,
            longitude=tpv.lon,
            altitude_m=tpv.alt_m,
            track_deg=tpv.track_deg,
            speed_mps=tpv.speed_mps,
            time=tpv.time,
        )
        if tpv.mode >= 3:
            self._status = GPSStatus.FIX_3D
        elif tpv.mode == 2:
            self._status = GPSStatus.FIX_2D
        else:
            self._status = GPSStatus.NO_FIX

    async def _on_sky(self, sky: SkyMessage) -> None:
        self._sats_used = sky.satellites_used
        self._sats_visible = sky.satellites_visible

    async def _on_device(self, path: str) -> None:
        if path:
            self._device = path
