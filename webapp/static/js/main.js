const broadcastBadge = document.getElementById('status-broadcast');
const psEl = document.getElementById('metric-ps');
const rtEl = document.getElementById('metric-rt');
const freqEl = document.getElementById('metric-frequency');
const powerEl = document.getElementById('metric-power');
const capEl = document.getElementById('metric-cap');
const overmodEl = document.getElementById('metric-overmod');
const watchdogEl = document.getElementById('metric-watchdog');
const audioLevelEl = document.getElementById('metric-audio-level');
const audioStatusEl = document.getElementById('metric-audio-status');
const rdsPiEl = document.getElementById('metric-rds-pi');
const rdsPtyEl = document.getElementById('metric-rds-pty');
const rdsFlagsEl = document.getElementById('metric-rds-flags');
const rdsPsEl = document.getElementById('metric-rds-ps');
const rdsPsMetaEl = document.getElementById('metric-rds-ps-meta');
const toggleBroadcast = document.getElementById('toggle-broadcast');

const configFields = document.getElementById('config-fields');
const configSelect = document.getElementById('config-select');
const refreshBtn = document.getElementById('btn-refresh');
const saveBtn = document.getElementById('btn-save');
const applyBtn = document.getElementById('btn-apply');
const duplicateBtn = document.getElementById('btn-duplicate');
const addPsBtn = document.getElementById('btn-add-ps');
const addRtTextBtn = document.getElementById('btn-add-rt-text');
const addSkipWordBtn = document.getElementById('btn-add-skip-word');

const psList = document.getElementById('ps-list');
const rtTextsList = document.getElementById('rt-texts-list');
const skipWordsList = document.getElementById('rt-skip-words-list');
const piInput = document.getElementById('rds-pi');
const audioPresetSelect = document.getElementById('audio-preset');
const audioPresetReset = document.getElementById('audio-preset-reset');

const monitorIntervalInput = document.getElementById('monitor-interval');
const monitorIntervalPreview = document.getElementById('monitor-interval-readonly');

const tabButtons = Array.from(document.querySelectorAll('.tab-rail__tab'));
const tabPanels = Array.from(document.querySelectorAll('.tab-panel'));

const AUDIO_PRESETS = {
  broadcast: {
    label: 'Broadcast reference (–16 dBFS)',
    values: {
      agc_on: true,
      limiter_on: true,
      comp_thr: -30,
      comp_att: 0,
      comp_rel: 2,
      comp_gain: 15,
      lim_rel: 50,
    },
  },
  music: {
    label: 'Music – Smooth',
    values: {
      agc_on: true,
      limiter_on: true,
      comp_thr: -24,
      comp_att: 2,
      comp_rel: 8,
      comp_gain: 12,
      lim_rel: 80,
    },
  },
  voice: {
    label: 'Speech – Articulate',
    values: {
      agc_on: true,
      limiter_on: true,
      comp_thr: -20,
      comp_att: 1,
      comp_rel: 4,
      comp_gain: 10,
      lim_rel: 40,
    },
  },
};

const AUDIO_FIELD_IDS = [
  'audio-agc',
  'audio-limiter',
  'audio-comp-thr',
  'audio-comp-att',
  'audio-comp-rel',
  'audio-comp-gain',
  'audio-lim-rel',
];

const feedback = document.getElementById('config-feedback');

let currentConfig = '';
let isDirty = false;
let formEnabled = false;
let suspendDirty = false;
let initialConfigLoaded = false;

function activateTab(name) {
  if (!name) {
    return;
  }

  tabButtons.forEach((button) => {
    const isActive = button.dataset.tab === name;
    button.classList.toggle('is-active', isActive);
    button.setAttribute('aria-selected', String(isActive));
  });

  tabPanels.forEach((panel) => {
    const isActive = panel.dataset.tabPanel === name;
    panel.classList.toggle('is-active', isActive);
    panel.toggleAttribute('hidden', !isActive);
  });
}

