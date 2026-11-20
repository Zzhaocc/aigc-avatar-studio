/* AIGC Avatar Studio · 控制台
 * 零依赖单页应用：拉数据 → 渲染 → 轮询。
 * 所有来自服务端的文本都经过 esc()，避免注入。
 */
'use strict';

const state = {
  health: null,
  catalog: null,
  models: [],
  voices: [],
  jobs: [],
  assets: [],
  job: null,
  jobId: null,
  view: 'studio',
  busy: false,
  pollTimer: null,
  modelsTimer: null,
};

const $ = (sel) => document.querySelector(sel);
const TERMINAL = ['succeeded', 'failed', 'cancelled'];

function esc(v) {
  return String(v == null ? '' : v).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}

const fmt = {
  dt(v) {
    if (!v) return '—';
    const d = new Date(v);
    return Number.isNaN(d.getTime()) ? '—' : d.toLocaleString('zh-CN', { hour12: false });
  },
  ms(v) {
    if (v == null) return '—';
    return v >= 1000 ? (v / 1000).toFixed(1) + 's' : v + 'ms';
  },
  bytes(v) {
    if (v == null) return '—';
    if (v >= 1048576) return (v / 1048576).toFixed(2) + ' MB';
    if (v >= 1024) return (v / 1024).toFixed(1) + ' KB';
    return v + ' B';
  },
  num(v, d = 0) {
    if (v == null) return '—';
    return Number(v).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
  },
  dur(v) {
    if (v == null) return '—';
    return Number(v).toFixed(1) + 's';
  },
};

function toast(message, kind = 'info') {
  const stack = $('#toast-stack');
  const node = document.createElement('div');
  node.className = `toast ${kind}`;
  node.innerHTML = `<span>${kind === 'error' ? '✕' : kind === 'success' ? '✓' : 'i'}</span>
    <span>${esc(message)}</span><button class="toast-close" type="button">×</button>`;
  node.querySelector('.toast-close').addEventListener('click', () => node.remove());
  stack.appendChild(node);
  setTimeout(() => node.remove(), 5000);
}

async function api(path, options = {}) {
  const init = { ...options, headers: { ...(options.body ? { 'Content-Type': 'application/json' } : {}) } };
  if (init.body && typeof init.body !== 'string') init.body = JSON.stringify(init.body);
  const resp = await fetch(path, init);
  const text = await resp.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = { raw: text }; }
  if (!resp.ok) {
    const detail = data && data.detail ? (typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail)) : resp.statusText;
    throw new Error(detail || `HTTP ${resp.status}`);
  }
  return data;
}

function statusPill(status) {
  const label = { queued: '排队中', running: '生成中', succeeded: '已完成', failed: '失败', cancelled: '已取消' }[status] || status;
  return `<span class="pill ${status}">${esc(label)}</span>`;
}

/* ------------------------------------------------------------------ */
async function boot() {
  try {
    const [health, catalog] = await Promise.all([api('/api/health'), api('/api/catalog')]);
    state.health = health;
    state.catalog = catalog;
    state.models = catalog.models || [];
    state.voices = catalog.voices || [];
    renderHealth();
    renderSelects();
    renderDemoChips();
    await loadJobs();
    const latest = state.jobs.find((j) => j.status === 'succeeded') || state.jobs[0];
    if (latest) await openJob(latest.id, { quiet: true });
    switchView('studio');
    startPolling();
  } catch (err) {
    $('#job-body').innerHTML = `<div class="empty"><div class="empty-mark">!</div>
      <div class="empty-title">加载失败</div><div class="empty-desc">${esc(err.message)}</div></div>`;
  }
}

function renderHealth() {
  const h = state.health || {};
  $('#health').innerHTML = `<span class="dot succeeded"></span>
    <div><div class="health-title">服务正常</div>
    <div class="health-sub">v${esc(h.version)} · ${esc(h.workers)} worker</div></div>`;
  $('#engine-info').textContent = `${h.engine || 'local-engine'} · ${h.ffmpeg || 'ffmpeg'}`;
}

