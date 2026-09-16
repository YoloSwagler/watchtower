# Watchtower

A lightweight, offline-first SDR listening post and field receiver, built
specifically for a **Raspberry Pi 4 Model B (2 GB RAM)** driving a 7"
1024x600 display in fullscreen Chromium. Watchtower turns an RTL-SDR dongle
and a u-blox USB GPS receiver into a compact, self-contained field
instrument: tune a frequency, listen, and see your position — with **zero
Internet access required at runtime**.

Watchtower is receive-only. It does not transmit, replay, decrypt, or
attack anything.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the technical design and the
reasoning behind it, and [ROADMAP.md](ROADMAP.md) for what's built versus
planned.

## Target hardware

- Raspberry Pi 4 Model B, 2 GB RAM, running Raspberry Pi OS (or another
  Debian-based distro).
- A 7" 1024x600 display, Chromium in kiosk/fullscreen mode.
- An RTL-SDR USB dongle (RTL2832U-based). Tuning range and HF capability
  depend entirely on the specific dongle/tuner — see "Hardware
  limitations" below.
- A USB GPS/GLONASS receiver based on the u-blox 7 chipset (or anything
  else `gpsd` supports).

Watchtower also runs fine on a regular Linux desktop for development —
that's how this phase was built and tested (see "Development status"
below).

## Current status: Phase 2 (field-test hardening, wideband scanner, bookmarks)

Phase 1 was field-tested on the actual target hardware (Pi 4B, RTL-SDR,
u-blox 7 GPS, 1024x600 Chromium, offline) — see "Field-test results" below.
Phase 2 fixes what that testing found and adds the interactive scanner.

Implemented:

- Single-process aiohttp app serving the UI, REST API, and audio stream.
- 1024x600 UI with a **Tune** / **Scan** tab pair: header status bar,
  receiver controls, wideband scanner + signal table, GPS panel, saved
  frequencies panel, system health panel — still fits the viewport with no
  scrolling.
- RTL-SDR detection via `rtl_test`, with device list and busy/absent states.
- gpsd detection and connection: fix status, lat/lon, altitude, satellite
  counts. GPS problems never block SDR use.
- Manual frequency entry; NFM / WFM / AM / USB / LSB demodulation modes;
  **Auto or manual-preset gain** (see "Gain" below), squelch / PPM controls.
  Gain can now be changed while listening.
- Start Listening / Stop Listening with `rtl_fm | ffmpeg` piped into a
  chunked WAV stream the browser plays directly — no Icecast.
- **Wideband scanner**: `rtl_power`-driven sweep over an operator-set
  frequency range, with noise-floor estimation and a compact, live-updating
  table of detected signals (frequency / SNR / power / last seen). Clicking
  a signal stops the scan and starts listening to it; stopping listening
  can return to the same scan.
- **Saved frequencies**: name, save, rename, delete, and one-tap re-tune
  presets (frequency, mode, gain or Auto, squelch, note), persisted in a
  small JSON file outside the application source tree.
- A single-owner SDR resource manager covering **both** RF-owning modes
  (listening and scanning): exactly one pipeline at a time, every
  subprocess tracked and cleanly torn down (whole process group, not just
  the top PID) on stop, error, or app shutdown.
- **Hot-plug recovery**: unplugging/reconnecting the RTL-SDR is detected
  automatically (a new `disconnected` state) and the browser session
  recovers without a manual page refresh.
- `--demo` mode: simulated SDR (with a real synthetic audio tone through
  the same ffmpeg-framed pipeline) and simulated GPS acquisition, plus
  simulated scanning (synthetic sweeps run through the real detection/
  tracking code), so the whole UI — including the full scan → find → save
  → select → listen → return-to-scan loop — works with no hardware
  attached at all.
- Automated tests (103 passing) covering everything that doesn't need
  physical hardware.

Not yet implemented (see [ROADMAP.md](ROADMAP.md)): spectrum/waterfall
display, offline mapping, recording, session logging, gpsd auto-start.

## Quick start

Requires Python 3.10+ (tested on 3.12).

```bash
git clone <this-repo> watchtower
cd watchtower
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 -m watchtower --demo
```

