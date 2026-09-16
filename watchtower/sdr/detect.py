"""RTL-SDR hardware detection via `rtl_test`.

`rtl_test` prints its device enumeration (from USB descriptor strings, which
does not require claiming the device) before attempting to actually open and
self-test the first device. We only care about the enumeration, so we parse
whatever device list appears in stdout regardless of whether the later
self-test step succeeds — that step can fail simply because something else
currently holds the dongle, which is not the same as the dongle being
absent.
"""

from __future__ import annotations

import asyncio
import re
import shutil

from watchtower.logging_setup import get_logger
from watchtower.sdr.base import SDRDevice

logger = get_logger("sdr.detect")

DETECT_TIMEOUT = 5.0

_DEVICE_LINE = re.compile(r"^\s*(\d+):\s*(.+?),\s*(.+?)(?:,\s*SN:\s*(\S+))?\s*$")


def find_rtl_test() -> str | None:
    return shutil.which("rtl_test")


def find_rtl_fm() -> str | None:
    return shutil.which("rtl_fm")


def find_rtl_power() -> str | None:
    return shutil.which("rtl_power")


def find_ffmpeg() -> str | None:
    return shutil.which("ffmpeg")


def parse_rtl_test_output(output: str) -> list[SDRDevice]:
    devices: list[SDRDevice] = []
    in_list = False
    for line in output.splitlines():
        if "Found" in line and "device" in line:
            in_list = True
            if line.strip().startswith("Found 0"):
                break
            continue
        if not in_list:
            continue
        match = _DEVICE_LINE.match(line)
        if not match:
            # Blank line or a subsequent "Using device ..." line ends the list.
            if line.strip() == "" or devices:
                break
            continue
        index, vendor, product, serial = match.groups()
        name = f"{vendor.strip()}, {product.strip()}"
        devices.append(SDRDevice(index=int(index), name=name, serial=serial))
    return devices


async def detect_devices() -> list[SDRDevice]:
    """Run `rtl_test -t` and parse the device list. Returns an empty list if
    the tool is missing, no devices are found, or detection times out.
    """
    rtl_test_path = find_rtl_test()
    if not rtl_test_path:
        return []

    try:
        proc = await asyncio.create_subprocess_exec(
            rtl_test_path,
            "-t",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except OSError as e:
        logger.warning("failed to launch rtl_test: %s", e)
        return []

    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=DETECT_TIMEOUT)
    except asyncio.TimeoutError:
        logger.warning("rtl_test detection timed out after %ss", DETECT_TIMEOUT)
        proc.kill()
        await proc.wait()
        return []

    output = stdout.decode(errors="replace")
    devices = parse_rtl_test_output(output)
    logger.debug("rtl_test output:\n%s", output)
    return devices
