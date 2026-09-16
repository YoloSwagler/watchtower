"""REST API: status polling and receiver/scanner/bookmark control actions."""

from __future__ import annotations

import json

from aiohttp import web

from watchtower.appkeys import BOOKMARKS_KEY, DEMO_MODE_KEY, GPS_KEY, SDR_KEY
from watchtower.gps.base import GPSSnapshot
from watchtower.scanner.base import DEFAULT_BIN_KHZ, ScanParams, validate_scan_range
from watchtower.sdr.base import (
    HF_ADVISORY_THRESHOLD_MHZ,
    DemodMode,
    ReceiverParams,
    SDRSnapshot,
    ScanSnapshot,
)
from watchtower.storage.bookmarks import Bookmark
from watchtower.system.health import check_tools

_MAX_NOTE_LENGTH = 280


def _parse_gain_db(raw) -> float | None:
    """None/missing/"auto" -> None (automatic tuner gain). May raise
    ValueError/TypeError for anything else that isn't a number.
    """
    if raw is None:
        return None
    if isinstance(raw, str) and raw.strip().lower() == "auto":
        return None
    return float(raw)


def _params_to_dict(params: ReceiverParams) -> dict:
    return {
        "frequency_mhz": params.frequency_mhz,
        "mode": params.mode.value,
        "device_index": params.device_index,
        "gain_db": params.gain_db,
        "squelch": params.squelch,
        "ppm": params.ppm,
    }


def _scan_params_to_dict(params: ScanParams) -> dict:
    return {
        "start_mhz": params.start_mhz,
        "end_mhz": params.end_mhz,
        "bin_khz": params.bin_khz,
        "device_index": params.device_index,
        "gain_db": params.gain_db,
        "ppm": params.ppm,
    }


def _scan_snapshot_to_dict(scan: ScanSnapshot | None) -> dict | None:
    if scan is None:
        return None
    return {
        "start_mhz": scan.start_mhz,
        "end_mhz": scan.end_mhz,
        "bin_khz": scan.bin_khz,
        "noise_floor_db": scan.noise_floor_db,
        "last_sweep_at": scan.last_sweep_at,
        "signals": [
            {
                "frequency_mhz": s.frequency_mhz,
                "power_db": s.power_db,
                "snr_db": s.snr_db,
                "bandwidth_khz": s.bandwidth_khz,
                "first_seen": s.first_seen,
                "last_seen": s.last_seen,
            }
            for s in scan.signals
        ],
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
        "scan": _scan_snapshot_to_dict(snap.scan),
        "can_resume_scan": snap.can_resume_scan,
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


def _bookmark_to_dict(b: Bookmark) -> dict:
    return {
        "id": b.id,
        "name": b.name,
        "frequency_mhz": b.frequency_mhz,
        "mode": b.mode,
        "gain_db": b.gain_db,
        "squelch": b.squelch,
        "note": b.note,
        "created_at": b.created_at,
        "updated_at": b.updated_at,
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
        gain_db = _parse_gain_db(body.get("gain_db"))
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


async def set_gain(request: web.Request) -> web.Response:
    sdr = request.app[SDR_KEY]
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"status": "error", "message": "Invalid JSON body"}, status=400)
    try:
        gain_db = _parse_gain_db(body.get("gain_db"))
    except (TypeError, ValueError):
        return web.json_response({"status": "error", "message": "gain_db must be a number, null, or \"auto\""}, status=400)

    ok, error = await sdr.set_gain(gain_db)
    if not ok:
        return web.json_response({"status": "error", "message": error}, status=409)
    return web.json_response({"status": "ok", "gain_db": gain_db})


async def start_scan(request: web.Request) -> web.Response:
    sdr = request.app[SDR_KEY]
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"status": "error", "message": "Invalid JSON body"}, status=400)

    try:
        start_mhz = float(body["start_mhz"])
        end_mhz = float(body["end_mhz"])
    except (KeyError, TypeError, ValueError):
        return web.json_response(
            {"status": "error", "message": "start_mhz and end_mhz are required and must be numbers"}, status=400
        )

    try:
        bin_khz = float(body.get("bin_khz", DEFAULT_BIN_KHZ))
        device_index = int(body.get("device_index", 0))
        ppm = int(body.get("ppm", 0))
        gain_db = _parse_gain_db(body.get("gain_db"))
    except (TypeError, ValueError):
        return web.json_response({"status": "error", "message": "Invalid numeric parameter"}, status=400)

    error = validate_scan_range(start_mhz, end_mhz, bin_khz)
    if error:
        return web.json_response({"status": "error", "message": error}, status=400)

    params = ScanParams(
        start_mhz=start_mhz, end_mhz=end_mhz, bin_khz=bin_khz, device_index=device_index, gain_db=gain_db, ppm=ppm
    )
    ok, error = await sdr.start_scan(params)
    if not ok:
        return web.json_response({"status": "error", "message": error}, status=409)
    return web.json_response({"status": "scanning", "params": _scan_params_to_dict(params)})