Then open **http://127.0.0.1:8080/** in Chromium (or any browser). `--demo`
runs the full UI with simulated SDR and GPS data — no hardware needed.

To run against real hardware, drop `--demo`:

```bash
python3 -m watchtower
```

Useful flags:

| Flag | Effect |
|---|---|
| `--demo` | Simulated SDR + GPS, no hardware required |
| `--port N` | Listen on port N (default 8080) |
| `--lan` | Bind `0.0.0.0` instead of `127.0.0.1` (explicit opt-in for LAN access) |
| `-v` / `--verbose` | Debug-level logging |
| `--gpsd-host`, `--gpsd-port` | Non-default gpsd location (default `127.0.0.1:2947`) |
| `--data-dir` | Where saved frequencies are stored (default `~/.local/share/watchtower`) |

## System dependencies (Debian / Raspberry Pi OS)

Watchtower's only **Python** dependency is `aiohttp` (see
`requirements.txt`). Everything else is an external system tool it shells
out to. On a fresh Raspberry Pi OS (or this dev machine), install:

```bash
sudo apt update
sudo apt install -y ffmpeg rtl-sdr gpsd gpsd-clients
```

| Tool | Package | Used for |
|---|---|---|
| `rtl_test` | `rtl-sdr` | Detecting attached RTL-SDR devices |
| `rtl_fm` | `rtl-sdr` | Demodulating the tuned frequency |
| `rtl_power` | `rtl-sdr` | Wideband sweep for the scanner |
| `ffmpeg` | `ffmpeg` | Framing raw PCM as a streamable WAV for the browser |
| `gpsd` | `gpsd` | Reading the u-blox GPS receiver |

The app never installs or modifies system packages itself — it only
detects what's present and reports missing tools in the UI's System panel.

### RTL-SDR udev rules (so it works without root)

```bash
sudo bash -c 'cat > /etc/udev/rules.d/20-rtlsdr.rules << EOF
SUBSYSTEM=="usb", ATTRS{idVendor}=="0bda", ATTRS{idProduct}=="2838", MODE="0666"
SUBSYSTEM=="usb", ATTRS{idVendor}=="0bda", ATTRS{idProduct}=="2832", MODE="0666"
EOF'
sudo udevadm control --reload-rules
sudo udevadm trigger
```

The stock kernel DVB-T driver will otherwise grab the dongle before
`rtl-sdr` can:

```bash
echo "blacklist dvb_usb_rtl28xxu" | sudo tee /etc/modprobe.d/blacklist-rtl.conf
sudo modprobe -r dvb_usb_rtl28xxu
```

Unplug and replug the dongle after adding the udev rules.

### GPS / gpsd

Watchtower is a **gpsd client only** — it does not launch gpsd itself in
this phase (see ARCHITECTURE.md, "GPS lifecycle", for why). Start it
manually against your u-blox device, which will normally show up as
`/dev/ttyACM0` or `/dev/ttyUSB0`:

```bash
sudo gpsd /dev/ttyACM0 -F /var/run/gpsd.sock
```

Or check what gpsd already sees:

```bash
gpsd -n -N /dev/ttyACM0   # foreground, verbose, for troubleshooting
cgps                       # quick terminal fix viewer, from gpsd-clients
```

If gpsd isn't running, Watchtower's System/GPS panel will show "device
found, gpsd not running" (if it can see a serial device) or "no GPS
device" (if it can't) rather than silently doing nothing.

## Hardware limitations (read this before assuming HF reception works)

USB and LSB (and the optional AM mode) are demodulation modes the
**software** supports — they say nothing about whether your specific
RTL-SDR dongle can actually tune the frequency you enter. A stock RTL-SDR
with an R820T/R820T2 tuner cannot receive HF (below roughly 24 MHz)
without **direct sampling mode** or an **upconverter**. Watchtower shows a
non-blocking advisory in the UI when you tune below 24 MHz, but it cannot
verify from software alone whether your particular hardware can actually
receive there — that depends on your tuner chip and any modifications.
Check your dongle's documentation.

## Gain