tabButtons.forEach((button, index) => {
  button.addEventListener('click', () => {
    activateTab(button.dataset.tab);
  });

  button.addEventListener('keydown', (event) => {
    if (event.key === 'Home') {
      event.preventDefault();
      tabButtons[0].focus();
      activateTab(tabButtons[0].dataset.tab);
      return;
    }

    if (event.key === 'End') {
      event.preventDefault();
      const last = tabButtons[tabButtons.length - 1];
      last.focus();
      activateTab(last.dataset.tab);
      return;
    }

    const forwardKeys = ['ArrowRight', 'ArrowDown'];
    const backwardKeys = ['ArrowLeft', 'ArrowUp'];
    let offset = 0;
    if (forwardKeys.includes(event.key)) {
      offset = 1;
    } else if (backwardKeys.includes(event.key)) {
      offset = -1;
    }

    if (offset === 0) {
      return;
    }

    event.preventDefault();
    const nextIndex = (index + offset + tabButtons.length) % tabButtons.length;
    const nextButton = tabButtons[nextIndex];
    nextButton.focus();
    activateTab(nextButton.dataset.tab);
  });
});

activateTab('overview');

function formatFrequency(khz) {
  const numeric = Number(khz);
  if (!Number.isFinite(numeric) || numeric <= 0) {
    return '—';
  }
  return `${(numeric / 1000).toFixed(2)} MHz`;
}

function fromKhzToMHz(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric) || numeric <= 0) {
    return '';
  }
  return (numeric / 1000).toFixed(2);
}

function toKhzFromMHz(raw) {
  if (raw === null || raw === undefined) {
    return '';
  }
  const trimmed = String(raw).trim();
  if (!trimmed) {
    return '';
  }
  const numeric = Number(trimmed);
  if (!Number.isFinite(numeric)) {
    return trimmed;
  }
  return Math.round(numeric * 1000).toString();
}

function sanitizePiDigits(raw) {
  return (raw || '')
    .toUpperCase()
    .replace(/[^0-9A-F]/g, '')
    .slice(0, 4);
}

function formatPiDigits(value) {
  if (value === null || value === undefined || value === '') {
    return '';
  }

  if (typeof value === 'number' && Number.isFinite(value)) {
    const constrained = Math.trunc(value) & 0xffff;
    return constrained.toString(16).toUpperCase().padStart(4, '0');
  }

  if (typeof value === 'string') {
    const trimmed = value.trim();
    if (!trimmed) {
      return '';
    }

    if (/^0x[0-9a-f]+$/i.test(trimmed)) {
      const numeric = parseInt(trimmed.slice(2), 16);
      if (Number.isFinite(numeric)) {
        return (numeric & 0xffff).toString(16).toUpperCase().padStart(4, '0');
      }
      return '';
    }

    if (/^[0-9]+$/i.test(trimmed)) {
      const numeric = parseInt(trimmed, 10);
      if (Number.isFinite(numeric)) {
        return (numeric & 0xffff).toString(16).toUpperCase().padStart(4, '0');
      }
      return '';
    }

    const sanitized = sanitizePiDigits(trimmed);
    if (!sanitized) {
      return '';
    }
    const numeric = parseInt(sanitized, 16);
    if (!Number.isFinite(numeric)) {
      return '';
    }
    return (numeric & 0xffff).toString(16).toUpperCase().padStart(4, '0');
  }

  return '';
}

function collectPiValue() {
  const digits = sanitizePiDigits(piInput.value);
  if (!digits) {
    return '';
  }
  return `0x${digits.padStart(4, '0')}`;
}

function toInt(value) {
  if (value === null || value === undefined || value === '') {
    return null;
  }
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) {
    return null;
  }
  return Math.trunc(numeric);
}

function normalizeAudioValues(audio) {
  if (!audio) {
    return null;
  }

  const normalized = {
    agc_on: Boolean(audio.agc_on),
    limiter_on: audio.limiter_on !== false,
    comp_thr: toInt(audio.comp_thr),
    comp_att: toInt(audio.comp_att),
    comp_rel: toInt(audio.comp_rel),
    comp_gain: toInt(audio.comp_gain),
    lim_rel: toInt(audio.lim_rel),
  };

  if (
    normalized.comp_thr === null ||
    normalized.comp_att === null ||
    normalized.comp_rel === null ||
    normalized.comp_gain === null ||
    normalized.lim_rel === null
  ) {
    return null;
  }

  return normalized;
}

