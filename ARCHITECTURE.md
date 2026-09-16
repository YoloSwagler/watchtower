# Watchtower — Architecture

Watchtower is a lightweight, offline-first SDR listening post and field
receiver for a Raspberry Pi 4B (2 GB RAM) driving a 7" 1024x600 display in
kiosk Chromium. This document describes the architecture as of the first
implemented vertical slice (Phase 1) and the reasoning behind the major
technical decisions. It will be updated as later phases land.

## Relationship to INTERCEPT

[INTERCEPT](https://github.com/smittix/intercept) was reviewed for
architectural patterns only — specifically its Listening Post module
(`routes/listening_post/`), GPS/gpsd integration (`routes/gps.py`,
`utils/gps.py`), and hardware documentation. No INTERCEPT code, assets, or
dependencies are reused anywhere in this project:

- INTERCEPT is a large Flask + Flask-SocketIO application covering dozens of
  unrelated SIGINT modes (ADS-B, AIS, Bluetooth, Wi-Fi, pagers, TSCM, satellite
  tracking, etc). Watchtower deliberately implements only the listening-post
  subset the user asked for, on a different (async, single-process) stack.
- Useful patterns confirmed by reading INTERCEPT and carried forward as
  *ideas*, not code: exclusive SDR claim/release around a single dongle,
  killing whole process groups (`os.killpg`) rather than single PIDs so
  `rtl_fm`/`ffmpeg` pipelines don't orphan, gpsd (not raw serial) as the GPS
  interface, and streaming demodulated audio to the browser as chunked WAV
  over a plain HTTP response instead of standing up Icecast.
- Everything else (module layout, web framework, process manager, state
  model, frontend) is original and written to fit the Pi 4B/2GB constraint
  and the single-purpose scope of this project.

INTERCEPT is GPL-3.0-licensed. Since no code or assets from it are copied,
vendored, or adapted, this has no licensing implications for Watchtower.

## Design priorities

1. **Fits in 2 GB of RAM on a Pi 4B, with margin.** One Python process, no
   Docker, no database server, no build toolchain.
2. **Works with zero network connectivity**, including on first boot.
3. **Receive-only.** No transmit, replay, decrypt, or attack tooling of any
   kind — scope is a listening post and field GPS readout.
4. **Small and legible codebase** over a flexible/pluggable one. One dongle,
   one operator, one browser tab.

## Language / framework choice

| Layer | Choice | Why |
|---|---|---|
| Backend | Python 3 + **aiohttp** | Single async process can serve HTTP (REST + static files), an audio stream, and (later) WebSocket spectrum data without extra app servers, extra threads-as-workers, or a WSGI/ASGI split. Alternatives considered: **Flask** (needs a separate WSGI server plus `gevent`/`eventlet`/Flask-SocketIO to do concurrent streaming well — more moving parts, more RAM); **FastAPI/Starlette** (fine, but pulls in Pydantic/Starlette's dependency graph for validation we don't need at this scale — aiohttp's request parsing is enough for a handful of simple JSON endpoints); a raw `asyncio` + `http.server` — plausible, but reimplements routing/WebSocket handling that aiohttp already provides in the standard-library-adjacent footprint we want. aiohttp ships its own WebSocket support and streaming `web.StreamResponse`, so no `Flask-SocketIO`/`gevent`/`eventlet` is needed. |
| Frontend | Vanilla HTML/CSS/JS, no framework, no build step | React/Vue/Angular/Electron are explicitly out of scope for a Pi 4B kiosk display. A single static page with plain JS `fetch`/`WebSocket` calls is trivial to serve, trivial to audit, and has zero build tooling to keep working offline. |
| DSP / SDR control | External native tools (`rtl_test`, `rtl_fm`, later `rtl_power`) | These are already-optimized C implementations; reimplementing FM/SSB demodulation or FFT spectrum analysis in Python on a Pi 4B would be slower and far more code for no benefit. Python's job is orchestration (spawn, own, tear down, parse), not DSP. |
| Audio bridge | `ffmpeg` (PCM → WAV framing only) | `rtl_fm` emits raw signed 16-bit PCM. `ffmpeg` wraps it in a streamable WAV container so Chromium's `<audio>` element can play it directly over HTTP with no browser-side decoding work and no extra JS audio library. |
| GPS | `gpsd` (system daemon) + a small hand-rolled async client for its JSON protocol | Decouples Watchtower from the exact serial device path and NMEA parsing details; gpsd already handles device enumeration, baud detection, and NMEA/UBX parsing for u-blox chipsets. A ~150-line asyncio TCP client for gpsd's `?WATCH=` JSON protocol avoids adding a third-party gpsd client dependency (`gps`/`gps3`/`gpsdclient`) for what is a small, stable, well-documented line protocol. |

### Why not the "obvious" heavier options

- **Docker**: adds an image build/pull step (network-dependent, contradicts
  offline-first), a container runtime daemon, and USB device passthrough
  complexity for the RTL-SDR — all avoidable since this runs on exactly one
  known Pi.
- **PostgreSQL**: there is no relational data model yet worth a database
  server. Phase 1 has no persistence at all; later phases (signal log,
  presets) will use SQLite (in-process, zero-daemon, file-based) if/when
  persistence is actually needed.
- **Icecast**: adds a second server process, a mount-point/auth config step,
  and 2–10 seconds of latency (per INTERCEPT's own docs). A direct
  `rtl_fm | ffmpeg` pipe streamed as chunked HTTP WAV is simpler to operate,
  has one fewer process to crash/orphan, and is lower latency.
- **Flask-SocketIO**: requires `eventlet`/`gevent` monkey-patching to get
  real concurrency, which fights with subprocess management and adds a
  non-trivial dependency footprint. aiohttp's native `asyncio` + WebSocket
  support does the same job with fewer parts.

## Process model

Watchtower runs as **one Python process** (`python3 -m watchtower`). Within
that process:

- The aiohttp event loop serves HTTP requests, the static UI, and (this
  phase) the audio stream.
- The SDR and GPS managers are asyncio-native objects living in
  `app['sdr']` / `app['gps']`, not separate processes or threads.
- Actual RF work happens in **subprocesses** the SDR manager owns and
  supervises (`rtl_fm`, `ffmpeg`, and in later phases `rtl_power`). These
  are the only extra OS processes Watchtower ever creates, and only while a
  receive operation is active.
- `gpsd` is expected to be running as its own system service (or started
  manually per the README) — Watchtower is a gpsd *client* only. Phase 1
  does not launch or manage the gpsd daemon itself (see "GPS lifecycle"
  below for why).

No background daemons, no worker pool, no message queue, no cron. Idle RAM
footprint is one Python interpreter plus aiohttp.

## SDR resource management

`watchtower/sdr/manager.py` defines `SDRManager`, the single owner of the
RTL-SDR dongle. Rules it enforces:

- **One active receive operation at a time.** The manager holds a state
  machine (`IDLE` → `LISTENING` → `IDLE`, with `SCANNING` reserved for a
  later phase) and refuses to let two RF-owning subprocess pipelines exist
  simultaneously. Starting a new listen session first tears down whatever
  the manager currently owns, waits for the OS to release the USB device,
  then starts the new pipeline. This mirrors INTERCEPT's "stop scanning to
  start listening" behaviour, generalized to a single owner rather than a
  claim/release registry across many modes — Watchtower only has one
  RF-owning mode in this phase, so a simpler single-slot state machine is
  sufficient (a multi-slot claim registry becomes necessary once scanning
  and listening can compete in Phase 3, and will be added then).
- **Every subprocess is tracked.** `watchtower/sdr/process.py` provides
  `ManagedProcess`, a thin wrapper around
  `asyncio.create_subprocess_exec(..., start_new_session=True)`. Starting a
  new session gives each pipeline its own process group, so stopping it
  means signalling the *group* (`os.killpg`, `SIGTERM` then `SIGKILL` after
  a timeout) rather than a single PID — this is what prevents an `rtl_fm`
  or `ffmpeg` child from surviving its parent if either half of a piped
  pair dies oddly.
- **Cleanup on every exit path.** The aiohttp app registers an `on_cleanup`
  hook that calls `SDRManager.shutdown()`, which terminates any live
  pipeline. `SDRManager` also tracks its `ManagedProcess` instances in a set
  so a crash partway through startup can't leave an orphan: if the second
  process in a pipeline fails to start, the first is torn down too.
- **Detection does not fight with an active session.** Device detection
  (`rtl_test`) briefly opens the dongle, so it is routed through the same
  manager and only actually runs `rtl_test` when the manager is `IDLE`.
  While `LISTENING`, `/api/sdr/devices` returns the last cached detection
  result with `"stale": true` rather than trying to reclaim the device.

## GPS lifecycle

`watchtower/gps/manager.py` (`GPSManager`) is a gpsd client, not a device
driver:

- It first checks whether gpsd is already reachable on `127.0.0.1:2947`. If
  so, it opens the `?WATCH={"enable":true,"json":true}` stream and parses
  `TPV` (position/fix) and `SKY` (satellite) JSON messages as they arrive.
- If gpsd is not reachable, it looks for a plausible serial device
  (`/dev/ttyUSB*`, `/dev/ttyACM*`) so the UI can distinguish **"no GPS
  hardware present"** from **"hardware is there but gpsd isn't running"**
  from **"gpsd is running but has no fix yet."**
- **Phase 1 deliberately does not launch `gpsd` itself.** INTERCEPT does
  this, but it means the application spawns and manages a system daemon,
  which cuts against "avoid unnecessary background daemons" and "don't
  modify the host unexpectedly" for a first slice. Instead, the README
  documents the one-line command (and optional systemd unit) to run gpsd
  against the u-blox device. Auto-starting gpsd when a device is detected
  but idle is a reasonable Phase 2 addition (tracked in ROADMAP.md) once
  the manual path is proven on real hardware.
- GPS failures (no gpsd, no device, connection drop) only ever affect
  `app['gps']` state. They are surfaced to the UI as a status string and
  never raise into, block, or disable the SDR/audio code path.

## Browser communication

| Purpose | Mechanism | Why |
|---|---|---|
| UI shell, JS, CSS, icons | Static files served by aiohttp (`web.static`) | No CDN, no build step, works with the network off. |
| SDR/GPS/health status | Plain REST (`GET /api/status`), polled by the browser every ~2s | At this scale (one browser tab, a handful of small JSON fields) a 2-second poll is simpler to reason about, test, and debug than a WebSocket channel, and costs essentially nothing on a Pi 4B. WebSocket push is reserved for Phase 3+ (spectrum/waterfall bins arriving many times a second), where polling would be wasteful — see ROADMAP.md. |
| Control actions (tune, start/stop, mode/gain/squelch/ppm) | REST `POST` endpoints, one per action | Simple, cacheable-never, easy to test with `curl`. |
| Demodulated audio | `GET /api/audio/stream` → `aiohttp.web.StreamResponse` emitting a single unbounded WAV stream | Matches the `<audio src="...">` model Chromium already understands; no MSE/WebAudio plumbing needed. The response ends (and the `<audio>` element naturally stops) when the SDR manager tears down the pipeline. |

## Audio path

```
rtl_fm  --(raw s16le PCM via pipe)-->  ffmpeg  --(WAV framing, stdout)-->  aiohttp StreamResponse  --(HTTP chunked)-->  <audio> element
```

- `rtl_fm` is invoked with the demodulation mode, frequency, gain, squelch,
  and ppm correction the operator set, plus a sample rate chosen per mode
  (170 kHz capture / 32 kHz output for WFM; 24 kHz for NFM/AM; 12 kHz for
  USB/LSB — matching common `rtl_fm` usage for each mode).
  `start_new_session=True` gives it its own process group as above.
- `ffmpeg` reads raw PCM on stdin and writes a streaming WAV (unknown-length
  `RIFF`/`data` header, PCM frames after) to stdout — no transcoding to a
  lossy codec, no extra latency budget spent on compression given the
  bandwidth involved is tiny (tens of kbps).
- The aiohttp route reads `ffmpeg`'s stdout in a loop and forwards each
  chunk to the client as it arrives — no buffering beyond OS pipe buffers,
  so latency is dominated by `rtl_fm`'s own internal buffering (typically
  well under a second).
- Only one audio client is supported at a time, matching the "one operator,
  one dongle" model. ffmpeg's stdout is a single pipe, so two concurrent
  readers would each get an arbitrary, non-overlapping slice of the same
  byte stream rather than a copy of it — the SDR manager therefore tracks
  whether a consumer is already attached and a second concurrent request
  gets no bytes rather than corrupting the active stream. This was found
  and fixed by actually opening two connections to `/api/audio/stream`
  during Phase 1 testing, not assumed safe from the code alone.
- Demo mode's `DemoSDRManager` produces a synthetic tone (sine wave plus a
  little noise) through the exact same `ffmpeg`-framed WAV path, so the
  entire browser audio pipeline — including Chromium autoplay/`<audio>`
  behaviour — can be exercised with zero RF hardware attached. `ffmpeg` is
  still required for demo audio; if it's missing, demo mode reports audio
  as unavailable rather than silently faking a stream.
- Stopping a listen session that nobody was actually reading (no browser
  ever connected to `/api/audio/stream`) is a real, if narrow, case: ffmpeg
  installs a SIGTERM handler that tries to flush pending output before
  exiting, and that flush blocks forever on a full, unread pipe. This was
  found by running repeated start/stop cycles end to end (not from reading
  the code) — the process only died on the SIGKILL that follows our
  termination timeout, adding a multi-second stall per cycle. The fix
  (`process.drain_discard`, used from both managers' `_stop_internal`) is
  to actively read-and-discard that process's own stdout during shutdown
  whenever no audio_stream() consumer is already attached — never both at
  once, which would reintroduce the two-readers-race bug above.
- Relatedly: `async for` exiting a generator via an exception does **not**
  close it (only running it to exhaustion or an explicit `.aclose()` does).
  `web/audio.py`'s stream handler now closes its `audio_stream()` generator
  in a `finally` block for exactly this reason — without it, a browser
  aborting a connection mid-stream (e.g. retuning quickly, which replaces
  the `<audio>` element's `src` and cancels the in-flight fetch) could
  leave the "one audio consumer at a time" flag stuck `True` forever,
  permanently refusing every future listener. Found the same way: by
  clicking Start/Stop rapidly in the browser and watching real network
  requests get aborted mid-flight, not by inspecting the code.
- Residual known limitation: clicking Start/Stop faster than a human
  realistically would (validated with scripted rapid-fire clicks, well
  under normal reaction time) can still occasionally leave one stop cycle
  needing the SIGKILL fallback instead of a clean SIGTERM, adding roughly
  a 2-second delay to that one Stop call. The SDR is still always released
  and no process is ever left orphaned — this is a latency edge case, not
  a correctness one — but it's called out here rather than claimed fixed,
  since it wasn't fully eliminated by the fixes above.

## Hardware capability honesty

Tuning range depends entirely on the attached dongle and whether it has
direct-sampling / an upconverter — a stock RTL-SDR (R820T/R820T2 tuner)
cannot receive HF (below ~24 MHz) without one. Watchtower:

- Exposes USB/LSB (and optional AM) as demodulation *modes* the software
  supports, independent of whether the currently attached hardware can
  actually reach the frequency being requested.
- Shows a non-blocking advisory in the UI when the requested frequency is
  below ~24 MHz, noting that direct sampling or an upconverter is likely
  required and that this cannot be verified from software alone.
- Never claims a specific tuning range for "the RTL-SDR" as a category —
  the detected device's actual tuner is not something `rtl_test`/`rtl_fm`
  reliably reports, so the docs (`README.md`) explain the limitation rather
  than the UI guessing at it.

## Directory structure

```
watchtower/
├── README.md
├── ARCHITECTURE.md
├── ROADMAP.md
├── requirements.txt          # runtime deps (just aiohttp)
├── requirements-dev.txt      # pytest etc., dev machine only
├── pytest.ini
├── scripts/
│   └── measure_resources.sh  # RSS/CPU sampling helper used for the Phase 1 report
├── watchtower/
│   ├── __init__.py
│   ├── __main__.py           # `python3 -m watchtower` entrypoint
│   ├── app.py                # aiohttp Application factory + wiring
│   ├── config.py             # AppConfig dataclass, CLI/env parsing
│   ├── logging_setup.py      # structured logging configuration
│   ├── sdr/
│   │   ├── __init__.py
│   │   ├── base.py           # SDRManagerBase ABC, SDRDevice/ReceiverState dataclasses
│   │   ├── detect.py         # rtl_test invocation + output parsing
│   │   ├── process.py        # ManagedProcess (subprocess group lifecycle)
│   │   ├── manager.py        # SDRManager (real hardware)
│   │   └── demo.py           # DemoSDRManager (simulated hardware)
│   ├── gps/
│   │   ├── __init__.py
│   │   ├── base.py           # GPSManagerBase ABC, GPSFix dataclass
│   │   ├── gpsd_client.py    # minimal asyncio gpsd JSON-protocol client
│   │   ├── manager.py        # GPSManager (real, via gpsd)
│   │   └── demo.py           # DemoGPSManager (simulated fix)
│   ├── system/
│   │   └── health.py         # tool/dependency presence checks for /api/status
│   └── web/
│       ├── routes.py         # REST endpoint handlers
│       ├── audio.py          # /api/audio/stream handler
│       └── static/
│           ├── index.html
│           ├── css/app.css
│           └── js/app.js
└── tests/
    ├── conftest.py
    ├── test_sdr_detect.py
    ├── test_sdr_process.py
    ├── test_sdr_demo_manager.py
    ├── test_gpsd_client.py
    ├── test_gps_demo_manager.py
    └── test_api_routes.py
```

## Hardware abstraction for testability

`SDRManagerBase` and `GPSManagerBase` define the interface the web layer
depends on (`snapshot()`, `list_devices()`/status, `start_listening()`/
`stop_listening()`, etc). `SDRManager`/`GPSManager` are the hardware-backed
implementations; `DemoSDRManager`/`DemoGPSManager` implement the same
interface with generated data. `watchtower/app.py` picks one pair at
startup based on `AppConfig.demo`. The web route handlers, and every test
that isn't specifically about parsing `rtl_test` output or the gpsd wire
protocol, only ever talk to the abstract interface — so almost all
application logic is testable on a machine with no SDR or GPS attached
at all (which is also how this Phase 1 was developed and tested).

Demo mode is an explicit `--demo` flag, not an automatic fallback when
hardware is absent. A field instrument should never silently substitute
fake data for a real reading — if the hardware isn't there, the real
managers report that honestly (`"rtl_test not found"` /
`"no supported devices found"` / `"gpsd unreachable"`) rather than the
application quietly switching to simulation.

## Configuration & security defaults

- Default bind address is `127.0.0.1`; LAN access requires an explicit
  `--lan` flag (documented as a conscious opt-in, since this device is
  designed to run untrusted/open networks in the field).
- The application never requires root. RTL-SDR access is via standard udev
  rules (documented in README.md) granting the `plugdev`/relevant group
  read/write on the device node, the same approach `rtl-sdr` upstream
  recommends. GPS access is via gpsd, which itself handles device
  permissions.
- No telemetry, analytics, or outbound network calls exist anywhere in the
  codebase.

## Known Phase 1 limitations (by design, see ROADMAP.md)

No wideband scanning, no automatic signal detection/list, no spectrum or
waterfall visualization, no offline mapping, no recording, no presets, no
session logging, no multi-SDR-type support (RTL-SDR only). These are
explicitly deferred, not accidentally missing.
