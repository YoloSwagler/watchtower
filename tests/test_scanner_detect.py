from watchtower.scanner.base import Sweep
from watchtower.scanner.detect import estimate_noise_floor, find_signals


def test_noise_floor_is_the_median_power():
    assert estimate_noise_floor([-90.0, -91.0, -89.0, -90.0, -90.0]) == -90.0


def test_noise_floor_of_empty_sweep_is_zero():
    assert estimate_noise_floor([]) == 0.0


def _sweep(powers: list[float], bin_hz: float = 25_000.0) -> Sweep:
    freqs = [88_000_000.0 + i * bin_hz for i in range(len(powers))]
    return Sweep(timestamp_key="t", freqs_hz=freqs, powers_db=powers, bin_hz=bin_hz)


def test_flat_noise_produces_no_signals():
    powers = [-90.0] * 20
    sweep = _sweep(powers)
    nf = estimate_noise_floor(powers)
    assert find_signals(sweep, nf) == []


def test_single_peak_is_detected_with_correct_center_and_snr():
    powers = [-90.0] * 10
    powers[5] = -60.0  # 30 dB above the -90 floor
    sweep = _sweep(powers)
    nf = estimate_noise_floor(powers)

    signals = find_signals(sweep, nf, threshold_db=10.0)

    assert len(signals) == 1
    sig = signals[0]
    assert sig.frequency_hz == sweep.freqs_hz[5]
    assert sig.power_db == -60.0
    assert sig.snr_db == -60.0 - nf


def test_adjacent_active_bins_group_into_one_signal_at_the_peak():
    powers = [-90.0] * 10
    powers[4] = -70.0
    powers[5] = -60.0  # peak
    powers[6] = -68.0
    sweep = _sweep(powers)
    nf = estimate_noise_floor(powers)

    signals = find_signals(sweep, nf, threshold_db=10.0)

    assert len(signals) == 1
    assert signals[0].frequency_hz == sweep.freqs_hz[5]
    assert signals[0].power_db == -60.0
    # bandwidth spans bins 4-6 inclusive, plus one bin width
    assert signals[0].bandwidth_hz == (sweep.freqs_hz[6] - sweep.freqs_hz[4]) + sweep.bin_hz


def test_two_separated_peaks_are_two_signals():
    powers = [-90.0] * 20
    powers[3] = -55.0
    powers[15] = -50.0
    sweep = _sweep(powers)
    nf = estimate_noise_floor(powers)

    signals = find_signals(sweep, nf, threshold_db=10.0)

    assert sorted(s.frequency_hz for s in signals) == [sweep.freqs_hz[3], sweep.freqs_hz[15]]


def test_empty_sweep_has_no_signals():
    sweep = Sweep(timestamp_key="t", freqs_hz=[], powers_db=[], bin_hz=25_000.0)
    assert find_signals(sweep, -90.0) == []


def test_threshold_controls_sensitivity():
    powers = [-90.0] * 10
    powers[5] = -83.0  # only 7 dB above floor
    sweep = _sweep(powers)
    nf = estimate_noise_floor(powers)

    assert find_signals(sweep, nf, threshold_db=10.0) == []
    assert len(find_signals(sweep, nf, threshold_db=5.0)) == 1