function renderSelects() {
  const voiceSel = $('#voice');
  voiceSel.innerHTML = state.voices.map((v) => `<option value="${esc(v.id)}">${esc(v.name)}</option>`).join('');
  const styleSel = $('#style');
  styleSel.innerHTML = (state.catalog.styles || []).map((s) => `<option value="${esc(s.key)}">${esc(s.label)}</option>`).join('');
  const resSel = $('#resolution');
  resSel.innerHTML = (state.catalog.resolution_presets || []).map((r) =>
    `<option value="${r.width}x${r.height}">${esc(r.label)}</option>`).join('');
  const modelSel = $('#model');
  modelSel.innerHTML = state.models.map((m) => {
    const disabled = m.builtin ? '' : (m.status === 'running' ? '' : 'disabled');
    return `<option value="${esc(m.id)}" ${disabled}>${esc(m.name)}${m.builtin ? ' · 内置' : ''}${m.status !== 'running' && !m.builtin ? '（未运行）' : ''}</option>`;
  }).join('');
  const local = state.models.find((m) => m.builtin);
  if (local) modelSel.value = local.id;
}

function renderDemoChips() {
  $('#demo-chips').innerHTML = (state.catalog.demo_scripts || []).map((s, i) =>
    `<button class="chip" data-demo="${i}" type="button">${esc(s.title)}</button>`).join('');
  $('#demo-chips').querySelectorAll('[data-demo]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const item = state.catalog.demo_scripts[Number(btn.dataset.demo)];
      $('#script').value = item.text;
      $('#script').dispatchEvent(new Event('input'));
    });
  });
}

/* ------------------------------------------------------------------ */
async function loadJobs() {
  const data = await api('/api/jobs?limit=30');
  state.jobs = data.items || [];
  renderRecent();
  if (state.view === 'jobs') renderJobsTable();
}

async function openJob(jobId, { quiet = false } = {}) {
  state.jobId = jobId;
  try {
    state.job = await api(`/api/jobs/${jobId}`);
    renderJob();
    if (!quiet) switchView('studio');
  } catch (err) {
    if (!quiet) toast(err.message, 'error');
  }
}

async function produceJob(demo = false) {
  if (state.busy) return;
  state.busy = true;
  const buttons = [$('#btn-produce'), $('#btn-demo')];
  buttons.forEach((b) => { if (b) b.disabled = true; });
  try {
    let data;
    if (demo) {
      data = await api('/api/demo/run', { method: 'POST' });
    } else {
      const script = $('#script').value.trim();
      if (!script) throw new Error('先输入脚本');
      const [w, h] = ($('#resolution').value || '720x1280').split('x').map(Number);
      data = await api('/api/produce', {
        method: 'POST',
        body: {
          script,
          title: script.slice(0, 18) + (script.length > 18 ? '…' : ''),
          voice_id: $('#voice').value || null,
          model_id: $('#model').value || null,
          style: $('#style').value || 'studio',
          width: w, height: h, fps: 2,
        },
      });
    }
    toast('任务已入队 ' + data.job_id, 'success');
    await openJob(data.job_id, { quiet: true });
    await loadJobs();
    switchView('studio');
  } catch (err) {
    toast(err.message, 'error');
  } finally {
    state.busy = false;
    buttons.forEach((b) => { if (b) b.disabled = false; });
  }
}

