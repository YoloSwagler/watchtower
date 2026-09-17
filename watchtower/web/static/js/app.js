"use strict";

const STATUS_POLL_MS = 2000;
const JSON_HEADERS = { "Content-Type": "application/json" };

// Mirrors watchtower/sdr/gain.py's GAIN_PRESETS_DB. Duplicated rather than
// fetched from the backend: this is a static, rarely-changing constant and
// the project deliberately has no frontend build step / shared-module
// mechanism between Python and vanilla JS.
const GAIN_PRESETS_DB = [
  0.0, 0.9, 1.4, 2.7, 3.7, 7.7, 8.7, 12.5, 14.4, 15.7,
  16.6, 19.7, 20.7, 22.9, 25.4, 28.0, 29.7, 32.8, 33.8,
  36.4, 37.2, 38.6, 40.2, 42.1, 43.4, 43.9, 44.5, 48.0, 49.6,
];

const el = (id) => document.getElementById(id);

const demoBadge = el("demo-badge");
const pillSdr = el("pill-sdr");
const pillGps = el("pill-gps");
const pillAudio = el("pill-audio");
const pillClockValue = el("pill-clock").querySelector(".pill-value");

const bannerError = el("banner-error");
const bannerHf = el("banner-hf");

const tabButtons = Array.from(document.querySelectorAll(".tab-btn"));
const tuneView = el("tune-view");
const scanView = el("scan-view");

const freqReadoutEl = el("freq-readout");
const tuneForm = el("tune-form");
const freqInput = el("freq-input");

const modeGroup = el("mode-group");
const modeButtons = Array.from(modeGroup.querySelectorAll(".mode-btn"));

const gainInput = el("gain-input");
const squelchInput = el("squelch-input");
const ppmInput = el("ppm-input");
const deviceSelect = el("device-select");

const btnStart = el("btn-start");
const btnStop = el("btn-stop");
const btnReturnToScan = el("btn-return-to-scan");
const btnRefreshDevices = el("btn-refresh-devices");

const audioPlayer = el("audio-player");

const scanStartInput = el("scan-start-input");
const scanEndInput = el("scan-end-input");
const scanBinSelect = el("scan-bin-select");
const btnScanStart = el("btn-scan-start");
const btnScanStop = el("btn-scan-stop");
const scanNoiseFloorEl = el("scan-noise-floor");
const scanSignalCountEl = el("scan-signal-count");
const scanLastSweepEl = el("scan-last-sweep");
const signalTableBody = el("signal-table-body");

const btnSaveCurrent = el("btn-save-current");
const bookmarkListEl = el("bookmark-list");
const bookmarkSaveRow = el("bookmark-save-row");
const bookmarkSaveNameInput = el("bookmark-save-name");
const btnBookmarkSaveConfirm = el("bookmark-save-confirm");
const btnBookmarkSaveCancel = el("bookmark-save-cancel");

const gpsStatusLine = document.querySelector(".gps-status-line");
const gpsStatusText = el("gps-status-text");
const gpsDot = el("gps-dot");
const gpsLat = el("gps-lat");
const gpsLon = el("gps-lon");
const gpsSats = el("gps-sats");
const gpsDevice = el("gps-device");
const gpsTime = el("gps-time");

const toolListEl = el("tool-list");

let activeTab = "tune";
let selectedMode = "nfm";
let currentSdrState = "idle";
let freqReadoutValue = null;
let statusFailureStreak = 0;
let bookmarksCache = [];
let editingBookmarkId = null;
let confirmingDeleteId = null;
let confirmingDeleteTimer = null;

const GPS_LABELS = {
  no_device: "No GPS device",
  device_no_gpsd: "Device found, gpsd not running",
  connecting: "Connecting…",
  no_fix: "No fix",
  fix_2d: "2D fix",
  fix_3d: "3D fix",
  error: "GPS error",
};

function setPillState(pillEl, stateClass, valueText) {
  pillEl.classList.remove("state-ok", "state-warn", "state-error", "state-disconnected");
  if (stateClass) pillEl.classList.add(stateClass);
  pillEl.querySelector(".pill-value").textContent = valueText;
}