function findMatchingAudioPreset(audio) {
  const normalized = normalizeAudioValues(audio);
  if (!normalized) {
    return null;
  }

  for (const [id, preset] of Object.entries(AUDIO_PRESETS)) {
    const presetValues = normalizeAudioValues(preset.values);
    if (!presetValues) continue;
    if (
      presetValues.agc_on === normalized.agc_on &&
      presetValues.limiter_on === normalized.limiter_on &&
      presetValues.comp_thr === normalized.comp_thr &&
      presetValues.comp_att === normalized.comp_att &&
      presetValues.comp_rel === normalized.comp_rel &&
      presetValues.comp_gain === normalized.comp_gain &&
      presetValues.lim_rel === normalized.lim_rel
    ) {
      return id;
    }
  }

  return null;
}

function setAudioPresetSelection(audio) {
  if (!audioPresetSelect || !audioPresetReset) {
    return;
  }

  if (!audio) {
    audioPresetSelect.value = '';
    audioPresetReset.disabled = true;
    return;
  }

  const match = findMatchingAudioPreset(audio);
  if (match) {
    audioPresetSelect.value = match;
    audioPresetReset.disabled = false;
  } else {
    audioPresetSelect.value = '';
    audioPresetReset.disabled = true;
  }
}

function readAudioFormValues() {
  return {
    agc_on: document.getElementById('audio-agc').checked,
    limiter_on: document.getElementById('audio-limiter').checked,
    comp_thr: document.getElementById('audio-comp-thr').value,
    comp_att: document.getElementById('audio-comp-att').value,
    comp_rel: document.getElementById('audio-comp-rel').value,
    comp_gain: document.getElementById('audio-comp-gain').value,
    lim_rel: document.getElementById('audio-lim-rel').value,
  };
}

function applyAudioPresetById(id) {
  const preset = AUDIO_PRESETS[id];
  if (!preset) {
    return;
  }

  suspendDirty = true;
  document.getElementById('audio-agc').checked = Boolean(preset.values.agc_on);
  document.getElementById('audio-limiter').checked = Boolean(preset.values.limiter_on);
  document.getElementById('audio-comp-thr').value = preset.values.comp_thr;
  document.getElementById('audio-comp-att').value = preset.values.comp_att;
  document.getElementById('audio-comp-rel').value = preset.values.comp_rel;
  document.getElementById('audio-comp-gain').value = preset.values.comp_gain;
  document.getElementById('audio-lim-rel').value = preset.values.lim_rel;
  suspendDirty = false;
  setAudioPresetSelection(preset.values);
  markDirty();
}

function renderRdsFlags(container, rds) {
  container.innerHTML = '';

  if (!rds || Object.keys(rds).length === 0) {
    const chip = document.createElement('span');
    chip.className = 'chip chip--inactive';
    chip.textContent = 'RDS Inactive';
    container.append(chip);
    return;
  }

  const di = rds.di || {};
  const descriptors = [
    { label: 'RDS', active: rds.enabled !== false },
    { label: 'TP', active: !!rds.tp },
    { label: 'TA', active: !!rds.ta },
    { label: rds.ms_music ? 'Music' : 'Speech', active: true },
    { label: 'Stereo', active: !!di.stereo },
    { label: 'Dyn PTY', active: !!di.dynamic_pty },
    { label: 'Compressed', active: !!di.compressed },
    { label: 'Art. Head', active: !!di.artificial_head },
  ];

  descriptors.forEach(({ label, active }) => {
    const chip = document.createElement('span');
    chip.className = `chip ${active ? 'chip--active' : 'chip--inactive'}`;
    chip.textContent = label;
    container.append(chip);
  });
}

