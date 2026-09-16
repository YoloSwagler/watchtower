from aiohttp.test_utils import TestClient, TestServer

from watchtower.app import create_app
from watchtower.config import AppConfig


async def demo_client(tmp_path=None, **overrides):
    kwargs = {"demo": True, **overrides}
    if tmp_path is not None:
        kwargs.setdefault("data_dir", str(tmp_path))
    app = create_app(AppConfig(**kwargs))
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


async def test_status_endpoint_reports_demo_mode():
    client = await demo_client()
    try:
        resp = await client.get("/api/status")
        assert resp.status == 200
        data = await resp.json()
        assert data["demo"] is True
        assert data["sdr"]["state"] == "idle"
        assert "tools" in data
        assert "gps" in data
    finally:
        await client.close()


async def test_index_page_served():
    client = await demo_client()
    try:
        resp = await client.get("/")
        assert resp.status == 200
        text = await resp.text()
        assert "Watchtower" in text
    finally:
        await client.close()


async def test_static_css_served():
    client = await demo_client()
    try:
        resp = await client.get("/static/css/app.css")
        assert resp.status == 200
    finally:
        await client.close()


async def test_start_listening_missing_frequency_returns_400():
    client = await demo_client()
    try:
        resp = await client.post("/api/sdr/listen/start", json={"mode": "nfm"})
        assert resp.status == 400
    finally:
        await client.close()


async def test_start_listening_invalid_mode_returns_400():
    client = await demo_client()
    try:
        resp = await client.post("/api/sdr/listen/start", json={"frequency_mhz": 100.0, "mode": "bogus"})
        assert resp.status == 400
    finally:
        await client.close()


async def test_start_then_stop_listening_demo_mode():
    client = await demo_client()
    try:
        start_resp = await client.post(
            "/api/sdr/listen/start", json={"frequency_mhz": 101.5, "mode": "wfm"}
        )
        assert start_resp.status == 200
        start_data = await start_resp.json()
        assert start_data["status"] == "listening"

        status_resp = await client.get("/api/status")
        status_data = await status_resp.json()
        assert status_data["sdr"]["state"] == "listening"
        assert status_data["sdr"]["params"]["frequency_mhz"] == 101.5

        stop_resp = await client.post("/api/sdr/listen/stop")
        assert stop_resp.status == 200

        status_resp = await client.get("/api/status")
        status_data = await status_resp.json()
        assert status_data["sdr"]["state"] == "idle"
    finally:
        await client.close()


async def test_devices_endpoint_returns_demo_device():
    client = await demo_client()
    try:
        resp = await client.get("/api/sdr/devices")
        assert resp.status == 200
        data = await resp.json()
        assert len(data["devices"]) == 1
    finally:
        await client.close()


async def test_start_listening_defaults_to_auto_gain():
    client = await demo_client()
    try:
        resp = await client.post("/api/sdr/listen/start", json={"frequency_mhz": 100.0, "mode": "nfm"})
        data = await resp.json()
        assert data["params"]["gain_db"] is None
    finally:
        await client.close()


async def test_set_gain_while_listening():
    client = await demo_client()
    try:
        await client.post("/api/sdr/listen/start", json={"frequency_mhz": 100.0, "mode": "nfm"})
        resp = await client.post("/api/sdr/listen/gain", json={"gain_db": 25})
        assert resp.status == 200
        data = await resp.json()
        assert data["gain_db"] == 25.0

        status = await (await client.get("/api/status")).json()
        assert status["sdr"]["params"]["gain_db"] == 25.0
        assert status["sdr"]["params"]["frequency_mhz"] == 100.0
    finally:
        await client.close()


async def test_set_gain_when_not_listening_returns_409():
    client = await demo_client()
    try:
        resp = await client.post("/api/sdr/listen/gain", json={"gain_db": 25})
        assert resp.status == 409
    finally:
        await client.close()


async def test_scan_start_stop_lifecycle():
    client = await demo_client()
    try:
        resp = await client.post("/api/sdr/scan/start", json={"start_mhz": 88.0, "end_mhz": 108.0})
        assert resp.status == 200
        data = await resp.json()
        assert data["status"] == "scanning"

        status = await (await client.get("/api/status")).json()
        assert status["sdr"]["state"] == "scanning"
        assert status["sdr"]["scan"]["start_mhz"] == 88.0

        resp = await client.post("/api/sdr/scan/stop")
        assert resp.status == 200

        status = await (await client.get("/api/status")).json()
        assert status["sdr"]["state"] == "idle"
        assert status["sdr"]["scan"] is None
        assert status["sdr"]["can_resume_scan"] is True
    finally:
        await client.close()


