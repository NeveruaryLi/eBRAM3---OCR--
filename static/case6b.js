'use strict';

const HISTORY_KEY = 'ebram_c6b_history';
const RESULT_KEY = '协议草案';
const state = {
  template: null,
  materials: [],
  sessionId: null,
  reviewContextToken: 0,
  templatePreflight: null,
  review: null,
  busy: false,
  history: [],
  currentLocalId: null,
  dirty: false,
  acceptedFieldIds: new Set(),
  dirtyFieldIds: new Set(),
  draftValues: new Map(),
  serviceActions: new Map(),
  editGeneration: 0,
  saveTimer: null,
  saveInFlight: false,
  saveQueued: false,
  savePaused: false,
  analysisRunId: null,
  analysisController: null,
  analysisRunToken: null,
  analysisCompletion: null,
  processing: false,
  cancelRequested: false,
  cancelRunToken: null,
  documentView: null,
  documentMode: 'draft',
  activeFieldId: null,
  pendingIndex: 0,
  fallbackMode: false,
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
  finalize: $('finalizeBtn'), downloadPanel: $('downloadPanel'),
  downloadMessage: $('downloadMessage'), downloadDocx: $('downloadDocxBtn'),
  downloadPdf: $('downloadPdfBtn'), reset: $('resetTaskBtn'), newTask: $('newTaskBtn'),
  history: $('historyList'), sidebar: $('sidebar'), sidebarToggle: $('sidebarToggle'),
  sidebarOverlay: $('sidebarOverlay'), mobileTheme: $('mobileThemeBtn'),
  templatePreflight: $('templatePreflight'),
  templatePreflightSummary: $('templatePreflightSummary'),
  templatePreflightDetails: $('templatePreflightDetails'),
  templateActions: $('templateActions'), replaceTemplate: $('replaceTemplateBtn'),
  removeTemplate: $('removeTemplateBtn'), conflictPanel: $('conflictPanel'),
  conflictList: $('conflictList'), cancelAnalysis: $('cancelAnalysisBtn'),
  documentCanvas: $('documentCanvas'), documentLoading: $('documentLoading'),
  documentMode: $('documentMode'), differenceToggle: $('differenceToggle'),
  previousPending: $('previousPendingBtn'), nextPending: $('nextPendingBtn'),
  pendingPosition: $('pendingPosition'), evidenceDrawer: $('evidenceDrawer'),
  evidenceTitle: $('evidenceTitle'), evidenceBody: $('evidenceBody'),
  evidenceClose: $('evidenceCloseBtn'), editorFallback: $('editorFallback'),
  fallbackReason: $('fallbackReason'), retrySave: $('retrySaveBtn'),
  dirtyBadge: $('dirtyBadge'),
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
  if (els.cancelAnalysis) {
    els.cancelAnalysis.disabled = !state.processing || !state.analysisRunId || state.cancelRequested;
  }
  document.querySelectorAll('.c6b-retry-one').forEach(button => { button.disabled = value; });
  syncUploadControls();
  syncReviewControls();
  updateStartButton();
}
function setProcessing(value) {
  state.processing = value;
  if (els.cancelAnalysis) {
    els.cancelAnalysis.hidden = !value;
    els.cancelAnalysis.disabled = !value || !state.analysisRunId || state.cancelRequested;
  }
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
function captureReviewContext() {
  return { sessionId: state.sessionId, token: state.reviewContextToken };
}
function isCurrentReviewContext(context) {
  return Boolean(context.sessionId)
    && state.sessionId === context.sessionId
    && state.reviewContextToken === context.token;
}
function invalidateReviewContext() { state.reviewContextToken += 1; }

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
      isDisabled: () => state.busy || state.saveInFlight,
      onDelete: () => deleteHistory(item.id),
    });
    node.append(title, menu);
    els.history.appendChild(node);
  });
}
async function deleteHistory(id) {
  const item = state.history.find(entry => entry.id === id);
  if (!item || state.busy || state.saveInFlight) return;
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
async function saveBeforeNavigation() {
  if (!state.review || (!state.dirty && !state.saveInFlight)) return true;
  if (state.saveInFlight) {
    toast('草案正在自动保存，请稍候再切换。');
    return false;
  }
  const saved = await flushAutoSave();
  if (!saved || state.dirty) {
    toast('当前草案仍有未保存内容，请重试保存后再切换。');
    return false;
  }
  return true;
}
async function switchHistory(item) {
  if (state.busy) return;
  if (!(await saveBeforeNavigation())) return;
  clearView(false); state.currentLocalId = item.id; state.sessionId = item.sessionId || null; renderHistory();
  syncUploadControls();
  if (state.sessionId) {
    const reviewContext = captureReviewContext();
    try {
      const review = await requestJson(`/case6b/session/${encodeURIComponent(reviewContext.sessionId)}/review`);
      if (!isCurrentReviewContext(reviewContext)) return;
      state.review = review;
      await renderReview(reviewContext);
    } catch (error) {
      if (!isCurrentReviewContext(reviewContext)) return;
      item.expired = true; saveHistory(); showError(error.message);
    }
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
function analysisCancellationRequested(runToken) {
  return state.analysisRunToken === runToken
    && state.cancelRequested
    && state.cancelRunToken === runToken;
}
function handleEvent(event, runToken) {
  if (state.analysisRunToken !== runToken) return;
  if (event.run_id) {
    state.analysisRunId = event.run_id;
    if (els.cancelAnalysis) els.cancelAnalysis.disabled = analysisCancellationRequested(runToken);
  }
  if (event.message) els.progressMessage.textContent = event.message;
  if (event.material_id) updateMaterialEvent(event);
  if (event.stage === 'material_summary') setStage('summary');
  if (event.stage === 'template_parse') setStage('review');
  if (event.type === 'error' && event.material_id) els.retry.hidden = false;
  if (event.type === 'fatal_error') throw new Error(event.message || '处理失败');
}
async function runAnalysis(url, options) {
  const runToken = Symbol('case6b-analysis');
  const controller = new AbortController();
  let resolveCompletion;
  const completion = new Promise(resolve => { resolveCompletion = resolve; });
  const reviewContext = captureReviewContext();
  state.analysisController = controller;
  state.analysisRunToken = runToken;
  state.analysisCompletion = completion;
  state.cancelRequested = false;
  state.cancelRunToken = null;
  state.analysisRunId = null;
  setProcessing(true);
  const requestOptions = { ...options, signal: controller.signal };
  let fatal = '';
  try {
    const response = await fetch(url, requestOptions);
    await readSse(response, event => {
      try { handleEvent(event, runToken); } catch (error) { fatal = error.message; }
    });
    if (fatal) throw new Error(fatal);
    if (analysisCancellationRequested(runToken) || !isCurrentReviewContext(reviewContext)) return;
    const review = await requestJson(`/case6b/session/${encodeURIComponent(reviewContext.sessionId)}/review`);
    if (analysisCancellationRequested(runToken) || !isCurrentReviewContext(reviewContext)) return;
    state.review = review;
    await renderReview(reviewContext);
    if (analysisCancellationRequested(runToken) || !isCurrentReviewContext(reviewContext)) return;
    updateHistory({ title: state.template?.name || currentHistory()?.title || '服务协议草案', sessionId: reviewContext.sessionId });
  } catch (error) {
    if (
      error.name === 'AbortError'
      && (analysisCancellationRequested(runToken) || state.analysisRunToken !== runToken)
    ) return;
    if (state.analysisRunToken !== runToken) return;
    els.retry.hidden = false;
    throw error;
  } finally {
    if (state.analysisRunToken === runToken) {
      state.analysisController = null;
      state.analysisRunId = null;
      state.analysisCompletion = null;
      state.cancelRequested = false;
      state.cancelRunToken = null;
      setProcessing(false);
    }
    resolveCompletion();
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
async function cancelAnalysis() {
  if (
    !state.sessionId
    || !state.analysisRunId
    || !state.analysisRunToken
    || !state.analysisController
    || !state.analysisCompletion
    || state.cancelRequested
  ) return;
  const confirmed = window.confirm(
    '中断后将废弃本轮 OCR、摘要和字段结果，但会保留本页已选择的文件供你删换。确定继续吗？',
  );
  if (!confirmed) return;
  const controller = state.analysisController;
  const runToken = state.analysisRunToken;
  const completion = state.analysisCompletion;
  const reviewContext = captureReviewContext();
  state.cancelRequested = true;
  state.cancelRunToken = runToken;
  if (els.cancelAnalysis) {
    els.cancelAnalysis.disabled = true;
    els.cancelAnalysis.textContent = '正在中断…';
  }
  const abandonedSessionId = reviewContext.sessionId;
  const runId = state.analysisRunId;
  try {
    await requestJson(
      `/case6b/session/${encodeURIComponent(abandonedSessionId)}/cancel`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ run_id: runId }),
      },
    );
  } catch (error) {
    if (!isCurrentReviewContext(reviewContext) || state.analysisRunToken !== runToken) return;
    if (error.code === 'STALE_ANALYSIS_RUN') {
      toast('任务状态刚刚发生变化；本地仍会退出本轮处理。');
    } else {
      toast('已停止等待；服务端任务可能继续到缓存过期，但不会影响新任务。');
    }
  } finally {
    controller.abort();
    await completion;
    if (!isCurrentReviewContext(reviewContext) || state.analysisRunToken !== runToken) return;
    invalidateReviewContext();
    state.sessionId = null;
    state.analysisRunToken = null;
    state.review = null;
    state.documentView = null;
    state.busy = false;
    if (els.cancelAnalysis) {
      els.cancelAnalysis.hidden = true;
      els.cancelAnalysis.textContent = '中断并修改文件';
    }
    els.progress.hidden = true;
    els.error.hidden = true;
    els.reviewPanel.hidden = true;
    els.downloadPanel.hidden = true;
    els.retry.hidden = true;
    els.idle.hidden = false;
    els.status.textContent = '可修改文件';
    els.templatePreflight.hidden = true;
    setStage('upload');
    renderSelectedFiles();
    syncUploadControls();
    updateStartButton();
    updateHistory({ sessionId: null, expired: false });
    toast('本轮处理已中断。你可以删除、更换或追加文件后重新开始。');
  }
}
function showError(message) {
  els.error.hidden = false; els.errorMessage.textContent = message; els.status.textContent = '未完成';
}

function syncReviewControls() {
  if (!els.reviewPanel) return;
  const locked = state.busy || state.saveInFlight || state.documentMode === 'original';
  els.documentCanvas?.querySelectorAll('.c6b-inline-field').forEach(control => {
    const field = reviewField(control.dataset.fieldId);
    control.contentEditable = String(Boolean(field?.editable) && !locked);
    control.setAttribute('aria-disabled', String(!field?.editable || locked));
  });
  els.reviewGroups?.querySelectorAll('textarea, input, select').forEach(control => {
    control.disabled = state.busy || control.dataset.locked === 'true';
  });
  els.conflictList?.querySelectorAll('select').forEach(control => {
    control.disabled = state.busy || state.saveInFlight;
  });
  if (els.finalize) {
    els.finalize.disabled = state.busy || state.saveInFlight || state.dirty
      || state.savePaused || (state.review?.unresolved_conflict_count || 0) > 0;
  }
  if (els.retrySave) els.retrySave.hidden = !state.savePaused;
}
function setSaveStatus(label, kind = '') {
  if (!els.dirtyBadge) return;
  els.dirtyBadge.textContent = label;
  els.dirtyBadge.className = `c6b-save-state ${kind}`.trim();
}
function setReviewDirty(value = true) {
  state.dirty = value;
  if (value) {
    state.editGeneration += 1;
    setSaveStatus('自动保存 · 未保存', 'dirty');
  } else {
    setSaveStatus('自动保存 · 已保存', 'saved');
  }
  syncReviewControls();
}
function scheduleAutoSave(delay = 800) {
  if (!state.review || state.savePaused) return;
  clearTimeout(state.saveTimer);
  state.saveTimer = setTimeout(() => { void flushAutoSave(); }, delay);
}
function reviewField(fieldId) {
  return state.review?.fields?.find(field => field.field_id === fieldId) || null;
}
function statusLabel(status) {
  return ({ FILLED: '材料已填', USER_CONFIRMED: '人工确认', NEEDS_CONFIRMATION: '待补充', REMOVE: '不使用', KEEP_BLANK: '保留空白', LEAVE_BLANK: '签署时填写' })[status] || status;
}
function statusClass(field) {
  if (field.status === 'USER_CONFIRMED') return 'user-confirmed';
  if (field.status === 'NEEDS_CONFIRMATION') return 'needs-confirmation';
  if (field.status === 'LEAVE_BLANK') return 'leave-blank';
  if (field.status === 'FILLED') return 'material-filled';
  return 'neutral';
}
function initializeReviewState() {
  state.draftValues = new Map();
  state.serviceActions = new Map();
  state.dirtyFieldIds = new Set();
  state.acceptedFieldIds = new Set();
  state.review.fields.forEach(field => {
    state.draftValues.set(field.field_id, field.value || '');
    if (field.group_index != null && field.service_action) {
      state.serviceActions.set(Number(field.group_index), field.service_action);
    }
  });
  state.editGeneration = 0;
  state.savePaused = false;
  setReviewDirty(false);
}
function markFieldDirty(fieldId, value) {
  state.draftValues.set(fieldId, value);
  state.dirtyFieldIds.add(fieldId);
  setReviewDirty(true);
  updatePendingNavigation();
  scheduleAutoSave();
}
function plainTextPaste(event) {
  event.preventDefault();
  const text = (event.clipboardData || window.clipboardData).getData('text/plain');
  document.execCommand('insertText', false, text.replace(/[\r\n]+/g, ' '));
}
function createInlineField(field, meta) {
  const control = document.createElement('span');
  control.className = `c6b-inline-field ${statusClass(field)}`;
  control.dataset.fieldId = field.field_id;
  control.dataset.original = meta.original_placeholder || '';
  control.dataset.status = field.status;
  control.dataset.placeholder = field.status === 'LEAVE_BLANK' ? '签署时填写' : '待补充';
  control.setAttribute('role', 'textbox');
  control.setAttribute('aria-label', `${field.label || field.field_id}：${statusLabel(field.status)}`);
  control.tabIndex = 0;
  control.textContent = state.draftValues.get(field.field_id) || '';
  control.contentEditable = String(Boolean(field.editable));
  control.addEventListener('focus', () => openEvidence(field.field_id));
  control.addEventListener('click', () => openEvidence(field.field_id));
  control.addEventListener('input', () => {
    if (state.documentMode !== 'draft' || !field.editable) return;
    markFieldDirty(field.field_id, control.textContent.trim());
  });
  control.addEventListener('blur', () => {
    if (state.dirty) scheduleAutoSave(0);
  });
  control.addEventListener('paste', plainTextPaste);
  control.addEventListener('keydown', event => {
    if (event.key === 'Enter') { event.preventDefault(); control.blur(); }
    if (event.key === 'Escape') control.blur();
  });
  return control;
}
function textNodes(container) {
  const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT);
  const nodes = [];
  while (walker.nextNode()) nodes.push(walker.currentNode);
  return nodes;
}
function replaceMarker(container, marker, control) {
  const nodes = textNodes(container);
  const text = nodes.map(node => node.nodeValue || '').join('');
  const start = text.indexOf(marker);
  if (start < 0 || text.indexOf(marker, start + marker.length) >= 0) return false;
  const end = start + marker.length;
  let cursor = 0;
  let startNode = null; let endNode = null; let startOffset = 0; let endOffset = 0;
  for (const node of nodes) {
    const next = cursor + (node.nodeValue || '').length;
    if (!startNode && start >= cursor && start < next) {
      startNode = node; startOffset = start - cursor;
    }
    if (endNode == null && end > cursor && end <= next) {
      endNode = node; endOffset = end - cursor; break;
    }
    cursor = next;
  }
  if (!startNode || !endNode) return false;
  if (startNode === endNode) {
    const value = startNode.nodeValue || '';
    const after = document.createTextNode(value.slice(endOffset));
    startNode.nodeValue = value.slice(0, startOffset);
    startNode.parentNode.insertBefore(control, startNode.nextSibling);
    control.parentNode.insertBefore(after, control.nextSibling);
    return true;
  }
  startNode.nodeValue = (startNode.nodeValue || '').slice(0, startOffset);
  let clearing = false;
  for (const node of nodes) {
    if (node === startNode) { clearing = true; continue; }
    if (!clearing) continue;
    if (node === endNode) {
      node.nodeValue = (node.nodeValue || '').slice(endOffset);
      break;
    }
    node.nodeValue = '';
  }
  startNode.parentNode.insertBefore(control, startNode.nextSibling);
  return true;
}
async function renderDocumentEditor(reviewContext) {
  if (!isCurrentReviewContext(reviewContext) || !state.review) return false;
  const review = state.review;
  els.documentLoading.hidden = false;
  if (!window.docx || typeof window.docx.renderAsync !== 'function') {
    throw new Error('Word 文档渲染组件未能加载');
  }
  const documentView = await requestJson(
    `/case6b/session/${encodeURIComponent(reviewContext.sessionId)}/document-view`,
  );
  if (!isCurrentReviewContext(reviewContext)) return false;
  if (!String(documentView.shell_url || '').includes('/document-shell')) {
    throw new Error('Word 草案地址无效');
  }
  const shellResponse = await fetch(documentView.shell_url, { cache: 'no-store' });
  if (!isCurrentReviewContext(reviewContext)) return false;
  if (!shellResponse.ok) throw new Error(`Word 草案读取失败（HTTP ${shellResponse.status}）`);
  const shell = await shellResponse.blob();
  if (!isCurrentReviewContext(reviewContext)) return false;
  const renderHost = document.createElement('div');
  await window.docx.renderAsync(shell, renderHost, renderHost, {
    className: 'c6b-docx',
    inWrapper: true,
    ignoreWidth: false,
    ignoreHeight: false,
    ignoreFonts: false,
    breakPages: true,
    useBase64URL: true,
    renderAltChunks: false,
  });
  if (!isCurrentReviewContext(reviewContext)) return false;
  renderHost.querySelectorAll('a').forEach(link => {
    link.removeAttribute('href'); link.removeAttribute('target');
  });
  const metadata = new Map(documentView.fields.map(field => [field.field_id, field]));
  for (const field of review.fields) {
    const meta = metadata.get(field.field_id);
    if (!meta || !replaceMarker(renderHost, meta.marker, createInlineField(field, meta))) {
      throw new Error(`字段 ${field.field_id} 无法稳定映射到 Word 草案`);
    }
  }
  if (!isCurrentReviewContext(reviewContext)) return false;
  state.documentView = documentView;
  state.fallbackMode = false;
  els.editorFallback.hidden = true;
  els.documentCanvas.hidden = false;
  els.documentCanvas.replaceChildren(...Array.from(renderHost.childNodes));
  els.documentLoading.hidden = true;
  applyDocumentMode('draft');
  updatePendingNavigation();
  return true;
}
function renderFallbackFields(reason) {
  state.fallbackMode = true;
  els.documentLoading.hidden = true;
  els.documentCanvas.hidden = true;
  els.editorFallback.hidden = false;
  els.fallbackReason.textContent = `${reason}。请使用下方简化字段表单继续，最终 DOCX 仍从原模板生成。`;
  els.reviewGroups.innerHTML = '';
  const serviceGroups = new Map();
  state.review.fields.forEach(field => {
    if (field.group_key === 'services') {
      if (!serviceGroups.has(field.group_index)) serviceGroups.set(field.group_index, []);
      serviceGroups.get(field.group_index).push(field);
      return;
    }
    const row = document.createElement('label');
    row.className = 'c6b-field-row'; row.dataset.fieldId = field.field_id;
    const title = document.createElement('strong'); title.textContent = field.label || field.field_id;
    const input = document.createElement('textarea');
    input.className = 'c6b-field-input'; input.value = state.draftValues.get(field.field_id) || '';
    input.dataset.locked = String(!field.editable); input.disabled = !field.editable;
    input.placeholder = field.status === 'LEAVE_BLANK' ? '签署时填写' : '待补充';
    input.addEventListener('input', () => markFieldDirty(field.field_id, input.value.trim()));
    input.addEventListener('focus', () => openEvidence(field.field_id));
    row.append(title, input); els.reviewGroups.appendChild(row);
  });
  serviceGroups.forEach((fields, groupIndex) => {
    const nameField = fields.find(field => field.field_kind === 'repeatable_service');
    const priceField = fields.find(field => field.field_kind === 'repeatable_price');
    const row = document.createElement('div'); row.className = 'c6b-field-row'; row.dataset.groupIndex = groupIndex;
    const title = document.createElement('strong'); title.textContent = `服务 ${groupIndex}`;
    const name = document.createElement('input'); name.className = 'c6b-field-input'; name.dataset.role = 'name'; name.value = state.draftValues.get(nameField.field_id) || '';
    const price = document.createElement('input'); price.className = 'c6b-field-input'; price.dataset.role = 'price'; price.value = state.draftValues.get(priceField.field_id) || '';
    const action = document.createElement('select'); action.dataset.role = 'action';
    [['included','正式服务'],['optional','可选服务'],['blank','保留空白'],['remove','移除']].forEach(([value,label]) => {
      const option = document.createElement('option'); option.value = value; option.textContent = label; action.appendChild(option);
    });
    action.value = state.serviceActions.get(Number(groupIndex)) || 'included';
    const change = () => {
      state.draftValues.set(nameField.field_id, name.value.trim());
      state.draftValues.set(priceField.field_id, price.value.trim());
      state.serviceActions.set(Number(groupIndex), action.value);
      state.dirtyFieldIds.add(nameField.field_id); state.dirtyFieldIds.add(priceField.field_id);
      setReviewDirty(true); scheduleAutoSave();
    };
    [name, price].forEach(input => input.addEventListener('input', change)); action.addEventListener('change', change);
    row.append(title, name, price, action); els.reviewGroups.appendChild(row);
  });
}
function applyDocumentMode(mode) {
  state.documentMode = mode;
  els.documentMode.querySelectorAll('[data-document-mode]').forEach(button => {
    button.setAttribute('aria-selected', String(button.dataset.documentMode === mode));
  });
  els.documentCanvas.querySelectorAll('.c6b-inline-field').forEach(control => {
    const field = reviewField(control.dataset.fieldId);
    if (!field) return;
    if (mode === 'original') {
      control.textContent = control.dataset.original || '________';
      control.contentEditable = 'false';
      control.classList.add('original-placeholder');
    } else {
      control.textContent = state.draftValues.get(field.field_id) || '';
      control.contentEditable = String(Boolean(field.editable) && !state.busy && !state.saveInFlight);
      control.classList.remove('original-placeholder');
    }
  });
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
    select.addEventListener('change', () => { setReviewDirty(true); scheduleAutoSave(); });
    row.append(title, select); els.conflictList.appendChild(row);
  });
  els.conflictPanel.hidden = !conflicts.length;
}
function openEvidence(fieldId) {
  const field = reviewField(fieldId);
  if (!field) return;
  state.activeFieldId = fieldId;
  els.evidenceDrawer.hidden = false;
  els.evidenceTitle.textContent = field.label || field.field_id;
  els.evidenceBody.innerHTML = '';
  const badge = document.createElement('span'); badge.className = `c6b-evidence-status ${statusClass(field)}`; badge.textContent = statusLabel(field.status);
  const id = document.createElement('small'); id.textContent = field.field_id;
  els.evidenceBody.append(badge, id);
  if (
    field.can_confirm && field.group_key !== 'services'
    && field.suggested_value && !state.draftValues.get(fieldId)
  ) {
    const suggestion = document.createElement('div'); suggestion.className = 'c6b-suggestion';
    const copy = document.createElement('p'); copy.textContent = field.suggested_value;
    const accept = document.createElement('button'); accept.type = 'button'; accept.dataset.acceptFieldId = fieldId; accept.textContent = '采用建议';
    accept.addEventListener('click', () => {
      state.draftValues.set(fieldId, field.suggested_value);
      state.acceptedFieldIds.add(fieldId); state.dirtyFieldIds.add(fieldId);
      const control = els.documentCanvas.querySelector(`[data-field-id="${CSS.escape(fieldId)}"]`);
      if (control) control.textContent = field.suggested_value;
      setReviewDirty(true); scheduleAutoSave(0); openEvidence(fieldId);
    });
    suggestion.append(copy, accept); els.evidenceBody.appendChild(suggestion);
  }
  if (field.group_index != null) {
    const select = document.createElement('select'); select.className = 'c6b-service-action';
    [['included','正式服务'],['optional','可选服务'],['blank','保留空白'],['remove','移除']].forEach(([value,label]) => {
      const option = document.createElement('option'); option.value = value; option.textContent = label; select.appendChild(option);
    });
    select.value = state.serviceActions.get(Number(field.group_index)) || 'included';
    select.addEventListener('change', () => {
      state.serviceActions.set(Number(field.group_index), select.value);
      state.review.fields.filter(item => item.group_index === field.group_index).forEach(item => state.dirtyFieldIds.add(item.field_id));
      setReviewDirty(true); scheduleAutoSave();
    });
    els.evidenceBody.appendChild(select);
  }
  const evidence = Array.isArray(field.evidence) ? field.evidence : [];
  const heading = document.createElement('h4'); heading.textContent = evidence.length ? `材料证据（${evidence.length}）` : '暂无直接证据'; els.evidenceBody.appendChild(heading);
  evidence.forEach(item => {
    const entry = document.createElement('div'); entry.className = 'c6b-evidence-entry';
    const source = document.createElement('strong'); source.textContent = item.source || '来源';
    const fact = document.createElement('p'); fact.textContent = item.fact || '';
    const locator = document.createElement('small'); locator.textContent = item.locator || item.location || item.source_location || '';
    entry.append(source, fact, locator); els.evidenceBody.appendChild(entry);
  });
}
function updatePendingNavigation() {
  const pending = [...els.documentCanvas.querySelectorAll('.c6b-inline-field.needs-confirmation')]
    .filter(control => !(state.draftValues.get(control.dataset.fieldId) || '').trim());
  if (!pending.length) state.pendingIndex = 0;
  else state.pendingIndex = Math.min(state.pendingIndex, pending.length - 1);
  els.pendingPosition.textContent = `${pending.length ? state.pendingIndex + 1 : 0} / ${pending.length} 待补`;
  els.previousPending.disabled = pending.length === 0;
  els.nextPending.disabled = pending.length === 0;
  return pending;
}
function movePending(direction) {
  const pending = updatePendingNavigation();
  if (!pending.length) return;
  state.pendingIndex = (state.pendingIndex + direction + pending.length) % pending.length;
  const control = pending[state.pendingIndex];
  if (state.documentMode !== 'draft') applyDocumentMode('draft');
  control.scrollIntoView({ behavior: 'smooth', block: 'center' });
  control.focus(); updatePendingNavigation();
}
function collectReview() {
  const manifestFields = state.review.fields;
  const field_updates = manifestFields
    .filter(field => state.dirtyFieldIds.has(field.field_id) && field.group_key !== 'services' && field.editable)
    .map(field => ({ field_id: field.field_id, value: (state.draftValues.get(field.field_id) || '').trim() }));
  const groupIndexes = new Set(
    manifestFields.filter(field => field.group_key === 'services' && state.dirtyFieldIds.has(field.field_id)).map(field => Number(field.group_index)),
  );
  const service_rows = [...groupIndexes].map(groupIndex => {
    const fields = manifestFields.filter(field => Number(field.group_index) === groupIndex);
    const name = fields.find(field => field.field_kind === 'repeatable_service');
    const price = fields.find(field => field.field_kind === 'repeatable_price');
    return {
      group_index: groupIndex,
      action: state.serviceActions.get(groupIndex) || 'included',
      name: (state.draftValues.get(name.field_id) || '').trim(),
      price: (state.draftValues.get(price.field_id) || '').trim(),
    };
  });
  const conflict_resolutions = [...els.conflictList.querySelectorAll('[data-conflict-id]')]
    .filter(row => row.querySelector('select').value)
    .map(row => ({ conflict_id: row.dataset.conflictId, value: row.querySelector('select').value }));
  const accepted_field_ids = [...state.acceptedFieldIds].filter(
    fieldId => reviewField(fieldId)?.can_confirm
      && reviewField(fieldId)?.group_key !== 'services',
  );
  return {
    version: state.review.version,
    field_updates,
    accepted_field_ids,
    service_rows,
    conflict_resolutions,
  };
}
function applySavedReview(review, preserveLocal) {
  state.review = review;
  if (!preserveLocal) {
    state.draftValues = new Map(review.fields.map(field => [field.field_id, field.value || '']));
    state.dirtyFieldIds.clear(); state.acceptedFieldIds.clear();
  }
  els.fieldCount.textContent = `${review.fields.length} 个字段`;
  els.unresolvedCount.textContent = `${review.unresolved_count} 个待补充`;
  els.documentCanvas.querySelectorAll('.c6b-inline-field').forEach(control => {
    const field = reviewField(control.dataset.fieldId); if (!field) return;
    control.className = `c6b-inline-field ${statusClass(field)}`;
    if (state.documentMode === 'original') control.classList.add('original-placeholder');
    control.dataset.status = field.status;
    if (!preserveLocal && state.documentMode === 'draft') control.textContent = field.value || '';
  });
  renderConflicts(review.conflicts || []);
  updatePendingNavigation(); syncReviewControls();
}
async function flushAutoSave() {
  clearTimeout(state.saveTimer);
  if (!state.sessionId || !state.review || !state.dirty || state.savePaused) return !state.dirty;
  if (state.saveInFlight) { state.saveQueued = true; return false; }
  const saveContext = captureReviewContext();
  const payload = collectReview();
  const generation = state.editGeneration;
  state.saveInFlight = true; state.saveQueued = false;
  setSaveStatus('自动保存 · 保存中', 'saving'); syncReviewControls();
  try {
    const review = await requestJson(
      `/case6b/session/${encodeURIComponent(saveContext.sessionId)}/review`,
      { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) },
    );
    if (!isCurrentReviewContext(saveContext)) return false;
    const changedDuringSave = state.editGeneration !== generation;
    if (!changedDuringSave) {
      state.dirty = false; state.dirtyFieldIds.clear(); state.acceptedFieldIds.clear();
      setSaveStatus('自动保存 · 已保存', 'saved');
    }
    applySavedReview(review, changedDuringSave);
    if (changedDuringSave) { setReviewDirty(true); state.saveQueued = true; }
    return !changedDuringSave;
  } catch (error) {
    if (!isCurrentReviewContext(saveContext)) return false;
    if (error.code === 'STALE_REVIEW') {
      state.savePaused = true;
      setSaveStatus('版本冲突 · 暂停保存', 'error');
      if (window.confirm('草案已在其他页面更新。重新加载服务器版本会放弃本页未保存修改，是否继续？')) {
        const review = await requestJson(`/case6b/session/${encodeURIComponent(saveContext.sessionId)}/review`);
        if (!isCurrentReviewContext(saveContext)) return false;
        state.review = review;
        initializeReviewState(); await renderReview(saveContext);
      }
    } else {
      state.savePaused = true;
      setSaveStatus('保存失败 · 可重试', 'error');
      toast(error.message);
    }
    return false;
  } finally {
    if (isCurrentReviewContext(saveContext)) {
      state.saveInFlight = false; syncReviewControls();
      if (state.saveQueued && !state.savePaused) { state.saveQueued = false; scheduleAutoSave(0); }
    }
  }
}
async function renderReview(reviewContext = captureReviewContext()) {
  if (!state.review || !isCurrentReviewContext(reviewContext)) return;
  initializeReviewState();
  els.progress.hidden = true; els.error.hidden = true; els.reviewPanel.hidden = false;
  els.status.textContent = '草案审阅'; setStage('review');
  els.fieldCount.textContent = `${state.review.fields.length} 个字段`;
  els.unresolvedCount.textContent = `${state.review.unresolved_count} 个待补充`;
  renderConflicts(state.review.conflicts || []);
  try {
    const rendered = await renderDocumentEditor(reviewContext);
    if (!rendered || !isCurrentReviewContext(reviewContext)) return;
  } catch (error) {
    if (!isCurrentReviewContext(reviewContext)) return;
    renderFallbackFields(error.message);
  }
  if (!isCurrentReviewContext(reviewContext)) return;
  syncReviewControls();
}
async function finalizeDraft() {
  if (!state.review || state.busy) return;
  if (state.saveInFlight) {
    toast('字段正在自动保存，请稍候再生成。');
    return;
  }
  if (state.dirty) {
    const saved = await flushAutoSave();
    if (!saved || state.dirty) {
      toast('仍有未保存字段，请先重试保存。');
      return;
    }
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
  clearTimeout(state.saveTimer);
  invalidateReviewContext();
  const analysisController = state.analysisController;
  state.analysisController = null; state.analysisRunToken = null; state.analysisCompletion = null; state.cancelRunToken = null;
  analysisController?.abort();
  state.template = null; state.materials = []; state.sessionId = null; state.review = null; state.templatePreflight = null;
  state.documentView = null; state.analysisRunId = null; state.processing = false; state.cancelRequested = false;
  state.dirty = false; state.dirtyFieldIds = new Set(); state.acceptedFieldIds = new Set(); state.draftValues = new Map();
  state.serviceActions = new Map(); state.saveInFlight = false; state.saveQueued = false; state.savePaused = false;
  state.documentMode = 'draft'; state.activeFieldId = null; state.pendingIndex = 0; state.fallbackMode = false;
  els.templateInput.value = ''; els.materialsInput.value = ''; els.templateState.textContent = '未选择'; els.materialsState.textContent = '未选择';
  els.templateActions.hidden = true;
  els.selectedFiles.innerHTML = ''; els.materialStatus.innerHTML = ''; els.idle.hidden = false; els.progress.hidden = true; els.error.hidden = true;
  els.reviewPanel.hidden = true; els.downloadPanel.hidden = true; els.retry.hidden = true; els.status.textContent = '等待文件'; setStage('upload'); updateStartButton();
  els.templatePreflight.hidden = true; els.conflictPanel.hidden = true; els.evidenceDrawer.hidden = true;
  els.documentCanvas.innerHTML = ''; els.documentCanvas.hidden = false; els.documentLoading.hidden = false; els.editorFallback.hidden = true;
  if (els.cancelAnalysis) { els.cancelAnalysis.hidden = true; els.cancelAnalysis.disabled = true; els.cancelAnalysis.textContent = '中断并修改文件'; }
  els.differenceToggle.checked = true; els.documentCanvas.classList.add('show-differences');
  if (updateCurrent && currentHistory()) updateHistory({ title: '新建草案', sessionId: null, expired: false });
  syncUploadControls();
}
async function newTask() {
  if (state.busy || !(await saveBeforeNavigation())) return;
  state.currentLocalId = null; ensureHistory(); clearView(false); closeSidebar();
}
function closeSidebar() { els.sidebar.classList.remove('open'); els.sidebarOverlay.classList.remove('open'); }

bindDropzone(els.templateDropzone, els.templateInput, files => setTemplate(files[0]));
bindDropzone(els.materialsDropzone, els.materialsInput, files => addMaterials(files));
els.removeTemplate.addEventListener('click', removeTemplate);
els.replaceTemplate.addEventListener('click', () => els.templateInput.click());
els.start.addEventListener('click', startWorkflow); els.retry.addEventListener('click', () => retryFailed());
els.cancelAnalysis.addEventListener('click', cancelAnalysis); els.finalize.addEventListener('click', finalizeDraft);
els.documentMode.addEventListener('click', event => {
  const button = event.target.closest('[data-document-mode]');
  if (button) applyDocumentMode(button.dataset.documentMode);
});
els.differenceToggle.addEventListener('change', () => {
  els.documentCanvas.classList.toggle('show-differences', els.differenceToggle.checked);
});
els.previousPending.addEventListener('click', () => movePending(-1));
els.nextPending.addEventListener('click', () => movePending(1));
els.evidenceClose.addEventListener('click', () => { els.evidenceDrawer.hidden = true; });
els.retrySave.addEventListener('click', () => { state.savePaused = false; scheduleAutoSave(0); });
els.downloadDocx.addEventListener('click', () => download('docx')); els.downloadPdf.addEventListener('click', () => download('pdf'));
els.reset.addEventListener('click', () => resetCurrent(true)); els.newTask.addEventListener('click', newTask);
els.sidebarToggle.addEventListener('click', () => { els.sidebar.classList.add('open'); els.sidebarOverlay.classList.add('open'); });
els.sidebarOverlay.addEventListener('click', closeSidebar); els.mobileTheme.addEventListener('click', toggleTheme);

loadHistory(); ensureHistory(); renderHistory(); clearView(false);