function renderRdsPs(container, metaEl, rds) {
  container.innerHTML = '';
  if (metaEl) {
    metaEl.textContent = '';
    metaEl.classList.add('is-hidden');
  }

  const rawValues = Array.isArray(rds?.ps)
    ? rds.ps.filter((item) => item && item.trim().length > 0)
    : [];
  const formattedValues = Array.isArray(rds?.ps_formatted) && rds.ps_formatted.length
    ? rds.ps_formatted
    : rawValues;
  const values = formattedValues.length ? formattedValues : rawValues;
  const activeIndex = Number.isFinite(rds?.ps_active_index) ? Number(rds.ps_active_index) : -1;

  if (!values.length) {
    container.textContent = '—';
    return;
  }

  values.forEach((value, index) => {
    const chip = document.createElement('span');
    const display = typeof value === 'string' ? value.trim() : '';
    chip.className = 'chip chip--inactive';
    chip.textContent = display || '—';
    if (index === activeIndex) {
      chip.classList.remove('chip--inactive');
      chip.classList.add('chip--active');
    }
    container.append(chip);
  });

  if (metaEl) {
    const count = Number.isFinite(rds?.ps_count) ? Number(rds.ps_count) : values.length;
    const speed = Number.isFinite(rds?.ps_speed) ? Number(rds.ps_speed) : null;
    const activeText = typeof rds?.ps_current === 'string' ? rds.ps_current.trim() : '';
    const parts = [];
    if (activeText) {
      parts.push(`Current: ${activeText}`);
    }
    parts.push(`Count: ${count}`);
    if (speed !== null) {
      parts.push(`Speed: ${speed}`);
    }
    if (rds?.ps_center !== undefined) {
      parts.push(rds.ps_center ? 'Centered' : 'Left aligned');
    }
    metaEl.textContent = parts.join(' · ');
    metaEl.classList.remove('is-hidden');
  }
}

function markDirty() {
  if (!formEnabled || suspendDirty) {
    return;
  }
  isDirty = true;
}

function syncMonitorIntervalPreview() {
  if (!monitorIntervalPreview || !monitorIntervalInput) {
    return;
  }

  const raw = monitorIntervalInput.value;
  const trimmed = typeof raw === 'string' ? raw.trim() : '';
  if (!trimmed) {
    monitorIntervalPreview.textContent = '—';
    return;
  }

  const numeric = Number(trimmed);
  if (Number.isFinite(numeric)) {
    const decimals = Math.abs(numeric % 1) < 1e-6 ? 0 : 1;
    monitorIntervalPreview.textContent = `${numeric.toFixed(decimals)} s`;
  } else {
    monitorIntervalPreview.textContent = trimmed;
  }
}

function setFormEnabled(enabled) {
  formEnabled = enabled;
  configFields.disabled = !enabled;
  saveBtn.disabled = !enabled;
  applyBtn.disabled = !enabled;
  duplicateBtn.disabled = !enabled;
  if (!enabled) {
    clearForm();
    currentConfig = '';
    isDirty = false;
  }
}

function clearForm() {
  suspendDirty = true;
  const inputs = configFields.querySelectorAll('input[type="text"], input[type="number"]');
  inputs.forEach((input) => {
    input.value = '';
  });
  const checkboxes = configFields.querySelectorAll('input[type="checkbox"]');
  checkboxes.forEach((input) => {
    input.checked = false;
  });
  renderList(psList, [], 'Station name');
  renderList(rtTextsList, [], 'Radiotext line');
  renderList(skipWordsList, [], 'word to skip');
  setAudioPresetSelection(null);
  syncMonitorIntervalPreview();
  suspendDirty = false;
}

function createListItem(container, value, placeholder) {
  const wrapper = document.createElement('div');
  wrapper.className = 'config-list-item';

  const input = document.createElement('input');
  input.type = 'text';
  input.className = 'pure-input-1';
  input.placeholder = placeholder;
  input.value = value || '';
  input.addEventListener('input', markDirty);

  const removeBtn = document.createElement('button');
  removeBtn.type = 'button';
  removeBtn.className = 'icon-button';
  removeBtn.innerHTML = '&times;';
  removeBtn.setAttribute('aria-label', 'Remove');
  removeBtn.addEventListener('click', () => {
    container.removeChild(wrapper);
    if (!container.children.length) {
      createListItem(container, '', placeholder);
    }
    markDirty();
  });

  wrapper.append(input, removeBtn);
  container.append(wrapper);
}

function renderList(container, values, placeholder) {
  container.innerHTML = '';
  const items = values && values.length ? values : [''];
  items.forEach((value) => createListItem(container, value, placeholder));
}

function readList(container) {
  return Array.from(container.querySelectorAll('input'))
    .map((input) => input.value.trim())
    .filter((value) => value.length > 0);
}

