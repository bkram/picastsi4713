/* eslint-disable no-console */

const statusPsCurrent = document.getElementById("statusPsCurrent");
const statusRt = document.getElementById("statusRt");
const statusFreqMeta = document.getElementById("statusFreqMeta");
const configSelectTop = document.getElementById("configSelectTop");
const setActiveTopBtn = document.getElementById("setActiveTopBtn");
const screenButtons = document.querySelectorAll("[data-screen-btn]");
const screens = document.querySelectorAll(".screen");
const txToggleBtn = document.getElementById("txToggleBtn");
const consoleOutput = document.getElementById("consoleOutput");
const consoleDot = document.getElementById("consoleDot");
const consolePauseBtn = document.getElementById("consolePauseBtn");
const consoleClearBtn = document.getElementById("consoleClearBtn");
const consoleFollow = document.getElementById("consoleFollow");
const consoleFilter = document.getElementById("consoleFilter");
const errors = {
  freq: document.getElementById("rf_frequency_msg"),
  pi: document.getElementById("rds_pi_msg"),
};

let currentCfg = null;
let activeCfg = null;
let logSource = null;
let logPaused = false;
let logFilter = "";
const logEntries = [];
const maxLogEntries = 500;
let isPopulating = false;
let autosaveTimer = null;
let txEnabled = null;

function displayConfigName(name) {
  if (!name) return "(none)";
  return name.endsWith(".json") ? name.slice(0, -5) : name;
}

const fields = {
  rf: {
    frequency_khz: document.getElementById("rf_frequency_khz"),
    power: document.getElementById("rf_power"),
    antenna_cap: document.getElementById("rf_antenna_cap"),
    antenna_cap_mode: document.getElementById("rf_antenna_cap_mode"),
    audio_dev_hz: document.getElementById("rf_audio_dev_hz"),
    audio_dev_no_rds_hz: document.getElementById("rf_audio_dev_no_rds_hz"),
    preemphasis: document.getElementById("rf_preemphasis"),
    manual_dev: document.getElementById("rf_manual_dev"),
    manual_dev_fields: document.getElementById("rf_dev_fields"),
  },
  streaming: {
    enabled: document.getElementById("rf_audio_enabled"),
    url: document.getElementById("rf_audio_url"),
  },
  rds: {
    pi: document.getElementById("rds_pi"),
    pty: document.getElementById("rds_pty"),
    tp: document.getElementById("rds_tp"),
    ta: document.getElementById("rds_ta"),
    ms_music: document.getElementById("rds_ms_music"),
    enabled: document.getElementById("rds_enabled"),
    di_stereo: document.getElementById("di_stereo"),
    di_artificial_head: document.getElementById("di_artificial_head"),
    di_compressed: document.getElementById("di_compressed"),
    di_dynamic_pty: document.getElementById("di_dynamic_pty"),
    ps: document.getElementById("rds_ps"),
    ps_center: document.getElementById("rds_ps_center"),
    ps_speed: document.getElementById("rds_ps_speed"),
    dev_hz: document.getElementById("rds_dev_hz"),
    rt_texts: document.getElementById("rds_rt_texts"),
    rt_speed_s: document.getElementById("rds_rt_speed_s"),
    rt_center: document.getElementById("rds_rt_center"),
    rt_skip_words: document.getElementById("rds_rt_skip_words"),
    rt_file: document.getElementById("rds_rt_file"),
    rt_ab_mode: document.getElementById("rds_rt_ab_mode"),
    rt_repeats: document.getElementById("rds_rt_repeats"),
    rt_gap_ms: document.getElementById("rds_rt_gap_ms"),
    rt_bank: document.getElementById("rds_rt_bank"),
  },
  uecp: {
    enabled: document.getElementById("uecp_enabled"),
    host: document.getElementById("uecp_host"),
    port: document.getElementById("uecp_port"),
  },
  monitor: {
    health: document.getElementById("monitor_health"),
    asq: document.getElementById("monitor_asq"),
    overmod_ignore_below_dbfs: document.getElementById(
      "overmod_ignore_below_dbfs",
    ),
    health_interval_s: document.getElementById("health_interval_s"),
    recovery_attempts: document.getElementById("recovery_attempts"),
    recovery_backoff_s: document.getElementById("recovery_backoff_s"),
  },
};

