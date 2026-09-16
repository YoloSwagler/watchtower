import asyncio
import os

import pytest

from watchtower.sdr.process import spawn


async def test_spawn_and_terminate_kills_process():
    mp = await spawn("sleep", "60", label="test-sleep")
    assert mp.is_running()

    await mp.terminate()

    assert not mp.is_running()
    with pytest.raises(ProcessLookupError):
        os.kill(mp.pid, 0)


async def test_terminate_is_idempotent():
    mp = await spawn("sleep", "60", label="test-sleep")
    await mp.terminate()
    # Calling terminate again on an already-dead process must not raise.
    await mp.terminate()
    assert not mp.is_running()


async def test_process_group_kills_child_of_shell():
    # A shell that backgrounds a child sleep and waits on it: killing the
    # process group must take the child down too, not just the shell.
    mp = await spawn("/bin/sh", "-c", "sleep 60 & wait", label="test-shell")
    await asyncio.sleep(0.2)
    assert mp.is_running()

    await mp.terminate()

    assert not mp.is_running()
    # No leftover `sleep 60` launched by this test should still be running.
    proc = await asyncio.create_subprocess_exec(
        "pgrep", "-f", "sleep 60", stdout=asyncio.subprocess.PIPE
    )
    stdout, _ = await proc.communicate()
    assert stdout.decode().strip() == ""