async def stop_scan(request: web.Request) -> web.Response:
    sdr = request.app[SDR_KEY]
    await sdr.stop_scan()
    return web.json_response({"status": "stopped"})


async def resume_scan(request: web.Request) -> web.Response:
    sdr = request.app[SDR_KEY]
    ok, error = await sdr.resume_scan()
    if not ok:
        return web.json_response({"status": "error", "message": error}, status=409)
    return web.json_response({"status": "scanning"})


async def list_bookmarks(request: web.Request) -> web.Response:
    store = request.app[BOOKMARKS_KEY]
    bookmarks = await store.list()
    return web.json_response({"bookmarks": [_bookmark_to_dict(b) for b in bookmarks]})


async def create_bookmark(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"status": "error", "message": "Invalid JSON body"}, status=400)

    name = str(body.get("name", "")).strip()
    if not name:
        return web.json_response({"status": "error", "message": "name is required"}, status=400)

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
        gain_db = _parse_gain_db(body.get("gain_db"))
        squelch = int(body.get("squelch", 0))
    except (TypeError, ValueError):
        return web.json_response({"status": "error", "message": "Invalid numeric parameter"}, status=400)

    note = str(body.get("note", ""))[:_MAX_NOTE_LENGTH]

    store = request.app[BOOKMARKS_KEY]
    bookmark = await store.create(
        name=name, frequency_mhz=frequency_mhz, mode=mode.value, gain_db=gain_db, squelch=squelch, note=note
    )
    return web.json_response({"bookmark": _bookmark_to_dict(bookmark)}, status=201)


async def update_bookmark(request: web.Request) -> web.Response:
    bookmark_id = request.match_info["id"]
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"status": "error", "message": "Invalid JSON body"}, status=400)

    fields: dict = {}
    if "name" in body:
        name = str(body["name"]).strip()
        if not name:
            return web.json_response({"status": "error", "message": "name cannot be empty"}, status=400)
        fields["name"] = name
    if "frequency_mhz" in body:
        try:
            frequency_mhz = float(body["frequency_mhz"])
        except (TypeError, ValueError):
            return web.json_response({"status": "error", "message": "frequency_mhz must be a number"}, status=400)
        if frequency_mhz <= 0:
            return web.json_response({"status": "error", "message": "frequency_mhz must be positive"}, status=400)
        fields["frequency_mhz"] = frequency_mhz
    if "mode" in body:
        try:
            fields["mode"] = DemodMode(str(body["mode"]).lower()).value
        except ValueError:
            valid = ", ".join(m.value for m in DemodMode)
            return web.json_response({"status": "error", "message": f"Invalid mode. Use one of: {valid}"}, status=400)
    if "gain_db" in body:
        try:
            fields["gain_db"] = _parse_gain_db(body["gain_db"])
        except (TypeError, ValueError):
            return web.json_response({"status": "error", "message": "Invalid gain_db"}, status=400)
    if "squelch" in body:
        try:
            fields["squelch"] = int(body["squelch"])
        except (TypeError, ValueError):
            return web.json_response({"status": "error", "message": "Invalid squelch"}, status=400)
    if "note" in body:
        fields["note"] = str(body["note"])[:_MAX_NOTE_LENGTH]

    store = request.app[BOOKMARKS_KEY]
    updated = await store.update(bookmark_id, **fields)
    if updated is None:
        return web.json_response({"status": "error", "message": "Bookmark not found"}, status=404)
    return web.json_response({"bookmark": _bookmark_to_dict(updated)})


async def delete_bookmark(request: web.Request) -> web.Response:
    bookmark_id = request.match_info["id"]
    store = request.app[BOOKMARKS_KEY]
    ok = await store.delete(bookmark_id)
    if not ok:
        return web.json_response({"status": "error", "message": "Bookmark not found"}, status=404)
    return web.json_response({"status": "deleted"})


def register_routes(app: web.Application) -> None:
    app.router.add_get("/api/status", get_status)
    app.router.add_get("/api/sdr/devices", get_devices)
    app.router.add_post("/api/sdr/listen/start", start_listening)
    app.router.add_post("/api/sdr/listen/stop", stop_listening)
    app.router.add_post("/api/sdr/listen/gain", set_gain)
    app.router.add_post("/api/sdr/scan/start", start_scan)
    app.router.add_post("/api/sdr/scan/stop", stop_scan)
    app.router.add_post("/api/sdr/scan/resume", resume_scan)
    app.router.add_get("/api/bookmarks", list_bookmarks)
    app.router.add_post("/api/bookmarks", create_bookmark)
    app.router.add_put("/api/bookmarks/{id}", update_bookmark)
    app.router.add_delete("/api/bookmarks/{id}", delete_bookmark)