function collectAutosaveFields() {
  const buckets = [];
  Object.values(fields).forEach((group) => {
    if (!group || typeof group !== "object") return;
    Object.values(group).forEach((el) => buckets.push(el));
  });
  return buckets.filter((el) => {
    if (!el || typeof el !== "object") return false;
    if (!("tagName" in el)) return false;
    return (
      el.tagName === "INPUT" ||
      el.tagName === "SELECT" ||
      el.tagName === "TEXTAREA"
    );
  });
}

function toCsv(list) {
  if (!Array.isArray(list)) return "";
  return list.join(" | ");
}

function parseCsv(value) {
  const raw = value || "";
  if (!raw.includes("|")) {
    const single = raw.trim();
    return single ? [single] : [];
  }
  return raw
    .split("|")
    .map((s) => s.trim())
    .filter(Boolean);
}

function splitPipeList(items) {
  if (!Array.isArray(items)) return [];
  const out = [];
  for (const item of items) {
    const val = typeof item === "string" ? item : String(item ?? "");
    if (!val) continue;
    if (val.includes("|")) {
      val
        .split("|")
        .map((s) => s.trim())
        .filter(Boolean)
        .forEach((s) => out.push(s));
    } else {
      const trimmed = val.trim();
      if (trimmed) out.push(trimmed);
    }
  }
  return out;
}

function formatFreqDisplay(khz) {
  const val = Number(khz);
  if (!Number.isFinite(val) || val <= 0) return "—";
  return `${(val / 1000).toFixed(2)} MHz`;
}

function formatUpdatedAt(ts) {
  if (!ts) return "—";
  const date = new Date(ts);
  if (Number.isNaN(date.getTime())) return "—";
  const yyyy = date.getFullYear();
  const mm = String(date.getMonth() + 1).padStart(2, "0");
  const dd = String(date.getDate()).padStart(2, "0");
  const hh = String(date.getHours()).padStart(2, "0");
  const min = String(date.getMinutes()).padStart(2, "0");
  const ss = String(date.getSeconds()).padStart(2, "0");
  return `${yyyy}-${mm}-${dd} ${hh}:${min}:${ss}`;
}

function formatLogLine(entry) {
  const level = entry.level || "INFO";
  const logger = entry.logger || "app";
  const msg = entry.message || "";
  const ts = entry.ts || "";
  const prefix = ts ? `${ts} ` : "";
  return `${prefix}[${level}] ${logger} - ${msg}`;
}

function matchesLogFilter(entry) {
  if (!logFilter) return true;
  const haystack = `${entry.ts || ""} ${entry.level || ""} ${
    entry.logger || ""
  } ${entry.message || ""}`.toLowerCase();
  return haystack.includes(logFilter);
}

function renderLogEntries() {
  if (!consoleOutput) return;
  consoleOutput.textContent = "";
  const frag = document.createDocumentFragment();
  logEntries.forEach((entry) => {
    if (!matchesLogFilter(entry)) return;
    const line = document.createElement("div");
    const levelClass = `level-${String(entry.level || "info").toLowerCase()}`;
    line.className = `log-line ${levelClass}`;
    line.textContent = formatLogLine(entry);
    frag.appendChild(line);
  });
  consoleOutput.appendChild(frag);
  if (consoleFollow && consoleFollow.checked) {
    consoleOutput.scrollTop = consoleOutput.scrollHeight;
  }
}

function addLogEntry(entry) {
  logEntries.push(entry);
  if (logEntries.length > maxLogEntries) {
    logEntries.shift();
  }
  if (logPaused) return;
  if (!consoleOutput) return;
  if (!matchesLogFilter(entry)) return;
  const line = document.createElement("div");
  const levelClass = `level-${String(entry.level || "info").toLowerCase()}`;
  line.className = `log-line ${levelClass}`;
  line.textContent = formatLogLine(entry);
  consoleOutput.appendChild(line);
  if (consoleFollow && consoleFollow.checked) {
    consoleOutput.scrollTop = consoleOutput.scrollHeight;
  }
}

