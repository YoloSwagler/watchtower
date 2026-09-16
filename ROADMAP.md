# Watchtower — Roadmap

Small, independently testable phases. Nothing here is committed to a
timeline; phases are ordered by dependency, not by date. See
`ARCHITECTURE.md` for the reasoning behind the technical choices referenced
below.

## Phase 1 — Vertical slice: app shell, hardware detection, manual receive ✅ (this delivery)

- aiohttp application serving the UI shell and REST API from one process.
- 1024x600-first UI: header status bar (SDR / GPS / audio state), receiver
  control panel, GPS panel, system health panel.
- RTL-SDR detection (`rtl_test`) with device list and busy/absent states.
- gpsd detection and connection; fix status, coordinates, altitude,
  satellite counts when available. GPS absent/no-fix never blocks SDR use.
- `SDRManager`: single-owner state machine, tracked subprocesses, clean
  start/stop, no orphaned `rtl_fm`/`ffmpeg` processes across repeated
  cycles.
- Manual frequency entry, mode selection (NFM, WFM, USB, LSB, optional AM),
  gain/squelch/PPM controls.
- Start Listening / Stop Listening, with browser audio playback via chunked
  WAV streaming.
- Demo mode (`--demo`) simulating SDR + GPS + audio tone end-to-end.
- Automated tests for everything not requiring physical hardware.
- Baseline RAM/CPU measurement, documented in README.md.

**Acceptance:** see the Phase 1 section of the final report — a person can
start the app, see accurate hardware/GPS status, and (with an RTL-SDR
attached) tune, listen, and stop cleanly; without hardware, demo mode
exercises the same UI paths.

## Phase 2 — Controls polish & GPS lifecycle

- Auto-start `gpsd` when a likely u-blox device is detected but gpsd isn't
  running yet (with a visible "starting GPS…" state), instead of requiring
  the operator to start it by hand. Keep the manual path documented as a
  fallback.
- Persist last-used frequency/mode/gain/squelch/ppm across restarts (a
  single small JSON or SQLite file — no server, no ORM).
- Named frequency presets (a short user-editable list, not a scanning
  database) with one-tap tuning.
- Improve error surfacing: map common `rtl_fm`/`ffmpeg` stderr patterns
  (device busy, no device, permission denied) to specific human-readable
  messages instead of a generic failure.
- Basic keyboard/touch ergonomics pass for the 7" display (bigger hit
  targets, on-screen numeric keypad for frequency entry so a physical
  keyboard isn't required in the field).

## Phase 3 — Wideband sweep & signal list

- `rtl_power`-driven sweep over an operator-defined frequency range,
  respecting the single-owner SDR state machine (sweep pauses/stops
  automatically when the operator selects a signal to listen to, and
  resumes/restarts cleanly when they stop listening — no simultaneous
  sweep + listen from one dongle).
- Parse `rtl_power` CSV output into a signal list: frequency, power (dBm),
  approximate SNR (relative to a rolling noise floor estimate), last-seen
  timestamp. Sorted, filterable list in the UI.
- Clicking a detected signal tunes the receiver to it and starts listening
  (reusing the Phase 1 audio path).
- This is the point where a WebSocket channel is added (spectrum/sweep
  updates arrive far more often than the 2s status poll can reasonably
  carry) — REST stays for control actions, WebSocket carries the
  high-frequency data.

## Phase 4 — Spectrum & waterfall visualization

- Canvas-based spectrum line + scrolling waterfall, fed by the Phase 3
  WebSocket channel (from the sweep and/or a continuous narrower-band power
  capture while listening).
- Click-to-tune directly on the spectrum/waterfall.
- Explicit CPU budget: target update rate is chosen for *useful RF
  information at low CPU cost*, not visual smoothness — measured on the Pi
  4B, not assumed.

## Phase 5 — Offline mapping (investigation)

- Evaluate local MBTiles (or an equivalent pre-baked offline tile set) for
  showing GPS position on a map without any internet tile service.
- If the storage/CPU cost isn't justified for a receiver-focused field
  tool, document that decision instead of building it — this phase is
  explicitly an investigation, not a commitment.

## Phase 6 — Deployment hardening

- systemd unit for the Watchtower service (start on boot, restart on
  crash, clean shutdown).
- Chromium kiosk launch configuration for the 7" display.
- udev rules and setup script for RTL-SDR permissions and DVB driver
  blacklist, packaged as a documented one-time setup step (not something
  the app does at runtime).
- Optional session/activity logging (what was tuned, when, GPS position at
  the time) — receive-only metadata, no recording of audio content unless
  explicitly requested in a later phase.

## Explicitly out of scope (not phases, just no)

Transmitting, signal replay, decryption, Wi-Fi/Bluetooth attack tooling, and
unrelated SIGINT modes (ADS-B, AIS, pagers, etc.). If a future need for one
of these arises, it belongs in a different, purpose-built project, not
grafted onto this one.
