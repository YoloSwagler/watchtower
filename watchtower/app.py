"""aiohttp application factory and entrypoint."""

from __future__ import annotations

import pathlib

from aiohttp import web

from watchtower.appkeys import CONFIG_KEY, DEMO_MODE_KEY, GPS_KEY, SDR_KEY
from watchtower.config import AppConfig
from watchtower.gps.demo import DemoGPSManager
from watchtower.gps.manager import GPSManager
from watchtower.logging_setup import configure_logging, get_logger
from watchtower.sdr.demo import DemoSDRManager
from watchtower.sdr.manager import SDRManager
from watchtower.web import audio as audio_routes
from watchtower.web import routes as api_routes

logger = get_logger("app")

STATIC_DIR = pathlib.Path(__file__).parent / "web" / "static"


def create_app(config: AppConfig) -> web.Application:
    app = web.Application()
    app[CONFIG_KEY] = config
    app[DEMO_MODE_KEY] = config.demo

    if config.demo:
        app[SDR_KEY] = DemoSDRManager()
        app[GPS_KEY] = DemoGPSManager()
    else:
        app[SDR_KEY] = SDRManager()
        app[GPS_KEY] = GPSManager(host=config.gpsd_host, port=config.gpsd_port)

    api_routes.register_routes(app)
    app.router.add_get("/api/audio/stream", audio_routes.stream_audio)

    async def index(request: web.Request) -> web.FileResponse:
        return web.FileResponse(STATIC_DIR / "index.html")

    app.router.add_get("/", index)
    app.router.add_static("/static/", STATIC_DIR, name="static")

    app.on_startup.append(_on_startup)
    app.on_cleanup.append(_on_cleanup)
    return app


async def _on_startup(app: web.Application) -> None:
    await app[GPS_KEY].start()
    logger.info("watchtower started (demo=%s)", app[DEMO_MODE_KEY])


async def _on_cleanup(app: web.Application) -> None:
    await app[GPS_KEY].stop()
    await app[SDR_KEY].shutdown()
    logger.info("watchtower shut down cleanly")


def run(config: AppConfig | None = None) -> None:
    config = config or AppConfig()
    configure_logging(config.verbose)
    app = create_app(config)
    logger.info("serving on http://%s:%s (demo=%s)", config.host, config.port, config.demo)
    web.run_app(app, host=config.host, port=config.port, print=None)
