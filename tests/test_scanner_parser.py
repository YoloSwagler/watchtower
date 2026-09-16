import pytest

from watchtower.scanner.parser import SweepAccumulator, parse_rtl_power_line


def test_parses_a_well_formed_line():
    line = "2024-01-01, 12:00:00, 88000000, 88100000, 25000, 4, -90.1, -89.5, -55.2, -91.0"
    row = parse_rtl_power_line(line)
    assert row.date == "2024-01-01"
    assert row.time == "12:00:00"
    assert row.hz_low == 88_000_000
    assert row.hz_high == 88_100_000
    assert row.hz_step == 25_000
    assert row.num_samples == 4
    assert row.dbs == [-90.1, -89.5, -55.2, -91.0]


def test_timestamp_key_combines_date_and_time():
    row = parse_rtl_power_line("2024-01-01, 12:00:00, 1, 2, 1, 1, -90.0")
    assert row.timestamp_key == "2024-01-01 12:00:00"


@pytest.mark.parametrize(
    "line",
    [
        "",
        "not,enough,fields",
        "2024-01-01, 12:00:00, notanumber, 2, 1, 1, -90.0",
        "2024-01-01, 12:00:00, 1, 2, 0, 1, -90.0",  # hz_step of 0
        "2024-01-01, 12:00:00, 1, 2, 1, 1",  # no power values
    ],
)
def test_rejects_malformed_lines(line):
    with pytest.raises(ValueError):
        parse_rtl_power_line(line)


def test_accumulator_groups_rows_by_timestamp_into_one_sweep():
    acc = SweepAccumulator()
    row1 = parse_rtl_power_line("2024-01-01, 12:00:00, 88000000, 88050000, 25000, 2, -90.0, -89.0")
    row2 = parse_rtl_power_line("2024-01-01, 12:00:00, 88050000, 88100000, 25000, 2, -88.0, -87.0")
    row3 = parse_rtl_power_line("2024-01-01, 12:00:04, 88000000, 88050000, 25000, 2, -91.0, -90.0")

    assert acc.ingest(row1) is None
    assert acc.ingest(row2) is None
    sweep = acc.ingest(row3)

    assert sweep is not None
    assert sweep.timestamp_key == "2024-01-01 12:00:00"
    assert sweep.freqs_hz == [88_000_000.0, 88_025_000.0, 88_050_000.0, 88_075_000.0]
    assert sweep.powers_db == [-90.0, -89.0, -88.0, -87.0]
    assert sweep.bin_hz == 25_000.0


def test_accumulator_sorts_hops_by_frequency_regardless_of_arrival_order():
    acc = SweepAccumulator()
    row_high = parse_rtl_power_line("2024-01-01, 12:00:00, 88050000, 88100000, 25000, 2, -88.0, -87.0")
    row_low = parse_rtl_power_line("2024-01-01, 12:00:00, 88000000, 88050000, 25000, 2, -90.0, -89.0")
    next_row = parse_rtl_power_line("2024-01-01, 12:00:04, 1, 2, 1, 1, -90.0")

    acc.ingest(row_high)
    acc.ingest(row_low)
    sweep = acc.ingest(next_row)

    assert sweep.freqs_hz == sorted(sweep.freqs_hz)


def test_accumulator_never_returns_the_still_accumulating_group():
    acc = SweepAccumulator()
    row = parse_rtl_power_line("2024-01-01, 12:00:00, 1, 2, 1, 1, -90.0")
    assert acc.ingest(row) is None
    assert acc.ingest(row) is None  # same timestamp again: still no completed sweep