Gain defaults to **Auto** (automatic tuner gain) — Phase 1 defaulted to
manual `0 dB`, which field-tested as effectively unusable. Pick a manual
value from the dropdown if Auto isn't giving good reception; a local WFM
station typically sounds best around 20–30 dB, while weaker NFM signals
(e.g. NOAA Weather Radio) often need more. Gain can be changed while
listening — Watchtower briefly restarts the receive pipeline in place
(frequency/mode/squelch/PPM are preserved) since `rtl_fm` has no live gain
control channel. The preset list is the standard R820T/R820T2 tuner gain
table; `rtl_fm`/`rtl_power` snap to the nearest gain the attached tuner
actually supports regardless, so this works even if your dongle's exact
steps differ slightly.

## Field-test results (Phase 1 → Phase 2)

Phase 1 was tested on the actual target hardware: Raspberry Pi 4B (2 GB),
RTL-SDR, USB u-blox 7 GPS/GLONASS receiver, 7" 1024x600 display, Chromium,
fully offline. Confirmed working: app launch, the 1024x600 UI, offline
runtime, RTL-SDR detection, u-blox detection via gpsd (GPS satellite data
visible; outdoor position fix still untested), browser audio, real WFM and
NFM reception (local commercial FM and NOAA Weather Radio at 162.475 MHz),
repeated Start/Stop and mode switching, and browser refresh mid-session.

