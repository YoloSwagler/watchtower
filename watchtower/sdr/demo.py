"""Simulated SDR manager for development/demo without RTL-SDR hardware.

Implements the same SDRManagerBase interface as the real manager. Tuning,
state transitions, and device listing are fully simulated. Audio is a real
synthetic tone streamed through the same ffmpeg WAV-framing step the real
pipeline uses (so the browser audio path is genuinely exercised end to end),
tone frequency shifting a little with the tuned frequency purely as a
listening cue that "tuning" changed something. If ffmpeg isn't available on
the dev machine, demo mode still simulates tuning/state but reports no audio
— it never fakes bytes as if they came from ffmpeg.

Demo scanning generates synthetic sweep data (watchtower/scanner/
demo_source.py — the only place RF data is fabricated) and runs it through
the exact same noise-floor/signal-detection/tracking pipeline real scanning
uses, so the rest of the scan code path is genuinely exercised too.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import replace

from watchtower.logging_setup import get_logger
from watchtower.scanner import detect as scanner_detect
from watchtower.scanner.base import ScanParams, validate_scan_range
from watchtower.scanner.demo_source import generate_demo_sweep
from watchtower.scanner.tracker import SignalTracker
from watchtower.sdr import detect, process
from watchtower.sdr.base import (
    HF_ADVISORY_THRESHOLD_MHZ,
    ReceiverParams,
    ReceiverState,
    SDRDevice,
    SDRManagerBase,
    SDRSnapshot,
    ScanSnapshot,
    TrackedSignalView,
)
from watchtower.sdr.process import ManagedProcess

logger = get_logger("sdr.demo")

DEMO_DEVICES = [SDRDevice(index=0, name="Demo RTL2838UHIDIR (simulated)", serial="DEMO0001")]

DEMO_SCAN_TICK_SECONDS = 1.0


def _demo_tone_hz(frequency_mhz: float) -> int:
    # Purely cosmetic: shift the demo tone a bit based on the tuned
    # frequency's fractional part, so retuning is audibly noticeable.
    fractional = frequency_mhz - int(frequency_mhz)
    return 300 + int(fractional * 900)


class DemoSDRManager(SDRManagerBase):
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._state = ReceiverState.IDLE
        self._error: str | None = None

        # Listen pipeline
        self._params: ReceiverParams | None = None
        self._tone_proc: ManagedProcess | None = None
        self._audio_client_lock = asyncio.Lock()
        self._audio_client_active = False

        # Scan pipeline
        self._scan_params: ScanParams | None = None
        self._last_scan_params: ScanParams | None = None
        self._scan_tracker: SignalTracker | None = None
        self._scan_noise_floor_db: float | None = None
        self._scan_last_sweep_at: float | None = None
        self._scan_signals: list = []
        self._scan_task: asyncio.Task | None = None
        self._scan_start_time: float = 0.0

    # ---------------------------------------------------------------- status

    async def snapshot(self) -> SDRSnapshot:
        hf_advisory = bool(self._params and self._params.frequency_mhz < HF_ADVISORY_THRESHOLD_MHZ)
        scan_snapshot = None
        if self._scan_params is not None:
            scan_snapshot = ScanSnapshot(
                start_mhz=self._scan_params.start_mhz,
                end_mhz=self._scan_params.end_mhz,
                bin_khz=self._scan_params.bin_khz,
                noise_floor_db=self._scan_noise_floor_db,
                last_sweep_at=self._scan_last_sweep_at,
                signals=[
                    TrackedSignalView(
                        frequency_mhz=s.frequency_hz / 1_000_000,
                        power_db=s.power_db,
                        snr_db=s.snr_db,
                        bandwidth_khz=s.bandwidth_hz / 1000,
                        first_seen=s.first_seen,
                        last_seen=s.last_seen,
                    )
                    for s in self._scan_signals
                ],
            )
        return SDRSnapshot(
            state=self._state,
            tools_available=True,
            devices=list(DEMO_DEVICES),
            devices_stale=False,
            params=self._params,
            error=self._error,
            hf_advisory=hf_advisory,
            scan=scan_snapshot,
            can_resume_scan=self._last_scan_params is not None,
        )

    async def refresh_devices(self) -> list[SDRDevice]:
        return list(DEMO_DEVICES)

    # --------------------------------------------------------------- listen

    async def start_listening(self, params: ReceiverParams) -> tuple[bool, str | None]:
        async with self._lock:
            return await self._start_listening_locked(params)

    async def _start_listening_locked(self, params: ReceiverParams) -> tuple[bool, str | None]:
        await self._stop_scan_internal()
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

    async def set_gain(self, gain_db: float | None) -> tuple[bool, str | None]:
        async with self._lock:
            if self._state != ReceiverState.LISTENING or self._params is None:
                return False, "Not currently listening."
            new_params = replace(self._params, gain_db=gain_db)
            return await self._start_listening_locked(new_params)

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

    # ----------------------------------------------------------------- scan

    async def start_scan(self, params: ScanParams) -> tuple[bool, str | None]:
        async with self._lock:
            return await self._start_scan_locked(params)

    async def _start_scan_locked(self, params: ScanParams) -> tuple[bool, str | None]:
        await self._stop_internal()
        await self._stop_scan_internal()

        error = validate_scan_range(params.start_mhz, params.end_mhz, params.bin_khz)
        if error:
            self._state, self._error = ReceiverState.ERROR, error
            return False, error

        self._state = ReceiverState.STARTING
        self._error = None
        await asyncio.sleep(0.15)  # simulate tuning delay, matching start_listening

        match_tolerance_hz = max(params.bin_khz * 1000 * 2, 5000)
        self._scan_params = params
        self._scan_tracker = SignalTracker(match_tolerance_hz=match_tolerance_hz)
        self._scan_noise_floor_db = None
        self._scan_last_sweep_at = None
        self._scan_signals = []
        self._scan_start_time = time.monotonic()
        self._scan_task = asyncio.create_task(self._scan_loop(params), name="demo-scan-loop")
        self._state = ReceiverState.SCANNING
        logger.info(
            "demo scanning: %.4f-%.4f MHz bin=%.1fkHz", params.start_mhz, params.end_mhz, params.bin_khz
        )
        return True, None

    async def stop_scan(self) -> None:
        async with self._lock:
            await self._stop_scan_internal()

    async def resume_scan(self) -> tuple[bool, str | None]:
        async with self._lock:
            if self._last_scan_params is None:
                return False, "No previous scan to resume."
            return await self._start_scan_locked(self._last_scan_params)

    async def _stop_scan_internal(self) -> None:
        if self._scan_task:
            self._scan_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._scan_task
            self._scan_task = None
        if self._scan_params is not None:
            self._last_scan_params = self._scan_params
        self._scan_params = None
        self._scan_tracker = None
        self._scan_noise_floor_db = None
        self._scan_last_sweep_at = None
        self._scan_signals = []
        if self._state in (ReceiverState.SCANNING, ReceiverState.STARTING):
            self._state = ReceiverState.IDLE

    async def _scan_loop(self, params: ScanParams) -> None:
        try:
            while True:
                await asyncio.sleep(DEMO_SCAN_TICK_SECONDS)
                if self._scan_tracker is None:
                    return
                t = time.monotonic() - self._scan_start_time
                sweep = generate_demo_sweep(params, t)
                noise_floor = scanner_detect.estimate_noise_floor(sweep.powers_db)
                detected = scanner_detect.find_signals(sweep, noise_floor)
                now = time.time()
                self._scan_signals = self._scan_tracker.update(detected, now=now)
                self._scan_noise_floor_db = noise_floor
                self._scan_last_sweep_at = now
        except asyncio.CancelledError:
            pass

    # ------------------------------------------------------------- shutdown

    async def shutdown(self) -> None:
        async with self._lock:
            await self._stop_internal()
            await self._stop_scan_internal()
