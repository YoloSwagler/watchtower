from aiohttp.test_utils import TestClient, TestServer

from watchtower.app import create_app
from watchtower.config import AppConfig


async def demo_client():
    app = create_app(AppConfig(demo=True))
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