That testing also surfaced the issues Phase 2 fixes: gain 0 meaning manual
0 dB instead of automatic (see "Gain" above), no way to change gain while
listening, and a hot-plugged/removed RTL-SDR leaving the browser session
stuck until a manual Chromium refresh (see ARCHITECTURE.md, "Hot-plug /
disconnect detection" for how that's now handled).

## Development status & how this was tested

This slice was developed and tested on a Linux desktop (Linux Mint 22.3,
x86_64, 4 cores, 7.7 GB RAM) with **no RTL-SDR or GPS hardware attached**.
That's exactly the situation `--demo` mode exists for, and it's also why
the architecture keeps hardware access behind small interfaces
(`SDRManagerBase`, `GPSManagerBase`) that the demo implementations satisfy
too.

Run the automated test suite:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
python3 -m pytest
```

103 tests, all passing, in under 15 seconds. They cover everything Phase 1
did (see below) plus: `rtl_power` CSV parsing, noise-floor estimation,
signal detection/peak-grouping/SNR, scan start/stop/repeated cycles, scan↔
listen transitions, invalid frequency ranges, SDR disconnect during
listening/scanning and reconnect, automatic/manual gain and live gain
changes, bookmark create/edit/delete/persistence, and demo scanning.
Phase 1's original coverage: `rtl_test` output parsing, subprocess group
lifecycle (spawn/terminate/no-orphans, including a shell-backgrounded
child process to specifically verify whole-process-group kill), the gpsd
JSON protocol parser, both demo managers' state machines, and the REST API
surface (status, start/stop listening, validation errors, static file
serving).

**What was verified by hand, in-browser, at 1024x600, in `--demo` mode:**
tuning, mode switching, Start/Stop Listening, the full Scan → find signals
→ select → listen → save → return-to-scan loop, live gain changes while
listening, bookmark rename/delete, GPS status/fix rendering, system health
panel, repeated start/stop cycles (including rapid-fire clicking) leaving
no orphaned processes, and the UI fitting the viewport with no scrolling
on both the Tune and Scan tabs. This hands-on pass caught and fixed real
bugs mocked unit tests alone did not — see "Found by testing, not
assumed" notes in [ARCHITECTURE.md](ARCHITECTURE.md), including a Phase 2
one: elements toggled via the `hidden` attribute (the Tune/Scan panels,
the inline bookmark-save row) stayed visible because a CSS rule setting
`display: flex` on the same element silently outranks the browser's
default `[hidden] { display: none }` — fixed with an explicit
`[hidden] { display: none !important; }` rule, found by actually looking
at the rendered page, not by reading the CSS.

**What still needs testing on real hardware** (not possible in this
environment): `rtl_power`-driven scanning against a real dongle (sweep
timing, signal detection quality against real RF, gain/PPM behaviour),
actual RTL-SDR hot-plug/unplug during both listening and scanning, and an
actual u-blox GPS outdoor position fix. If you've installed
`ffmpeg`/`rtl-sdr`/`gpsd` per the instructions above, `--demo` mode will
additionally exercise the *real* ffmpeg-framed audio pipeline (as a
synthetic tone) and honest "tool installed but no device" detection —
closer to, but still short of, real RF and GPS hardware.

### Resource usage (measured on the dev machine)

Measured with `scripts/measure_resources.sh` on the x86_64 dev machine
described above (Linux Mint 22.3, 4 cores, 7.7 GB RAM) — **not** a Pi 4B,
and CPU numbers on a Pi 4B's slower cores will be higher even for identical
work. Treat this as evidence the design is lightweight, not as a Pi 4B
number:

| Scenario | RSS | CPU |
|---|---|---|
| `--demo`, idle (no listen/scan session, GPS demo loop ticking every 1s) | ~37 MB | settles under 4% after the first second |
| `--demo`, actively listening (ffmpeg tone pipeline running) | ~39 MB Watchtower + ~48 MB ffmpeg (~87 MB combined) | ffmpeg briefly spikes on start, settles low |
| `--demo`, actively scanning | ~38 MB (no subprocess — synthetic sweeps are pure Python; see ARCHITECTURE.md, "Demo scanning") | settles under 3% after the first second |

For comparison, this entire footprint is a small fraction of the Pi 4B's
2 GB. The real `rtl_fm`/`rtl_power` binaries are compiled C and have their
own well-known modest footprint independent of Watchtower; real scanning
(unlike demo scanning) does spawn `rtl_power` as a subprocess.

A real Raspberry Pi 4B measurement (and a measurement with the real
`rtl_fm`/`rtl_power` pipelines against actual hardware, not demo data) is
still needed — see "Remaining limitations" / real-hardware test list at
the end of this document before relying on these numbers for capacity
planning on the actual target hardware.

## Project layout

See "Directory structure" in [ARCHITECTURE.md](ARCHITECTURE.md).

## Eventual installation model (not yet implemented)

Phase 5 of the roadmap covers packaging this as a systemd service plus a
Chromium kiosk launch configuration, so the Pi boots directly into
Watchtower fullscreen. For now, run it manually as shown above.

## Security notes

- Binds to `127.0.0.1` by default; LAN access requires the explicit
  `--lan` flag.
- Never requires root — SDR access is via udev rules, GPS access is via
  gpsd.
- No telemetry, analytics, or outbound network calls anywhere in the code.

## Real-hardware tests to run before Phase 3

Everything below was exercised in `--demo` mode and with automated tests,
but needs confirming against real hardware before trusting it in the
field:

- Real `rtl_power` scanning: sweep timing/responsiveness across a few
  range/bin-size combinations, and whether the detection thresholds
  (`SIGNAL_THRESHOLD_DB` / noise-floor estimate in
  `watchtower/scanner/base.py` and `detect.py`) find real stations without
  drowning in false positives on a noisy band.
- Selecting a real detected signal → listen → confirm audio quality and
  correct tuning, then Stop → Return to Scan.
- Gain: try Auto vs. a few manual presets against a real local station on
  both WFM and NFM; confirm live gain changes while listening produce only
  a brief interruption.
- Hot-plug: unplug/replug the RTL-SDR while idle, while listening, and
  while scanning; confirm the UI reaches `disconnected` and recovers to
  `idle` on its own, with no manual Chromium refresh and no orphaned
  `rtl_fm`/`rtl_power`/`ffmpeg` processes (`ps aux | grep -E 'rtl_|ffmpeg'`
  after each cycle).
- Bookmarks: save a real detected/tuned frequency, restart Watchtower, and
  confirm it's still there (`~/.local/share/watchtower/bookmarks.json` by
  default).
- Actual RSS/CPU measurement on the Pi 4B itself, both idle and during
  real scanning/listening (`scripts/measure_resources.sh` runs the demo
  scenarios; real-hardware numbers still need a manual pass since the
  script can't script real RF).
- GPS outdoor position fix (still outstanding from Phase 1).
