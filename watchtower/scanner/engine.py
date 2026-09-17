"""Turns a stream of per-hop Sweep chunks into live scan state.

Both SDRManager (fed from real rtl_power hop lines) and DemoSDRManager
(fed from synthetic per-hop sweeps) drive one of these — this is the one
place "how do sweep chunks become the live UI state" is implemented, so
real and demo scanning behave identically from here on.

Detection runs per hop (ingest_hop), not once per full sweep across the
whole requested range — rtl_power's own hop width is capped well below
most requested ranges, so a wide scan is naturally several hops, each
arriving as its own integration completes. Running detection on each hop
as it arrives is what makes the signal table fill in progressively during
a scan instead of only updating once a full pass completes, and it costs
nothing extra: the hop's bins were already being parsed regardless.

The noise floor estimate (noise_floor_db) is refreshed once per full sweep
(complete_sweep) rather than per hop, since a full sweep's bins give a much
more stable median than any single ~100-bin hop. The very first hop of the
very first scan has no full-sweep floor yet, so ingest_hop bootstraps from
that hop's own local estimate until complete_sweep() runs for the first
time.

One accepted limitation: a signal that happens to straddle a hop boundary
can show up as two weaker partial detections for one hop-cycle instead of
one clean one — SignalTracker's frequency-proximity matching absorbs this
over subsequent hops/sweeps rather than needing explicit boundary-merging
logic, which would add real complexity for a rare edge case.
"""

from __future__ import annotations

from watchtower.scanner.base import ScanParams, Sweep, TrackedSignal
from watchtower.scanner.detect import estimate_noise_floor, find_signals
from watchtower.scanner.tracker import SignalTracker


class ScanEngine:
    def __init__(self, params: ScanParams) -> None:
        self.params = params
        tolerance_hz = max(params.bin_khz * 1000 * 2, 5000)
        self._tracker = SignalTracker(match_tolerance_hz=tolerance_hz)
        self.noise_floor_db: float | None = None
        self.last_sweep_at: float | None = None
        self.current_freq_mhz: float | None = None
        self.signals: list[TrackedSignal] = []

    def ingest_hop(self, hop: Sweep, now: float) -> None:
        """Feed one hop's worth of bins as it arrives. Updates the live
        signal list and the current-frequency cursor immediately.
        """
        if not hop.freqs_hz:
            return
        floor = self.noise_floor_db if self.noise_floor_db is not None else estimate_noise_floor(hop.powers_db)
        detected = find_signals(hop, floor)
        self.signals = self._tracker.update(detected, now=now)
        self.current_freq_mhz = ((hop.freqs_hz[0] + hop.freqs_hz[-1]) / 2) / 1_000_000.0

    def complete_sweep(self, full_sweep: Sweep, now: float) -> None:
        """Call once a full pass across the requested range finishes, to
        refresh the noise floor estimate from the whole range's bins.
        """
        if not full_sweep.powers_db:
            return
        self.noise_floor_db = estimate_noise_floor(full_sweep.powers_db)
        self.last_sweep_at = now