function updateStatus(data) {
  if (!data) return;
  const psValue = typeof data.ps === 'string' ? data.ps.trim() : data.ps;
  psEl.textContent = psValue && psValue.length ? psValue : '—';
  rtEl.textContent = data.rt || '—';
  freqEl.textContent = formatFrequency(data.frequency_khz);
  powerEl.textContent = data.power ?? '—';
  capEl.textContent = data.antenna_cap ?? '—';
  overmodEl.classList.toggle('is-hidden', !data.overmodulation);
  watchdogEl.textContent = data.watchdog_status || '—';
  if (audioLevelEl && audioStatusEl) {
    const audio = data.audio || {};
    const levelValue = Number.isFinite(data.audio_input_dbfs)
      ? Number(data.audio_input_dbfs)
      : Number.isFinite(audio.input_level_dbfs)
        ? Number(audio.input_level_dbfs)
        : null;
    audioLevelEl.textContent = levelValue === null ? '—' : `${levelValue} dBFS`;

    const limiterEnabled = audio.limiter_on !== undefined ? !!audio.limiter_on : true;
    let statusText = 'Telemetry idle';
    let statusClass = '';
    if (levelValue === null) {
      statusText = 'Telemetry idle';
    } else if (audio.overmod || data.overmodulation) {
      statusText = 'Clipping';
      statusClass = 'is-danger';
    } else if (levelValue >= -1) {
      statusText = 'Hot input';
      statusClass = 'is-warning';
    } else if (!limiterEnabled) {
      statusText = 'Limiter off';
    } else {
      statusText = 'Limiter idle';
    }

    audioStatusEl.textContent = statusText;
    audioStatusEl.classList.remove('is-warning', 'is-danger');
    if (statusClass) {
      audioStatusEl.classList.add(statusClass);
    }
  }
  const rds = data.rds || {};
  if (rdsPiEl) {
    rdsPiEl.textContent = rds.pi || '—';
  }
  if (rdsPtyEl) {
    if (typeof rds.pty === 'number' && Number.isFinite(rds.pty)) {
      rdsPtyEl.textContent = rds.pty;
    } else if (typeof rds.pty === 'string' && rds.pty.trim().length > 0) {
      rdsPtyEl.textContent = rds.pty;
    } else {
      rdsPtyEl.textContent = '—';
    }
  }
  if (rdsFlagsEl) {
    renderRdsFlags(rdsFlagsEl, rds);
  }
  if (rdsPsEl) {
    renderRdsPs(rdsPsEl, rdsPsMetaEl, rds);
  }

  const broadcasting = Boolean(data.broadcasting);
  broadcastBadge.textContent = broadcasting ? 'ON' : 'OFF';
  broadcastBadge.classList.toggle('status-pill--on', broadcasting);
  broadcastBadge.classList.toggle('status-pill--off', !broadcasting);
  toggleBroadcast.checked = broadcasting;
  toggleBroadcast.disabled = !data.config_name;

  if (data.config_name) {
    if (!currentConfig) {
      currentConfig = data.config_name;
    }
    if (configSelect && configSelect.value !== data.config_name) {
      configSelect.value = data.config_name;
    }
    if (!initialConfigLoaded && currentConfig === data.config_name) {
      initialConfigLoaded = true;
      loadConfig(currentConfig);
    }
  }
}

async function fetchStatus() {
  try {
    const res = await fetch('/api/status');
    if (!res.ok) return null;
    const data = await res.json();
    updateStatus(data);
    return data;
  } catch (error) {
    console.error('Failed to fetch status', error);
    return null;
  }
}

function subscribeEvents() {
  const source = new EventSource('/events');
  source.onmessage = (event) => {
    try {
      const payload = JSON.parse(event.data);
      updateStatus(payload);
    } catch (error) {
      console.error('Invalid SSE payload', error);
    }
  };
  source.onerror = () => {
    source.close();
    setTimeout(subscribeEvents, 3000);
  };
}

