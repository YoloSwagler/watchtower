"""Synthetic sweep generation for demo-mode scanning.

This is the *only* place demo scanning fabricates RF data. Signal
placement is a function of absolute frequency, not hop-relative position,
so a synthetic signal sits at the same frequency and looks the same
regardless of which hop is being generated or how the range is chunked —
that's what lets DemoSDRManager step through a scan range hop by hop (see
iter_demo_hops) the same way real rtl_power does, feeding each hop through
the same ScanEngine real scanning uses.
"""

from __future__ import annotations

import math
import random
from collections.abc import Iterator

from watchtower.scanner.base import ScanParams, Sweep

# Fractional positions (0-1) across the requested range where a demo signal
# may appear. Kept as fixed fractions (not fixed frequencies) so any
# start/end range the operator types in still shows a handful of signals.
_SIGNAL_FRACTIONS = [0.12, 0.28, 0.46, 0.63, 0.81]

# Caps the synthetic bin count regardless of requested bin size, purely so
# demo-mode CPU/memory stays bounded — this has no bearing on real scanning,
# which just passes the requested bin size straight to rtl_power.
_DEMO_MAX_BINS = 2000

# Roughly matches a real rtl_power hop's practical width, so demo scanning
# steps through a wide range in similarly-sized, similarly-paced chunks.
DEMO_HOP_HZ = 2_400_000.0


def generate_demo_hop(
    params: ScanParams,
    t: float,
    hop_start_hz: float,
    hop_end_hz: float,
    rng: random.Random | None = None,
) -> Sweep:
    """Synthetic bins for one hop-sized slice [hop_start_hz, hop_end_hz) of
    the operator-requested range.
    """
    rng = rng or random.Random()
    bin_hz = params.bin_khz * 1000.0
    span = max(hop_end_hz - hop_start_hz, bin_hz)
    n_bins = max(1, min(int(round(span / bin_hz)), _DEMO_MAX_BINS))
    freqs = [hop_start_hz + i * bin_hz for i in range(n_bins)]

    base_noise = -95.0 + 2.5 * math.sin(t / 17.0)
    powers = [base_noise + rng.uniform(-1.5, 1.5) for _ in freqs]

    start_hz = params.start_mhz * 1_000_000.0
    end_hz = params.end_mhz * 1_000_000.0
    full_span = end_hz - start_hz

    for idx, frac in enumerate(_SIGNAL_FRACTIONS):
        center_hz = start_hz + frac * full_span
        # A slow presence cycle so signals fade in/out and occasionally miss
        # a pass entirely — this is what exercises last-seen aging/expiry
        # in demo mode without needing real intermittent RF.
        presence = math.sin(t / 9.0 + idx * 1.7)
        if presence < -0.2:
            continue
        amplitude_db = 18.0 + 12.0 * max(0.0, presence) + idx * 2.0
        half_width_hz = 60_000.0 + (idx % 3) * 50_000.0
        if center_hz < hop_start_hz - half_width_hz * 4 or center_hz > hop_end_hz + half_width_hz * 4:
            continue  # this signal doesn't reach into the current hop at all
        for i, f in enumerate(freqs):
            falloff = math.exp(-(((f - center_hz) / half_width_hz) ** 2))
            if falloff < 0.02:
                continue
            candidate = base_noise + amplitude_db * falloff + rng.uniform(-0.5, 0.5)
            powers[i] = max(powers[i], candidate)

    return Sweep(timestamp_key=f"demo-{t:.3f}", freqs_hz=freqs, powers_db=powers, bin_hz=bin_hz)


def generate_demo_sweep(params: ScanParams, t: float, rng: random.Random | None = None) -> Sweep:
    """The whole requested range in one Sweep — a convenience wrapper
    around generate_demo_hop for callers that don't need hop stepping.
    """
    start_hz = params.start_mhz * 1_000_000.0
    end_hz = params.end_mhz * 1_000_000.0
    return generate_demo_hop(params, t, start_hz, end_hz, rng=rng)


def iter_demo_hops(params: ScanParams, hop_hz: float = DEMO_HOP_HZ) -> Iterator[tuple[float, float]]:
    """Yields (hop_start_hz, hop_end_hz) covering the requested range in
    hop-sized steps, mirroring how rtl_power itself breaks a wide range
    into several hops rather than sweeping it in one shot.
    """
    start_hz = params.start_mhz * 1_000_000.0
    end_hz = params.end_mhz * 1_000_000.0
    step = min(hop_hz, end_hz - start_hz)
    if step <= 0:
        step = end_hz - start_hz
    cursor = start_hz
    while cursor < end_hz:
        hop_end = min(cursor + step, end_hz)
        yield cursor, hop_end
        cursor = hop_end