function showError(message) {
  bannerError.hidden = false;
  bannerError.textContent = message;
}

function clearError() {
  bannerError.hidden = true;
  bannerError.textContent = "";
}

function setActiveTab(tab) {
  activeTab = tab;
  for (const btn of tabButtons) btn.classList.toggle("active", btn.dataset.tab === tab);
  tuneView.hidden = tab !== "tune";
  scanView.hidden = tab !== "scan";
}

function setActiveMode(mode) {
  selectedMode = mode;
  for (const btn of modeButtons) {
    btn.classList.toggle("active", btn.dataset.mode === mode);
  }
}

function renderFreqReadout() {
  freqReadoutEl.textContent = freqReadoutValue == null ? "— . — — — —" : freqReadoutValue.toFixed(4);
}

function populateGainSelect() {
  gainInput.innerHTML = "";
  const autoOpt = document.createElement("option");
  autoOpt.value = "";
  autoOpt.textContent = "Auto";
  gainInput.appendChild(autoOpt);
  for (const g of GAIN_PRESETS_DB) {
    const opt = document.createElement("option");
    opt.value = String(g);
    opt.textContent = g.toFixed(1) + " dB";
    gainInput.appendChild(opt);
  }
}

function currentGainDb() {
  return gainInput.value === "" ? null : parseFloat(gainInput.value);
}

function getFormParams() {
  return {
    frequency_mhz: parseFloat(freqInput.value),
    mode: selectedMode,
    device_index: parseInt(deviceSelect.value || "0", 10),
    gain_db: currentGainDb(),
    squelch: parseInt(squelchInput.value || "0", 10),
    ppm: parseInt(ppmInput.value || "0", 10),
  };
}

function formatAgeSeconds(epochSeconds) {
  if (epochSeconds == null) return "—";
  const age = Date.now() / 1000 - epochSeconds;
  if (age < 1.5) return "just now";
  if (age < 60) return Math.round(age) + "s ago";
  return Math.round(age / 60) + "m ago";
}

function renderDevices(devices, stale) {
  const previousSelection = deviceSelect.value;
  deviceSelect.innerHTML = "";

  if (devices.length === 0) {
    const opt = document.createElement("option");
    opt.value = "0";
    opt.textContent = "No device detected";
    deviceSelect.appendChild(opt);
  } else {
    for (const d of devices) {
      const opt = document.createElement("option");
      opt.value = String(d.index);
      opt.textContent = `#${d.index} ${d.name}${stale ? " (cached)" : ""}`;
      deviceSelect.appendChild(opt);
    }
    const stillValid = Array.from(deviceSelect.options).some((o) => o.value === previousSelection);
    if (stillValid) deviceSelect.value = previousSelection;
  }
}

function renderSignals(scan) {
  signalTableBody.innerHTML = "";
  if (!scan || !scan.signals || scan.signals.length === 0) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 4;
    td.className = "muted";
    td.textContent = scan ? "No active signals yet." : "Start a scan to find signals.";
    tr.appendChild(td);
    signalTableBody.appendChild(tr);
    return;
  }

  const nowSec = Date.now() / 1000;
  for (const s of scan.signals) {
    const age = nowSec - s.last_seen;
    const tr = document.createElement("tr");
    tr.className = "signal-row";
    if (s.snr_db >= 20 && age < 6) tr.classList.add("signal-strong");
    if (age > 12) tr.classList.add("signal-stale");

    const cells = [s.frequency_mhz.toFixed(4), s.snr_db.toFixed(1) + " dB", s.power_db.toFixed(1) + " dBm", formatAgeSeconds(s.last_seen)];
    for (const text of cells) {
      const td = document.createElement("td");
      td.textContent = text;
      tr.appendChild(td);
    }
    tr.addEventListener("click", () => selectSignal(s.frequency_mhz));
    signalTableBody.appendChild(tr);
  }
}

