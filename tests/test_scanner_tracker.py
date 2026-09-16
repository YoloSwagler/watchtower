from watchtower.scanner.base import DetectedSignal
from watchtower.scanner.tracker import SignalTracker


def _sig(freq_hz: float, power_db: float = -60.0, snr_db: float = 30.0, bw_hz: float = 25_000.0) -> DetectedSignal:
    return DetectedSignal(frequency_hz=freq_hz, power_db=power_db, snr_db=snr_db, bandwidth_hz=bw_hz)


def test_new_signal_gets_first_seen_equal_to_last_seen():
    tracker = SignalTracker(match_tolerance_hz=10_000)
    result = tracker.update([_sig(100_000_000)], now=1000.0)
    assert len(result) == 1
    assert result[0].first_seen == 1000.0
    assert result[0].last_seen == 1000.0


def test_re_observed_signal_keeps_first_seen_but_advances_last_seen():
    tracker = SignalTracker(match_tolerance_hz=10_000)
    tracker.update([_sig(100_000_000)], now=1000.0)
    result = tracker.update([_sig(100_002_000)], now=1010.0)  # within tolerance, drifted slightly

    assert len(result) == 1
    assert result[0].first_seen == 1000.0
    assert result[0].last_seen == 1010.0
    assert result[0].frequency_hz == 100_002_000  # tracks the latest reading's frequency


def test_signal_outside_tolerance_is_a_new_entry():
    tracker = SignalTracker(match_tolerance_hz=5_000)
    tracker.update([_sig(100_000_000)], now=1000.0)
    result = tracker.update([_sig(100_050_000)], now=1001.0)

    assert len(result) == 2


def test_unobserved_signal_survives_until_ttl_expires():
    tracker = SignalTracker(match_tolerance_hz=5_000, ttl_seconds=30.0)
    tracker.update([_sig(100_000_000)], now=1000.0)

    still_there = tracker.update([], now=1020.0)  # 20s later, within TTL
    assert len(still_there) == 1
    assert still_there[0].last_seen == 1000.0  # not re-observed, last_seen unchanged

    gone = tracker.update([], now=1031.0)  # 31s later, past TTL
    assert gone == []


def test_current_returns_the_same_snapshot_as_the_last_update():
    tracker = SignalTracker(match_tolerance_hz=5_000)
    result = tracker.update([_sig(100_000_000), _sig(101_000_000)], now=1000.0)
    assert tracker.current() == result


def test_results_are_sorted_by_frequency():
    tracker = SignalTracker(match_tolerance_hz=5_000)
    result = tracker.update([_sig(101_000_000), _sig(100_000_000), _sig(102_000_000)], now=1000.0)
    assert [s.frequency_hz for s in result] == [100_000_000, 101_000_000, 102_000_000]