async def test_scan_start_invalid_range_returns_400():
    client = await demo_client()
    try:
        resp = await client.post("/api/sdr/scan/start", json={"start_mhz": 108.0, "end_mhz": 88.0})
        assert resp.status == 400
    finally:
        await client.close()


async def test_scan_start_missing_fields_returns_400():
    client = await demo_client()
    try:
        resp = await client.post("/api/sdr/scan/start", json={"start_mhz": 88.0})
        assert resp.status == 400
    finally:
        await client.close()


async def test_selecting_a_signal_stops_scan_and_starts_listening():
    client = await demo_client()
    try:
        await client.post("/api/sdr/scan/start", json={"start_mhz": 88.0, "end_mhz": 108.0})
        resp = await client.post("/api/sdr/listen/start", json={"frequency_mhz": 95.3, "mode": "wfm"})
        assert resp.status == 200

        status = await (await client.get("/api/status")).json()
        assert status["sdr"]["state"] == "listening"
        assert status["sdr"]["scan"] is None
        assert status["sdr"]["can_resume_scan"] is True
    finally:
        await client.close()


async def test_resume_scan_after_stopping_listening():
    client = await demo_client()
    try:
        await client.post("/api/sdr/scan/start", json={"start_mhz": 88.0, "end_mhz": 108.0})
        await client.post("/api/sdr/listen/start", json={"frequency_mhz": 95.3, "mode": "wfm"})
        await client.post("/api/sdr/listen/stop")

        resp = await client.post("/api/sdr/scan/resume")
        assert resp.status == 200

        status = await (await client.get("/api/status")).json()
        assert status["sdr"]["state"] == "scanning"
    finally:
        await client.close()


async def test_resume_scan_without_prior_scan_returns_409():
    client = await demo_client()
    try:
        resp = await client.post("/api/sdr/scan/resume")
        assert resp.status == 409
    finally:
        await client.close()


async def test_bookmark_crud_lifecycle(tmp_path):
    client = await demo_client(tmp_path)
    try:
        resp = await client.get("/api/bookmarks")
        assert (await resp.json())["bookmarks"] == []

        resp = await client.post(
            "/api/bookmarks",
            json={"name": "NOAA Weather", "frequency_mhz": 162.475, "mode": "nfm", "note": "channel 1"},
        )
        assert resp.status == 201
        bookmark = (await resp.json())["bookmark"]
        assert bookmark["name"] == "NOAA Weather"
        assert bookmark["gain_db"] is None
        bookmark_id = bookmark["id"]

        resp = await client.get("/api/bookmarks")
        assert len(( await resp.json())["bookmarks"]) == 1

        resp = await client.put(f"/api/bookmarks/{bookmark_id}", json={"name": "NOAA WX Renamed"})
        assert resp.status == 200
        assert (await resp.json())["bookmark"]["name"] == "NOAA WX Renamed"

        resp = await client.delete(f"/api/bookmarks/{bookmark_id}")
        assert resp.status == 200

        resp = await client.get("/api/bookmarks")
        assert (await resp.json())["bookmarks"] == []
    finally:
        await client.close()


async def test_bookmark_create_requires_name_and_frequency(tmp_path):
    client = await demo_client(tmp_path)
    try:
        resp = await client.post("/api/bookmarks", json={"frequency_mhz": 100.0, "mode": "nfm"})
        assert resp.status == 400

        resp = await client.post("/api/bookmarks", json={"name": "X", "mode": "nfm"})
        assert resp.status == 400
    finally:
        await client.close()


async def test_bookmark_update_unknown_id_returns_404(tmp_path):
    client = await demo_client(tmp_path)
    try:
        resp = await client.put("/api/bookmarks/does-not-exist", json={"name": "X"})
        assert resp.status == 404
    finally:
        await client.close()


async def test_bookmark_delete_unknown_id_returns_404(tmp_path):
    client = await demo_client(tmp_path)
    try:
        resp = await client.delete("/api/bookmarks/does-not-exist")
        assert resp.status == 404
    finally:
        await client.close()


async def test_bookmarks_persist_across_app_restarts(tmp_path):
    client1 = await demo_client(tmp_path)
    try:
        await client1.post("/api/bookmarks", json={"name": "Local FM", "frequency_mhz": 101.5, "mode": "wfm"})
    finally:
        await client1.close()

    client2 = await demo_client(tmp_path)
    try:
        resp = await client2.get("/api/bookmarks")
        bookmarks = (await resp.json())["bookmarks"]
        assert len(bookmarks) == 1
        assert bookmarks[0]["name"] == "Local FM"
    finally:
        await client2.close()
