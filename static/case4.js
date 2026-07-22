'use strict';

const MAX_BYTES = 25 * 1024 * 1024;
const RESULT_KEY = '译文PDF';
const directionNames = {
  en_to_zh_tw: 'English → 繁體中文',
  zh_tw_to_en: '繁體中文 → English',
};

const els = {
  directionButtons: [...document.querySelectorAll('.c4-direction-btn')],
  dropzone: document.getElementById('dropzone'),
  input: document.getElementById('pdfInput'),
  fileCard: document.getElementById('fileCard'),
  fileName: document.getElementById('fileName'),
  fileMeta: document.getElementById('fileMeta'),
  removeFile: document.getElementById('removeFileBtn'),
  translate: document.getElementById('translateBtn'),
  reset: document.getElementById('resetBtn'),
  mobileReset: document.getElementById('mobileResetBtn'),
  mobileTheme: document.getElementById('mobileThemeBtn'),
  statusPill: document.getElementById('statusPill'),
  idle: document.getElementById('idleState'),
  progress: document.getElementById('progressState'),
  progressFill: document.getElementById('progressFill'),
  message: document.getElementById('currentMessage'),
  pageList: document.getElementById('pageList'),
  success: document.getElementById('successState'),
  successMeta: document.getElementById('successMeta'),
  error: document.getElementById('errorState'),
  errorMessage: document.getElementById('errorMessage'),
  download: document.getElementById('downloadBtn'),
  stages: [...document.querySelectorAll('.c4-stage')],
};

const state = {
  file: null,
  direction: 'en_to_zh_tw',
  sessionId: null,
  pageCount: 0,
  resultReady: false,
  busy: false,
};

function bytesLabel(bytes) {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function setDirection(direction) {
  if (state.busy) return;
  state.direction = direction;
  els.directionButtons.forEach((button) => {
    const active = button.dataset.direction === direction;
    button.classList.toggle('active', active);
    button.setAttribute('aria-checked', String(active));
  });
  updateFileMeta();
}

function updateFileMeta() {
  if (!state.file) return;
  const pages = state.pageCount ? ` · ${state.pageCount} 页` : '';
  els.fileMeta.textContent = `${bytesLabel(state.file.size)}${pages} · ${directionNames[state.direction]}`;
}

function chooseFile(file) {
  if (!file || state.busy) return;
  if (!file.name.toLowerCase().endsWith('.pdf') && file.type !== 'application/pdf') {
    toast('请选择 PDF 文件');
    return;
  }
  if (file.size > MAX_BYTES) {
    toast('PDF 文件不能超过 25 MB');
    return;
  }
  if (file.size === 0) {
    toast('PDF 文件为空');
    return;
  }
  state.file = file;
  state.pageCount = 0;
  els.fileName.textContent = file.name;
  updateFileMeta();
  els.fileCard.hidden = false;
  els.dropzone.hidden = true;
  els.translate.disabled = false;
}

function clearFile() {
  if (state.busy) return;
  state.file = null;
  state.pageCount = 0;
  els.input.value = '';
  els.fileCard.hidden = true;
  els.dropzone.hidden = false;
  els.translate.disabled = true;
}

function setBusy(busy) {
  state.busy = busy;
  els.translate.disabled = busy || !state.file;
  els.removeFile.disabled = busy;
  els.directionButtons.forEach((button) => { button.disabled = busy; });
}

function showProgress() {
  els.idle.hidden = true;
  els.success.hidden = true;
  els.error.hidden = true;
  els.progress.hidden = false;
  els.statusPill.textContent = '处理中';
  els.statusPill.classList.add('active');
}

function initPages(count) {
  els.pageList.replaceChildren();
  for (let page = 1; page <= count; page += 1) {
    const row = document.createElement('div');
    row.className = 'c4-page-row';
    row.dataset.page = String(page);
    row.innerHTML = `<span>Page ${String(page).padStart(2, '0')}</span><span class="bar"><i></i></span><small>等待</small>`;
    els.pageList.appendChild(row);
  }
}

function setPageStatus(page, status) {
  const row = els.pageList.querySelector(`[data-page="${page}"]`);
  if (!row) return;
  row.classList.toggle('processing', status === 'processing');
  row.classList.toggle('done', status === 'done');
  row.querySelector('small').textContent = status === 'done' ? '完成' : status === 'processing' ? '翻译中' : '等待';
  if (status === 'processing') row.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
}

function setStage(stage) {
  const order = ['split', 'translate', 'merge'];
  const activeIndex = order.indexOf(stage);
  els.stages.forEach((element) => {
    const index = order.indexOf(element.dataset.stage);
    element.classList.toggle('done', index < activeIndex);
    element.classList.toggle('active', index === activeIndex);
  });
}

function updateProgress(event) {
  const page = Number(event.page || 0);
  const total = Number(event.total || state.pageCount || 1);
  els.message.textContent = event.message || '正在处理...';
  if (event.stage === 'split') {
    setStage('split');
    els.progressFill.style.width = `${Math.min(14, 4 + (page / total) * 10)}%`;
  } else if (event.stage === 'translate') {
    setStage('translate');
    setPageStatus(page, event.status === 'done' ? 'done' : 'processing');
    els.progressFill.style.width = `${15 + (page / total) * (event.status === 'done' ? 73 : 64)}%`;
  } else if (event.stage === 'merge') {
    setStage('merge');
    els.progressFill.style.width = '94%';
  }
}

async function readSse(response, onEvent) {
  if (!response.body) throw new Error('浏览器不支持流式响应');
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
    const blocks = buffer.split(/\r?\n\r?\n/);
    buffer = blocks.pop() || '';
    for (const block of blocks) {
      const data = block.split(/\r?\n/)
        .filter((line) => line.startsWith('data:'))
        .map((line) => line.slice(5).trimStart())
        .join('\n');
      if (data) onEvent(JSON.parse(data));
    }
    if (done) break;
  }
  if (buffer.trim()) {
    const line = buffer.split(/\r?\n/).find((item) => item.startsWith('data:'));
    if (line) onEvent(JSON.parse(line.slice(5).trimStart()));
  }
}

