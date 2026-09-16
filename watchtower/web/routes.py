"""REST API: status polling and receiver control actions."""

from __future__ import annotations

import json

from aiohttp import web

from watchtower.appkeys import DEMO_MODE_KEY, GPS_KEY, SDR_KEY
from watchtower.gps.base import GPSSnapshot
from watchtower.sdr.base import (
    HF_ADVISORY_THRESHOLD_MHZ,
    DemodMode,
    ReceiverParams,
    SDRSnapshot,
)
from watchtower.system.health import check_tools


def _params_to_dict(params: ReceiverParams) -> dict:
    return {
        "frequency_mhz": params.frequency_mhz,
        "mode": params.mode.value,
        "device_index": params.device_index,
        "gain_db": params.gain_db,
        "squelch": params.squelch,
        "ppm": params.ppm,
    }


def _sdr_snapshot_to_dict(snap: SDRSnapshot) -> dict:
    return {
        "state": snap.state.value,
        "tools_available": snap.tools_available,
        "devices": [{"index": d.index, "name": d.name, "serial": d.serial} for d in snap.devices],
        "devices_stale": snap.devices_stale,
        "params": _params_to_dict(snap.params) if snap.params else None,
        "error": snap.error,
        "hf_advisory": snap.hf_advisory,
        "hf_advisory_threshold_mhz": HF_ADVISORY_THRESHOLD_MHZ,
    }


def _gps_snapshot_to_dict(snap: GPSSnapshot) -> dict:
    fix = None
    if snap.fix:
        fix = {
            "latitude": snap.fix.latitude,
            "longitude": snap.fix.longitude,
            "altitude_m": snap.fix.altitude_m,
            "track_deg": snap.fix.track_deg,
            "speed_mps": snap.fix.speed_mps,
            "time": snap.fix.time,
        }
    return {
        "status": snap.status.value,
        "fix": fix,
        "satellites_used": snap.satellites_used,
        "satellites_visible": snap.satellites_visible,
        "device": snap.device,
        "error": snap.error,
    }


async def get_status(request: web.Request) -> web.Response:
    sdr = request.app[SDR_KEY]
    gps = request.app[GPS_KEY]
    sdr_snap = await sdr.snapshot()
    gps_snap = await gps.snapshot()
    return web.json_response(
        {
            "demo": request.app[DEMO_MODE_KEY],
            "sdr": _sdr_snapshot_to_dict(sdr_snap),
            "gps": _gps_snapshot_to_dict(gps_snap),
            "tools": check_tools().as_dict(),
        }
    )


async def get_devices(request: web.Request) -> web.Response:
    sdr = request.app[SDR_KEY]
    devices = await sdr.refresh_devices()
    return web.json_response({"devices": [{"index": d.index, "name": d.name, "serial": d.serial} for d in devices]})


async def start_listening(request: web.Request) -> web.Response:
    sdr = request.app[SDR_KEY]
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"status": "error", "message": "Invalid JSON body"}, status=400)

    try:
        frequency_mhz = float(body["frequency_mhz"])
    except (KeyError, TypeError, ValueError):
        return web.json_response(
            {"status": "error", "message": "frequency_mhz is required and must be a number"}, status=400
        )
    if frequency_mhz <= 0:
        return web.json_response({"status": "error", "message": "frequency_mhz must be positive"}, status=400)

    mode_raw = str(body.get("mode", "nfm")).lower()
    try:
        mode = DemodMode(mode_raw)
    except ValueError:
        valid = ", ".join(m.value for m in DemodMode)
        return web.json_response({"status": "error", "message": f"Invalid mode. Use one of: {valid}"}, status=400)

    try:
        device_index = int(body.get("device_index", 0))
        gain_db = float(body.get("gain_db", 0.0))
        squelch = int(body.get("squelch", 0))
        ppm = int(body.get("ppm", 0))
    except (TypeError, ValueError):
        return web.json_response({"status": "error", "message": "Invalid numeric parameter"}, status=400)

    params = ReceiverParams(
        frequency_mhz=frequency_mhz,
        mode=mode,
        device_index=device_index,
        gain_db=gain_db,
        squelch=squelch,
        ppm=ppm,
    )
    ok, error = await sdr.start_listening(params)
    if not ok:
        return web.json_response({"status": "error", "message": error}, status=409)
    return web.json_response({"status": "listening", "params": _params_to_dict(params)})


async def stop_listening(request: web.Request) -> web.Response:
    sdr = request.app[SDR_KEY]
    await sdr.stop_listening()
    return web.json_response({"status": "stopped"})


def register_routes(app: web.Application) -> None:
    app.router.add_get("/api/status", get_status)
    app.router.add_get("/api/sdr/devices", get_devices)
    app.router.add_post("/api/sdr/listen/start", start_listening)
    app.router.add_post("/api/sdr/listen/stop", stop_listening)
