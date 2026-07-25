'use strict';

const HISTORY_KEY = 'ebram_c6b_history';
const RESULT_KEY = '协议草案';
const state = {
  template: null,
  materials: [],
  sessionId: null,
  templatePreflight: null,
  review: null,
  busy: false,
  history: [],
  currentLocalId: null,
  dirty: false,
  acceptedFieldIds: new Set(),
  activeFilter: 'pending',
  previewReady: false,
};

const $ = id => document.getElementById(id);
const els = {
  templateInput: $('templateInput'), materialsInput: $('materialsInput'),
  templateDropzone: $('templateDropzone'), materialsDropzone: $('materialsDropzone'),
  templateState: $('templateState'), materialsState: $('materialsState'),
  selectedFiles: $('selectedFiles'), start: $('startBtn'), idle: $('idleState'),
  progress: $('progressState'), progressFill: $('progressFill'), progressMessage: $('progressMessage'),
  materialStatus: $('materialStatus'), retry: $('retryBtn'), error: $('errorState'),
  errorMessage: $('errorMessage'), status: $('statusPill'), reviewPanel: $('reviewPanel'),
  reviewGroups: $('reviewGroups'), fieldCount: $('fieldCount'), unresolvedCount: $('unresolvedCount'),
  saveReview: $('saveReviewBtn'), finalize: $('finalizeBtn'), downloadPanel: $('downloadPanel'),
  downloadMessage: $('downloadMessage'), downloadDocx: $('downloadDocxBtn'),
  downloadPdf: $('downloadPdfBtn'), reset: $('resetTaskBtn'), newTask: $('newTaskBtn'),
  history: $('historyList'), sidebar: $('sidebar'), sidebarToggle: $('sidebarToggle'),
  sidebarOverlay: $('sidebarOverlay'), mobileTheme: $('mobileThemeBtn'),
  templatePreflight: $('templatePreflight'),
  templatePreflightSummary: $('templatePreflightSummary'),
  templatePreflightDetails: $('templatePreflightDetails'),
  templateActions: $('templateActions'), replaceTemplate: $('replaceTemplateBtn'),
  removeTemplate: $('removeTemplateBtn'), conflictPanel: $('conflictPanel'),
  conflictList: $('conflictList'),
  reviewWorkbench: $('reviewWorkbench'), reviewTabs: $('reviewTabs'),
  previewFrame: $('previewFrame'), previewFallback: $('previewFallback'),
  previewStatus: $('previewStatus'), reviewFilters: $('reviewFilters'),
  dirtyBadge: $('dirtyBadge'), filterEmpty: $('filterEmpty'),
};

function localId() { return `c6b_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`; }
function fileSize(bytes) { return bytes > 1024 * 1024 ? `${(bytes / 1024 / 1024).toFixed(1)} MB` : `${Math.ceil(bytes / 1024)} KB`; }
function friendlyStatus(value) {
  return ({ pending: '等待处理', extracting: '正在提取', summarizing: '正在摘要', complete: '已完成', failed: '失败' })[value] || value;
}
function setStage(name) {
  const order = ['upload', 'extract', 'summary', 'review', 'generate'];
  const active = order.indexOf(name);
  document.querySelectorAll('.c6b-stage-strip > div').forEach((node, index) => {
    node.classList.toggle('active', index === active);
    node.classList.toggle('done', index < active);
  });
  els.progressFill.style.width = `${Math.max(3, ((active + 1) / order.length) * 100)}%`;
}
function setBusy(value) {
  state.busy = value;
  [els.start, els.retry, els.reset, els.newTask].forEach(button => {
    if (button) button.disabled = value;
  });
  document.querySelectorAll('.c6b-retry-one').forEach(button => { button.disabled = value; });
  syncUploadControls();
  syncReviewControls();
  updateStartButton();
}
function syncUploadControls() {
  document.querySelectorAll('.c6b-file-remove').forEach(button => {
    button.disabled = state.busy || Boolean(state.sessionId);
  });
  [els.templateInput, els.materialsInput, els.replaceTemplate, els.removeTemplate].forEach(control => {
    if (control) control.disabled = state.busy || Boolean(state.sessionId);
  });
}
function updateStartButton() { els.start.disabled = state.busy || !state.template || state.materials.length < 1; }

