import json

from watchtower.gps.gpsd_client import GpsdClient


class Recorder:
    def __init__(self):
        self.tpv = []
        self.sky = []
        self.devices = []

    async def on_tpv(self, msg):
        self.tpv.append(msg)

    async def on_sky(self, msg):
        self.sky.append(msg)

    async def on_device(self, path):
        self.devices.append(path)


def make_client(recorder: Recorder) -> GpsdClient:
    return GpsdClient("127.0.0.1", 2947, recorder.on_tpv, recorder.on_sky, recorder.on_device)


async def test_handles_tpv_3d_fix():
    recorder = Recorder()
    client = make_client(recorder)
    line = json.dumps(
        {
            "class": "TPV",
            "mode": 3,
            "lat": 51.4769,
            "lon": -0.0005,
            "altHAE": 45.2,
            "track": 180.0,
            "speed": 0.1,
            "time": "2026-09-16T12:00:00.000Z",
        }
    ).encode() + b"\n"

    await client._handle_line(line)

    assert len(recorder.tpv) == 1
    tpv = recorder.tpv[0]
    assert tpv.mode == 3
    assert tpv.lat == 51.4769
    assert tpv.alt_m == 45.2


async def test_handles_tpv_no_fix():
    recorder = Recorder()
    client = make_client(recorder)
    line = json.dumps({"class": "TPV", "mode": 1}).encode() + b"\n"

    await client._handle_line(line)

    assert recorder.tpv[0].mode == 1
    assert recorder.tpv[0].lat is None


async def test_handles_sky_with_explicit_counts():
    recorder = Recorder()
    client = make_client(recorder)
    line = json.dumps({"class": "SKY", "uSat": 7, "nSat": 11, "satellites": []}).encode() + b"\n"

    await client._handle_line(line)

    assert recorder.sky[0].satellites_used == 7
    assert recorder.sky[0].satellites_visible == 11


async def test_handles_sky_computed_from_satellite_list():
    recorder = Recorder()
    client = make_client(recorder)
    satellites = [{"used": True}, {"used": True}, {"used": False}]
    line = json.dumps({"class": "SKY", "satellites": satellites}).encode() + b"\n"

    await client._handle_line(line)

    assert recorder.sky[0].satellites_used == 2
    assert recorder.sky[0].satellites_visible == 3


async def test_handles_devices_message():
    recorder = Recorder()
    client = make_client(recorder)
    line = json.dumps({"class": "DEVICES", "devices": [{"path": "/dev/ttyACM0"}]}).encode() + b"\n"

    await client._handle_line(line)

    assert recorder.devices == ["/dev/ttyACM0"]


async def test_ignores_malformed_json():
    recorder = Recorder()
    client = make_client(recorder)
    await client._handle_line(b"not json\n")
    assert recorder.tpv == []
    assert recorder.sky == []


async def test_ignores_unknown_message_class():
    recorder = Recorder()
    client = make_client(recorder)
    line = json.dumps({"class": "VERSION", "release": "3.25"}).encode() + b"\n"
    await client._handle_line(line)
    assert recorder.tpv == []
    assert recorder.sky == []
    assert recorder.devices == []
