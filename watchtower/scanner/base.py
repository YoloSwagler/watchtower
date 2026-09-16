"""Shared types, constants, and validation for the wideband scanner.

See ARCHITECTURE.md, "Wideband scanner", for how these fit together:
rtl_power CSV -> SweepAccumulator -> Sweep -> find_signals -> DetectedSignal
-> SignalTracker -> TrackedSignal.
"""

from __future__ import annotations

from dataclasses import dataclass

# Bounds chosen to keep worst-case scan-cycle time and CPU reasonable on a
# Pi 4B, not because the hardware can't technically go wider/finer.
MAX_SCAN_SPAN_MHZ = 1000.0
MIN_BIN_KHZ = 1.0
MAX_BIN_KHZ = 1000.0
DEFAULT_BIN_KHZ = 25.0

# rtl_power's -i (integration interval, seconds) per hop.
SCAN_INTERVAL_SECONDS = 4.0

# A bin is "active" (part of a signal) once it's this many dB above the
# sweep's estimated noise floor.
SIGNAL_THRESHOLD_DB = 10.0

# Tracked signals not re-observed within this many seconds are dropped.
SIGNAL_TTL_SECONDS = 30.0


@dataclass(frozen=True)
class ScanParams:
    start_mhz: float
    end_mhz: float
    bin_khz: float = DEFAULT_BIN_KHZ
    device_index: int = 0
    gain_db: float | None = None
    ppm: int = 0


@dataclass(frozen=True)
class RtlPowerRow:
    """One CSV line from rtl_power: one hop's worth of one integration
    interval. A full sweep over a wide range is several of these sharing
    one (date, time) stamp.
    """

    date: str
    time: str
    hz_low: int
    hz_high: int
    hz_step: int
    num_samples: int
    dbs: list[float]

    @property
    def timestamp_key(self) -> str:
        return f"{self.date} {self.time}"


@dataclass(frozen=True)
class Sweep:
    """One completed full-range pass: all hops for one timestamp, merged
    and sorted by frequency.
    """

    timestamp_key: str
    freqs_hz: list[float]
    powers_db: list[float]
    bin_hz: float


@dataclass(frozen=True)
class DetectedSignal:
    """One active signal found in a single sweep. See ARCHITECTURE.md,
    "Signal detection approach", for why center/bandwidth are computed this
    simply.
    """

    frequency_hz: float
    power_db: float
    snr_db: float
    bandwidth_hz: float


@dataclass(frozen=True)
class TrackedSignal:
    """A DetectedSignal matched across sweeps, with first/last-seen times."""

    frequency_hz: float
    power_db: float
    snr_db: float
    bandwidth_hz: float
    first_seen: float
    last_seen: float


def validate_scan_range(start_mhz: float, end_mhz: float, bin_khz: float) -> str | None:
    """Returns an error message, or None if the range/bin size are usable."""
    if start_mhz <= 0 or end_mhz <= 0:
        return "Start and end frequency must be positive."
    if end_mhz <= start_mhz:
        return "End frequency must be greater than start frequency."
    if (end_mhz - start_mhz) > MAX_SCAN_SPAN_MHZ:
        return f"Scan span cannot exceed {MAX_SCAN_SPAN_MHZ:.0f} MHz."
    if bin_khz < MIN_BIN_KHZ or bin_khz > MAX_BIN_KHZ:
        return f"Bin size must be between {MIN_BIN_KHZ:.0f} kHz and {MAX_BIN_KHZ:.0f} kHz."
    return None