function renderJob() {
  const job = state.job;
  const body = $('#job-body');
  const status = $('#job-status');
  if (!job) {
    status.className = 'pill';
    status.textContent = '空闲';
    body.innerHTML = `<div class="empty"><div class="empty-mark">◍</div><div class="empty-title">还没有任务</div>
      <div class="empty-desc">输入脚本后点「生成视频」，或先试一下「一键示例」。</div></div>`;
    return;
  }
  status.className = `pill ${job.status}`;
  status.textContent = { queued: '排队中', running: '生成中', succeeded: '已完成', failed: '失败', cancelled: '已取消' }[job.status] || job.status;

  const fillClass = job.status === 'succeeded' ? 'done' : job.status === 'failed' ? 'fail' : '';
  const steps = (job.steps || []).map((s) => {
    const icon = s.status === 'succeeded' ? '✓' : s.status === 'failed' ? '✗' : s.status === 'cancelled' ? '–' : '·';
    const color = s.status === 'succeeded' ? '#34d399' : s.status === 'failed' ? '#f87171' : s.status === 'cancelled' ? '#64748b' : '#22d3ee';
    return `<div class="step-row">
      <span class="step-no">${String(s.seq).padStart(2, '0')}</span>
      <div><div class="step-name" style="color:${color}">${icon} ${esc(s.name)}</div>
        <div class="step-detail">${esc(s.detail || '')}</div></div>
      <span class="step-lat">${s.latency_ms ? fmt.ms(s.latency_ms) : ''}</span></div>`;
  }).join('');

  const assets = (job.assets || []).map((a) =>
    `<a class="asset" href="${esc(a.url)}" target="_blank" rel="noopener"><b>${esc(a.kind)}</b> · ${esc(a.filename)} · ${fmt.bytes(a.size_bytes)}</a>`).join('');

  const video = (job.assets || []).find((a) => a.kind === 'video');
  const audio = (job.assets || []).find((a) => a.kind === 'audio');

  body.innerHTML = `
    <div class="job-title" style="font-size:13px">${esc(job.title || '未命名作品')}</div>
    <div class="job-sub">${esc(job.id)} · ${fmt.dt(job.created_at)}${job.duration_ms ? ' · 耗时 ' + fmt.ms(job.duration_ms) : ''}</div>
    <div class="progress"><div class="progress-fill ${fillClass}" style="width:${job.progress}%"></div></div>
    <div class="small muted">${job.progress}%${job.error ? ' · <span style="color:#f87171">' + esc(job.error) + '</span>' : ''}</div>
    ${video ? `<div class="video-wrap"><video controls preload="metadata" src="${esc(video.preview_url || video.url)}"></video></div>` : ''}
    ${audio && !video ? `<audio controls style="width:100%;margin-top:10px" src="${esc(audio.preview_url || audio.url)}"></audio>` : ''}
    <div class="steps">${steps}</div>
    ${assets ? `<div class="asset-row">${assets}</div>` : ''}
    <div class="asset-row">
      ${job.status === 'running' || job.status === 'queued' ? '<button class="btn btn-sm btn-danger" id="btn-job-cancel" type="button">取消任务</button>' : ''}
      ${job.status === 'failed' || job.status === 'cancelled' ? '<button class="btn btn-sm" id="btn-job-retry" type="button">重试</button>' : ''}
    </div>`;
  const cancel = $('#btn-job-cancel');
  const retry = $('#btn-job-retry');
  if (cancel) cancel.addEventListener('click', async () => {
    try { await api(`/api/jobs/${job.id}/cancel`, { method: 'POST' }); toast('已请求取消', 'success'); await openJob(job.id, { quiet: true }); }
    catch (err) { toast(err.message, 'error'); }
  });
  if (retry) retry.addEventListener('click', async () => {
    try {
      const data = await api(`/api/jobs/${job.id}/retry`, { method: 'POST' });
      toast('已重新入队', 'success');
      await openJob(data.job_id, { quiet: true });
      await loadJobs();
    } catch (err) { toast(err.message, 'error'); }
  });
}

function renderRecent() {
  const box = $('#recent-jobs');
  if (!state.jobs.length) {
    box.innerHTML = '<div class="empty"><div class="empty-title">暂无任务</div><div class="empty-desc">生成第一条视频后会出现在这里。</div></div>';
    return;
  }
  box.innerHTML = state.jobs.map((j) => `
    <div class="job-item" data-job="${esc(j.id)}">
      <div><div class="job-title">${esc(j.title || '未命名')}</div><div class="job-sub">${esc(j.id)}</div></div>
      <div class="job-meta">${j.output && j.output.duration ? fmt.dur(j.output.duration) : '—'}</div>
      <div class="job-meta">${j.duration_ms ? fmt.ms(j.duration_ms) : '—'}</div>
      ${statusPill(j.status)}
    </div>`).join('');
  box.querySelectorAll('[data-job]').forEach((el) => {
    el.addEventListener('click', () => openJob(el.dataset.job));
  });
}