async function requestJson(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) {
    let message = `请求失败（HTTP ${response.status}）`;
    try { message = (await response.json()).detail || message; } catch (_) { /* no JSON */ }
    throw new Error(message);
  }
  return response.json();
}

async function startTranslation() {
  if (!state.file || state.busy) return;
  setBusy(true);
  state.resultReady = false;
  showProgress();
  els.message.textContent = '正在校验并上传 PDF...';
  els.progressFill.style.width = '3%';
  try {
    const uploadForm = new FormData();
    uploadForm.append('pdf_file', state.file, state.file.name);
    uploadForm.append('direction', state.direction);
    const uploaded = await requestJson('/case4/session/upload', { method: 'POST', body: uploadForm });
    state.sessionId = uploaded.session_id;
    state.pageCount = uploaded.page_count;
    updateFileMeta();
    initPages(state.pageCount);

    const analyzeForm = new FormData();
    analyzeForm.append('session_id', state.sessionId);
    const response = await fetch('/pdf/session/analyze', { method: 'POST', body: analyzeForm });
    if (!response.ok) throw new Error(`分析请求失败（HTTP ${response.status}）`);
    let fatalMessage = '';
    await readSse(response, (event) => {
      if (event.type === 'progress') updateProgress(event);
      if (event.type === 'result' && event.result_key === RESULT_KEY) state.resultReady = true;
      if (event.type === 'fatal_error' || event.type === 'error') fatalMessage = event.message || '翻译失败';
    });
    if (fatalMessage) throw new Error(fatalMessage);
    if (!state.resultReady) throw new Error('翻译流程结束，但没有生成可下载的 PDF');
    finishSuccess();
  } catch (error) {
    showError(error instanceof Error ? error.message : String(error));
  } finally {
    setBusy(false);
  }
}

function finishSuccess() {
  els.progressFill.style.width = '100%';
  els.stages.forEach((stage) => { stage.classList.remove('active'); stage.classList.add('done'); });
  els.progress.hidden = true;
  els.error.hidden = true;
  els.success.hidden = false;
  els.statusPill.textContent = '已完成';
  els.successMeta.textContent = `${state.pageCount} 页 · ${directionNames[state.direction]}`;
}

function showError(message) {
  els.progress.hidden = true;
  els.success.hidden = true;
  els.error.hidden = false;
  els.errorMessage.textContent = message;
  els.statusPill.textContent = '未完成';
}

function parseFilename(disposition) {
  const utf8 = disposition.match(/filename\*=UTF-8''([^;]+)/i);
  if (utf8) return decodeURIComponent(utf8[1]);
  const plain = disposition.match(/filename="?([^";]+)"?/i);
  return plain ? plain[1] : 'translated.pdf';
}

async function downloadResult() {
  if (!state.resultReady || !state.sessionId) return;
  els.download.disabled = true;
  try {
    const form = new FormData();
    form.append('session_id', state.sessionId);
    form.append('party', RESULT_KEY);
    form.append('output_format', 'pdf');
    const response = await fetch('/pdf/session/report', { method: 'POST', body: form });
    if (!response.ok) {
      let message = `下载失败（HTTP ${response.status}）`;
      try { message = (await response.json()).detail || message; } catch (_) { /* no JSON */ }
      throw new Error(message);
    }
    const blob = await response.blob();
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = parseFilename(response.headers.get('content-disposition') || '');
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(link.href), 1000);
  } catch (error) {
    toast(error instanceof Error ? error.message : String(error));
  } finally {
    els.download.disabled = false;
  }
}

els.directionButtons.forEach((button) => button.addEventListener('click', () => setDirection(button.dataset.direction)));
els.input.addEventListener('change', () => chooseFile(els.input.files[0]));
els.removeFile.addEventListener('click', clearFile);
els.translate.addEventListener('click', startTranslation);
els.download.addEventListener('click', downloadResult);
els.reset.addEventListener('click', () => { if (!state.busy || window.confirm('翻译正在进行，确定离开当前任务吗？')) window.location.reload(); });
els.mobileReset.addEventListener('click', () => els.reset.click());
els.mobileTheme.addEventListener('click', toggleTheme);
['dragenter', 'dragover'].forEach((name) => els.dropzone.addEventListener(name, (event) => { event.preventDefault(); els.dropzone.classList.add('dragover'); }));
['dragleave', 'drop'].forEach((name) => els.dropzone.addEventListener(name, (event) => { event.preventDefault(); els.dropzone.classList.remove('dragover'); }));
els.dropzone.addEventListener('drop', (event) => chooseFile(event.dataTransfer.files[0]));
