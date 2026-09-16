"use strict";

const STATUS_POLL_MS = 2000;

const el = (id) => document.getElementById(id);

const demoBadge = el("demo-badge");
const pillSdr = el("pill-sdr");
const pillGps = el("pill-gps");
const pillAudio = el("pill-audio");
const pillClockValue = el("pill-clock").querySelector(".pill-value");

const bannerError = el("banner-error");
const bannerHf = el("banner-hf");

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
const btnRefreshDevices = el("btn-refresh-devices");

const audioPlayer = el("audio-player");

const gpsStatusLine = document.querySelector(".gps-status-line");
const gpsStatusText = el("gps-status-text");
const gpsDot = el("gps-dot");
const gpsLat = el("gps-lat");
const gpsLon = el("gps-lon");
const gpsAlt = el("gps-alt");
const gpsSats = el("gps-sats");
const gpsDevice = el("gps-device");
const gpsTime = el("gps-time");

const toolListEl = el("tool-list");
const deviceListEl = el("device-list");

let selectedMode = "nfm";
let currentSdrState = "idle";
let freqReadoutValue = null;
let statusFailureStreak = 0;

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
  pillEl.classList.remove("state-ok", "state-warn", "state-error");
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

function setActiveMode(mode) {
  selectedMode = mode;
  for (const btn of modeButtons) {
    btn.classList.toggle("active", btn.dataset.mode === mode);
  }
}

function renderFreqReadout() {
  freqReadoutEl.textContent = freqReadoutValue == null ? "— . — — — —" : freqReadoutValue.toFixed(4);
}

function getFormParams() {
  return {
    frequency_mhz: parseFloat(freqInput.value),
    mode: selectedMode,
    device_index: parseInt(deviceSelect.value || "0", 10),
    gain_db: parseFloat(gainInput.value || "0"),
    squelch: parseInt(squelchInput.value || "0", 10),
    ppm: parseInt(ppmInput.value || "0", 10),
  };
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

  deviceListEl.innerHTML = "";
  if (devices.length === 0) {
    const li = document.createElement("li");
    li.className = "muted";
    li.textContent = "No devices detected.";
    deviceListEl.appendChild(li);
  } else {
    for (const d of devices) {
      const li = document.createElement("li");
      li.textContent = `#${d.index} ${d.name}${d.serial ? " SN:" + d.serial : ""}`;
      deviceListEl.appendChild(li);
    }
  }
}

function applyStatus(data) {
  demoBadge.hidden = !data.demo;

  const sdr = data.sdr;
  const gps = data.gps;
  const tools = data.tools;

  currentSdrState = sdr.state;

  const sdrClass =
    sdr.state === "listening" ? "state-ok" : sdr.state === "error" ? "state-error" : sdr.devices.length === 0 ? "state-warn" : "";
  setPillState(pillSdr, sdrClass, sdr.state);

  btnStart.disabled = sdr.state === "listening" || sdr.state === "starting";
  btnStop.disabled = sdr.state !== "listening" && sdr.state !== "starting";

  if (sdr.error) {
    showError(sdr.error);
  } else {
    clearError();
  }

  bannerHf.hidden = !sdr.hf_advisory;

  if (sdr.params) {
    freqReadoutValue = sdr.params.frequency_mhz;
    setActiveMode(sdr.params.mode);
  }
  renderFreqReadout();

  const audioActive = sdr.state === "listening";
  setPillState(pillAudio, audioActive ? "state-ok" : "", audioActive ? "streaming" : "idle");

  renderDevices(sdr.devices, sdr.devices_stale);

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
    gpsAlt.textContent = gps.fix.altitude_m != null ? gps.fix.altitude_m.toFixed(1) + " m" : "—";
    gpsTime.textContent = gps.fix.time || "—";
  } else {
    gpsLat.textContent = "—";
    gpsLon.textContent = "—";
    gpsAlt.textContent = "—";
    gpsTime.textContent = "—";
  }
  gpsSats.textContent =
    gps.satellites_used != null && gps.satellites_visible != null ? `${gps.satellites_used} / ${gps.satellites_visible}` : "—";
  gpsDevice.textContent = gps.device || "—";

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

async function startListening() {
  const params = getFormParams();
  if (!params.frequency_mhz || params.frequency_mhz <= 0 || Number.isNaN(params.frequency_mhz)) {
    showError("Enter a valid frequency first.");
    return;
  }
  clearError();
  try {
    const res = await fetch("/api/sdr/listen/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(params),
    });
    const data = await res.json();
    if (!res.ok || data.status === "error") {
      showError(data.message || "Failed to start listening.");
      return;
    }
    audioPlayer.src = "/api/audio/stream?t=" + Date.now();
    audioPlayer.play().catch(() => {
      /* autoplay may require another user gesture; button click already is one */
    });
  } catch (err) {
    showError("Could not reach the Watchtower server.");
  }
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

modeButtons.forEach((btn) => btn.addEventListener("click", () => setActiveMode(btn.dataset.mode)));
btnStart.addEventListener("click", startListening);
btnStop.addEventListener("click", stopListening);
btnRefreshDevices.addEventListener("click", refreshDevices);

function updateClock() {
  pillClockValue.textContent = new Date().toLocaleTimeString();
}

setActiveMode("nfm");
renderFreqReadout();
updateClock();
setInterval(updateClock, 1000);

refreshStatus();
refreshDevices();
setInterval(refreshStatus, STATUS_POLL_MS);