function renderBookmarks() {
  bookmarkListEl.innerHTML = "";
  if (bookmarksCache.length === 0) {
    const li = document.createElement("li");
    li.className = "muted";
    li.textContent = "No saved frequencies yet.";
    bookmarkListEl.appendChild(li);
    return;
  }

  for (const b of bookmarksCache) {
    const li = document.createElement("li");
    li.className = "bookmark-item";

    if (editingBookmarkId === b.id) {
      li.classList.add("editing");
      const input = document.createElement("input");
      input.type = "text";
      input.className = "bookmark-edit-input";
      input.maxLength = 40;
      input.value = b.name;
      input.addEventListener("keydown", (e) => {
        e.stopPropagation();
        if (e.key === "Enter") {
          e.preventDefault();
          confirmRename(b, input.value);
        } else if (e.key === "Escape") {
          editingBookmarkId = null;
          renderBookmarks();
        }
      });
      input.addEventListener("blur", () => confirmRename(b, input.value));
      input.addEventListener("click", (e) => e.stopPropagation());
      li.appendChild(input);
      bookmarkListEl.appendChild(li);
      input.focus();
      input.select();
      continue;
    }

    const main = document.createElement("div");
    main.className = "bookmark-main";
    const nameEl = document.createElement("div");
    nameEl.className = "bookmark-name";
    nameEl.textContent = b.name;
    const detailEl = document.createElement("div");
    detailEl.className = "bookmark-detail";
    const gainText = b.gain_db == null ? "Auto" : b.gain_db.toFixed(1) + "dB";
    detailEl.textContent = `${b.frequency_mhz.toFixed(4)} MHz · ${b.mode.toUpperCase()} · ${gainText}`;
    main.appendChild(nameEl);
    main.appendChild(detailEl);

    const actions = document.createElement("div");
    actions.className = "bookmark-actions";
    const editBtn = document.createElement("button");
    editBtn.className = "bookmark-action-btn";
    editBtn.type = "button";
    editBtn.textContent = "✎";
    editBtn.title = "Rename";
    editBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      editingBookmarkId = b.id;
      renderBookmarks();
    });

    const isConfirming = confirmingDeleteId === b.id;
    const delBtn = document.createElement("button");
    delBtn.className = "bookmark-action-btn" + (isConfirming ? " confirm-delete" : "");
    delBtn.type = "button";
    delBtn.textContent = isConfirming ? "✓" : "×";
    delBtn.title = isConfirming ? "Confirm delete" : "Delete";
    delBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      handleDeleteClick(b);
    });
    actions.appendChild(editBtn);
    actions.appendChild(delBtn);

    li.appendChild(main);
    li.appendChild(actions);
    li.addEventListener("click", () => selectBookmark(b));
    bookmarkListEl.appendChild(li);
  }
}