/* ------------------------------------------------------------------ */
async function renderModels() {
  const data = await api('/api/models');
  state.models = data.items || [];
  $('#model-grid').innerHTML = state.models.map((m) => {
    const downloading = m.status === 'downloading';
    return `<div class="model-card ${m.status}">
      <div class="model-top">
        <div><div class="model-name">${esc(m.name)}</div><div class="model-key">${esc(m.key)} · ${esc(m.kind)}</div></div>
        <span class="pill ${m.status === 'running' ? 'succeeded' : m.status === 'downloading' ? 'running' : m.status === 'error' ? 'failed' : 'queued'}">${esc(m.status)}</span>
      </div>
      <div class="model-desc">${esc(m.description)}</div>
      <div class="model-meta"><span>${m.size_mb ? m.size_mb + ' MB' : '内置'}</span><span>v${esc(m.version)}</span>${m.port ? `<span>:</span><span>:${m.port}</span>` : ''}</div>
      ${downloading ? `<div class="mini-progress"><div style="width:${m.progress}%"></div></div>
        <div class="small muted">下载中 ${m.progress}%</div>` : ''}
      <div class="tags">${(m.tags || []).map((t) => `<span class="tag">${esc(t)}</span>`).join('')}</div>
      <div class="model-actions">
        ${m.builtin ? '<span class="small muted">内置引擎常驻运行</span>' : ''}
        ${!m.builtin && m.status === 'not_installed' ? `<button class="btn btn-sm" data-act="install" data-id="${esc(m.id)}">安装</button>` : ''}
        ${!m.builtin && (m.status === 'installed' || m.status === 'stopped') ? `<button class="btn btn-sm btn-primary" data-act="start" data-id="${esc(m.id)}">启动</button>` : ''}
        ${!m.builtin && m.status === 'running' ? `<button class="btn btn-sm" data-act="stop" data-id="${esc(m.id)}">停止</button>` : ''}
        ${!m.builtin && (m.status === 'installed' || m.status === 'stopped') ? `<button class="btn btn-sm btn-danger" data-act="uninstall" data-id="${esc(m.id)}">卸载</button>` : ''}
        ${m.last_error ? `<span class="small" style="color:#f87171">${esc(m.last_error)}</span>` : ''}
      </div>
    </div>`;
  }).join('');
  $('#model-grid').querySelectorAll('[data-act]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const act = btn.dataset.act;
      btn.disabled = true;
      try {
        await api(`/api/models/${btn.dataset.id}/${act}`, { method: 'POST' });
        toast(`${act} 已提交`, 'success');
      } catch (err) { toast(err.message, 'error'); }
      await renderModels();
      renderSelects();
    });
  });
  renderSelects();
  if (state.models.some((m) => ['downloading', 'starting'].includes(m.status)) && !state.modelsTimer) {
    state.modelsTimer = setInterval(async () => {
      if (state.view !== 'models') { clearInterval(state.modelsTimer); state.modelsTimer = null; return; }
      await renderModels();
    }, 1200);
  }
}

/* ------------------------------------------------------------------ */
function renderVoices() {
  $('#voice-grid').innerHTML = state.voices.map((v) => `
    <div class="voice-card">
      <div class="voice-name">${esc(v.name)}</div>
      <div class="voice-note">${esc(v.note || '')}</div>
      <div class="kv"><span>pitch <b>${fmt.num(v.pitch, 2)}</b></span><span>speed <b>${fmt.num(v.speed, 2)}</b></span><span>${esc(v.timbre)}</span></div>
      <div class="kv"><span>${esc(v.kind)}</span>${v.reference_duration ? `<span>参考 ${fmt.dur(v.reference_duration)}</span>` : ''}</div>
      ${v.kind !== 'preset' ? `<button class="btn btn-sm btn-danger" data-del="${esc(v.id)}">删除</button>` : ''}
    </div>`).join('');
  $('#voice-grid').querySelectorAll('[data-del]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      try { await api(`/api/voices/${btn.dataset.del}`, { method: 'DELETE' }); toast('已删除', 'success'); }
      catch (err) { toast(err.message, 'error'); }
      await refreshVoices();
    });
  });
}