function setConsoleStatus(ok) {
  if (consoleDot) {
    consoleDot.style.background = ok
      ? "linear-gradient(135deg, var(--accent), var(--accent-2))"
      : "#f87171";
    consoleDot.style.boxShadow = ok
      ? "0 0 10px rgba(34, 211, 238, 0.6)"
      : "0 0 10px rgba(248, 113, 113, 0.6)";
  }
}

function initConsoleStream() {
  if (!consoleOutput || typeof EventSource === "undefined") return;
  if (logSource) {
    logSource.close();
  }
  setConsoleStatus(false);
  logSource = new EventSource("/api/logs/stream");
  logSource.onopen = () => {
    setConsoleStatus(true);
  };
  logSource.onerror = () => {
    setConsoleStatus(false);
  };
  logSource.onmessage = (event) => {
    try {
      const entry = JSON.parse(event.data);
      addLogEntry(entry);
    } catch (err) {
      console.warn("Bad log payload", err);
    }
  };
}

function mhzToKhz(value) {
  const val = Number(value);
  if (!Number.isFinite(val)) return 0;
  return Math.round(val * 1000);
}

function khzToMhzInput(khz) {
  const val = Number(khz);
  if (!Number.isFinite(val) || val <= 0) return "";
  return (val / 1000).toFixed(3).replace(/\.?0+$/, "");
}

function showError(key, message) {
  const el = errors[key];
  if (!el) return;
  const input =
    key === "freq"
      ? fields.rf.frequency_khz
      : key === "pi"
        ? fields.rds.pi
        : null;
  if (message) {
    el.textContent = message;
    el.classList.remove("hidden");
    if (input) input.classList.add("invalid");
  } else {
    el.textContent = "";
    el.classList.add("hidden");
    if (input) input.classList.remove("invalid");
  }
}

function validateForm() {
  let ok = true;
  const freqMhz = Number(fields.rf.frequency_khz.value);
  if (!Number.isFinite(freqMhz) || freqMhz < 76 || freqMhz > 108) {
    showError("freq", "Frequency must be between 76.0 and 108.0 MHz.");
    ok = false;
  } else {
    showError("freq", "");
  }

  const piRaw = (fields.rds.pi.value || "").trim();
  if (piRaw) {
    const norm = piRaw.replace(/^0x/i, "");
    if (!/^[0-9a-fA-F]{1,4}$/.test(norm)) {
      showError("pi", "PI must be 1–4 hex digits (e.g., 0x1234).");
      ok = false;
    } else {
      showError("pi", "");
    }
  } else {
    showError("pi", "");
  }
  return ok;
}

function applyCapAutoState() {
  const auto = fields.rf.antenna_cap_mode.value === "auto";
  fields.rf.antenna_cap.disabled = auto;
  const wrap = document.getElementById("rf_antenna_cap_wrap");
  if (wrap) wrap.classList.toggle("hidden", auto);
  if (auto) fields.rf.antenna_cap.value = "";
}

function applyDeviationVisibility() {
  const manual = fields.rf.manual_dev.value !== "auto";
  fields.rf.manual_dev_fields.classList.toggle("hidden", !manual);
  fields.rf.audio_dev_hz.disabled = !manual;
  fields.rf.audio_dev_no_rds_hz.disabled = !manual;
}

function applyUecpState() {
  const enabled = fields.uecp.enabled.checked;
  const targets = [
    fields.rds.enabled,
    fields.rds.pi,
    fields.rds.pty,
    fields.rds.tp,
    fields.rds.ta,
    fields.rds.ms_music,
    fields.rds.di_stereo,
    fields.rds.di_artificial_head,
    fields.rds.di_compressed,
    fields.rds.di_dynamic_pty,
    fields.rds.ps,
    fields.rds.ps_center,
    fields.rds.ps_speed,
    fields.rds.rt_texts,
    fields.rds.rt_speed_s,
    fields.rds.rt_center,
    fields.rds.rt_skip_words,
    fields.rds.rt_file,
    fields.rds.rt_ab_mode,
    fields.rds.rt_repeats,
    fields.rds.rt_gap_ms,
    fields.rds.rt_bank,
  ];
  targets.forEach((el) => {
    if (el) el.disabled = enabled;
  });
  if (enabled) {
    fields.rds.enabled.checked = true;
  }
}

