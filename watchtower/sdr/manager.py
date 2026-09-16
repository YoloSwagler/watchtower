"""Real hardware SDRManager: owns the RTL-SDR dongle exclusively.

Pipeline for a listen session:

    rtl_fm (raw s16le PCM on stdout) -> ffmpeg (WAV framing on stdout)

Only one pipeline exists at a time. Starting a new one always tears down
whatever is currently running first (see ARCHITECTURE.md, "SDR resource
management").
"""

from __future__ import annotations

import asyncio
import contextlib

from watchtower.logging_setup import get_logger
from watchtower.sdr import detect, process
from watchtower.sdr.base import (
    HF_ADVISORY_THRESHOLD_MHZ,
    DemodMode,
    ReceiverParams,
    ReceiverState,
    SDRDevice,
    SDRManagerBase,
    SDRSnapshot,
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
    return [
        rtl_fm_path,
        "-M", _RTL_FM_MODE[params.mode],
        "-f", str(freq_hz),
        "-s", str(capture_rate),
        "-r", str(output_rate),
        "-g", str(params.gain_db),
        "-p", str(params.ppm),
        "-l", str(params.squelch),
        "-d", str(params.device_index),
    ]  # fmt: skip


def _build_ffmpeg_cmd(ffmpeg_path: str, output_rate: int) -> list[str]:
    return [
        ffmpeg_path,
        "-hide_banner", "-loglevel", "error",
        "-fflags", "nobuffer", "-flags", "low_delay",
        "-probesize", "32", "-analyzeduration", "0",
        "-f", "s16le", "-ar", str(output_rate), "-ac", "1", "-i", "pipe:0",
        "-acodec", "pcm_s16le", "-ar", "44100", "-f", "wav", "pipe:1",
    ]  # fmt: skip


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
        self._params: ReceiverParams | None = None
        self._error: str | None = None
        self._rtl_proc: ManagedProcess | None = None
        self._ffmpeg_proc: ManagedProcess | None = None
        self._pump_task: asyncio.Task | None = None
        self._cached_devices: list[SDRDevice] = []
        self._devices_stale = False
        self._audio_client_lock = asyncio.Lock()
        self._audio_client_active = False

    async def snapshot(self) -> SDRSnapshot:
        tools_available = detect.find_rtl_fm() is not None and detect.find_ffmpeg() is not None
        hf_advisory = bool(self._params and self._params.frequency_mhz < HF_ADVISORY_THRESHOLD_MHZ)
        return SDRSnapshot(
            state=self._state,
            tools_available=tools_available,
            devices=list(self._cached_devices),
            devices_stale=self._devices_stale,
            params=self._params,
            error=self._error,
            hf_advisory=hf_advisory,
        )

    async def refresh_devices(self) -> list[SDRDevice]:
        async with self._lock:
            if self._state != ReceiverState.IDLE:
                self._devices_stale = True
                return list(self._cached_devices)
        devices = await detect.detect_devices()
        async with self._lock:
            self._cached_devices = devices
            self._devices_stale = False
        return devices

    async def start_listening(self, params: ReceiverParams) -> tuple[bool, str | None]:
        async with self._lock:
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
            logger.info(
                "listening: %.4f MHz mode=%s device=%s", params.frequency_mhz, params.mode.value, params.device_index
            )
            return True, None

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

    async def shutdown(self) -> None:
        async with self._lock:
            await self._stop_internal()
