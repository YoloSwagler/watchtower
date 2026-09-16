"""Audio streaming endpoint: forwards the SDR manager's WAV byte stream."""

from __future__ import annotations

import asyncio

from aiohttp import web

from watchtower.appkeys import SDR_KEY
from watchtower.logging_setup import get_logger

logger = get_logger("web.audio")


async def stream_audio(request: web.Request) -> web.StreamResponse:
    sdr = request.app[SDR_KEY]
    response = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": "audio/wav",
            "Cache-Control": "no-cache, no-store",
            "X-Accel-Buffering": "no",
        },
    )
    await response.prepare(request)
    gen = sdr.audio_stream()
    try:
        async for chunk in gen:
            await response.write(chunk)
    except (ConnectionResetError, asyncio.CancelledError):
        logger.debug("audio stream client disconnected")
    finally:
        # `async for` does not close a generator it exits out of via an
        # exception (only normal exhaustion does) — without an explicit
        # aclose() here, a client that disconnects mid-stream leaves the
        # generator suspended forever. That's not just a leaked coroutine:
        # the manager's "one audio consumer at a time" guard (see
        # SDRManager.audio_stream) sets a flag in this generator's own
        # `finally` block, so leaving it unclosed would permanently wedge
        # that guard — no future listener could ever attach again, and the
        # shutdown-time pipe-drain guard would wrongly think a consumer was
        # still attached. Found by hand: rapid Start/Stop clicks in the
        # browser reproduced exactly this stuck state.
        await gen.aclose()
    return response
