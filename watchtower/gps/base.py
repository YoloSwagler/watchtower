"""Shared types and the abstract interface both real and demo GPS managers implement."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum


class GPSStatus(str, Enum):
    NO_DEVICE = "no_device"  # no gpsd, no plausible serial device either
    DEVICE_NO_GPSD = "device_no_gpsd"  # a serial device exists but gpsd isn't running
    CONNECTING = "connecting"  # gpsd reachable, handshake in progress
    NO_FIX = "no_fix"  # gpsd running, no position fix yet
    FIX_2D = "fix_2d"
    FIX_3D = "fix_3d"
    ERROR = "error"


@dataclass(frozen=True)
class GPSFix:
    latitude: float | None
    longitude: float | None
    altitude_m: float | None
    track_deg: float | None
    speed_mps: float | None
    time: str | None


@dataclass(frozen=True)
class GPSSnapshot:
    status: GPSStatus
    fix: GPSFix | None = None
    satellites_used: int | None = None
    satellites_visible: int | None = None
    device: str | None = None
    error: str | None = None


class GPSManagerBase(ABC):
    """Interface the web layer depends on. Implemented by GPSManager (real,
    via gpsd) and DemoGPSManager (simulated).
    """

    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...

    @abstractmethod
    async def snapshot(self) -> GPSSnapshot: ...