async function refreshVoices() {
  const data = await api('/api/voices');
  state.voices = data.items || [];
  renderVoices();
  renderSelects();
}

/* ------------------------------------------------------------------ */
function renderJobsTable() {
  if (!state.jobs.length) {
    $('#jobs-table').innerHTML = '<div class="empty"><div class="empty-title">队列为空</div></div>';
    return;
  }
  $('#jobs-table').innerHTML = `<table><thead><tr><th>任务</th><th>状态</th><th>进度</th><th>时长</th><th>耗时</th><th>创建时间</th><th></th></tr></thead>
    <tbody>${state.jobs.map((j) => `<tr>
      <td><div style="font-weight:600">${esc(j.title || '未命名')}</div><div class="mono muted">${esc(j.id)}</div></td>
      <td>${statusPill(j.status)}</td>
      <td>${j.progress}%</td>
      <td>${j.output && j.output.duration ? fmt.dur(j.output.duration) : '—'}</td>
      <td>${j.duration_ms ? fmt.ms(j.duration_ms) : '—'}</td>
      <td class="muted">${fmt.dt(j.created_at)}</td>
      <td><button class="btn btn-sm" data-open="${esc(j.id)}">查看</button></td>
    </tr>`).join('')}</tbody></table>`;
  $('#jobs-table').querySelectorAll('[data-open]').forEach((btn) => {
    btn.addEventListener('click', () => { openJob(btn.dataset.open); });
  });
}

/* ------------------------------------------------------------------ */
async function loadAssets() {
  const data = await api('/api/assets?limit=80');
  state.assets = data.items || [];
  const sel = $('#tool-asset');
  sel.innerHTML = state.assets.map((a) => `<option value="${esc(a.id)}">${esc(a.kind)} · ${esc(a.filename)} · ${fmt.bytes(a.size_bytes)}</option>`).join('')
    || '<option value="">暂无产物，先生成一条视频</option>';
}

async function runTool() {
  const assetId = $('#tool-asset').value;
  if (!assetId) { toast('还没有可操作的产物', 'error'); return; }
  const tool = $('#tool-name').value;
  const arg = $('#tool-arg').value.trim();
  const args = {};
  if (tool === 'change_speed') args.factor = Number(arg || 1.25);
  if (tool === 'thumbnail') args.at = Number(arg || 1.0);
  try {
    $('#tool-result').textContent = '执行中…';
    const data = await api(`/api/tools/${tool}`, { method: 'POST', body: { asset_id: assetId, args } });
    $('#tool-result').textContent = JSON.stringify(data.result, null, 2) + (data.asset ? `\n\n// 新产物：${data.asset.id} (${data.asset.kind})` : '');
    if (data.asset) { await loadAssets(); toast('已生成新产物', 'success'); }
  } catch (err) {
    $('#tool-result').textContent = '// 失败：' + err.message;
    toast(err.message, 'error');
  }
}

