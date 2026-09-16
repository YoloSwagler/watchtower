from watchtower.sdr.detect import parse_rtl_test_output

SAMPLE_ONE_DEVICE = """\
Found 1 device(s):
  0:  Realtek, RTL2838UHIDIR, SN: 00000001

Using device 0: Generic RTL2832U OEM
Found Rafael Micro R820T tuner
"""

SAMPLE_TWO_DEVICES = """\
Found 2 device(s):
  0:  Realtek, RTL2838UHIDIR, SN: 00000001
  1:  Realtek, RTL2838UHIDIR, SN: 00000002

Using device 0: Generic RTL2832U OEM
"""

SAMPLE_NO_DEVICES = "No supported devices found.\n"


def test_parses_single_device():
    devices = parse_rtl_test_output(SAMPLE_ONE_DEVICE)
    assert len(devices) == 1
    assert devices[0].index == 0
    assert devices[0].name == "Realtek, RTL2838UHIDIR"
    assert devices[0].serial == "00000001"


def test_parses_multiple_devices():
    devices = parse_rtl_test_output(SAMPLE_TWO_DEVICES)
    assert [d.index for d in devices] == [0, 1]
    assert devices[1].serial == "00000002"


def test_no_devices_found():
    devices = parse_rtl_test_output(SAMPLE_NO_DEVICES)
    assert devices == []


def test_empty_output():
    assert parse_rtl_test_output("") == []
