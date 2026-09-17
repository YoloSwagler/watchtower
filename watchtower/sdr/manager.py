"""Real hardware SDRManager: owns the RTL-SDR dongle exclusively.

Pipeline for a listen session:

    rtl_fm (raw s16le PCM on stdout) -> ffmpeg (WAV framing on stdout)

Pipeline for a scan session:

    rtl_power (CSV sweep rows on stdout) -> per-hop -> ScanEngine.ingest_hop
        (live signal list, updated as each hop arrives)
    rtl_power rows -> SweepAccumulator -> full Sweep -> ScanEngine.complete_sweep
        (refreshes the noise floor once a full pass completes)

Only one pipeline (listen or scan) exists at a time — both are owned and
torn down through the same asyncio.Lock and the same ReceiverState field.
Starting either one always tears down whatever the manager currently owns
first. See ARCHITECTURE.md, "Receiver state model" and "Wideband scanner".

Scan results (the ScanEngine) deliberately survive stop_scan() — they are
only replaced when a *new* scan starts, not cleared on stop, so the
operator can browse what was found after stopping without losing it. See
ARCHITECTURE.md, "Wideband scanner" for why.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import replace

from watchtower.logging_setup import get_logger
from watchtower.scanner import parser as scanner_parser
from watchtower.scanner.base import ScanParams, Sweep, validate_scan_range
from watchtower.scanner.engine import ScanEngine
from watchtower.scanner.parser import SweepAccumulator
from watchtower.sdr import detect, process
from watchtower.sdr.base import (
    HF_ADVISORY_THRESHOLD_MHZ,
    DemodMode,
    ReceiverParams,
    ReceiverState,
    SDRDevice,
    SDRManagerBase,
    SDRSnapshot,
    ScanSnapshot,
    TrackedSignalView,
)
from watchtower.sdr.process import ManagedProcess

logger = get_logger("sdr.manager")

_RTL_FM_MODE = {
    DemodMode.NFM: "fm",
    DemodMode.WFM: "wbfm",
    DemodMode.USB: "usb",
    DemodMode.LSB: "lsb",
    DemodMode.AM: "am",
}

STARTUP_HEALTH_CHECK_DELAY = 0.3

# How often the background hot-plug monitor ticks. Modest on purpose: while
# a pipeline is active this only ever checks a cached returncode (no
# subprocess spawned); it only spawns rtl_test (fast, enumeration-only)
# while idle/disconnected. See ARCHITECTURE.md, "Hot-plug / disconnect
# detection".
MONITOR_INTERVAL_SECONDS = 3.0

SCAN_INTERVAL_SECONDS = 4


def _rates_for_mode(mode: DemodMode) -> tuple[int, int]:
    """(capture_rate, output_rate) in Hz for each demod mode."""
    if mode == DemodMode.WFM:
        return 170_000, 32_000
    if mode in (DemodMode.USB, DemodMode.LSB):
        return 12_000, 12_000
    return 24_000, 24_000  # NFM, AM


def _build_rtl_fm_cmd(rtl_fm_path: str, params: ReceiverParams) -> list[str]:
    capture_rate, output_rate = _rates_for_mode(params.mode)
    freq_hz = int(round(params.frequency_mhz * 1_000_000))
    cmd = [
        rtl_fm_path,
        "-M", _RTL_FM_MODE[params.mode],
        "-f", str(freq_hz),
        "-s", str(capture_rate),
        "-r", str(output_rate),
    ]  # fmt: skip
    if params.gain_db is not None:
        cmd += ["-g", str(params.gain_db)]
    cmd += [
        "-p", str(params.ppm),
        "-l", str(params.squelch),
        "-d", str(params.device_index),
    ]  # fmt: skip
    return cmd


def _build_ffmpeg_cmd(ffmpeg_path: str, output_rate: int) -> list[str]:
    return [
        ffmpeg_path,
        "-hide_banner", "-loglevel", "error",
        "-fflags", "nobuffer", "-flags", "low_delay",
        "-probesize", "32", "-analyzeduration", "0",
        "-f", "s16le", "-ar", str(output_rate), "-ac", "1", "-i", "pipe:0",
        "-acodec", "pcm_s16le", "-ar", "44100", "-f", "wav", "pipe:1",
    ]  # fmt: skip


def _build_rtl_power_cmd(rtl_power_path: str, params: ScanParams) -> list[str]:
    start_hz = int(round(params.start_mhz * 1_000_000))
    end_hz = int(round(params.end_mhz * 1_000_000))
    bin_hz = int(round(params.bin_khz * 1000))
    cmd = [
        rtl_power_path,
        "-f", f"{start_hz}:{end_hz}:{bin_hz}",
        "-i", str(SCAN_INTERVAL_SECONDS),
        "-d", str(params.device_index),
    ]  # fmt: skip
    if params.gain_db is not None:
        cmd += ["-g", str(params.gain_db)]
    if params.ppm:
        cmd += ["-p", str(params.ppm)]
    cmd.append("-")  # dump CSV rows to stdout instead of a file
    return cmd


async def _pump_pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """Copy bytes from rtl_fm's stdout to ffmpeg's stdin.

    asyncio subprocess pipes are asyncio.StreamReader/StreamWriter objects,
    not raw file descriptors — unlike `subprocess.Popen`, you cannot hand
    one asyncio subprocess's `.stdout` directly to another's `stdin=` and
    get an OS-level pipe splice; asyncio has no API for that. This small
    relay task is the portable way to chain two asyncio subprocesses. The
    extra copy through Python is irrelevant at audio bitrates.
    """
    try:
        while True:
            chunk = await reader.read(65536)
            if not chunk:
                break
            writer.write(chunk)
            await writer.drain()
    except (asyncio.CancelledError, ConnectionResetError, BrokenPipeError):
        pass
    finally:
        with contextlib.suppress(Exception):
            writer.close()


async def _read_stderr_text(mp: ManagedProcess, timeout: float = 0.5) -> str:
    if not mp.proc.stderr:
        return ""
    try:
        data = await asyncio.wait_for(mp.proc.stderr.read(), timeout=timeout)
    except asyncio.TimeoutError:
        return ""
    return data.decode(errors="replace").strip()


def _friendly_error(stderr_text: str) -> str:
    lowered = stderr_text.lower()
    if "usb_claim_interface" in lowered or "busy" in lowered:
        return "SDR device is busy (in use by another process)."
    if "no supported devices" in lowered or "failed to open rtlsdr" in lowered:
        return "No RTL-SDR device found."
    if "permission denied" in lowered:
        return "Permission denied opening the SDR device. Check udev rules / group membership."
    lines = [line for line in stderr_text.splitlines() if line.strip()]
    return lines[-1] if lines else "Receive pipeline exited immediately."


class SDRManager(SDRManagerBase):
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._state = ReceiverState.IDLE
        self._error: str | None = None
        self._cached_devices: list[SDRDevice] = []
        self._devices_stale = False
        self._device_seen_once = False

        # Listen pipeline
        self._params: ReceiverParams | None = None
        self._rtl_proc: ManagedProcess | None = None
        self._ffmpeg_proc: ManagedProcess | None = None
        self._pump_task: asyncio.Task | None = None
        self._audio_client_lock = asyncio.Lock()
        self._audio_client_active = False

        # Scan pipeline. _scan_engine deliberately outlives stop_scan() —
        # see module docstring — and is only replaced by the next
        # start_scan(). _scan_accumulator is per-session (real scanning
        # only) since it just groups rtl_power's hop lines into full
        # sweeps; it's recreated each start_scan().
        self._scan_proc: ManagedProcess | None = None
        self._scan_reader_task: asyncio.Task | None = None
        self._scan_accumulator: SweepAccumulator | None = None
        self._scan_engine: ScanEngine | None = None

        # Hot-plug monitor
        self._monitor_task: asyncio.Task | None = None

    # ---------------------------------------------------------------- status

    async def snapshot(self) -> SDRSnapshot:
        tools_available = detect.find_rtl_fm() is not None and detect.find_ffmpeg() is not None
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
            tools_available=tools_available,
            devices=list(self._cached_devices),
            devices_stale=self._devices_stale,
            params=self._params,
            error=self._error,
            hf_advisory=hf_advisory,
            scan=scan_snapshot,
            has_scan_results=self._scan_engine is not None,
        )

    async def refresh_devices(self) -> list[SDRDevice]:
        async with self._lock:
            if self._state not in (ReceiverState.IDLE, ReceiverState.DISCONNECTED):
                self._devices_stale = True
                return list(self._cached_devices)
        devices = await detect.detect_devices()
        async with self._lock:
            self._cached_devices = devices
            self._devices_stale = False
            if devices:
                self._device_seen_once = True
        return devices

    # --------------------------------------------------------------- listen

    async def start_listening(self, params: ReceiverParams) -> tuple[bool, str | None]:
        async with self._lock:
            return await self._start_listening_locked(params)

    async def _start_listening_locked(self, params: ReceiverParams) -> tuple[bool, str | None]:
        await self._stop_scan_internal()
        await self._stop_internal()

        rtl_fm_path = detect.find_rtl_fm()
        ffmpeg_path = detect.find_ffmpeg()
        if not rtl_fm_path:
            self._state, self._error = ReceiverState.ERROR, "rtl_fm not found. Install the rtl-sdr package."
            return False, self._error
        if not ffmpeg_path:
            self._state, self._error = ReceiverState.ERROR, "ffmpeg not found. Install ffmpeg."
            return False, self._error

        self._state = ReceiverState.STARTING
        self._error = None
        _, output_rate = _rates_for_mode(params.mode)

        try:
            rtl_proc = await process.spawn(
                *_build_rtl_fm_cmd(rtl_fm_path, params),
                label="rtl_fm",
                stdin=asyncio.subprocess.DEVNULL,
            )
        except OSError as e:
            self._state, self._error = ReceiverState.ERROR, f"Failed to start rtl_fm: {e}"
            return False, self._error

        try:
            ffmpeg_proc = await process.spawn(
                *_build_ffmpeg_cmd(ffmpeg_path, output_rate),
                label="ffmpeg",
                stdin=asyncio.subprocess.PIPE,
            )
        except OSError as e:
            await rtl_proc.terminate()
            self._state, self._error = ReceiverState.ERROR, f"Failed to start ffmpeg: {e}"
            return False, self._error

        pump_task = asyncio.create_task(
            _pump_pipe(rtl_proc.proc.stdout, ffmpeg_proc.proc.stdin), name="sdr-audio-pump"
        )

        await asyncio.sleep(STARTUP_HEALTH_CHECK_DELAY)
        if rtl_proc.proc.returncode is not None or ffmpeg_proc.proc.returncode is not None:
            stderr_text = (await _read_stderr_text(rtl_proc)) or (await _read_stderr_text(ffmpeg_proc))
            pump_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pump_task
            await ffmpeg_proc.terminate()
            await rtl_proc.terminate()
            self._state = ReceiverState.ERROR
            self._error = _friendly_error(stderr_text)
            logger.warning("listen pipeline failed to start: %s", self._error)
            return False, self._error

        self._rtl_proc = rtl_proc
        self._ffmpeg_proc = ffmpeg_proc
        self._pump_task = pump_task
        self._params = params
        self._state = ReceiverState.LISTENING
        self._device_seen_once = True
        logger.info(
            "listening: %.4f MHz mode=%s device=%s", params.frequency_mhz, params.mode.value, params.device_index
        )
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
        # Terminate ffmpeg (the final stage) first, while the rtl_fm->ffmpeg
        # pump task is still alive and still draining rtl_fm's stdout — that
        # avoids the same undrained-pipe stall on rtl_fm's side. If nobody
        # is reading ffmpeg's own stdout (no attached audio_stream()
        # consumer), drain it ourselves so ffmpeg's SIGTERM flush handler
        # can't block on a full pipe — see process.drain_discard.
        if self._ffmpeg_proc:
            drain_task = None
            if self._ffmpeg_proc.proc.stdout and not self._audio_client_active:
                drain_task = asyncio.create_task(process.drain_discard(self._ffmpeg_proc.proc.stdout))
            await self._ffmpeg_proc.terminate()
            if drain_task:
                drain_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await drain_task
            self._ffmpeg_proc = None
        if self._rtl_proc:
            await self._rtl_proc.terminate()
            self._rtl_proc = None
        if self._pump_task:
            self._pump_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._pump_task
            self._pump_task = None
        if self._state == ReceiverState.LISTENING or self._state == ReceiverState.STARTING:
            self._state = ReceiverState.IDLE
        self._params = None

    async def audio_stream(self):
        """Yield WAV bytes for the current listen session to exactly one
        consumer at a time. ffmpeg's stdout is a single pipe: two concurrent
        readers would each get an arbitrary, non-overlapping slice of the
        same byte stream (a `read()` race), corrupting both. A second
        concurrent caller gets no bytes rather than a corrupted stream.
        """
        proc = self._ffmpeg_proc
        if not proc or not proc.proc.stdout:
            return
        async with self._audio_client_lock:
            if self._audio_client_active:
                logger.warning("audio stream already has a consumer; refusing a second one")
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

        rtl_power_path = detect.find_rtl_power()
        if not rtl_power_path:
            self._state, self._error = ReceiverState.ERROR, "rtl_power not found. Install the rtl-sdr package."
            return False, self._error

        self._state = ReceiverState.STARTING
        self._error = None

        try:
            scan_proc = await process.spawn(
                *_build_rtl_power_cmd(rtl_power_path, params),
                label="rtl_power",
                stdin=asyncio.subprocess.DEVNULL,
            )
        except OSError as e:
            self._state, self._error = ReceiverState.ERROR, f"Failed to start rtl_power: {e}"
            return False, self._error

        await asyncio.sleep(STARTUP_HEALTH_CHECK_DELAY)
        if scan_proc.proc.returncode is not None:
            stderr_text = await _read_stderr_text(scan_proc)
            await scan_proc.terminate()
            self._state = ReceiverState.ERROR
            self._error = _friendly_error(stderr_text)
            logger.warning("scan failed to start: %s", self._error)
            return False, self._error

        self._scan_proc = scan_proc
        self._scan_accumulator = SweepAccumulator()
        self._scan_engine = ScanEngine(params)  # fresh engine: discards whatever the last scan showed
        self._scan_reader_task = asyncio.create_task(self._read_scan_output(scan_proc), name="sdr-scan-reader")
        self._state = ReceiverState.SCANNING
        self._device_seen_once = True
        logger.info(
            "scanning: %.4f-%.4f MHz bin=%.1fkHz device=%s",
            params.start_mhz, params.end_mhz, params.bin_khz, params.device_index,
        )  # fmt: skip
        return True, None

    async def stop_scan(self) -> None:
        async with self._lock:
            await self._stop_scan_internal()

    async def _stop_scan_internal(self) -> None:
        if self._scan_reader_task:
            self._scan_reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._scan_reader_task
            self._scan_reader_task = None
        if self._scan_proc:
            await self._scan_proc.terminate()
            self._scan_proc = None
        self._scan_accumulator = None
        # self._scan_engine is deliberately left alone — see module docstring.
        if self._state in (ReceiverState.SCANNING, ReceiverState.STARTING):
            self._state = ReceiverState.IDLE

    async def _read_scan_output(self, proc: ManagedProcess) -> None:
        stream = proc.proc.stdout
        if stream is None:
            return
        try:
            while True:
                line = await stream.readline()
                if not line:
                    break
                self._ingest_scan_line(line.decode(errors="replace"))
        except (asyncio.CancelledError, ConnectionResetError):
            pass

    def _ingest_scan_line(self, text: str) -> None:
        # Runs outside self._lock (it's the scan reader task, not a public
        # entry point) — deliberately: these are cosmetic telemetry fields
        # read by snapshot(), not correctness-critical state, and
        # _stop_scan_internal() always cancels+awaits this task before
        # this can run again, so there's no write-after-stop race.
        text = text.strip()
        if not text or self._scan_accumulator is None or self._scan_engine is None:
            return
        try:
            row = scanner_parser.parse_rtl_power_line(text)
        except ValueError:
            logger.debug("ignoring unparseable rtl_power line: %r", text[:200])
            return

        now = time.time()
        hop = Sweep(
            timestamp_key=row.timestamp_key,
            freqs_hz=[float(row.hz_low + i * row.hz_step) for i in range(len(row.dbs))],
            powers_db=list(row.dbs),
            bin_hz=float(row.hz_step),
        )
        self._scan_engine.ingest_hop(hop, now)

        full_sweep = self._scan_accumulator.ingest(row)
        if full_sweep is not None:
            self._scan_engine.complete_sweep(full_sweep, now)

    # --------------------------------------------------------- hot-plug monitor

    async def start_monitor(self) -> None:
        if self._monitor_task is None:
            self._monitor_task = asyncio.create_task(self._monitor_loop(), name="sdr-hotplug-monitor")

    async def stop_monitor(self) -> None:
        if self._monitor_task:
            self._monitor_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._monitor_task
            self._monitor_task = None

    async def _monitor_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(MONITOR_INTERVAL_SECONDS)
                await self._monitor_tick()
            except asyncio.CancelledError:
                return
            except Exception:
                logger.exception("hotplug monitor tick failed")

    async def _monitor_tick(self) -> None:
        async with self._lock:
            state = self._state
            if state == ReceiverState.LISTENING:
                dead = (self._rtl_proc is not None and self._rtl_proc.proc.returncode is not None) or (
                    self._ffmpeg_proc is not None and self._ffmpeg_proc.proc.returncode is not None
                )
                if dead:
                    await self._handle_unexpected_exit_locked()
                return
            if state == ReceiverState.SCANNING:
                if self._scan_proc is not None and self._scan_proc.proc.returncode is not None:
                    await self._handle_unexpected_exit_locked()
                return
            if state not in (ReceiverState.IDLE, ReceiverState.DISCONNECTED):
                return  # STARTING/ERROR: nothing useful to check this tick

        # Only reached for IDLE/DISCONNECTED. detect_devices() spawns
        # rtl_test, so it runs without holding the lock — a concurrent
        # start_listening/start_scan shouldn't have to wait on a hardware
        # probe.
        devices = await detect.detect_devices()
        async with self._lock:
            if self._state not in (ReceiverState.IDLE, ReceiverState.DISCONNECTED):
                return  # became busy while we were probing; ignore this tick
            self._cached_devices = devices
            self._devices_stale = False
            present = len(devices) > 0
            if present:
                self._device_seen_once = True
            if self._state == ReceiverState.DISCONNECTED and present:
                self._state = ReceiverState.IDLE
                self._error = None
                logger.info("SDR reconnected")
            elif self._state == ReceiverState.IDLE and not present and self._device_seen_once:
                self._state = ReceiverState.DISCONNECTED
                self._error = "SDR disconnected"
                logger.warning("SDR disconnected (was idle)")

    async def _handle_unexpected_exit_locked(self) -> None:
        """Caller must already hold self._lock (see _monitor_tick)."""
        logger.warning("receive pipeline exited unexpectedly; treating as SDR disconnect")
        if self._state == ReceiverState.SCANNING:
            await self._stop_scan_internal()
        else:
            await self._stop_internal()
        self._state = ReceiverState.DISCONNECTED
        self._error = "SDR disconnected"

    # ------------------------------------------------------------- shutdown

    async def shutdown(self) -> None:
        await self.stop_monitor()
        async with self._lock:
            await self._stop_internal()
            await self._stop_scan_internal()
