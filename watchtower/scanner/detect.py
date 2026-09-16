"""Noise floor estimation and peak/signal detection over one Sweep.

Deliberately simple (see ARCHITECTURE.md, "Signal detection approach"):
this is meant to help an operator find active frequencies, not to measure,
classify, or decode anything.
"""

from __future__ import annotations

import statistics

from watchtower.scanner.base import SIGNAL_THRESHOLD_DB, DetectedSignal, Sweep


def estimate_noise_floor(powers_db: list[float]) -> float:
    """Median power across the sweep. A global median is crude compared to
    a per-band rolling floor, but it's simple, robust to the peaks it's
    trying to distinguish from, and adequate for "what's active right now."
    """
    if not powers_db:
        return 0.0
    return statistics.median(powers_db)


def find_signals(
    sweep: Sweep,
    noise_floor_db: float,
    threshold_db: float = SIGNAL_THRESHOLD_DB,
) -> list[DetectedSignal]:
    """Group bins at or above noise_floor_db + threshold_db into signals.

    Adjacent active bins become one signal; a run of active bins separated
    by a frequency gap much larger than one bin (possible at rtl_power hop
    boundaries) is treated as two signals, not one spanning both.
    """
    if not sweep.freqs_hz:
        return []

    pairs = sorted(zip(sweep.freqs_hz, sweep.powers_db), key=lambda fp: fp[0])
    threshold = noise_floor_db + threshold_db
    bin_hz = sweep.bin_hz or 1.0
    max_gap_hz = bin_hz * 1.5

    signals: list[DetectedSignal] = []
    group: list[tuple[float, float]] = []

    def finalize(g: list[tuple[float, float]]) -> None:
        if not g:
            return
        freqs = [f for f, _ in g]
        powers = [p for _, p in g]
        peak_index = max(range(len(g)), key=lambda i: powers[i])
        peak_power = powers[peak_index]
        signals.append(
            DetectedSignal(
                frequency_hz=freqs[peak_index],
                power_db=peak_power,
                snr_db=peak_power - noise_floor_db,
                bandwidth_hz=(freqs[-1] - freqs[0]) + bin_hz,
            )
        )

    for freq, power in pairs:
        if power >= threshold:
            if group and (freq - group[-1][0]) > max_gap_hz:
                finalize(group)
                group = []
            group.append((freq, power))
        else:
            finalize(group)
            group = []
    finalize(group)

    return signals
