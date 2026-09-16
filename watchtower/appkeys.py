"""Typed aiohttp Application keys, shared by app.py and the route modules.

Kept in their own module (rather than defined in app.py) so route modules
can import the keys without creating a circular import with app.py, which
imports the route modules to register them.
"""

from __future__ import annotations

from aiohttp import web

from watchtower.config import AppConfig
from watchtower.gps.base import GPSManagerBase
from watchtower.sdr.base import SDRManagerBase

CONFIG_KEY: web.AppKey[AppConfig] = web.AppKey("config", AppConfig)
DEMO_MODE_KEY: web.AppKey[bool] = web.AppKey("demo_mode", bool)
SDR_KEY: web.AppKey[SDRManagerBase] = web.AppKey("sdr", SDRManagerBase)
GPS_KEY: web.AppKey[GPSManagerBase] = web.AppKey("gps", GPSManagerBase)
