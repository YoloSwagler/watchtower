"""Subprocess lifecycle helper: own a whole process group, kill it cleanly.

rtl_fm and ffmpeg run piped together. If we only ever tracked and signalled
the immediate PID we started, a crash or unusual shutdown ordering could
leave the other half of the pipe running as an orphan attached to the
dongle. Starting each process in its own session (process group) lets us
signal the whole group at once.
"""

from __future__ import annotations

import asyncio
import os
import signal

from watchtower.logging_setup import get_logger

logger = get_logger("sdr.process")

TERMINATE_TIMEOUT = 2.0


class ManagedProcess:
    def __init__(self, proc: asyncio.subprocess.Process, label: str) -> None:
        self.proc = proc
        self.label = label

    @property
    def pid(self) -> int:
        return self.proc.pid

    def is_running(self) -> bool:
        return self.proc.returncode is None

    async def terminate(self) -> None:
        """Terminate the process group: SIGTERM, then SIGKILL if it doesn't
        exit within TERMINATE_TIMEOUT seconds. Safe to call multiple times
        or after the process has already exited.
        """
        if self.proc.returncode is not None:
            return

        pgid = None
        try:
            pgid = os.getpgid(self.proc.pid)
        except ProcessLookupError:
            return

        try:
            os.killpg(pgid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass

        try:
            await asyncio.wait_for(self.proc.wait(), timeout=TERMINATE_TIMEOUT)
            return
        except asyncio.TimeoutError:
            logger.warning("%s (pid=%s) did not exit after SIGTERM, sending SIGKILL", self.label, self.proc.pid)

        try:
            os.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            await asyncio.wait_for(self.proc.wait(), timeout=TERMINATE_TIMEOUT)
        except asyncio.TimeoutError:
            logger.error("%s (pid=%s) still alive after SIGKILL", self.label, self.proc.pid)


async def drain_discard(stream: asyncio.StreamReader) -> None:
    """Read and discard a stdout pipe until EOF or cancellation.

    Some tools (ffmpeg in particular) install a signal handler that tries to
    flush pending output before exiting on SIGTERM. If nobody is reading
    that output — e.g. a listen session is stopped before any browser
    client ever connected to /api/audio/stream — the OS pipe buffer fills,
    the flush blocks, and the process doesn't actually exit until the
    SIGKILL that follows our termination timeout. Draining the pipe
    concurrently with termination avoids that stall. Only call this when
    nothing else (an active audio_stream() consumer) is already reading the
    same stream — see the callers in sdr/manager.py and sdr/demo.py.
    """
    try:
        while True:
            chunk = await stream.read(65536)
            if not chunk:
                break
    except (asyncio.CancelledError, ConnectionResetError):
        pass


async def spawn(*cmd: str, label: str, stdin=None, stdout=asyncio.subprocess.PIPE) -> ManagedProcess:
    """Spawn a subprocess in its own session/process group."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=stdin,
        stdout=stdout,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    logger.debug("spawned %s (pid=%s): %s", label, proc.pid, " ".join(cmd))
    return ManagedProcess(proc, label)
