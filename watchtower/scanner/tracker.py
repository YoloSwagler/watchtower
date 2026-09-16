"""Turns per-sweep DetectedSignals into a stable, decaying tracked list.

Without this, the signal list in the UI would flicker and fully regenerate
every sweep cycle (a few seconds), and lose "how long has this been active"
information. SignalTracker is a plain in-memory dict, bounded by TTL-based
expiry — no raw sweep data is ever kept here, only the current tracked
signals (see ARCHITECTURE.md, "no unbounded in-memory sweep histories").
"""

from __future__ import annotations

from watchtower.scanner.base import SIGNAL_TTL_SECONDS, DetectedSignal, TrackedSignal


class SignalTracker:
    def __init__(self, match_tolerance_hz: float, ttl_seconds: float = SIGNAL_TTL_SECONDS) -> None:
        self._tolerance = match_tolerance_hz
        self._ttl = ttl_seconds
        self._tracked: dict[float, TrackedSignal] = {}

    def update(self, detected: list[DetectedSignal], now: float) -> list[TrackedSignal]:
        """Match each detected signal to the closest existing tracked entry
        within tolerance (extending its first_seen), or start a new one;
        drop existing entries neither matched this round nor re-observed
        within ttl_seconds. Returns the resulting list, sorted by frequency.
        """
        remaining_existing = dict(self._tracked)
        updated: dict[float, TrackedSignal] = {}

        for sig in detected:
            match_key = None
            best_dist = None
            for key, existing in remaining_existing.items():
                dist = abs(existing.frequency_hz - sig.frequency_hz)
                if dist <= self._tolerance and (best_dist is None or dist < best_dist):
                    best_dist = dist
                    match_key = key
            first_seen = now
            if match_key is not None:
                first_seen = remaining_existing.pop(match_key).first_seen

            entry = TrackedSignal(
                frequency_hz=sig.frequency_hz,
                power_db=sig.power_db,
                snr_db=sig.snr_db,
                bandwidth_hz=sig.bandwidth_hz,
                first_seen=first_seen,
                last_seen=now,
            )
            updated[entry.frequency_hz] = entry

        for key, existing in remaining_existing.items():
            if now - existing.last_seen <= self._ttl:
                updated.setdefault(key, existing)

        self._tracked = updated
        return sorted(self._tracked.values(), key=lambda t: t.frequency_hz)

    def current(self) -> list[TrackedSignal]:
        return sorted(self._tracked.values(), key=lambda t: t.frequency_hz)