function applyStatus(data) {
  demoBadge.hidden = !data.demo;

  const sdr = data.sdr;
  const gps = data.gps;
  const tools = data.tools;

  currentSdrState = sdr.state;

  const sdrClass =
    sdr.state === "listening" || sdr.state === "scanning"
      ? "state-ok"
      : sdr.state === "error" || sdr.state === "disconnected"
        ? "state-error"
        : sdr.devices.length === 0
          ? "state-warn"
          : "";
  setPillState(pillSdr, sdrClass, sdr.state);

  const busyOrGone = ["listening", "starting", "scanning", "disconnected"].includes(sdr.state);
  btnStart.disabled = busyOrGone;
  btnStop.disabled = sdr.state !== "listening" && sdr.state !== "starting";
  btnScanStart.disabled = busyOrGone;
  btnScanStop.disabled = sdr.state !== "scanning";
  btnReturnToScan.hidden = !sdr.can_resume_scan || sdr.state === "scanning" || sdr.state === "starting";
  btnReturnToScan.disabled = sdr.state === "disconnected";

  if (sdr.error) {
    showError(sdr.state === "disconnected" ? "SDR disconnected. Reconnect the dongle to continue." : sdr.error);
  } else {
    clearError();
  }

  bannerHf.hidden = !sdr.hf_advisory;

  if (sdr.params && (sdr.state === "listening" || sdr.state === "starting")) {
    freqReadoutValue = sdr.params.frequency_mhz;
    setActiveMode(sdr.params.mode);
    gainInput.value = sdr.params.gain_db == null ? "" : String(sdr.params.gain_db);
    squelchInput.value = String(sdr.params.squelch);
    ppmInput.value = String(sdr.params.ppm);
  }
  renderFreqReadout();

  const audioActive = sdr.state === "listening";
  setPillState(pillAudio, audioActive ? "state-ok" : "", audioActive ? "streaming" : "idle");
  if (!audioActive && audioPlayer.hasAttribute("src")) {
    audioPlayer.pause();
    audioPlayer.removeAttribute("src");
    audioPlayer.load();
  }

  renderDevices(sdr.devices, sdr.devices_stale);
  renderSignals(sdr.scan);

  scanNoiseFloorEl.textContent = "Noise floor: " + (sdr.scan && sdr.scan.noise_floor_db != null ? sdr.scan.noise_floor_db.toFixed(1) + " dBm" : "—");
  scanSignalCountEl.textContent = "Signals: " + (sdr.scan ? sdr.scan.signals.length : "—");
  scanLastSweepEl.textContent = "Last sweep: " + (sdr.scan ? formatAgeSeconds(sdr.scan.last_sweep_at) : "—");

  btnSaveCurrent.disabled = !sdr.params || sdr.state === "disconnected";
  if (btnSaveCurrent.disabled && !bookmarkSaveRow.hidden) closeSaveCurrentRow();

  const gpsLabel = GPS_LABELS[gps.status] || gps.status;
  const gpsClass =
    gps.status === "fix_2d" || gps.status === "fix_3d"
      ? "state-ok"
      : gps.status === "no_fix" || gps.status === "connecting" || gps.status === "device_no_gpsd"
        ? "state-warn"
        : gps.status === "error"
          ? "state-error"
          : "";
  setPillState(pillGps, gpsClass, gpsLabel);
  gpsStatusText.textContent = gpsLabel;
  gpsStatusLine.className = "gps-status-line " + gpsClass;
  gpsDot.className = "dot";

  if (gps.fix) {
    gpsLat.textContent = gps.fix.latitude != null ? gps.fix.latitude.toFixed(5) + "°" : "—";
    gpsLon.textContent = gps.fix.longitude != null ? gps.fix.longitude.toFixed(5) + "°" : "—";
    gpsTime.textContent = gps.fix.time || "—";
  } else {
    gpsLat.textContent = "—";
    gpsLon.textContent = "—";
    gpsTime.textContent = "—";
  }
  gpsSats.textContent =
    gps.satellites_used != null && gps.satellites_visible != null ? `${gps.satellites_used} / ${gps.satellites_visible}` : "—";
  gpsDevice.textContent = gps.device || "—";
  gpsDevice.title = gps.device || "";

  for (const li of toolListEl.querySelectorAll("li")) {
    const ok = !!tools[li.dataset.tool];
    li.classList.toggle("tool-ok", ok);
    li.classList.toggle("tool-missing", !ok);
  }
}

async function refreshStatus() {
  try {
    const res = await fetch("/api/status");
    if (!res.ok) throw new Error(`status ${res.status}`);
    const data = await res.json();
    statusFailureStreak = 0;
    applyStatus(data);
  } catch (err) {
    statusFailureStreak += 1;
    if (statusFailureStreak >= 2) {
      showError("Lost connection to the Watchtower server. Retrying…");
    }
  }
}

async function refreshDevices() {
  try {
    const res = await fetch("/api/sdr/devices");
    const data = await res.json();
    renderDevices(data.devices, false);
  } catch (err) {
    // status polling will surface the connection problem
  }
}

async function refreshBookmarks() {
  try {
    const res = await fetch("/api/bookmarks");
    const data = await res.json();
    bookmarksCache = data.bookmarks;
    renderBookmarks();
  } catch (err) {
    // non-critical; the list just stays as it was
  }
}

