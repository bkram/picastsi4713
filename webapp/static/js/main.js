const broadcastBadge = document.getElementById('status-broadcast');
const psEl = document.getElementById('metric-ps');
const rtEl = document.getElementById('metric-rt');
const rtSourceEl = document.getElementById('metric-rt-source');
const freqEl = document.getElementById('metric-frequency');
const powerEl = document.getElementById('metric-power');
const capEl = document.getElementById('metric-cap');
const overmodEl = document.getElementById('metric-overmod');
const watchdogEl = document.getElementById('metric-watchdog');
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

const feedback = document.getElementById('config-feedback');

let currentConfig = '';
let isDirty = false;
let formEnabled = false;
let suspendDirty = false;

function formatFrequency(khz) {
  if (!khz) return '—';
  return `${(khz / 1000).toFixed(3)} MHz`;
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

function renderRdsFlags(container, rds) {
  container.innerHTML = '';

  if (!rds || Object.keys(rds).length === 0) {
    const badge = document.createElement('span');
    badge.className = 'badge rounded-pill text-bg-secondary';
    badge.textContent = 'RDS Inactive';
    container.append(badge);
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
    const badge = document.createElement('span');
    badge.className = `badge rounded-pill ${active ? 'text-bg-success' : 'text-bg-secondary'}`;
    badge.textContent = label;
    container.append(badge);
  });
}

function renderRdsPs(container, metaEl, rds) {
  container.innerHTML = '';
  if (metaEl) {
    metaEl.textContent = '';
    metaEl.classList.add('d-none');
  }

  const values = Array.isArray(rds?.ps) ? rds.ps.filter((item) => item && item.trim().length > 0) : [];
  if (!values.length) {
    container.textContent = '—';
    return;
  }

  values.forEach((value) => {
    const badge = document.createElement('span');
    badge.className = 'badge rounded-pill text-bg-primary';
    badge.textContent = value;
    container.append(badge);
  });

  if (metaEl) {
    const count = Number.isFinite(rds?.ps_count) ? Number(rds.ps_count) : values.length;
    const speed = Number.isFinite(rds?.ps_speed) ? Number(rds.ps_speed) : null;
    const parts = [`Count: ${count}`];
    if (speed !== null) {
      parts.push(`Speed: ${speed}`);
    }
    if (rds?.ps_center !== undefined) {
      parts.push(rds.ps_center ? 'Centered' : 'Left aligned');
    }
    metaEl.textContent = parts.join(' · ');
    metaEl.classList.remove('d-none');
  }
}

function markDirty() {
  if (!formEnabled || suspendDirty) {
    return;
  }
  isDirty = true;
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
  suspendDirty = false;
}

function createListItem(container, value, placeholder) {
  const wrapper = document.createElement('div');
  wrapper.className = 'config-list-item';

  const input = document.createElement('input');
  input.type = 'text';
  input.className = 'form-control';
  input.placeholder = placeholder;
  input.value = value || '';
  input.addEventListener('input', markDirty);

  const removeBtn = document.createElement('button');
  removeBtn.type = 'button';
  removeBtn.className = 'btn btn-outline-secondary';
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
  psEl.textContent = data.ps || '—';
  rtEl.textContent = data.rt || '—';
  rtSourceEl.textContent = data.rt_source ? `Source: ${data.rt_source}` : 'Source: —';
  freqEl.textContent = formatFrequency(data.frequency_khz);
  powerEl.textContent = data.power ?? '—';
  capEl.textContent = data.antenna_cap ?? '—';
  overmodEl.classList.toggle('d-none', !data.overmodulation);
  watchdogEl.textContent = data.watchdog_status || '—';
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
  broadcastBadge.dataset.state = broadcasting ? 'on' : 'off';
  broadcastBadge.classList.toggle('bg-success', broadcasting);
  broadcastBadge.classList.toggle('bg-secondary', !broadcasting);
  toggleBroadcast.checked = broadcasting;
  toggleBroadcast.disabled = !data.config_name;

  if (data.config_name && !currentConfig) {
    currentConfig = data.config_name;
    configSelect.value = currentConfig;
  }
}

async function fetchStatus() {
  try {
    const res = await fetch('/api/status');
    if (!res.ok) return;
    const data = await res.json();
    updateStatus(data);
  } catch (error) {
    console.error('Failed to fetch status', error);
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
    if (!res.ok) return;
    const data = await res.json();
    if (!data.items) return;
    const previous = configSelect.value;
    configSelect.innerHTML = '<option value="">Select configuration…</option>';
    for (const item of data.items) {
      const option = document.createElement('option');
      option.value = item;
      option.textContent = item;
      configSelect.append(option);
    }
    if (previous && data.items.includes(previous)) {
      configSelect.value = previous;
    }
  } catch (error) {
    console.error('Failed to refresh configs', error);
  }
}

function showFeedback(message, type = 'success') {
  feedback.textContent = message;
  feedback.className = `alert alert-${type}`;
  feedback.classList.remove('d-none');
  setTimeout(() => feedback.classList.add('d-none'), 4000);
}

function populateForm(config) {
  suspendDirty = true;
  document.getElementById('rf-frequency').value = config.rf?.frequency_khz ?? '';
  document.getElementById('rf-power').value = config.rf?.power ?? '';
  document.getElementById('rf-antenna').value = config.rf?.antenna_cap ?? '';

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

  suspendDirty = false;
  isDirty = false;
}

function collectFormData() {
  return {
    rf: {
      frequency_khz: document.getElementById('rf-frequency').value.trim(),
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
      interval_s: document.getElementById('monitor-interval').value.trim(),
      recovery_attempts: document.getElementById('monitor-recovery-attempts').value.trim(),
      recovery_backoff_s: document.getElementById('monitor-recovery-backoff').value.trim(),
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
  } catch (error) {
    console.error('Failed to load config', error);
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

clearForm();
fetchStatus();
refreshConfigs();
subscribeEvents();