function activateScreen(name) {
  screens.forEach((section) => {
    section.classList.toggle("active", section.dataset.screen === name);
  });
  screenButtons.forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.screenBtn === name);
  });
  if (name) {
    try {
      localStorage.setItem("ui.screen", name);
    } catch (err) {
      console.warn("Failed to persist screen", err);
    }
  }
}

async function fetchJson(url, opts = {}) {
  const res = await fetch(url, opts);
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `HTTP ${res.status}`);
  }
  return res.json();
}

async function setTxEnabled(enabled) {
  await fetchJson("/api/tx", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled }),
  });
}

function updateTxButton(enabled) {
  if (!txToggleBtn) return;
  txEnabled = enabled;
  txToggleBtn.textContent = "On Air";
  txToggleBtn.classList.toggle("contrast", enabled);
}

function populateForm(cfg) {
  isPopulating = true;
  currentCfg = cfg.__name || currentCfg;
  const rf = cfg.rf || {};
  fields.rf.frequency_khz.value = khzToMhzInput(rf.frequency_khz);
  fields.rf.power.value = rf.power ?? "";
  fields.rf.audio_dev_hz.value = rf.audio_deviation_hz ?? "";
  fields.rf.audio_dev_no_rds_hz.value = rf.audio_deviation_no_rds_hz ?? "";
  fields.rf.preemphasis.value = rf.preemphasis || "us50";
  fields.rf.manual_dev.value =
    rf.manual_deviation === false ? "auto" : "manual";
  const streaming = cfg.streaming || {};
  fields.streaming.enabled.checked = !!streaming.enabled;
  fields.streaming.url.value =
    streaming.url ?? streaming.stream_url ?? rf.audio?.stream_url ?? "";
  const capAuto =
    rf.antenna_cap_auto === true ||
    rf.antenna_cap === 0 ||
    rf.antenna_cap === null ||
    (typeof rf.antenna_cap === "string" &&
      rf.antenna_cap.toLowerCase() === "auto");
  fields.rf.antenna_cap_mode.value = capAuto ? "auto" : "manual";
  fields.rf.antenna_cap.value = capAuto ? "" : (rf.antenna_cap ?? "");
  applyCapAutoState();
  applyDeviationVisibility();

  const rds = cfg.rds || {};
  let piHex = "";
  if (typeof rds.pi === "string") {
    piHex = rds.pi.replace(/^0x/i, "");
  } else if (typeof rds.pi === "number") {
    piHex = rds.pi.toString(16);
  }
  fields.rds.pi.value = piHex;
  const ptyVal = Number(rds.pty);
  fields.rds.pty.value =
    Number.isFinite(ptyVal) && ptyVal >= 0 && ptyVal <= 31
      ? String(ptyVal)
      : "0";
  fields.rds.tp.checked = !!rds.tp;
  fields.rds.ta.checked = !!rds.ta;
  fields.rds.ms_music.checked = rds.ms_music !== false;
  fields.rds.enabled.checked = rds.enabled !== false;
  const di = rds.di || {};
  fields.rds.di_stereo.checked = di.stereo !== false;
  fields.rds.di_artificial_head.checked = !!di.artificial_head;
  fields.rds.di_compressed.checked = !!di.compressed;
  fields.rds.di_dynamic_pty.checked = !!di.dynamic_pty;

  fields.rds.ps_center.checked = rds.ps_center !== false;
  fields.rds.ps_speed.value = rds.ps_speed ?? "";
  fields.rds.ps.value = toCsv(splitPipeList(rds.ps || []));

  const rt = rds.rt || {};
  fields.rds.dev_hz.value = rds.deviation_hz ?? "";
  fields.rds.rt_texts.value = toCsv(splitPipeList(rt.texts || []));
  fields.rds.rt_speed_s.value = rt.speed_s ?? "";
  fields.rds.rt_center.checked = rt.center !== false;
  fields.rds.rt_skip_words.value = toCsv(rt.skip_words || []);
  fields.rds.rt_file.value = rt.file_path ?? "";
  fields.rds.rt_ab_mode.value = rt.ab_mode || "auto";
  fields.rds.rt_repeats.value = rt.repeats ?? "";
  fields.rds.rt_gap_ms.value = rt.gap_ms ?? "";
  if (rt.gap_ms !== undefined && rt.gap_ms !== null) {
    const gapMs = Number(rt.gap_ms);
    fields.rds.rt_gap_ms.value = Number.isFinite(gapMs)
      ? String(gapMs / 1000)
      : "";
  } else {
    fields.rds.rt_gap_ms.value = "";
  }
  fields.rds.rt_bank.value = rt.bank ?? "";

  const uecp = cfg.uecp || {};
  fields.uecp.enabled.checked = !!uecp.enabled;
  fields.uecp.host.value = uecp.host ?? "0.0.0.0";
  fields.uecp.port.value = uecp.port ?? 9100;
  applyUecpState();

  fields.monitor.health.checked =
    (cfg.monitor && cfg.monitor.health !== false) || false;
  fields.monitor.asq.checked = !!(cfg.monitor && cfg.monitor.asq);
  const ignoreDbfs = cfg.monitor?.overmod_ignore_below_dbfs;
  fields.monitor.overmod_ignore_below_dbfs.value =
    ignoreDbfs === undefined ? "-5" : ignoreDbfs;
  fields.monitor.health_interval_s.value =
    cfg.monitor?.interval_s ?? cfg.monitor?.health_interval_s ?? "";
  fields.monitor.recovery_attempts.value = cfg.monitor?.recovery_attempts ?? "";
  fields.monitor.recovery_backoff_s.value =
    cfg.monitor?.recovery_backoff_s ?? "";
  isPopulating = false;
}