async function refreshConfigs() {
  try {
    const res = await fetch('/api/configs');
    if (!res.ok) return null;
    const data = await res.json();
    if (!data.items) return data;
    const previous = configSelect.value || currentConfig;
    configSelect.innerHTML = '<option value="">Select configuration…</option>';
    for (const item of data.items) {
      const option = document.createElement('option');
      option.value = item;
      option.textContent = item;
      configSelect.append(option);
    }
    if (previous && data.items.includes(previous)) {
      configSelect.value = previous;
      currentConfig = previous;
    }
    if (!initialConfigLoaded && currentConfig && data.items.includes(currentConfig)) {
      await loadConfig(currentConfig);
    }
    return data;
  } catch (error) {
    console.error('Failed to refresh configs', error);
    return null;
  }
}

function showFeedback(message, type = 'success') {
  feedback.textContent = message;
  feedback.classList.remove('is-hidden', 'notice--success', 'notice--error');
  feedback.classList.add('notice', 'notice--inline');
  if (type === 'success') {
    feedback.classList.add('notice--success');
  } else {
    feedback.classList.add('notice--error');
  }
  setTimeout(() => feedback.classList.add('is-hidden'), 4000);
}

function populateForm(config) {
  suspendDirty = true;
  document.getElementById('rf-frequency').value = fromKhzToMHz(
    config.rf?.frequency_khz
  );
  document.getElementById('rf-power').value = config.rf?.power ?? '';
  document.getElementById('rf-antenna').value = config.rf?.antenna_cap ?? '';

  const audio = config.audio || {};
  document.getElementById('audio-agc').checked = Boolean(audio.agc_on);
  document.getElementById('audio-limiter').checked = audio.limiter_on !== false;
  document.getElementById('audio-comp-thr').value =
    audio.comp_thr !== undefined ? audio.comp_thr : '';
  document.getElementById('audio-comp-att').value =
    audio.comp_att !== undefined ? audio.comp_att : '';
  document.getElementById('audio-comp-rel').value =
    audio.comp_rel !== undefined ? audio.comp_rel : '';
  document.getElementById('audio-comp-gain').value =
    audio.comp_gain !== undefined ? audio.comp_gain : '';
  document.getElementById('audio-lim-rel').value =
    audio.lim_rel !== undefined ? audio.lim_rel : '';
  setAudioPresetSelection(audio);

  piInput.value = formatPiDigits(config.rds?.pi);
  document.getElementById('rds-pty').value = config.rds?.pty ?? '';
  document.getElementById('rds-deviation').value = config.rds?.deviation_hz ?? '';
  document.getElementById('rds-tp').checked = Boolean(config.rds?.tp);
  document.getElementById('rds-ta').checked = Boolean(config.rds?.ta);
  document.getElementById('rds-ms-music').checked = Boolean(config.rds?.ms_music);

  const di = config.rds?.di || {};
  document.getElementById('di-stereo').checked = Boolean(di.stereo);
  document.getElementById('di-artificial-head').checked = Boolean(di.artificial_head);
  document.getElementById('di-compressed').checked = Boolean(di.compressed);
  document.getElementById('di-dynamic-pty').checked = Boolean(di.dynamic_pty);

  renderList(psList, config.rds?.ps || [], 'Station name');
  document.getElementById('rds-ps-center').checked = Boolean(config.rds?.ps_center);
  document.getElementById('rds-ps-speed').value = config.rds?.ps_speed ?? '';
  document.getElementById('rds-ps-count').value = config.rds?.ps_count ?? '';

  const rt = config.rds?.rt || {};
  document.getElementById('rt-text').value = rt.text ?? '';
  document.getElementById('rt-speed').value = rt.speed_s ?? '';
  document.getElementById('rt-center').checked = Boolean(rt.center);
  renderList(rtTextsList, rt.texts || [], 'Radiotext line');
  document.getElementById('rt-file-path').value = rt.file_path ?? '';
  renderList(skipWordsList, rt.skip_words || [], 'word to skip');
  document.getElementById('rt-ab-mode').value = rt.ab_mode ?? 'auto';
  document.getElementById('rt-repeats').value = rt.repeats ?? '';
  document.getElementById('rt-gap').value = rt.gap_ms ?? '';
  document.getElementById('rt-bank').value = rt.bank ?? '';

  const monitor = config.monitor || {};
  document.getElementById('monitor-health').checked = Boolean(monitor.health);
  document.getElementById('monitor-asq').checked = Boolean(monitor.asq);
  document.getElementById('monitor-interval').value = monitor.interval_s ?? '';
  document.getElementById('monitor-recovery-attempts').value = monitor.recovery_attempts ?? '';
  document.getElementById('monitor-recovery-backoff').value = monitor.recovery_backoff_s ?? '';
  syncMonitorIntervalPreview();

  suspendDirty = false;
  isDirty = false;
}

