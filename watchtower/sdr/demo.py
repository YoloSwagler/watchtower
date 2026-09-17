"""Simulated SDR manager for development/demo without RTL-SDR hardware.

Implements the same SDRManagerBase interface as the real manager. Tuning,
state transitions, and device listing are fully simulated. Audio is a real
synthetic tone streamed through the same ffmpeg WAV-framing step the real
pipeline uses (so the browser audio path is genuinely exercised end to end),
tone frequency shifting a little with the tuned frequency purely as a
listening cue that "tuning" changed something. If ffmpeg isn't available on
the dev machine, demo mode still simulates tuning/state but reports no audio
— it never fakes bytes as if they came from ffmpeg.

Demo scanning steps through the requested range in hop-sized chunks (see
scanner/demo_source.py, the only place RF data is fabricated) and feeds
each synthetic hop through the same ScanEngine real scanning uses, so the
rest of the scan code path — including signals appearing incrementally as
hops arrive — is genuinely exercised too, not separately faked.

Like the real manager, scan results (the ScanEngine) survive stop_scan();
see sdr/manager.py's module docstring.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import replace

from watchtower.logging_setup import get_logger
from watchtower.scanner import demo_source
from watchtower.scanner.base import ScanParams, Sweep, validate_scan_range
from watchtower.scanner.engine import ScanEngine
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

# Time between simulated hops — mirrors how real scanning arrives as a
# stream of hop-sized chunks, not one full-range update at a time.
DEMO_HOP_TICK_SECONDS = 0.6


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

        # Scan pipeline. _scan_engine deliberately outlives stop_scan() —
        # see sdr/manager.py's module docstring — replaced only by the
        # next start_scan().
        self._scan_engine: ScanEngine | None = None
        self._scan_task: asyncio.Task | None = None
        self._scan_start_time: float = 0.0

    # ---------------------------------------------------------------- status

    async def snapshot(self) -> SDRSnapshot:
        hf_advisory = bool(self._params and self._params.frequency_mhz < HF_ADVISORY_THRESHOLD_MHZ)
        scan_snapshot = None
        if self._scan_engine is not None:
            engine = self._scan_engine
            scan_snapshot = ScanSnapshot(
                start_mhz=engine.params.start_mhz,
                end_mhz=engine.params.end_mhz,
                bin_khz=engine.params.bin_khz,
                noise_floor_db=engine.noise_floor_db,
                last_sweep_at=engine.last_sweep_at,
                current_freq_mhz=engine.current_freq_mhz if self._state == ReceiverState.SCANNING else None,
                signals=[
                    TrackedSignalView(
                        frequency_mhz=s.frequency_hz / 1_000_000,
                        power_db=s.power_db,
                        snr_db=s.snr_db,
                        bandwidth_khz=s.bandwidth_hz / 1000,
                        first_seen=s.first_seen,
                        last_seen=s.last_seen,
                    )
                    for s in engine.signals
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
            has_scan_results=self._scan_engine is not None,
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

        self._scan_engine = ScanEngine(params)  # fresh engine: discards whatever the last scan showed
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

    async def _stop_scan_internal(self) -> None:
        if self._scan_task:
            self._scan_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._scan_task
            self._scan_task = None
        # self._scan_engine is deliberately left alone — see sdr/manager.py.
        if self._state in (ReceiverState.SCANNING, ReceiverState.STARTING):
            self._state = ReceiverState.IDLE

    async def _scan_loop(self, params: ScanParams) -> None:
        """Steps through the requested range in hop-sized chunks (like real
        rtl_power), feeding each hop to ScanEngine as it's generated so
        signals appear incrementally instead of only once a full pass
        completes, then rolls into the next pass.
        """
        try:
            while True:
                hop_freqs: list[float] = []
                hop_powers: list[float] = []
                last_hop: Sweep | None = None
                for hop_start_hz, hop_end_hz in demo_source.iter_demo_hops(params):
                    await asyncio.sleep(DEMO_HOP_TICK_SECONDS)
                    if self._scan_engine is None:
                        return
                    t = time.monotonic() - self._scan_start_time
                    hop = demo_source.generate_demo_hop(params, t, hop_start_hz, hop_end_hz)
                    self._scan_engine.ingest_hop(hop, time.time())
                    hop_freqs.extend(hop.freqs_hz)
                    hop_powers.extend(hop.powers_db)
                    last_hop = hop
                if last_hop is not None and self._scan_engine is not None:
                    full_sweep = Sweep(
                        timestamp_key=last_hop.timestamp_key, freqs_hz=hop_freqs, powers_db=hop_powers,
                        bin_hz=last_hop.bin_hz,
                    )  # fmt: skip
                    self._scan_engine.complete_sweep(full_sweep, time.time())
        except asyncio.CancelledError:
            pass

    # ------------------------------------------------------------- shutdown

    async def shutdown(self) -> None:
        async with self._lock:
            await self._stop_internal()
            await self._stop_scan_internal()