function collectForm() {
  const overmodIgnoreRaw = fields.monitor.overmod_ignore_below_dbfs.value;
  const overmodIgnoreVal =
    overmodIgnoreRaw === "" ? undefined : Number(overmodIgnoreRaw);
  const freqKhz = mhzToKhz(fields.rf.frequency_khz.value);

  return {
    rf: {
      frequency_khz: freqKhz,
      power: Number(fields.rf.power.value) || 88,
      antenna_cap_auto: fields.rf.antenna_cap_mode.value === "auto",
      antenna_cap:
        fields.rf.antenna_cap_mode.value === "auto"
          ? "auto"
          : Number(fields.rf.antenna_cap.value) || 4,
      audio_deviation_hz: Number(fields.rf.audio_dev_hz.value) || undefined,
      audio_deviation_no_rds_hz:
        fields.rf.audio_dev_no_rds_hz.value === ""
          ? undefined
          : Number(fields.rf.audio_dev_no_rds_hz.value) || undefined,
      preemphasis: fields.rf.preemphasis.value || "us50",
      manual_deviation: fields.rf.manual_dev.value !== "auto",
    },
    streaming: {
      enabled: fields.streaming.enabled.checked,
      url: fields.streaming.url.value,
    },
    rds: {
      pi: (() => {
        const raw = (fields.rds.pi.value || "").trim();
        if (!raw) return 0;
        return `0x${raw.replace(/^0x/i, "")}`;
      })(),
      pty: Number(fields.rds.pty.value) || 0,
      tp: fields.rds.tp.checked,
      ta: fields.rds.ta.checked,
      ms_music: fields.rds.ms_music.checked,
      enabled: fields.uecp.enabled.checked ? true : fields.rds.enabled.checked,
      di: {
        stereo: fields.rds.di_stereo.checked,
        artificial_head: fields.rds.di_artificial_head.checked,
        compressed: fields.rds.di_compressed.checked,
        dynamic_pty: fields.rds.di_dynamic_pty.checked,
      },
      ps: parseCsv(fields.rds.ps.value),
      ps_center: fields.rds.ps_center.checked,
      ps_speed: Number(fields.rds.ps_speed.value) || 10,
      deviation_hz: Number(fields.rds.dev_hz.value) || 200,
      rt: {
        texts: parseCsv(fields.rds.rt_texts.value),
        speed_s: Number(fields.rds.rt_speed_s.value) || 10,
        center: fields.rds.rt_center.checked,
        skip_words: parseCsv(fields.rds.rt_skip_words.value),
        file_path: fields.rds.rt_file.value,
        ab_mode: fields.rds.rt_ab_mode.value,
        repeats: Number(fields.rds.rt_repeats.value) || 3,
        gap_ms: (() => {
          const raw = fields.rds.rt_gap_ms.value;
          if (raw === "") return 60;
          const sec = Number(raw);
          return Number.isFinite(sec) ? Math.round(sec * 1000) : 60;
        })(),
        bank:
          fields.rds.rt_bank.value === ""
            ? undefined
            : Number(fields.rds.rt_bank.value),
      },
    },
    uecp: {
      enabled: fields.uecp.enabled.checked,
      host: fields.uecp.host.value || "0.0.0.0",
      port: Number(fields.uecp.port.value) || 9100,
    },
    monitor: {
      health: fields.monitor.health.checked,
      asq: fields.monitor.asq.checked,
      ...(overmodIgnoreVal === undefined
        ? {}
        : { overmod_ignore_below_dbfs: overmodIgnoreVal }),
      interval_s: Number(fields.monitor.health_interval_s.value) || 5,
      recovery_attempts: Number(fields.monitor.recovery_attempts.value) || 3,
      recovery_backoff_s: Number(fields.monitor.recovery_backoff_s.value) || 2,
    },
  };
}