function collectFormData() {
  return {
    rf: {
      frequency_khz: toKhzFromMHz(document.getElementById('rf-frequency').value),
      power: document.getElementById('rf-power').value.trim(),
      antenna_cap: document.getElementById('rf-antenna').value.trim(),
    },
    rds: {
      pi: collectPiValue(),
      pty: document.getElementById('rds-pty').value.trim(),
      deviation_hz: document.getElementById('rds-deviation').value.trim(),
      tp: document.getElementById('rds-tp').checked,
      ta: document.getElementById('rds-ta').checked,
      ms_music: document.getElementById('rds-ms-music').checked,
      di: {
        stereo: document.getElementById('di-stereo').checked,
        artificial_head: document.getElementById('di-artificial-head').checked,
        compressed: document.getElementById('di-compressed').checked,
        dynamic_pty: document.getElementById('di-dynamic-pty').checked,
      },
      ps: readList(psList),
      ps_center: document.getElementById('rds-ps-center').checked,
      ps_speed: document.getElementById('rds-ps-speed').value.trim(),
      ps_count: document.getElementById('rds-ps-count').value.trim(),
      rt: {
        text: document.getElementById('rt-text').value.trim(),
        texts: readList(rtTextsList),
        speed_s: document.getElementById('rt-speed').value.trim(),
        center: document.getElementById('rt-center').checked,
        file_path: document.getElementById('rt-file-path').value.trim(),
        skip_words: readList(skipWordsList),
        ab_mode: document.getElementById('rt-ab-mode').value,
        repeats: document.getElementById('rt-repeats').value.trim(),
        gap_ms: document.getElementById('rt-gap').value.trim(),
        bank: document.getElementById('rt-bank').value.trim(),
      },
    },
    monitor: {
      health: document.getElementById('monitor-health').checked,
      asq: document.getElementById('monitor-asq').checked,
      interval_s: monitorIntervalInput ? monitorIntervalInput.value.trim() : '',
      recovery_attempts: document.getElementById('monitor-recovery-attempts').value.trim(),
      recovery_backoff_s: document.getElementById('monitor-recovery-backoff').value.trim(),
    },
    audio: {
      agc_on: document.getElementById('audio-agc').checked,
      limiter_on: document.getElementById('audio-limiter').checked,
      comp_thr: document.getElementById('audio-comp-thr').value.trim(),
      comp_att: document.getElementById('audio-comp-att').value.trim(),
      comp_rel: document.getElementById('audio-comp-rel').value.trim(),
      comp_gain: document.getElementById('audio-comp-gain').value.trim(),
      lim_rel: document.getElementById('audio-lim-rel').value.trim(),
    },
  };
}

async function loadConfig(name) {
  if (!name) return;
  try {
    const res = await fetch(`/api/configs/${encodeURIComponent(name)}`);
    const data = await res.json();
    if (!res.ok) {
      showFeedback(data.error || 'Unable to load configuration', 'danger');
      return;
    }
    populateForm(data.config || {});
    setFormEnabled(true);
    currentConfig = name;
    saveBtn.disabled = false;
    applyBtn.disabled = false;
    duplicateBtn.disabled = false;
    initialConfigLoaded = true;
  } catch (error) {
    console.error('Failed to load config', error);
    if (name === currentConfig) {
      initialConfigLoaded = false;
    }
  }
}

