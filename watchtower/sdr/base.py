"""Shared types and the abstract interface both real and demo SDR managers implement."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from watchtower.scanner.base import ScanParams


class ReceiverState(str, Enum):
    IDLE = "idle"
    STARTING = "starting"
    LISTENING = "listening"
    SCANNING = "scanning"
    DISCONNECTED = "disconnected"
    ERROR = "error"


class DemodMode(str, Enum):
    NFM = "nfm"
    WFM = "wfm"
    USB = "usb"
    LSB = "lsb"
    AM = "am"


# Modes below this frequency (MHz) on a stock RTL-SDR tuner typically require
# direct sampling or an upconverter. This is advisory only — Watchtower has
# no reliable way to detect actual tuner capability from software.
HF_ADVISORY_THRESHOLD_MHZ = 24.0


@dataclass(frozen=True)
class SDRDevice:
    index: int
    name: str
    serial: str | None = None


@dataclass(frozen=True)
class ReceiverParams:
    frequency_mhz: float
    mode: DemodMode
    device_index: int = 0
    gain_db: float | None = None  # None = automatic tuner gain (rtl_fm/rtl_power convention)
    squelch: int = 0  # 0 = squelch off
    ppm: int = 0


@dataclass(frozen=True)
class SDRSnapshot:
    state: ReceiverState
    tools_available: bool
    devices: list[SDRDevice] = field(default_factory=list)
    devices_stale: bool = False
    params: ReceiverParams | None = None
    error: str | None = None
    hf_advisory: bool = False
    scan: "ScanSnapshot | None" = None
    can_resume_scan: bool = False


@dataclass(frozen=True)
class ScanSnapshot:
    """Read-only view of the current/last scan for /api/status. Defined here
    (rather than in watchtower/scanner/) so SDRSnapshot doesn't need to
    import the scanner package, avoiding a circular import — the scanner
    package imports back from sdr.base for ReceiverState-adjacent types.
    """

    start_mhz: float
    end_mhz: float
    bin_khz: float
    noise_floor_db: float | None
    last_sweep_at: float | None
    signals: list[TrackedSignalView] = field(default_factory=list)


@dataclass(frozen=True)
class TrackedSignalView:
    frequency_mhz: float
    power_db: float
    snr_db: float
    bandwidth_khz: float
    first_seen: float
    last_seen: float


class SDRManagerBase(ABC):
    """Interface the web layer depends on. Implemented by SDRManager (real)
    and DemoSDRManager (simulated) so application logic is hardware-agnostic.
    """

    @abstractmethod
    async def snapshot(self) -> SDRSnapshot: ...

    @abstractmethod
    async def refresh_devices(self) -> list[SDRDevice]: ...

    @abstractmethod
    async def start_listening(self, params: ReceiverParams) -> tuple[bool, str | None]:
        """Returns (ok, error_message)."""
        ...

    @abstractmethod
    async def stop_listening(self) -> None: ...

    @abstractmethod
    async def set_gain(self, gain_db: float | None) -> tuple[bool, str | None]:
        """Change gain for the current listen session in place (frequency,
        mode, squelch, ppm all preserved). Returns (ok, error_message);
        ok=False with an error if nothing is currently listening.
        """
        ...

    @abstractmethod
    async def start_scan(self, params: "ScanParams") -> tuple[bool, str | None]:
        """Returns (ok, error_message)."""
        ...

    @abstractmethod
    async def stop_scan(self) -> None: ...

    @abstractmethod
    async def resume_scan(self) -> tuple[bool, str | None]:
        """Restart the most recently stopped scan (e.g. after a listen
        session started from a detected signal ends). Returns
        (ok, error_message); ok=False if there is no remembered scan.
        """
        ...

    @abstractmethod
    def audio_stream(self):
        """Async generator yielding audio bytes (WAV-framed) for the current
        listen session, starting with the WAV header. Empty/ends immediately
        if nothing is listening.
        """
        ...

    @abstractmethod
    async def shutdown(self) -> None: ...

    async def start_monitor(self) -> None:
        """Start any background hardware-presence monitoring. No-op by
        default; overridden by the real SDRManager. DemoSDRManager has
        nothing to monitor (simulated hardware never disconnects).
        """
        return

    async def stop_monitor(self) -> None:
        return