async function startListeningWithParams(params) {
  clearError();
  try {
    const res = await fetch("/api/sdr/listen/start", {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify(params),
    });
    const data = await res.json();
    if (!res.ok || data.status === "error") {
      showError(data.message || "Failed to start listening.");
      return false;
    }
    audioPlayer.src = "/api/audio/stream?t=" + Date.now();
    audioPlayer.play().catch(() => {
      /* autoplay may require another user gesture; button click already is one */
    });
    return true;
  } catch (err) {
    showError("Could not reach the Watchtower server.");
    return false;
  }
}

async function startListening() {
  const params = getFormParams();
  if (!params.frequency_mhz || params.frequency_mhz <= 0 || Number.isNaN(params.frequency_mhz)) {
    showError("Enter a valid frequency first.");
    return;
  }
  await startListeningWithParams(params);
  await refreshStatus();
}

async function stopListening() {
  try {
    await fetch("/api/sdr/listen/stop", { method: "POST" });
  } catch (err) {
    showError("Could not reach the Watchtower server.");
  }
  audioPlayer.pause();
  audioPlayer.removeAttribute("src");
  audioPlayer.load();
  await refreshStatus();
}

async function selectSignal(frequencyMhz) {
  const ok = await startListeningWithParams({
    frequency_mhz: frequencyMhz,
    mode: selectedMode,
    device_index: parseInt(deviceSelect.value || "0", 10),
    gain_db: currentGainDb(),
    squelch: parseInt(squelchInput.value || "0", 10),
    ppm: parseInt(ppmInput.value || "0", 10),
  });
  if (ok) setActiveTab("tune");
  await refreshStatus();
}

async function selectBookmark(b) {
  const ok = await startListeningWithParams({
    frequency_mhz: b.frequency_mhz,
    mode: b.mode,
    gain_db: b.gain_db,
    squelch: b.squelch,
    ppm: parseInt(ppmInput.value || "0", 10),
  });
  if (ok) setActiveTab("tune");
  await refreshStatus();
}

async function startScan() {
  const startMhz = parseFloat(scanStartInput.value);
  const endMhz = parseFloat(scanEndInput.value);
  if (!startMhz || !endMhz || startMhz <= 0 || endMhz <= 0 || Number.isNaN(startMhz) || Number.isNaN(endMhz)) {
    showError("Enter a valid start and end frequency.");
    return;
  }
  clearError();
  try {
    const res = await fetch("/api/sdr/scan/start", {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify({ start_mhz: startMhz, end_mhz: endMhz, bin_khz: parseFloat(scanBinSelect.value) }),
    });
    const data = await res.json();
    if (!res.ok || data.status === "error") {
      showError(data.message || "Failed to start scan.");
    }
  } catch (err) {
    showError("Could not reach the Watchtower server.");
  }
  await refreshStatus();
}

async function stopScan() {
  try {
    await fetch("/api/sdr/scan/stop", { method: "POST" });
  } catch (err) {
    showError("Could not reach the Watchtower server.");
  }
  await refreshStatus();
}

async function returnToScan() {
  clearError();
  try {
    if (currentSdrState === "listening" || currentSdrState === "starting") {
      await fetch("/api/sdr/listen/stop", { method: "POST" });
      audioPlayer.pause();
      audioPlayer.removeAttribute("src");
      audioPlayer.load();
    }
    const res = await fetch("/api/sdr/scan/resume", { method: "POST" });
    const data = await res.json();
    if (!res.ok || data.status === "error") {
      showError(data.message || "No previous scan to resume.");
      return;
    }
    setActiveTab("scan");
  } catch (err) {
    showError("Could not reach the Watchtower server.");
  }
  await refreshStatus();
}

async function changeGainLive() {
  if (currentSdrState !== "listening") return; // otherwise it's just a setting for the next Start
  try {
    const res = await fetch("/api/sdr/listen/gain", {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify({ gain_db: currentGainDb() }),
    });
    const data = await res.json();
    if (!res.ok || data.status === "error") {
      showError(data.message || "Failed to change gain.");
    }
  } catch (err) {
    showError("Could not reach the Watchtower server.");
  }
  await refreshStatus();
}