function loadHistory() {
  try { state.history = JSON.parse(localStorage.getItem(HISTORY_KEY) || '[]'); } catch { state.history = []; }
  if (!Array.isArray(state.history)) state.history = [];
  state.history = state.history.filter(item => item && item.id).slice(0, 50);
}
function saveHistory() { localStorage.setItem(HISTORY_KEY, JSON.stringify(state.history.slice(0, 50))); }
function currentHistory() { return state.history.find(item => item.id === state.currentLocalId); }
function ensureHistory() {
  if (currentHistory()) return currentHistory();
  const item = { id: localId(), title: '新建草案', sessionId: null, updatedAt: Date.now(), expired: false };
  state.history.unshift(item); state.currentLocalId = item.id; saveHistory(); renderHistory(); return item;
}
function updateHistory(values) { Object.assign(ensureHistory(), values, { updatedAt: Date.now() }); saveHistory(); renderHistory(); }
function renderHistory() {
  els.history.innerHTML = '';
  if (!state.history.length) {
    const empty = document.createElement('div'); empty.className = 'history-empty'; empty.textContent = '尚无草案记录'; els.history.appendChild(empty); return;
  }
  state.history.forEach(item => {
    const node = document.createElement('div');
    node.className = `history-item${item.id === state.currentLocalId ? ' active' : ''}`;
    const title = document.createElement('button');
    title.type = 'button';
    title.className = 'history-title history-title-button';
    title.textContent = item.title || '新建草案';
    title.title = title.textContent;
    title.addEventListener('click', () => switchHistory(item));
    const menu = createHistoryMenu({
      label: title.textContent,
      isDisabled: () => state.busy,
      onDelete: () => deleteHistory(item.id),
    });
    node.append(title, menu);
    els.history.appendChild(node);
  });
}
async function deleteHistory(id) {
  const item = state.history.find(entry => entry.id === id);
  if (!item || state.busy) return;
  state.history = state.history.filter(entry => entry.id !== id);
  const wasCurrent = state.currentLocalId === id;
  if (wasCurrent) {
    const next = state.history[0] || null;
    state.currentLocalId = next?.id || null;
    clearView(false);
    if (next) await switchHistory(next);
    else ensureHistory();
  }
  saveHistory();
  renderHistory();
  if (item.sessionId) {
    try {
      const response = await fetch(`/case6b/session/${encodeURIComponent(item.sessionId)}`, {
        method: 'DELETE',
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
    } catch {
      toast('草案已从本地删除；服务端缓存将在两小时内自动清理');
    }
  }
}
async function switchHistory(item) {
  if (state.busy) return;
  clearView(false); state.currentLocalId = item.id; state.sessionId = item.sessionId || null; renderHistory();
  syncUploadControls();
  if (state.sessionId) {
    try {
      state.review = await requestJson(`/case6b/session/${encodeURIComponent(state.sessionId)}/review`);
      renderReview();
      await createPreview();
    } catch (error) { item.expired = true; saveHistory(); showError(error.message); }
  }
  closeSidebar();
}

function setTemplate(file) {
  if (!file || state.busy || state.sessionId) return;
  if (!file.name.toLowerCase().endsWith('.docx')) { toast('模板仅支持 DOCX'); return; }
  state.template = file;
  els.templateState.textContent = file.name;
  els.templateActions.hidden = false;
  renderSelectedFiles();
  updateStartButton();
}
function removeTemplate() {
  if (state.busy || state.sessionId) return;
  state.template = null;
  els.templateInput.value = '';
  els.templateState.textContent = '未选择';
  els.templateActions.hidden = true;
  renderSelectedFiles();
  updateStartButton();
}
function fileIdentity(file) {
  return `${file.name.toLowerCase()}|${file.size}|${file.lastModified || 0}`;
}
function addMaterials(files) {
  if (state.busy || state.sessionId) return;
  const accepted = ['pdf', 'png', 'jpg', 'jpeg', 'jfif', 'xlsx', 'docx', 'csv'];
  const values = [...files].filter(file => accepted.includes(file.name.split('.').pop().toLowerCase()));
  const known = new Set(state.materials.map(fileIdentity));
  const additions = values.filter(file => !known.has(fileIdentity(file)));
  if (state.materials.length + additions.length > 20) { toast('最多选择 20 份事实材料'); return; }
  state.materials.push(...additions);
  if (values.length !== additions.length) toast('已忽略重复选择的材料');
  els.materialsInput.value = '';
  els.materialsState.textContent = state.materials.length ? `${state.materials.length} 份` : '未选择';
  renderSelectedFiles();
  updateStartButton();
}
function removeMaterial(index) {
  if (state.busy || state.sessionId) return;
  state.materials.splice(index, 1);
  els.materialsState.textContent = state.materials.length ? `${state.materials.length} 份` : '未选择';
  renderSelectedFiles();
  updateStartButton();
}
function renderSelectedFiles() {
  els.selectedFiles.innerHTML = '';
  const entries = [
    ...(state.template ? [{ file: state.template, kind: 'template', index: -1 }] : []),
    ...state.materials.map((file, index) => ({ file, kind: 'material', index })),
  ];
  entries.forEach(({ file, kind: entryKind, index }) => {
    const row = document.createElement('div'); row.className = 'c6b-selected-file';
    const kind = document.createElement('b'); kind.textContent = file.name.split('.').pop().toUpperCase();
    const name = document.createElement('span'); name.textContent = file.name;
    name.title = file.name;
    const size = document.createElement('small'); size.textContent = fileSize(file.size);
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'c6b-file-remove';
    remove.setAttribute('aria-label', `移除文件：${file.name}`);
    remove.textContent = '移除';
    remove.disabled = state.busy || Boolean(state.sessionId);
    remove.addEventListener('click', () => {
      if (entryKind === 'template') removeTemplate();
      else removeMaterial(index);
    });
    row.append(kind, name, size, remove); els.selectedFiles.appendChild(row);
  });
}
function bindDropzone(zone, input, onFiles) {
  ['dragenter', 'dragover'].forEach(type => zone.addEventListener(type, event => { event.preventDefault(); zone.classList.add('dragover'); }));
  ['dragleave', 'drop'].forEach(type => zone.addEventListener(type, event => { event.preventDefault(); zone.classList.remove('dragover'); }));
  zone.addEventListener('drop', event => onFiles(event.dataTransfer.files));
  input.addEventListener('change', () => onFiles(input.files));
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, options);
  if (!response.ok) {
    let message = `请求失败（HTTP ${response.status}）`;
    let code = '';
    try {
      const payload = await response.json();
      const detail = payload?.detail;
      message = typeof detail === 'string' ? detail : (detail && detail.message) || message;
      code = typeof detail === 'object' ? detail.code || '' : '';
    } catch { /* response was not JSON */ }
    const error = new Error(message);
    error.status = response.status;
    error.code = code;
    throw error;
  }
  return response.status === 204 ? {} : response.json();
}
async function readSse(response, onEvent) {
  if (!response.ok) throw new Error(`分析请求失败（HTTP ${response.status}）`);
  const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = '';
  while (true) {
    const { value, done } = await reader.read(); if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const blocks = buffer.split(/\r?\n\r?\n/); buffer = blocks.pop() || '';
    blocks.forEach(block => {
      const data = block.split(/\r?\n/).find(line => line.startsWith('data:'));
      if (data) onEvent(JSON.parse(data.slice(5).trimStart()));
    });
  }
}

function renderMaterialStatus(materials) {
  els.materialStatus.innerHTML = '';
  materials.forEach(material => {
    const row = document.createElement('div'); row.className = `c6b-material-row ${material.status || 'pending'}`; row.dataset.id = material.material_id;
    const kind = document.createElement('b'); kind.textContent = (material.file_type || material.filename.split('.').pop()).toUpperCase();
    const copy = document.createElement('div'); const name = document.createElement('strong'); name.textContent = material.filename;
    const meta = document.createElement('small'); meta.textContent = material.error || `${material.units || 1} 个处理单元`;
    copy.append(name, meta); const status = document.createElement('span'); status.textContent = friendlyStatus(material.status || 'pending');
    row.append(kind, copy, status);
    if (material.status === 'failed') row.append(makeRetryButton(material.material_id));
    els.materialStatus.appendChild(row);
  });
}
function makeRetryButton(materialId) {
  const button = document.createElement('button'); button.type = 'button'; button.className = 'c6b-retry-one';
  button.textContent = '仅重试此项'; button.disabled = state.busy;
  button.addEventListener('click', () => retryFailed(materialId));
  return button;
}
function updateMaterialEvent(event) {
  const row = els.materialStatus.querySelector(`[data-id="${CSS.escape(event.material_id || '')}"]`);
  if (!row) return;
  row.className = `c6b-material-row ${event.status || (event.type === 'error' ? 'failed' : 'summarizing')}`;
  row.querySelector('span').textContent = friendlyStatus(event.status || (event.type === 'error' ? 'failed' : 'summarizing'));
  if (event.message) row.querySelector('small').textContent = event.message;
  const retry = row.querySelector('.c6b-retry-one');
  if (event.type === 'error' && !retry) row.append(makeRetryButton(event.material_id));
  if (event.status === 'complete' && retry) retry.remove();
}
function beginProgress() {
  els.idle.hidden = true; els.progress.hidden = false; els.error.hidden = true; els.reviewPanel.hidden = true; els.downloadPanel.hidden = true;
  els.status.textContent = '处理中'; setStage('extract');
}
function handleEvent(event) {
  if (event.message) els.progressMessage.textContent = event.message;
  if (event.material_id) updateMaterialEvent(event);
  if (event.stage === 'material_summary') setStage('summary');
  if (event.stage === 'template_parse') setStage('review');
  if (event.type === 'error' && event.material_id) els.retry.hidden = false;
  if (event.type === 'fatal_error') throw new Error(event.message || '处理失败');
}
async function runAnalysis(url, options) {
  const response = await fetch(url, options); let fatal = '';
  await readSse(response, event => {
    try { handleEvent(event); } catch (error) { fatal = error.message; }
  });
  if (fatal) throw new Error(fatal);
  try {
    state.review = await requestJson(`/case6b/session/${encodeURIComponent(state.sessionId)}/review`);
    renderReview();
    await createPreview({ manageBusy: false });
    updateHistory({ title: state.template?.name || currentHistory()?.title || '服务协议草案', sessionId: state.sessionId });
  } catch (error) {
    els.retry.hidden = false; throw error;
  }
}
function renderTemplatePreflight(preflight) {
  state.templatePreflight = preflight;
  els.templatePreflight.hidden = false;
  els.templatePreflightSummary.textContent = `已自动识别 ${preflight.field_count} 个可稳定回填的占位字段。Agent L 将结合上下文解释字段含义，无需选择模板档案。`;
  els.templatePreflightDetails.innerHTML = '';
  const details = [
    ['占位字段', `${preflight.field_count} 个`],
    ['重复区块', `${(preflight.repeat_blocks || []).length} 个`],
    ['签署字段', `${(preflight.signature_sections || []).length} 个`],
    ['识别方式', '下划线 / 方括号 · 自动定位'],
  ];
  details.forEach(([label, value]) => {
    const item = document.createElement('div'); const strong = document.createElement('strong'); const span = document.createElement('span');
    strong.textContent = label; span.textContent = value; item.append(strong, span); els.templatePreflightDetails.appendChild(item);
  });
}
async function continueAnalysis() {
  const analyze = new FormData(); analyze.append('session_id', state.sessionId);
  beginProgress();
  await runAnalysis('/pdf/session/analyze', { method: 'POST', body: analyze });
}
async function startWorkflow() {
  if (state.busy || !state.template || !state.materials.length) return;
  setBusy(true); beginProgress(); els.progressMessage.textContent = '正在校验并上传文件...';
  try {
    const body = new FormData(); body.append('template_file', state.template, state.template.name);
    state.materials.forEach(file => body.append('material_files', file, file.name));
    const uploaded = await requestJson('/case6b/session/upload', { method: 'POST', body });
    state.sessionId = uploaded.session_id; renderMaterialStatus(uploaded.materials);
    updateHistory({ title: uploaded.template.filename, sessionId: state.sessionId });
    renderTemplatePreflight(uploaded.template);
    await continueAnalysis();
  } catch (error) { showError(error.message); } finally { setBusy(false); }
}
async function retryFailed(materialId = null) {
  if (!state.sessionId || state.busy) return;
  setBusy(true); beginProgress(); els.retry.hidden = true;
  const query = materialId ? `?material_id=${encodeURIComponent(materialId)}` : '';
  try { await runAnalysis(`/case6b/session/${encodeURIComponent(state.sessionId)}/retry${query}`, { method: 'POST' }); }
  catch (error) { showError(error.message); } finally { setBusy(false); }
}
function showError(message) {
  els.error.hidden = false; els.errorMessage.textContent = message; els.status.textContent = '未完成';
}

function syncReviewControls() {
  if (!els.reviewPanel) return;
  els.reviewPanel.querySelectorAll('textarea, input, select').forEach(control => {
    const serviceRow = control.closest('[data-group-index]');
    const disabledByAction = serviceRow
      && ['name', 'price'].includes(control.dataset.role)
      && ['blank', 'remove'].includes(serviceRow.dataset.action);
    control.disabled = state.busy || control.dataset.locked === 'true' || disabledByAction;
  });
  els.reviewPanel.querySelectorAll('.c6b-accept-btn').forEach(button => {
    button.disabled = state.busy || state.acceptedFieldIds.has(button.dataset.fieldId);
  });
  els.saveReview.disabled = state.busy || !state.dirty;
  els.finalize.disabled = state.busy || state.dirty
    || Number(state.review?.unresolved_conflict_count || 0) > 0;
}
function clearPreview(message = '保存字段后即可刷新文档预览。') {
  state.previewReady = false;
  els.previewFrame.hidden = true;
  els.previewFrame.removeAttribute('src');
  els.previewFallback.hidden = false;
  els.previewFallback.querySelector('strong').textContent = '预览需要刷新';
  els.previewFallback.querySelector('p').textContent = message;
  els.previewStatus.textContent = '待刷新';
  els.previewStatus.className = 'c6b-preview-state stale';
}
function setReviewDirty(value = true) {
  state.dirty = value;
  if (value) {
    els.downloadPanel.hidden = true;
    clearPreview();
  }
  els.dirtyBadge.textContent = value ? '未保存' : '已保存';
  els.dirtyBadge.classList.toggle('dirty', value);
  syncReviewControls();
}
function setReviewView(view) {
  els.reviewWorkbench.dataset.mobileView = view;
  els.reviewTabs.querySelectorAll('[data-review-view]').forEach(button => {
    button.setAttribute('aria-selected', String(button.dataset.reviewView === view));
  });
}
async function createPreview({ manageBusy = true } = {}) {
  if (!state.review || !state.sessionId || state.dirty) return false;
  if (manageBusy) setBusy(true);
  els.previewFrame.hidden = true;
  els.previewFallback.hidden = false;
  els.previewFallback.querySelector('strong').textContent = '正在生成原模板预览';
  els.previewFallback.querySelector('p').textContent = '正在预填字段并转换为 PDF，请稍候…';
  els.previewStatus.textContent = '生成中';
  els.previewStatus.className = 'c6b-preview-state loading';
  try {
    const result = await requestJson(
      `/case6b/session/${encodeURIComponent(state.sessionId)}/preview`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ version: state.review.version }),
      },
    );
    if (!result.pdf_ready) {
      state.previewReady = false;
      els.previewStatus.textContent = 'PDF 不可用';
      els.previewStatus.className = 'c6b-preview-state error';
      els.previewFallback.querySelector('strong').textContent = '暂时无法显示 PDF 预览';
      els.previewFallback.querySelector('p').textContent =
        `${result.pdf_error || '文档转换失败'}；字段仍可审阅，DOCX 正式文件仍可生成。`;
      return false;
    }
    state.previewReady = true;
    state.review.preview_ready = true;
    state.review.preview_version = result.version;
    els.previewFallback.hidden = true;
    els.previewFrame.hidden = false;
    const separator = result.preview_url.includes('?') ? '&' : '?';
    els.previewFrame.src = `${result.preview_url}${separator}_=${Date.now()}`;
    els.previewStatus.textContent = `版本 ${result.version}`;
    els.previewStatus.className = 'c6b-preview-state ready';
    return true;
  } catch (error) {
    if (error.code === 'STALE_REVIEW') {
      state.review = await requestJson(
        `/case6b/session/${encodeURIComponent(state.sessionId)}/review`,
      );
      renderReview();
      toast('审阅版本已更新，请重新检查字段');
    } else {
      state.previewReady = false;
      els.previewStatus.textContent = '预览失败';
      els.previewStatus.className = 'c6b-preview-state error';
      els.previewFallback.querySelector('strong').textContent = '无法生成 PDF 预览';
      els.previewFallback.querySelector('p').textContent =
        `${error.message}；字段仍可审阅，DOCX 正式文件仍可生成。`;
    }
    return false;
  } finally {
    if (manageBusy) setBusy(false);
  }
}
function fieldGroup(field) {
  const key = field.semantic_key || '';
  if (field.group_key === 'services') return '服务与价格';
  if (field.field_kind && field.field_kind.startsWith('signature')) return '签署区';
  if (/provider|client|party|address/.test(key)) return '协议主体';
  if (/amount|price|invoice|payment/.test(key)) return '费用与付款';
  if (/date|term|notice|return/.test(key)) return '期限与终止';
  return '其他条款';
}
function statusLabel(status) {
  return ({ FILLED: '证据已填', USER_CONFIRMED: '人工确认', NEEDS_CONFIRMATION: '待确认', REMOVE: '不使用', KEEP_BLANK: '保留空白', LEAVE_BLANK: '签署时填写' })[status] || status;
}
function makeEvidence(field) {
  const evidence = Array.isArray(field.evidence) ? field.evidence : [];
  const details = document.createElement('details');
  details.className = 'c6b-field-evidence';
  const summary = document.createElement('summary');
  summary.textContent = evidence.length ? `查看材料证据（${evidence.length}）` : '暂无直接证据';
  details.appendChild(summary);
  if (evidence.length) {
    const list = document.createElement('ul');
    evidence.forEach(item => {
      const entry = document.createElement('li');
      const source = document.createElement('strong');
      source.textContent = item.source || '来源';
      const fact = document.createElement('span');
      const locator = item.locator || item.location || item.source_location || '';
      fact.textContent = `${item.fact || ''}${locator ? ` · ${locator}` : ''}`;
      entry.append(source, fact);
      list.appendChild(entry);
    });
    details.appendChild(list);
  }
  return details;
}
function markFieldDirty() { setReviewDirty(true); }
function makeScalarField(field) {
  const row = document.createElement('div');
  row.className = `c6b-field-row${field.status === 'NEEDS_CONFIRMATION' ? ' needs-attention' : ''}`;
  row.dataset.fieldId = field.field_id;
  row.dataset.status = field.status;
  row.dataset.kind = field.field_kind || 'scalar';
  const label = document.createElement('div'); label.className = 'c6b-field-label';
  const labelTop = document.createElement('div'); labelTop.className = 'c6b-field-label-top';
  const strong = document.createElement('strong'); strong.textContent = field.label || field.field_id;
  const status = document.createElement('span');
  status.className = `c6b-field-status ${field.status === 'NEEDS_CONFIRMATION' ? 'pending' : ''} ${!field.editable ? 'locked' : ''}`;
  status.textContent = statusLabel(field.status);
  status.dataset.sourceType = field.source_type || 'evidence';
  labelTop.append(strong, status);
  const id = document.createElement('small'); id.textContent = field.field_id;
  label.append(labelTop, id);
  const valueWrap = document.createElement('div'); valueWrap.className = 'c6b-field-value';
  const input = document.createElement('textarea');
  input.className = 'c6b-field-input';
  input.rows = 1;
  input.value = field.value || '';
  input.dataset.kind = 'scalar';
  input.dataset.locked = String(!field.editable);
  input.disabled = !field.editable;
  input.addEventListener('input', markFieldDirty);
  valueWrap.append(input);
  if (field.can_confirm) {
    const accept = document.createElement('button');
    accept.type = 'button';
    accept.className = 'c6b-accept-btn';
    accept.dataset.fieldId = field.field_id;
    accept.textContent = '接受当前建议';
    accept.addEventListener('click', () => {
      state.acceptedFieldIds.add(field.field_id);
      accept.textContent = '已接受，待保存';
      row.classList.add('accepted-pending');
      setReviewDirty(true);
      syncReviewControls();
    });
    valueWrap.appendChild(accept);
  }
  valueWrap.appendChild(makeEvidence(field));
  row.append(label, valueWrap);
  return row;
}
function makeServiceRow(groupIndex, fields) {
  const nameField = fields.find(field => field.field_kind === 'repeatable_service');
  const priceField = fields.find(field => field.field_kind === 'repeatable_price');
  const pending = fields.some(field => field.status === 'NEEDS_CONFIRMATION');
  const row = document.createElement('div');
  row.className = `c6b-field-row${pending ? ' needs-attention' : ''}`;
  row.dataset.groupIndex = groupIndex;
  row.dataset.status = pending ? 'NEEDS_CONFIRMATION' : (fields[0]?.status || 'FILLED');
  row.dataset.kind = 'service';
  row.dataset.action = fields.every(field => field.status === 'REMOVE')
    ? 'remove'
    : (nameField?.service_action || (fields.every(field => field.status === 'KEEP_BLANK') ? 'blank' : 'included'));
  const label = document.createElement('div'); label.className = 'c6b-field-label';
  const labelTop = document.createElement('div'); labelTop.className = 'c6b-field-label-top';
  const title = document.createElement('strong'); title.textContent = `服务 ${groupIndex}`;
  const status = document.createElement('span'); status.className = `c6b-field-status${pending ? ' pending' : ''}`; status.textContent = pending ? '待确认' : '服务行';
  labelTop.append(title, status);
  const help = document.createElement('small'); help.textContent = '名称与价格成对处理';
  label.append(labelTop, help);
  const controls = document.createElement('div'); controls.className = 'c6b-service-controls';
  const name = document.createElement('input'); name.className = 'c6b-field-input'; name.value = nameField?.value || ''; name.placeholder = '服务名称'; name.dataset.role = 'name';
  const price = document.createElement('input'); price.className = 'c6b-field-input'; price.value = priceField?.value || ''; price.placeholder = '价格及计费单位'; price.dataset.role = 'price';
  const action = document.createElement('select'); action.dataset.role = 'action';
  [['included','正式服务'],['optional','可选服务'],['blank','保留空白'],['remove','移除']].forEach(([value, labelText]) => {
    const option = document.createElement('option'); option.value = value; option.textContent = labelText; option.selected = value === row.dataset.action; action.appendChild(option);
  });
  const sync = () => {
    row.dataset.action = action.value;
    name.disabled = price.disabled = state.busy || ['blank','remove'].includes(action.value);
    markFieldDirty();
  };
  action.addEventListener('change', sync);
  name.addEventListener('input', markFieldDirty);
  price.addEventListener('input', markFieldDirty);
  name.disabled = price.disabled = ['blank','remove'].includes(action.value);
  controls.append(name, price, action, makeEvidence(nameField || {}));
  row.append(label, controls);
  return row;
}
function renderReview() {
  if (!state.review) return;
  state.dirty = false;
  state.acceptedFieldIds = new Set();
  els.progress.hidden = false;
  els.error.hidden = true;
  els.reviewPanel.hidden = false;
  els.status.textContent = '等待审阅';
  setStage('review');
  els.fieldCount.textContent = `${state.review.fields.length} 个字段`;
  els.unresolvedCount.textContent = `${state.review.unresolved_count} 个待确认`;
  els.dirtyBadge.textContent = '已保存';
  els.dirtyBadge.classList.remove('dirty');
  renderConflicts(state.review.conflicts || []);
  const groups = new Map();
  state.review.fields.forEach(field => {
    const group = fieldGroup(field);
    if (!groups.has(group)) groups.set(group, []);
    groups.get(group).push(field);
  });
  els.reviewGroups.innerHTML = '';
  groups.forEach((fields, name) => {
    const section = document.createElement('section'); section.className = 'c6b-field-group';
    const heading = document.createElement('h3'); heading.textContent = name; section.appendChild(heading);
    if (name === '服务与价格') {
      const rows = new Map();
      fields.forEach(field => {
        if (!rows.has(field.group_index)) rows.set(field.group_index, []);
        rows.get(field.group_index).push(field);
      });
      rows.forEach((values, index) => section.appendChild(makeServiceRow(index, values)));
    } else {
      fields.forEach(field => section.appendChild(makeScalarField(field)));
    }
    els.reviewGroups.appendChild(section);
  });
  applyReviewFilter('pending');
  syncReviewControls();
}
function renderConflicts(conflicts) {
  els.conflictList.innerHTML = '';
  conflicts.forEach(conflict => {
    const row = document.createElement('label'); row.className = 'c6b-conflict-row'; row.dataset.conflictId = conflict.conflict_id;
    const title = document.createElement('strong'); title.textContent = conflict.semantic_key || '资料冲突';
    const select = document.createElement('select'); select.dataset.role = 'conflict';
    const placeholder = document.createElement('option'); placeholder.value = ''; placeholder.textContent = '请选择经核对的值'; select.appendChild(placeholder);
    (conflict.candidates || []).forEach(candidate => {
      const option = document.createElement('option'); option.value = candidate.value; option.textContent = candidate.value; option.selected = conflict.resolved_value === candidate.value; select.appendChild(option);
    });
    select.addEventListener('change', markFieldDirty);
    row.append(title, select); els.conflictList.appendChild(row);
  });
}
function rowMatchesFilter(row, filter) {
  if (filter === 'all') return true;
  if (filter === 'pending') return row.dataset.status === 'NEEDS_CONFIRMATION';
  if (filter === 'filled') return ['FILLED', 'USER_CONFIRMED'].includes(row.dataset.status);
  if (filter === 'signature') return String(row.dataset.kind).startsWith('signature');
  return false;
}
function applyReviewFilter(filter) {
  state.activeFilter = filter;
  els.reviewFilters.querySelectorAll('[data-filter]').forEach(button => {
    button.setAttribute('aria-pressed', String(button.dataset.filter === filter));
  });
  let visibleRows = 0;
  els.reviewGroups.querySelectorAll('.c6b-field-group').forEach(group => {
    let groupRows = 0;
    group.querySelectorAll('.c6b-field-row').forEach(row => {
      const visible = filter !== 'conflicts' && rowMatchesFilter(row, filter);
      row.hidden = !visible;
      if (visible) groupRows += 1;
    });
    group.hidden = groupRows === 0;
    visibleRows += groupRows;
  });
  const showConflicts = (state.review?.conflicts || []).length > 0
    && ['pending', 'conflicts', 'all'].includes(filter);
  els.conflictPanel.hidden = !showConflicts;
  els.filterEmpty.hidden = visibleRows > 0 || showConflicts;
}
function collectReview() {
  const field_updates = [...els.reviewGroups.querySelectorAll('[data-field-id]')]
    .filter(row => row.querySelector('textarea').dataset.locked !== 'true')
    .map(row => ({
      field_id: row.dataset.fieldId,
      value: row.querySelector('textarea').value.trim(),
    }));
  const service_rows = [...els.reviewGroups.querySelectorAll('[data-group-index]')].map(row => ({
    group_index: Number(row.dataset.groupIndex),
    action: row.dataset.action,
    name: row.querySelector('[data-role="name"]').value.trim(),
    price: row.querySelector('[data-role="price"]').value.trim(),
  }));
  const conflict_resolutions = [...els.conflictList.querySelectorAll('[data-conflict-id]')]
    .filter(row => row.querySelector('select').value)
    .map(row => ({
      conflict_id: row.dataset.conflictId,
      value: row.querySelector('select').value,
    }));
  return {
    version: state.review.version,
    field_updates,
    accepted_field_ids: [...state.acceptedFieldIds],
    service_rows,
    conflict_resolutions,
  };
}
async function saveReview() {
  if (!state.review || state.busy) return false;
  if (!state.dirty) return createPreview();
  const payload = collectReview();
  setBusy(true);
  try {
    state.review = await requestJson(
      `/case6b/session/${encodeURIComponent(state.sessionId)}/review`,
      {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      },
    );
    renderReview();
    await createPreview({ manageBusy: false });
    toast('字段已保存，文档预览已刷新');
    return true;
  } catch (error) {
    if (error.code === 'STALE_REVIEW') {
      state.review = await requestJson(
        `/case6b/session/${encodeURIComponent(state.sessionId)}/review`,
      );
      renderReview();
      toast('审阅版本已更新，请重新检查字段');
    } else {
      showError(error.message);
    }
    return false;
  } finally {
    setBusy(false);
  }
}
async function finalizeDraft() {
  if (!state.review || state.busy) return;
  if (state.dirty) {
    toast('请先保存字段并刷新预览');
    return;
  }
  if (state.review.unresolved_conflict_count > 0) {
    showError(`仍有 ${state.review.unresolved_conflict_count} 项材料冲突待解决，无法生成正式文档。`);
    return;
  }
  const allow = state.review.unresolved_count === 0 || window.confirm(
    `仍有 ${state.review.unresolved_count} 个必填字段待确认。继续生成时会以黄色标记写入正式文档，是否继续？`,
  );
  if (!allow) return;
  setBusy(true);
  setStage('generate');
  els.status.textContent = '正在生成';
  els.progress.hidden = false;
  els.progressMessage.textContent = '正在保留原模板版式并生成正式 DOCX / PDF...';
  try {
    const result = await requestJson(
      `/case6b/session/${encodeURIComponent(state.sessionId)}/finalize`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          version: state.review.version,
          allow_unresolved: true,
        }),
      },
    );
    els.downloadPanel.hidden = false;
    els.downloadDocx.disabled = !result.docx_ready;
    els.downloadPdf.disabled = !result.pdf_ready;
    els.downloadMessage.textContent = result.pdf_error
      ? `${result.pdf_error}；DOCX 仍可下载。`
      : 'DOCX 与 PDF 均已准备完成，请在正式使用前人工复核。';
    els.status.textContent = '已生成';
    els.progress.hidden = true;
    updateHistory({ title: currentHistory()?.title || '服务协议草案', sessionId: state.sessionId });
  } catch (error) {
    showError(error.message);
  } finally {
    setBusy(false);
  }
}
async function download(format) {
  if (!state.sessionId) return;
  const body = new FormData(); body.append('session_id', state.sessionId); body.append('party', RESULT_KEY); body.append('output_format', format);
  const response = await fetch('/pdf/session/report', { method: 'POST', body });
  if (!response.ok) { let message = `下载失败（HTTP ${response.status}）`; try { message = (await response.json()).detail || message; } catch {} toast(typeof message === 'string' ? message : message.message); return; }
  const blob = await response.blob(); const disposition = response.headers.get('Content-Disposition') || '';
  const match = disposition.match(/filename\*=UTF-8''([^;]+)/i); const filename = match ? decodeURIComponent(match[1]) : `service-agreement-draft.${format}`;
  const url = URL.createObjectURL(blob); const anchor = document.createElement('a'); anchor.href = url; anchor.download = filename; anchor.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}
