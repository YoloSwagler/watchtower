"""Synthetic sweep generation for demo-mode scanning.

This is the *only* place demo scanning fabricates RF data. The Sweep it
produces is fed through the exact same estimate_noise_floor/find_signals/
SignalTracker pipeline real scanning uses (see DemoSDRManager.start_scan),
so demo mode exercises real detection/tracking logic end to end.
"""

from __future__ import annotations

import math
import random

from watchtower.scanner.base import ScanParams, Sweep

# Fractional positions (0-1) across the requested range where a demo signal
# may appear. Kept as fixed fractions (not fixed frequencies) so any
# start/end range the operator types in still shows a handful of signals.
_SIGNAL_FRACTIONS = [0.12, 0.28, 0.46, 0.63, 0.81]

# Caps the synthetic bin count regardless of requested bin size, purely so
# demo-mode CPU/memory stays bounded — this has no bearing on real scanning,
# which just passes the requested bin size straight to rtl_power.
_DEMO_MAX_BINS = 2000


def generate_demo_sweep(params: ScanParams, t: float, rng: random.Random | None = None) -> Sweep:
    rng = rng or random.Random()
    start_hz = params.start_mhz * 1_000_000.0
    end_hz = params.end_mhz * 1_000_000.0
    requested_bins = max(8, int((end_hz - start_hz) / (params.bin_khz * 1000.0)))
    n_bins = min(requested_bins, _DEMO_MAX_BINS)
    bin_hz = (end_hz - start_hz) / n_bins
    freqs = [start_hz + i * bin_hz for i in range(n_bins)]

    base_noise = -95.0 + 2.5 * math.sin(t / 17.0)
    powers = [base_noise + rng.uniform(-1.5, 1.5) for _ in freqs]

    for idx, frac in enumerate(_SIGNAL_FRACTIONS):
        center = int(frac * n_bins)
        if center <= 0 or center >= n_bins - 1:
            continue
        # A slow presence cycle so signals fade in/out and occasionally miss
        # a sweep entirely — this is what exercises last-seen aging/expiry
        # in demo mode without needing real intermittent RF.
        presence = math.sin(t / 9.0 + idx * 1.7)
        if presence < -0.2:
            continue
        amplitude_db = 18.0 + 12.0 * max(0.0, presence) + idx * 2.0
        width = 1 + (idx % 3)
        for offset in range(-width, width + 1):
            i = center + offset
            if 0 <= i < n_bins:
                falloff = max(0.0, 1.0 - abs(offset) / (width + 1))
                candidate = base_noise + amplitude_db * falloff + rng.uniform(-0.5, 0.5)
                powers[i] = max(powers[i], candidate)

    return Sweep(timestamp_key=f"demo-{t:.3f}", freqs_hz=freqs, powers_db=powers, bin_hz=bin_hz)