async function saveConfig() {
  if (!currentConfig) return;
  try {
    const payload = collectFormData();
    const res = await fetch(`/api/configs/${encodeURIComponent(currentConfig)}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ config: payload })
    });
    const data = await res.json();
    if (!res.ok) {
      showFeedback(data.error || 'Save failed', 'danger');
      return;
    }
    showFeedback('Configuration saved');
    isDirty = false;
    await refreshConfigs();
  } catch (error) {
    console.error('Save failed', error);
  }
}

async function applyConfig() {
  if (!currentConfig) return;
  try {
    const res = await fetch(`/api/configs/${encodeURIComponent(currentConfig)}/apply`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' }
    });
    const data = await res.json();
    if (!res.ok) {
      showFeedback(data.error || 'Apply failed', 'danger');
      return;
    }
    showFeedback('Configuration applied');
    updateStatus(data);
  } catch (error) {
    console.error('Apply failed', error);
  }
}

async function duplicateConfig() {
  if (!currentConfig) return;
  const name = prompt('Duplicate as (relative path):');
  if (!name) return;
  try {
    const payload = collectFormData();
    const res = await fetch(`/api/configs/${encodeURIComponent(name)}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ config: payload })
    });
    const data = await res.json();
    if (!res.ok) {
      showFeedback(data.error || 'Duplicate failed', 'danger');
      return;
    }
    showFeedback('Configuration duplicated');
    currentConfig = name;
    await refreshConfigs();
    configSelect.value = name;
  } catch (error) {
    console.error('Duplicate failed', error);
  }
}

async function toggleBroadcasting(enabled) {
  try {
    const res = await fetch('/api/broadcast', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled })
    });
    const data = await res.json();
    if (!res.ok) {
      showFeedback(data.error || 'Broadcast toggle failed', 'danger');
      toggleBroadcast.checked = !enabled;
      return;
    }
    updateStatus(data);
  } catch (error) {
    console.error('Broadcast toggle failed', error);
  }
}

configFields.addEventListener('input', markDirty);
configFields.addEventListener('change', markDirty);

if (monitorIntervalInput) {
  monitorIntervalInput.addEventListener('input', syncMonitorIntervalPreview);
}

piInput.addEventListener('input', () => {
  const sanitized = sanitizePiDigits(piInput.value);
  if (sanitized !== piInput.value) {
    piInput.value = sanitized;
  }
  markDirty();
});

piInput.addEventListener('blur', () => {
  const digits = sanitizePiDigits(piInput.value);
  piInput.value = digits ? digits.padStart(4, '0') : '';
});

configSelect.addEventListener('change', (event) => {
  const value = event.target.value;
  currentConfig = value;
  if (value) {
    loadConfig(value);
  } else {
    setFormEnabled(false);
  }
});

refreshBtn.addEventListener('click', refreshConfigs);
saveBtn.addEventListener('click', saveConfig);
applyBtn.addEventListener('click', applyConfig);
duplicateBtn.addEventListener('click', duplicateConfig);
toggleBroadcast.addEventListener('change', (event) => toggleBroadcasting(event.target.checked));

addPsBtn.addEventListener('click', () => {
  createListItem(psList, '', 'Station name');
  markDirty();
});

addRtTextBtn.addEventListener('click', () => {
  createListItem(rtTextsList, '', 'Radiotext line');
  markDirty();
});

addSkipWordBtn.addEventListener('click', () => {
  createListItem(skipWordsList, '', 'word to skip');
  markDirty();
});

AUDIO_FIELD_IDS.forEach((id) => {
  const el = document.getElementById(id);
  if (!el) {
    return;
  }
  const handleChange = () => {
    if (suspendDirty) {
      return;
    }
    setAudioPresetSelection(readAudioFormValues());
  };
  el.addEventListener('input', handleChange);
  el.addEventListener('change', handleChange);
});

if (audioPresetSelect) {
  audioPresetSelect.addEventListener('change', (event) => {
    event.stopPropagation();
    if (audioPresetReset) {
      audioPresetReset.disabled = !audioPresetSelect.value;
    }
  });
}

if (audioPresetReset) {
  audioPresetReset.addEventListener('click', (event) => {
    event.preventDefault();
    event.stopPropagation();
    if (!audioPresetSelect.value) {
      return;
    }
    applyAudioPresetById(audioPresetSelect.value);
  });
}

async function bootstrap() {
  clearForm();
  const statusPromise = fetchStatus();
  const configsPromise = refreshConfigs();
  const status = await statusPromise;
  await configsPromise;

  if (!initialConfigLoaded && status?.config_name) {
    currentConfig = status.config_name;
    await loadConfig(status.config_name);
  }

  subscribeEvents();
}

bootstrap();
