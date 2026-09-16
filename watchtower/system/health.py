"""System dependency presence checks, independent of demo/real mode.

Shown in the UI's health panel so the operator can tell "the app is fine but
a tool is missing" apart from "the app is broken."
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass


@dataclass(frozen=True)
class ToolStatus:
    rtl_test: bool
    rtl_fm: bool
    rtl_power: bool
    ffmpeg: bool
    gpsd: bool

    def as_dict(self) -> dict:
        return {
            "rtl_test": self.rtl_test,
            "rtl_fm": self.rtl_fm,
            "rtl_power": self.rtl_power,
            "ffmpeg": self.ffmpeg,
            "gpsd": self.gpsd,
        }


def check_tools() -> ToolStatus:
    return ToolStatus(
        rtl_test=shutil.which("rtl_test") is not None,
        rtl_fm=shutil.which("rtl_fm") is not None,
        rtl_power=shutil.which("rtl_power") is not None,
        ffmpeg=shutil.which("ffmpeg") is not None,
        gpsd=shutil.which("gpsd") is not None,
    )