/* ------------------------------------------------------------------ */
async function renderMetrics() {
  const data = await api('/api/metrics');
  const j = data.jobs, a = data.assets, m = data.models;
  const cards = [
    ['任务总数', fmt.num(j.total), `成功 ${j.succeeded} · 失败 ${j.failed}`],
    ['成功率', j.success_rate == null ? '—' : (j.success_rate * 100).toFixed(1) + '%', '按已结束任务统计'],
    ['平均耗时', j.avg_duration_ms ? fmt.ms(j.avg_duration_ms) : '—', '端到端'],
    ['产物总数', fmt.num(a.count), `视频 ${a.video_count} 条`],
    ['产物体积', fmt.bytes(a.total_bytes), '磁盘占用'],
    ['模型磁盘', fmt.num(m.disk_mb) + ' MB', `已安装 ${m.installed} · 运行 ${m.running}`],
  ];
  $('#metric-grid').innerHTML = cards.map(([label, value, foot]) => `
    <div class="metric-card"><div class="metric-label">${esc(label)}</div>
    <div class="metric-value">${esc(value)}</div><div class="metric-foot">${esc(foot)}</div></div>`).join('');
  $('#metrics-agents').innerHTML = `<div class="panel-title" style="margin-bottom:8px">最近任务</div>
    <table><thead><tr><th>任务</th><th>状态</th><th>时长</th><th>耗时</th></tr></thead><tbody>
    ${(data.recent || []).map((r) => `<tr><td>${esc(r.title || r.id)}</td><td>${statusPill(r.status)}</td>
      <td>${r.output && r.output.duration ? fmt.dur(r.output.duration) : '—'}</td><td>${r.duration_ms ? fmt.ms(r.duration_ms) : '—'}</td></tr>`).join('')}
    </tbody></table>`;
}

/* ------------------------------------------------------------------ */
function switchView(view) {
  state.view = view;
  document.querySelectorAll('.nav-item').forEach((el) => el.classList.toggle('active', el.dataset.view === view));
  document.querySelectorAll('.view').forEach((el) => el.classList.toggle('hidden', el.id !== `view-${view}`));
  if (view === 'models') renderModels();
  if (view === 'voices') refreshVoices();
  if (view === 'jobs') { loadJobs(); renderJobsTable(); }
  if (view === 'tools') loadAssets();
  if (view === 'metrics') renderMetrics();
}

/* ------------------------------------------------------------------ */
function showModal({ title, desc, fields, confirmText, onConfirm, danger }) {
  const root = $('#modal-root');
  root.innerHTML = `<div class="modal-backdrop"><div class="modal">
    <div class="modal-title">${esc(title)}</div>
    ${desc ? `<div class="modal-desc">${esc(desc)}</div>` : ''}
    <div class="modal-body" id="modal-body"></div>
    <div class="modal-foot">
      <button class="btn btn-ghost" id="modal-cancel" type="button">取消</button>
      <button class="btn ${danger ? 'btn-danger' : 'btn-primary'}" id="modal-ok" type="button">${esc(confirmText || '确认')}</button>
    </div></div></div>`;

  const body = $('#modal-body');
  (fields || []).forEach((f) => {
    const label = document.createElement('label');
    label.className = 'modal-label';
    label.innerHTML = `<span>${esc(f.label)}</span>`;
    let input;
    if (f.type === 'select') {
      input = document.createElement('select');
      input.className = 'modal-input';
      (f.options || []).forEach((o) => {
        const opt = document.createElement('option');
        opt.value = o.value;
        opt.textContent = o.label;
        input.appendChild(opt);
      });
      input.value = f.value ?? (f.options && f.options[0] ? f.options[0].value : '');
    } else if (f.type === 'file') {
      input = document.createElement('input');
      input.type = 'file';
      input.accept = 'audio/wav,audio/*';
      input.className = 'modal-input';
    } else {
      input = document.createElement('input');
      input.type = f.type || 'text';
      input.className = 'modal-input';
      input.value = f.value ?? '';
      input.placeholder = f.placeholder || '';
      if (f.step) input.step = f.step;
      if (f.min != null) input.min = f.min;
      if (f.max != null) input.max = f.max;
    }
    input.name = f.name;
    label.appendChild(input);
    body.appendChild(label);
  });

  const close = () => { root.innerHTML = ''; };
  $('#modal-cancel').addEventListener('click', close);
  $('#modal-ok').addEventListener('click', async () => {
    const values = {};
    body.querySelectorAll('input, select').forEach((el) => {
      if (el.type === 'file') values[el.name] = el.files && el.files[0] ? el.files[0] : null;
      else values[el.name] = el.value;
    });
    close();
    try { await onConfirm(values); } catch (err) { toast(err.message, 'error'); }
  });
}

