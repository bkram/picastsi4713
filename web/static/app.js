/* eslint-disable no-console */

const statusPsCurrent = document.getElementById("statusPsCurrent");
const statusRt = document.getElementById("statusRt");
const statusBank = document.getElementById("statusBank");
const statusTs = document.getElementById("statusTs");
const currentCfgEl = document.getElementById("currentCfg");
const activeNameEl = document.getElementById("activeName");
const configList = document.getElementById("configList");
const reloadBtn = document.getElementById("reloadBtn");
const saveBtn = document.getElementById("saveBtn");
const setActiveBtn = document.getElementById("setActiveBtn");
const importBtn = document.getElementById("importBtn");
const importFile = document.getElementById("importFile");

let currentCfg = null;
let activeCfg = null;

function updateLabels() {
  currentCfgEl.textContent = `Current: ${currentCfg || "(none)"}`;
  activeNameEl.textContent = `Config: ${activeCfg || "—"}`;
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

async function fetchJson(url, opts = {}) {
  const res = await fetch(url, opts);
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `HTTP ${res.status}`);
  }
  return res.json();
}

function populateForm(cfg) {
  currentCfg = cfg.__name || currentCfg;
  const rf = cfg.rf || {};
  fields.rf.frequency_khz.value = rf.frequency_khz ?? "";
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
  fields.rds.pty.value = rds.pty ?? "";
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
  fields.rds.rt_bank.value = rt.bank ?? "";

  fields.monitor.health.checked =
    (cfg.monitor && cfg.monitor.health !== false) || false;
  fields.monitor.asq.checked = !!(cfg.monitor && cfg.monitor.asq);
  const ignoreDbfs = cfg.monitor?.overmod_ignore_below_dbfs;
  fields.monitor.overmod_ignore_below_dbfs.value =
    ignoreDbfs === undefined ? "-5" : ignoreDbfs;
  fields.monitor.health_interval_s.value = cfg.monitor?.health_interval_s ?? "";
  fields.monitor.recovery_attempts.value = cfg.monitor?.recovery_attempts ?? "";
  fields.monitor.recovery_backoff_s.value =
    cfg.monitor?.recovery_backoff_s ?? "";
}

function collectForm() {
  const overmodIgnoreRaw = fields.monitor.overmod_ignore_below_dbfs.value;
  const overmodIgnoreVal =
    overmodIgnoreRaw === "" ? undefined : Number(overmodIgnoreRaw);

  return {
    rf: {
      frequency_khz: Number(fields.rf.frequency_khz.value) || 0,
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
      enabled: fields.rds.enabled.checked,
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
        gap_ms: Number(fields.rds.rt_gap_ms.value) || 60,
        bank:
          fields.rds.rt_bank.value === ""
            ? undefined
            : Number(fields.rds.rt_bank.value),
      },
    },
    monitor: {
      health: fields.monitor.health.checked,
      asq: fields.monitor.asq.checked,
      ...(overmodIgnoreVal === undefined
        ? {}
        : { overmod_ignore_below_dbfs: overmodIgnoreVal }),
      health_interval_s: Number(fields.monitor.health_interval_s.value) || 5,
      recovery_attempts: Number(fields.monitor.recovery_attempts.value) || 3,
      recovery_backoff_s: Number(fields.monitor.recovery_backoff_s.value) || 2,
    },
  };
}

async function loadConfigs() {
  const cfgs = await fetchJson("/api/configs");
  configList.innerHTML = "";
  cfgs.forEach((name) => {
    const btn = document.createElement("button");
    btn.textContent = name;
    if (activeCfg && name === activeCfg) {
      btn.classList.add("primary");
    }
    btn.addEventListener("click", () => loadConfig(name));
    configList.appendChild(btn);
  });
}

async function loadConfig(name) {
  const cfg = await fetchJson(`/api/configs-json/${encodeURIComponent(name)}`);
  cfg.__name = name;
  populateForm(cfg);
  currentCfg = name;
  updateLabels();
}

async function saveConfig() {
  if (!currentCfg) {
    alert("No config selected.");
    return;
  }
  const body = collectForm();
  await fetchJson(`/api/configs-json/${encodeURIComponent(currentCfg)}`, {
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

async function reloadCurrent() {
  if (!currentCfg) {
    await loadConfigs();
    return;
  }
  await loadConfig(currentCfg);
}

async function pollStatus() {
  try {
    const data = await fetchJson("/api/status");
    const psList = data.ps || [];
    const psNow = data.ps_current || (psList.length ? psList[0] : null);
    statusPsCurrent.textContent = psNow || "—";
    statusRt.textContent = data.rt_text || "";
    statusBank.textContent = data.rt_bank ?? "";
    statusTs.textContent = data.rt_updated_at || "";
    if (data.config_path) {
      const parts = data.config_path.split("/");
      const name = parts[parts.length - 1];
      const changed = activeCfg !== name;
      activeCfg = name;
      if (!currentCfg) {
        await loadConfig(name);
      } else {
        updateLabels();
      }
      if (changed) {
        await loadConfigs();
      }
      updateLabels();
    }
  } catch (err) {
    console.warn("Status poll failed:", err);
  } finally {
    setTimeout(pollStatus, 1000);
  }
}

importBtn.addEventListener("click", async () => {
  const file = importFile.files[0];
  if (!file) return;
  const text = await file.text();
  let name = file.name;
  if (!name.endsWith(".json")) {
    alert("Only .json files allowed");
    return;
  }
  await fetchJson(`/api/configs-json/${encodeURIComponent(name)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: text,
  });
  await loadConfigs();
  await loadConfig(name);
});

saveBtn.addEventListener("click", () => saveConfig().catch((e) => alert(e)));
setActiveBtn.addEventListener("click", () =>
  setActive().catch((e) => alert(e)),
);
reloadBtn.addEventListener("click", () =>
  reloadCurrent().catch((e) => alert(e)),
);
fields.rf.manual_dev.addEventListener("change", applyDeviationVisibility);
fields.rf.antenna_cap_mode.addEventListener("change", applyCapAutoState);

window.addEventListener("load", () => {
  loadConfigs().catch(console.error);
  pollStatus();
});