async function resetCurrent(confirmFirst = true) {
  if (state.busy) return;
  if (confirmFirst && (state.sessionId || state.template || state.materials.length) && !window.confirm('确定清空当前草案任务吗？')) return;
  const backendSessionId = state.sessionId;
  clearView(true);
  if (backendSessionId) {
    try {
      const response = await fetch(
        `/case6b/session/${encodeURIComponent(backendSessionId)}`,
        { method: 'DELETE' },
      );
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
    } catch {
      toast('当前草案已重置；服务端缓存将在两小时内自动清理');
    }
  }
}
function clearView(updateCurrent) {
  state.template = null; state.materials = []; state.sessionId = null; state.review = null; state.templatePreflight = null;
  state.dirty = false; state.acceptedFieldIds = new Set(); state.previewReady = false; state.activeFilter = 'pending';
  els.templateInput.value = ''; els.materialsInput.value = ''; els.templateState.textContent = '未选择'; els.materialsState.textContent = '未选择';
  els.templateActions.hidden = true;
  els.selectedFiles.innerHTML = ''; els.materialStatus.innerHTML = ''; els.idle.hidden = false; els.progress.hidden = true; els.error.hidden = true;
  els.reviewPanel.hidden = true; els.downloadPanel.hidden = true; els.retry.hidden = true; els.status.textContent = '等待文件'; setStage('upload'); updateStartButton();
  els.templatePreflight.hidden = true; els.conflictPanel.hidden = true;
  els.previewFrame.hidden = true; els.previewFrame.removeAttribute('src');
  els.previewFallback.hidden = false; els.previewStatus.textContent = '等待生成';
  els.previewStatus.className = 'c6b-preview-state';
  setReviewView('preview');
  if (updateCurrent && currentHistory()) updateHistory({ title: '新建草案', sessionId: null, expired: false });
  syncUploadControls();
}
function newTask() { if (state.busy) return; state.currentLocalId = null; ensureHistory(); clearView(false); closeSidebar(); }
function closeSidebar() { els.sidebar.classList.remove('open'); els.sidebarOverlay.classList.remove('open'); }

