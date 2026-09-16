"""Parsing for rtl_power's CSV stdout format.

Each line is one hop, one integration interval:

    date, time, Hz_low, Hz_high, Hz_step, num_samples, dB, dB, dB, ...

rtl_power's own hop width is capped (~2.8 MHz), so a full pass over a wider
operator-requested range comes out as several lines sharing one (date,
time) stamp before the next timestamp starts. SweepAccumulator groups those
into one completed Sweep per full pass.
"""

from __future__ import annotations

from watchtower.scanner.base import RtlPowerRow, Sweep


def parse_rtl_power_line(line: str) -> RtlPowerRow:
    """Raises ValueError on anything that isn't a well-formed data line
    (blank lines, stray log/error output on stdout, truncated lines).
    """
    parts = [p.strip() for p in line.strip().split(",")]
    if len(parts) < 7:
        raise ValueError(f"malformed rtl_power line (too few fields): {line!r}")

    date, time_str, hz_low_s, hz_high_s, hz_step_s, num_samples_s = parts[:6]
    db_parts = [p for p in parts[6:] if p != ""]
    if not db_parts:
        raise ValueError(f"malformed rtl_power line (no power values): {line!r}")

    try:
        hz_low = int(float(hz_low_s))
        hz_high = int(float(hz_high_s))
        hz_step = int(float(hz_step_s))
        num_samples = int(float(num_samples_s))
        dbs = [float(p) for p in db_parts]
    except ValueError as e:
        raise ValueError(f"malformed rtl_power line: {line!r}") from e

    if hz_step <= 0:
        raise ValueError(f"malformed rtl_power line (non-positive hz_step): {line!r}")

    return RtlPowerRow(
        date=date,
        time=time_str,
        hz_low=hz_low,
        hz_high=hz_high,
        hz_step=hz_step,
        num_samples=num_samples,
        dbs=dbs,
    )


class SweepAccumulator:
    """Groups consecutive RtlPowerRows sharing one timestamp into a Sweep.

    Feed rows in stream order via ingest(). A Sweep is returned once a row
    with a *different* timestamp arrives (i.e. the previous group is known
    complete) — the currently-accumulating group is never returned early,
    since more hops for it may still be coming.
    """

    def __init__(self) -> None:
        self._current_key: str | None = None
        self._rows: list[RtlPowerRow] = []

    def ingest(self, row: RtlPowerRow) -> Sweep | None:
        if self._current_key is None:
            self._current_key = row.timestamp_key
            self._rows = [row]
            return None

        if row.timestamp_key == self._current_key:
            self._rows.append(row)
            return None

        completed = self._build_sweep(self._current_key, self._rows)
        self._current_key = row.timestamp_key
        self._rows = [row]
        return completed

    @staticmethod
    def _build_sweep(key: str, rows: list[RtlPowerRow]) -> Sweep:
        rows_sorted = sorted(rows, key=lambda r: r.hz_low)
        freqs: list[float] = []
        powers: list[float] = []
        for row in rows_sorted:
            for i, db in enumerate(row.dbs):
                freqs.append(float(row.hz_low + i * row.hz_step))
                powers.append(db)
        bin_hz = float(rows_sorted[0].hz_step) if rows_sorted else 0.0
        return Sweep(timestamp_key=key, freqs_hz=freqs, powers_db=powers, bin_hz=bin_hz)
