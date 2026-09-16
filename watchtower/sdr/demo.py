"""Simulated SDR manager for development/demo without RTL-SDR hardware.

Implements the same SDRManagerBase interface as the real manager. Tuning,
state transitions, and device listing are fully simulated. Audio is a real
synthetic tone streamed through the same ffmpeg WAV-framing step the real
pipeline uses (so the browser audio path is genuinely exercised end to end),
tone frequency shifting a little with the tuned frequency purely as a
listening cue that "tuning" changed something. If ffmpeg isn't available on
the dev machine, demo mode still simulates tuning/state but reports no audio
— it never fakes bytes as if they came from ffmpeg.
"""

from __future__ import annotations

import asyncio
import contextlib

from watchtower.logging_setup import get_logger
from watchtower.sdr import detect, process
from watchtower.sdr.base import (
    HF_ADVISORY_THRESHOLD_MHZ,
    ReceiverParams,
    ReceiverState,
    SDRDevice,
    SDRManagerBase,
    SDRSnapshot,
)
from watchtower.sdr.process import ManagedProcess

logger = get_logger("sdr.demo")

DEMO_DEVICES = [SDRDevice(index=0, name="Demo RTL2838UHIDIR (simulated)", serial="DEMO0001")]


def _demo_tone_hz(frequency_mhz: float) -> int:
    # Purely cosmetic: shift the demo tone a bit based on the tuned
    # frequency's fractional part, so retuning is audibly noticeable.
    fractional = frequency_mhz - int(frequency_mhz)
    return 300 + int(fractional * 900)


class DemoSDRManager(SDRManagerBase):
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._state = ReceiverState.IDLE
        self._params: ReceiverParams | None = None
        self._error: str | None = None
        self._tone_proc: ManagedProcess | None = None
        self._audio_client_lock = asyncio.Lock()
        self._audio_client_active = False

    async def snapshot(self) -> SDRSnapshot:
        hf_advisory = bool(self._params and self._params.frequency_mhz < HF_ADVISORY_THRESHOLD_MHZ)
        return SDRSnapshot(
            state=self._state,
            tools_available=True,
            devices=list(DEMO_DEVICES),
            devices_stale=False,
            params=self._params,
            error=self._error,
            hf_advisory=hf_advisory,
        )

    async def refresh_devices(self) -> list[SDRDevice]:
        return list(DEMO_DEVICES)

    async def start_listening(self, params: ReceiverParams) -> tuple[bool, str | None]:
        async with self._lock:
            await self._stop_internal()
            self._state = ReceiverState.STARTING
            self._error = None
            await asyncio.sleep(0.15)  # simulate tuning delay

            ffmpeg_path = detect.find_ffmpeg()
            if ffmpeg_path:
                tone_hz = _demo_tone_hz(params.frequency_mhz)
                cmd = [
                    ffmpeg_path,
                    "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", f"sine=frequency={tone_hz}:sample_rate=48000",
                    "-f", "lavfi", "-i", "anoisesrc=color=pink:amplitude=0.03:sample_rate=48000",
                    "-filter_complex", "amix=inputs=2:duration=first:dropout_transition=0",
                    "-ac", "1", "-ar", "48000", "-acodec", "pcm_s16le", "-f", "wav", "pipe:1",
                ]  # fmt: skip
                try:
                    self._tone_proc = await process.spawn(*cmd, label="demo-tone")
                except OSError as e:
                    logger.warning("demo tone generator failed to start: %s", e)
                    self._tone_proc = None
            else:
                logger.info("ffmpeg not found; demo mode will simulate tuning without audio")

            self._params = params
            self._state = ReceiverState.LISTENING
            logger.info("demo listening: %.4f MHz mode=%s", params.frequency_mhz, params.mode.value)
            return True, None

    async def stop_listening(self) -> None:
        async with self._lock:
            await self._stop_internal()

    async def _stop_internal(self) -> None:
        # See SDRManager._stop_internal for why: drain the tone process's
        # own stdout ourselves if nobody else is attached, so its SIGTERM
        # flush handler can't stall on a full, unread pipe.
        if self._tone_proc:
            drain_task = None
            if self._tone_proc.proc.stdout and not self._audio_client_active:
                drain_task = asyncio.create_task(process.drain_discard(self._tone_proc.proc.stdout))
            await self._tone_proc.terminate()
            if drain_task:
                drain_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await drain_task
            self._tone_proc = None
        if self._state in (ReceiverState.LISTENING, ReceiverState.STARTING):
            self._state = ReceiverState.IDLE
        self._params = None

    async def audio_stream(self):
        """Single-consumer guard — see SDRManager.audio_stream for why."""
        proc = self._tone_proc
        if not proc or not proc.proc.stdout:
            return
        async with self._audio_client_lock:
            if self._audio_client_active:
                logger.warning("demo audio stream already has a consumer; refusing a second one")
                return
            self._audio_client_active = True
        try:
            stdout = proc.proc.stdout
            while True:
                chunk = await stdout.read(8192)
                if not chunk:
                    break
                yield chunk
        except (asyncio.CancelledError, ConnectionResetError):
            pass
        finally:
            self._audio_client_active = False

    async def shutdown(self) -> None:
        async with self._lock:
            await self._stop_internal()