async function loadConfigs() {
  const cfgs = await fetchJson("/api/configs");
  const selected = currentCfg;
  if (configSelectTop) {
    configSelectTop.innerHTML = "";
  }
  cfgs.forEach((name) => {
    const opt = document.createElement("option");
    opt.value = name;
    const label = displayConfigName(name);
    opt.textContent = activeCfg === name ? `${label} (active)` : label;
    if (configSelectTop) {
      configSelectTop.appendChild(opt);
    }
  });
  const nextValue =
    selected && cfgs.includes(selected)
      ? selected
      : activeCfg && cfgs.includes(activeCfg)
        ? activeCfg
        : "";
  if (configSelectTop && nextValue) {
    configSelectTop.value = nextValue;
  }
}

async function loadConfig(name) {
  if (autosaveTimer) {
    clearTimeout(autosaveTimer);
    autosaveTimer = null;
  }
  const cfg = await fetchJson(`/api/configs-json/${encodeURIComponent(name)}`);
  cfg.__name = name;
  populateForm(cfg);
  currentCfg = name;
}

async function saveConfigFor(name) {
  if (!name) return;
  if (!validateForm()) return;
  const body = collectForm();
  await fetchJson(`/api/configs-json/${encodeURIComponent(name)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  await loadConfigs();
}

async function setActive() {
  if (!currentCfg) {
    alert("No config selected.");
    return;
  }
  await fetchJson("/api/active-config", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name: currentCfg }),
  });
}

async function pollStatus() {
  try {
    const data = await fetchJson("/api/status");
    const psList = data.ps || [];
    const psNow = data.ps_current || (psList.length ? psList[0] : null);
    statusPsCurrent.textContent = psNow || "—";
    const freqDisplay = formatFreqDisplay(data.freq_khz);
    if (statusFreqMeta) {
      statusFreqMeta.textContent = freqDisplay;
    }
    statusRt.textContent = data.rt_text || "";
    if (data.config_path) {
      const parts = data.config_path.split("/");
      const name = parts[parts.length - 1];
      const changed = activeCfg !== name;
      activeCfg = name;
      if (!currentCfg) {
        await loadConfig(name);
      }
      if (changed) {
        await loadConfigs();
      }
    }
    updateTxButton(data.tx_enabled !== false);
  } catch (err) {
    console.warn("Status poll failed:", err);
  } finally {
    setTimeout(pollStatus, 1000);
  }
}

function scheduleAutosave() {
  if (isPopulating) return;
  if (!currentCfg) return;
  if (autosaveTimer) clearTimeout(autosaveTimer);
  const targetCfg = currentCfg;
  autosaveTimer = setTimeout(() => {
    if (currentCfg !== targetCfg) {
      return;
    }
    saveConfigFor(targetCfg)
      .then(() => {
        const now = new Date();
        const ts = now.toISOString().slice(0, 19).replace("T", " ");
        addLogEntry({
          message: `Autosaved ${displayConfigName(targetCfg)}`,
          level: "INFO",
          logger: "ui",
          ts,
        });
        if (activeCfg && targetCfg === activeCfg && currentCfg === targetCfg) {
          return fetchJson("/api/reload-config", { method: "POST" })
            .then(() => {
              const tsApplied = new Date()
                .toISOString()
                .slice(0, 19)
                .replace("T", " ");
              addLogEntry({
                message: `Applied ${displayConfigName(targetCfg)}`,
                level: "INFO",
                logger: "ui",
                ts: tsApplied,
              });
            })
            .catch((e) => console.warn("Auto-apply failed", e));
        }
        return null;
      })
      .catch((e) => console.warn("Autosave failed", e));
  }, 5000);
}

const autosaveFields = collectAutosaveFields();
autosaveFields.forEach((el) => {
  el.addEventListener("input", scheduleAutosave);
  el.addEventListener("change", scheduleAutosave);
});
if (setActiveTopBtn) {
  setActiveTopBtn.addEventListener("click", () =>
    setActive().catch((e) => alert(e)),
  );
}
if (txToggleBtn) {
  txToggleBtn.addEventListener("click", async () => {
    try {
      const enable = txEnabled === null ? true : !txEnabled;
      await setTxEnabled(enable);
      updateTxButton(enable);
    } catch (e) {
      alert(e);
    }
  });
}
fields.rf.manual_dev.addEventListener("change", applyDeviationVisibility);
fields.rf.antenna_cap_mode.addEventListener("change", applyCapAutoState);
fields.uecp.enabled.addEventListener("change", applyUecpState);
fields.rf.frequency_khz.addEventListener("input", () => {
  showError("freq", "");
});
fields.rds.pi.addEventListener("input", () => showError("pi", ""));
screenButtons.forEach((btn) =>
  btn.addEventListener("click", () => activateScreen(btn.dataset.screenBtn)),
);
if (configSelectTop) {
  configSelectTop.addEventListener("change", () => {
    const name = configSelectTop.value;
    if (name) {
      if (autosaveTimer) {
        clearTimeout(autosaveTimer);
        autosaveTimer = null;
      }
      loadConfig(name).catch((e) => alert(e));
    }
  });
}

window.addEventListener("load", () => {
  const savedScreen = (() => {
    try {
      return localStorage.getItem("ui.screen");
    } catch (_err) {
      return null;
    }
  })();
  if (savedScreen) {
    activateScreen(savedScreen);
  } else if (screenButtons.length) {
    activateScreen(screenButtons[0].dataset.screenBtn);
  }
  loadConfigs().catch(console.error);
  pollStatus();
  initConsoleStream();
});

if (consolePauseBtn) {
  consolePauseBtn.addEventListener("click", () => {
    logPaused = !logPaused;
    consolePauseBtn.textContent = logPaused ? "Resume" : "Pause";
    if (!logPaused) {
      renderLogEntries();
    }
  });
}

if (consoleClearBtn) {
  consoleClearBtn.addEventListener("click", () => {
    logEntries.length = 0;
    if (consoleOutput) consoleOutput.textContent = "";
  });
}

if (consoleFilter) {
  consoleFilter.addEventListener("input", () => {
    logFilter = (consoleFilter.value || "").trim().toLowerCase();
    renderLogEntries();
  });
}
