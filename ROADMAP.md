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

## Phase 2 — Field-test hardening, wideband scanner & bookmarks ✅ (this delivery)

Scoped from real hardware field-test results (Pi 4B, RTL-SDR, u-blox 7 GPS,
1024x600 Chromium, offline) rather than as originally sketched above —
gain/hot-plug fixes turned out to matter more immediately than gpsd
auto-start, and the wideband scanner (originally planned for Phase 3) was
pulled forward as Phase 2's primary deliverable. See ARCHITECTURE.md's
"Phase 2" section for full design reasoning.

- **Gain fix**: `gain_db` is now `float | None` (`None` = automatic tuner
  gain, matching `rtl_fm`/`rtl_power`'s real convention); Phase 1 silently
  sent manual `0 dB`, which field-tested as unusable. Gain can now be
  changed while listening (`set_gain`), which transparently restarts the
  `rtl_fm | ffmpeg` pipeline in place (frequency/mode/squelch/ppm
  preserved, no rtl_tcp or new architecture introduced).
- **RTL-SDR hot-plug recovery**: a new `ReceiverState.DISCONNECTED` and a
  lightweight background monitor (checks tracked-process liveness while
  active, `rtl_test` enumeration only while idle, every 3s) mean the
  existing browser session recovers automatically after an unplug/replug —
  no manual Chromium refresh required.
- **Wideband scanner**: `rtl_power`-driven sweep over an operator-defined
  frequency range, added as a second RF-owning mode inside the same
  `SDRManager` single-owner state machine (new `ReceiverState.SCANNING`;
  starting either mode tears down whatever the manager currently owns).
  Parses `rtl_power` CSV output into completed sweeps, estimates a noise
  floor, groups adjacent active bins into signals with an approximate
  center frequency/SNR/last-seen time, and tracks them with TTL-based
  expiry (bounded memory, no raw sweep history retained). Compact
  frequency/SNR/power/last-seen table in the UI; selecting a signal stops
  the scan and starts listening; stopping listening can return to the
  remembered scan.
- **Saved frequencies (bookmarks)**: named presets (frequency, mode, gain
  or Auto, squelch, optional note) in a single JSON file outside the
  application source tree, with atomic writes. Create/rename/delete;
  selecting one tunes and starts listening in one action. A detected or
  currently-tuned frequency can be saved directly.
- Demo mode extended end to end: synthetic scanning (sweep/noise floor/
  signals/last-seen) runs the exact same detection/tracking code real
  scanning does — only the sweep *input* is fake, isolated in
  `watchtower/scanner/demo_source.py`.
- Automated tests added for: `rtl_power` parsing, noise-floor estimation,
  signal detection/grouping/SNR, scan start/stop/repeated cycles, scan↔
  listen transitions, invalid frequency ranges, SDR disconnect during
  listening/scanning, reconnect, auto/manual gain, live gain change,
  bookmark CRUD/persistence, demo scanning. All Phase 1 tests still pass
  unmodified in behavior (only the `gain_db` default changed, from `0.0`
  meaning manual to `None` meaning auto).

**Deferred out of this phase, unchanged from the original Phase 2 sketch
above:** gpsd auto-start, on-screen numeric keypad / broader touch
ergonomics pass, mapped `rtl_fm`/`ffmpeg` stderr message catalog beyond
Phase 1's existing busy/missing/permission cases. These remain reasonable
future small phases but weren't required by the field-test results or the
scanner deliverable.

## Phase 3 — Spectrum & waterfall visualization

- Canvas-based spectrum line + scrolling waterfall. Phase 2's scanner
  output (`Sweep`/`DetectedSignal` from `watchtower/scanner/`) was
  deliberately kept structured (parallel frequency/power arrays, not an
  opaque blob) so this phase can consume it without replacing the scanner
  backend — but no chart/canvas code was written in Phase 2 itself.
- This is the point a WebSocket channel gets added (spectrum/sweep updates
  arrive far more often than the 2s status poll can reasonably carry) — REST
  stays for control actions, WebSocket carries the high-frequency data.
- Click-to-tune directly on the spectrum/waterfall.
- Explicit CPU budget: target update rate is chosen for *useful RF
  information at low CPU cost*, not visual smoothness — measured on the Pi
  4B, not assumed.

## Phase 4 — Offline mapping (investigation)

- Evaluate local MBTiles (or an equivalent pre-baked offline tile set) for
  showing GPS position on a map without any internet tile service.
- If the storage/CPU cost isn't justified for a receiver-focused field
  tool, document that decision instead of building it — this phase is
  explicitly an investigation, not a commitment.

## Phase 5 — Deployment hardening

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