function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(',')[1]);
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

/* ------------------------------------------------------------------ */
function startPolling() {
  if (state.pollTimer) clearInterval(state.pollTimer);
  state.pollTimer = setInterval(async () => {
    const active = state.jobs.some((j) => j.status === 'running' || j.status === 'queued');
    if (state.job && (state.job.status === 'running' || state.job.status === 'queued')) {
      await openJob(state.job.id, { quiet: true });
    }
    if (active) await loadJobs();
  }, 1200);
}

/* ------------------------------------------------------------------ */
document.querySelectorAll('.nav-item').forEach((el) => el.addEventListener('click', () => switchView(el.dataset.view)));
$('#btn-produce').addEventListener('click', () => produceJob(false));
$('#btn-demo').addEventListener('click', () => produceJob(true));
$('#btn-refresh').addEventListener('click', loadJobs);
$('#btn-models-refresh').addEventListener('click', renderModels);
$('#btn-jobs-refresh').addEventListener('click', async () => { await loadJobs(); renderJobsTable(); });
$('#btn-metrics-refresh').addEventListener('click', renderMetrics);
$('#btn-tool-run').addEventListener('click', runTool);

$('#script').addEventListener('input', () => {
  const text = $('#script').value;
  const chars = text.length;
  const pauses = (text.match(/[，。！？,.!?;；:：]/g) || []).length;
  const seconds = (chars * 0.16 + pauses * 0.12) / 1.0;
  $('#script-meta').textContent = `${chars} 字`;
  $('#estimate').textContent = chars ? `预计成片 ${seconds.toFixed(1)} 秒 · ${Math.ceil(chars / 28)} 句` : '预计时长 —';
});

$('#btn-new-voice').addEventListener('click', () => {
  showModal({
    title: '新建音色',
    desc: '自定义音高、语速与音色倾向。',
    confirmText: '创建',
    fields: [
      { name: 'name', label: '名称', placeholder: '例如：主讲人 · 男声' },
      { name: 'pitch', label: '音高（0.5–2.0）', type: 'number', value: '1.0', step: '0.01', min: '0.5', max: '2.0' },
      { name: 'speed', label: '语速（0.5–2.0）', type: 'number', value: '1.0', step: '0.01', min: '0.5', max: '2.0' },
      { name: 'timbre', label: '音色', type: 'select', options: [
        { value: 'warm', label: '温暖' }, { value: 'bright', label: '清亮' }, { value: 'deep', label: '低沉' },
      ] },
    ],
    onConfirm: async (v) => {
      if (!v.name.trim()) throw new Error('名称不能为空');
      await api('/api/voices', { method: 'POST', body: {
        name: v.name.trim(), pitch: Number(v.pitch), speed: Number(v.speed), timbre: v.timbre, note: '自定义音色',
      } });
      toast('音色已创建', 'success');
      await refreshVoices();
    },
  });
});

$('#btn-clone').addEventListener('click', () => {
  showModal({
    title: '克隆音色',
    desc: '上传 3–10 秒 WAV 参考音频；不上传则使用内置样本演示分析流程。',
    confirmText: '开始克隆',
    fields: [
      { name: 'name', label: '名称', placeholder: '例如：客户 A · 女声' },
      { name: 'audio', label: '参考音频（可选）', type: 'file' },
    ],
    onConfirm: async (v) => {
      if (!v.name.trim()) throw new Error('名称不能为空');
      let audio_b64 = null;
      if (v.audio) audio_b64 = await fileToBase64(v.audio);
      const data = await api('/api/voices/clone', { method: 'POST', body: { name: v.name.trim(), audio_b64 } });
      toast(`已克隆：基频 ${data.analysis ? data.analysis.f0 : '?'}Hz`, 'success');
      await refreshVoices();
    },
  });
});

boot();