function openSaveCurrentRow() {
  if (freqReadoutValue == null) return;
  bookmarkSaveRow.hidden = false;
  bookmarkSaveNameInput.value = "";
  bookmarkSaveNameInput.focus();
}

function closeSaveCurrentRow() {
  bookmarkSaveRow.hidden = true;
  bookmarkSaveNameInput.value = "";
}

async function confirmSaveCurrent() {
  const trimmed = bookmarkSaveNameInput.value.trim();
  if (!trimmed || freqReadoutValue == null) {
    closeSaveCurrentRow();
    return;
  }
  try {
    await fetch("/api/bookmarks", {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify({
        name: trimmed,
        frequency_mhz: freqReadoutValue,
        mode: selectedMode,
        gain_db: currentGainDb(),
        squelch: parseInt(squelchInput.value || "0", 10),
      }),
    });
  } catch (err) {
    showError("Could not reach the Watchtower server.");
  }
  closeSaveCurrentRow();
  await refreshBookmarks();
}

async function confirmRename(b, newName) {
  editingBookmarkId = null;
  const trimmed = newName.trim();
  if (trimmed && trimmed !== b.name) {
    try {
      await fetch(`/api/bookmarks/${b.id}`, {
        method: "PUT",
        headers: JSON_HEADERS,
        body: JSON.stringify({ name: trimmed }),
      });
    } catch (err) {
      showError("Could not reach the Watchtower server.");
    }
  }
  await refreshBookmarks();
}

function handleDeleteClick(b) {
  if (confirmingDeleteId === b.id) {
    clearTimeout(confirmingDeleteTimer);
    confirmingDeleteId = null;
    deleteBookmark(b);
    return;
  }
  confirmingDeleteId = b.id;
  clearTimeout(confirmingDeleteTimer);
  confirmingDeleteTimer = setTimeout(() => {
    confirmingDeleteId = null;
    renderBookmarks();
  }, 4000);
  renderBookmarks();
}

async function deleteBookmark(b) {
  try {
    await fetch(`/api/bookmarks/${b.id}`, { method: "DELETE" });
  } catch (err) {
    showError("Could not reach the Watchtower server.");
  }
  await refreshBookmarks();
}

tuneForm.addEventListener("submit", (e) => {
  e.preventDefault();
  const value = parseFloat(freqInput.value);
  if (!value || value <= 0 || Number.isNaN(value)) {
    showError("Enter a valid frequency first.");
    return;
  }
  clearError();
  freqReadoutValue = value;
  renderFreqReadout();
  if (currentSdrState === "listening" || currentSdrState === "starting") {
    startListening();
  }
});

tabButtons.forEach((btn) => btn.addEventListener("click", () => setActiveTab(btn.dataset.tab)));
modeButtons.forEach((btn) => btn.addEventListener("click", () => setActiveMode(btn.dataset.mode)));
btnStart.addEventListener("click", startListening);
btnStop.addEventListener("click", stopListening);
btnReturnToScan.addEventListener("click", returnToScan);
btnRefreshDevices.addEventListener("click", refreshDevices);
btnScanStart.addEventListener("click", startScan);
btnScanStop.addEventListener("click", stopScan);
btnSaveCurrent.addEventListener("click", openSaveCurrentRow);
btnBookmarkSaveConfirm.addEventListener("click", confirmSaveCurrent);
btnBookmarkSaveCancel.addEventListener("click", closeSaveCurrentRow);
bookmarkSaveNameInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    e.preventDefault();
    confirmSaveCurrent();
  } else if (e.key === "Escape") {
    closeSaveCurrentRow();
  }
});
gainInput.addEventListener("change", changeGainLive);

function updateClock() {
  pillClockValue.textContent = new Date().toLocaleTimeString();
}

populateGainSelect();
setActiveMode("nfm");
setActiveTab("tune");
renderFreqReadout();
updateClock();
setInterval(updateClock, 1000);

refreshStatus();
refreshDevices();
refreshBookmarks();
setInterval(refreshStatus, STATUS_POLL_MS);