bindDropzone(els.templateDropzone, els.templateInput, files => setTemplate(files[0]));
bindDropzone(els.materialsDropzone, els.materialsInput, files => addMaterials(files));
els.removeTemplate.addEventListener('click', removeTemplate);
els.replaceTemplate.addEventListener('click', () => els.templateInput.click());
els.start.addEventListener('click', startWorkflow); els.retry.addEventListener('click', () => retryFailed());
els.saveReview.addEventListener('click', saveReview); els.finalize.addEventListener('click', finalizeDraft);
els.reviewFilters.addEventListener('click', event => {
  const button = event.target.closest('[data-filter]');
  if (button) applyReviewFilter(button.dataset.filter);
});
els.reviewTabs.addEventListener('click', event => {
  const button = event.target.closest('[data-review-view]');
  if (button) setReviewView(button.dataset.reviewView);
});
els.downloadDocx.addEventListener('click', () => download('docx')); els.downloadPdf.addEventListener('click', () => download('pdf'));
els.reset.addEventListener('click', () => resetCurrent(true)); els.newTask.addEventListener('click', newTask);
els.sidebarToggle.addEventListener('click', () => { els.sidebar.classList.add('open'); els.sidebarOverlay.classList.add('open'); });
els.sidebarOverlay.addEventListener('click', closeSidebar); els.mobileTheme.addEventListener('click', toggleTheme);

loadHistory(); ensureHistory(); renderHistory(); clearView(false);
