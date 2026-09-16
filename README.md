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

## Current status: Phase 1 (vertical slice)

Implemented:

- Single-process aiohttp app serving the UI, REST API, and audio stream.
- 1024x600 UI: header status bar, receiver controls, GPS panel, system
  health panel — fits the viewport with no scrolling.
- RTL-SDR detection via `rtl_test`, with device list and busy/absent states.
- gpsd detection and connection: fix status, lat/lon, altitude, satellite
  counts. GPS problems never block SDR use.
- Manual frequency entry; NFM / WFM / AM / USB / LSB demodulation modes;
  gain / squelch / PPM controls.
- Start Listening / Stop Listening with `rtl_fm | ffmpeg` piped into a
  chunked WAV stream the browser plays directly — no Icecast.
- A single-owner SDR resource manager: exactly one receive pipeline at a
  time, every subprocess tracked and cleanly torn down (whole process
  group, not just the top PID) on stop, error, or app shutdown.
- `--demo` mode: simulated SDR (with a real synthetic audio tone through
  the same ffmpeg-framed pipeline) and simulated GPS acquisition, so the
  whole UI works with no hardware attached at all.
- Automated tests (30 passing) covering everything that doesn't need
  physical hardware.

Not yet implemented (see [ROADMAP.md](ROADMAP.md)): wideband sweep,
automatic signal list, spectrum/waterfall display, offline mapping,
recording, presets, session logging.

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

## Development status & how this was tested

This Phase 1 slice was developed and tested on a Linux desktop (Linux Mint
22.3, x86_64, 4 cores, 7.7 GB RAM) with **no RTL-SDR or GPS hardware
attached**. That's exactly the situation `--demo` mode exists for, and it's
also why the architecture keeps hardware access behind small interfaces
(`SDRManagerBase`, `GPSManagerBase`) that the demo implementations satisfy
too.

Run the automated test suite:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
python3 -m pytest
```

30 tests, all passing, in under 2 seconds. They cover: `rtl_test` output
parsing, subprocess group lifecycle (spawn/terminate/no-orphans, including
a shell-backgrounded child process to specifically verify whole-process-group
kill), the gpsd JSON protocol parser, both demo managers' state machines,
and the REST API surface (status, start/stop listening, validation errors,
static file serving).

**What was verified by hand, in-browser, at 1024x600, in `--demo` mode:**
tuning, mode switching, Start/Stop Listening, GPS status/fix rendering,
system health panel, repeated start/stop cycles (including rapid-fire
clicking) leaving no orphaned processes, and the UI fitting the viewport
with no scrolling. This hands-on pass caught and fixed three real bugs
that mocked unit tests alone did not: asyncio subprocess pipes can't be
chained the way `subprocess.Popen` pipes can (rtl_fm → ffmpeg needed an
explicit relay task, not direct pipe-passing), a second concurrent reader
of the audio stream corrupted both streams, and a browser aborting an
audio connection mid-stream could permanently wedge the "one consumer at
a time" guard. See "Found by testing, not assumed" notes in
[ARCHITECTURE.md](ARCHITECTURE.md) for details. One related latency edge
case remains and is documented there rather than claimed fixed: clicking
Start/Stop faster than humanly realistic can occasionally make one Stop
call take ~2s longer (SIGKILL fallback) — the SDR is still always released
and nothing is ever left orphaned.

**What still needs testing on real hardware** (not possible in this
environment): an actual RTL-SDR dongle (detection, tuning accuracy, gain/
squelch/PPM behaviour, real audio quality per mode) and an actual u-blox
GPS receiver via gpsd (real fix acquisition, satellite data, device
hot-plug behaviour). If you've installed `ffmpeg`/`rtl-sdr`/`gpsd` per the
instructions above, `--demo` mode will additionally exercise the *real*
ffmpeg-framed audio pipeline (as a synthetic tone) and honest "tool
installed but no device" detection — closer to, but still short of, real
RF and GPS hardware.

### Resource usage (measured on the dev machine)

Measured with `scripts/measure_resources.sh` on the x86_64 dev machine
described above (Linux Mint 22.3, 4 cores, 7.7 GB RAM) — **not** a Pi 4B,
and CPU numbers on a Pi 4B's slower cores will be higher even for identical
work. Treat this as evidence the design is lightweight, not as a Pi 4B
number:

| Scenario | RSS | CPU |
|---|---|---|
| `--demo`, idle (no listen session, GPS demo loop ticking every 1s) | ~36–37 MB | settles under 2% after the first second |
| `--demo`, actively "listening" (ffmpeg tone pipeline running) | ~48 MB for the Watchtower process; ffmpeg itself adds another ~48 MB while active | ffmpeg briefly spikes to ~10% on start, settles low |

For comparison, this entire footprint is a small fraction of the Pi 4B's
2 GB. The real `rtl_fm`/`rtl_power` binaries are compiled C and have their
own well-known modest footprint independent of Watchtower.

A real Raspberry Pi 4B measurement (and a measurement with the real
`rtl_fm` pipeline, not the demo tone) is still needed and is called out in
the final delivery report as follow-up before relying on these numbers for
capacity planning on the actual target hardware.

## Project layout

See "Directory structure" in [ARCHITECTURE.md](ARCHITECTURE.md).

## Eventual installation model (not yet implemented)

Phase 6 of the roadmap covers packaging this as a systemd service plus a
Chromium kiosk launch configuration, so the Pi boots directly into
Watchtower fullscreen. For now, run it manually as shown above.

## Security notes

- Binds to `127.0.0.1` by default; LAN access requires the explicit
  `--lan` flag.
- Never requires root — SDR access is via udev rules, GPS access is via
  gpsd.
- No telemetry, analytics, or outbound network calls anywhere in the code.
