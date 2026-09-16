"""Manual gain presets for the UI's gain control.

`rtl_fm`/`rtl_power` snap any requested gain to the nearest value the
attached tuner actually supports, so this list does not need to be exact
for correct behaviour — it only needs to offer sensible choices. These are
the well-known gain steps (in dB) for the Rafael Micro R820T/R820T2 tuner,
the chip on the great majority of RTL-SDR dongles (including the one this
project's field test used). Probing the actually-attached tuner's real gain
table would require a new dependency (`pyrtlsdr`/SoapySDR) that neither
`rtl_test` nor `rtl_power` gives us for free — not justified for a preset
list. See ARCHITECTURE.md, "Gain handling".
"""

from __future__ import annotations

GAIN_PRESETS_DB: list[float] = [
    0.0, 0.9, 1.4, 2.7, 3.7, 7.7, 8.7, 12.5, 14.4, 15.7,
    16.6, 19.7, 20.7, 22.9, 25.4, 28.0, 29.7, 32.8, 33.8,
    36.4, 37.2, 38.6, 40.2, 42.1, 43.4, 43.9, 44.5, 48.0, 49.6,
]  # fmt: skip
