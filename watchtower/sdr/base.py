"""Shared types and the abstract interface both real and demo SDR managers implement."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum


class ReceiverState(str, Enum):
    IDLE = "idle"
    STARTING = "starting"
    LISTENING = "listening"
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
    gain_db: float = 0.0  # 0 = automatic gain (rtl_fm convention)
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
    def audio_stream(self):
        """Async generator yielding audio bytes (WAV-framed) for the current
        listen session, starting with the WAV header. Empty/ends immediately
        if nothing is listening.
        """
        ...

    @abstractmethod
    async def shutdown(self) -> None: ...
