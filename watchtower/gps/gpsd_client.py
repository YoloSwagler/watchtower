"""Minimal asyncio client for gpsd's JSON line protocol.

gpsd speaks newline-delimited JSON over a plain TCP socket (default
127.0.0.1:2947). We only need a handful of message classes:

  VERSION  - sent on connect, informational
  DEVICES  - lists the serial device(s) gpsd is reading
  TPV      - Time-Position-Velocity: the current fix (or lack of one)
  SKY      - satellite visibility/use

This intentionally does not depend on the `gps`/`gps3`/`gpsdclient`
third-party packages — the protocol is small, stable, and documented
(gpsd_json(5)), and hand-rolling ~100 lines keeps the dependency list at
just `aiohttp`.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Awaitable, Callable

from watchtower.logging_setup import get_logger

logger = get_logger("gps.gpsd_client")

WATCH_COMMAND = b'?WATCH={"enable":true,"json":true}\n'
CONNECT_TIMEOUT = 3.0
READ_TIMEOUT = 10.0  # gpsd sends at least a TPV or keepalive-ish message regularly when watching


@dataclass
class TPVMessage:
    mode: int
    lat: float | None
    lon: float | None
    alt_m: float | None
    track_deg: float | None
    speed_mps: float | None
    time: str | None


@dataclass
class SkyMessage:
    satellites_used: int
    satellites_visible: int


class GpsdClient:
    """One connection attempt + read loop. Callers should loop construction
    of this class for reconnect behaviour (see GPSManager).
    """

    def __init__(
        self,
        host: str,
        port: int,
        on_tpv: Callable[[TPVMessage], Awaitable[None]],
        on_sky: Callable[[SkyMessage], Awaitable[None]],
        on_device: Callable[[str], Awaitable[None]],
    ) -> None:
        self._host = host
        self._port = port
        self._on_tpv = on_tpv
        self._on_sky = on_sky
        self._on_device = on_device
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None

    async def connect(self) -> None:
        self._reader, self._writer = await asyncio.wait_for(
            asyncio.open_connection(self._host, self._port), timeout=CONNECT_TIMEOUT
        )
        self._writer.write(WATCH_COMMAND)
        await self._writer.drain()

    async def close(self) -> None:
        if self._writer:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except Exception:
                pass
        self._reader = None
        self._writer = None

    async def run_forever(self) -> None:
        """Read and dispatch messages until the connection closes or errors."""
        assert self._reader is not None
        while True:
            line = await asyncio.wait_for(self._reader.readline(), timeout=READ_TIMEOUT)
            if not line:
                break
            await self._handle_line(line)

    async def _handle_line(self, line: bytes) -> None:
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            logger.debug("ignoring malformed gpsd line: %r", line[:200])
            return

        msg_class = msg.get("class")
        if msg_class == "TPV":
            await self._on_tpv(
                TPVMessage(
                    mode=int(msg.get("mode", 0)),
                    lat=msg.get("lat"),
                    lon=msg.get("lon"),
                    alt_m=msg.get("altHAE", msg.get("alt")),
                    track_deg=msg.get("track"),
                    speed_mps=msg.get("speed"),
                    time=msg.get("time"),
                )
            )
        elif msg_class == "SKY":
            satellites = msg.get("satellites", [])
            used = msg.get("uSat")
            visible = msg.get("nSat")
            if used is None:
                used = sum(1 for s in satellites if s.get("used"))
            if visible is None:
                visible = len(satellites)
            await self._on_sky(SkyMessage(satellites_used=used, satellites_visible=visible))
        elif msg_class == "DEVICES":
            devices = msg.get("devices", [])
            if devices:
                await self._on_device(devices[0].get("path", ""))
