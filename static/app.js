/**
 * eBRAM AI 文档助手 — 前端逻辑
 *
 * 两条主要流程：
 *   1. 文字聊天  → POST /chat             → 普通 JSON 响应
 *   2. PDF 上传  → POST /pdf/pages/chat   → SSE 流式响应（逐页进度+结果）
 *
 * 新增功能：
 *   Feature 1: 左侧边栏聊天历史记录（localStorage 持久化）
 *   Feature 2: AI 消息复制按钮（hover 显示，剪贴板 API）
 *   Feature 3: 消息时间戳（hover 显示 HH:mm，data-time 属性）
 */

'use strict';

// ── 配置 ──────────────────────────────────────────────────────────────────────
const API_BASE    = '';
const THEME_KEY   = 'ebram_theme';
const HISTORY_KEY = 'ebram_history';
const MAX_SESSIONS = 50;

// ── SVG 图标常量 ──────────────────────────────────────────────────────────────
const COPY_ICON = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/></svg>`;
const CHECK_ICON = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>`;

// ── DOM 引用 ──────────────────────────────────────────────────────────────────
const messagesWrap  = document.getElementById('messagesWrap');
const messagesList  = document.getElementById('messagesList');
const welcome       = document.getElementById('welcome');
const textInput     = document.getElementById('textInput');
const sendBtn       = document.getElementById('sendBtn');
const fileInput     = document.getElementById('fileInput');
const progressPanel = document.getElementById('progressPanel');
const progressStage = document.getElementById('progressStage');
const progressRatio = document.getElementById('progressRatio');
const progressFill  = document.getElementById('progressFill');
const progressDots  = document.getElementById('progressDots');
const dragOverlay    = document.getElementById('dragOverlay');
const newChatBtn     = document.getElementById('newChatBtn');
const themeBtn       = document.getElementById('themeBtn');
const historyList    = document.getElementById('historyList');
const fileQueuePanel = document.getElementById('fileQueuePanel');
const fqList         = document.getElementById('fqList');
const fqCount        = document.getElementById('fqCount');
const processBtn     = document.getElementById('processBtn');
const analyzeBtn     = document.getElementById('analyzeBtn');
// 甲方下载按钮
const reportBtnWordA = document.getElementById('reportBtnWordA');
const reportBtnPdfA  = document.getElementById('reportBtnPdfA');
// 乙方下载按钮
const reportBtnWordB = document.getElementById('reportBtnWordB');
const reportBtnPdfB  = document.getElementById('reportBtnPdfB');
// 通用下载按钮
const reportBtnWordG = document.getElementById('reportBtnWordG');
const reportBtnPdfG  = document.getElementById('reportBtnPdfG');
const fqHeader       = document.getElementById('fqHeader');
const fqBody         = document.getElementById('fqBody');
const fqToggle       = document.getElementById('fqToggle');

// ── 状态 ──────────────────────────────────────────────────────────────────────
let busy = false;
let dragCount = 0;
let totalPages = 0;
let currentConversationId = null;

// Feature 1: 会话状态
let sessions = [];          // Session[]
let currentSessionId = null;

// 多文档队列状态
let multiSessionId = null;         // 后端 session_store key（UUID）
let fileQueue = [];                // [{name, file, status, party}]
let fqCollapsed = false;           // 文件队列面板折叠状态

// 综合分析结果（甲乙双方独立）
let analysisResults = {};          // { "甲方": "分析文本", "乙方": "分析文本", ... }
let conversationIdPartyA = null;   // Agent B 甲方 conversation_id
let conversationIdPartyB = null;   // Agent B 乙方 conversation_id
let followupConversationId = null; // 文字追问 conversation_id（首次创建后复用）

// ── marked.js 配置 ────────────────────────────────────────────────────────────
marked.use({ breaks: true, gfm: true });

// ── 工具函数 ──────────────────────────────────────────────────────────────────
function generateId() {
  return `s_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`;
}

function formatTime(ts) {
  const d = new Date(ts);
  const hh = String(d.getHours()).padStart(2, '0');
  const mm = String(d.getMinutes()).padStart(2, '0');
  return `${hh}:${mm}`;
}

// ── 主题管理 ──────────────────────────────────────────────────────────────────
function applyTheme(theme) {
  if (theme === 'light') {
    document.documentElement.classList.add('light');
  } else {
    document.documentElement.classList.remove('light');
  }
  localStorage.setItem(THEME_KEY, theme);
}

function toggleTheme() {
  const isLight = document.documentElement.classList.contains('light');
  applyTheme(isLight ? 'dark' : 'light');
}

applyTheme(localStorage.getItem(THEME_KEY) || 'dark');
themeBtn.addEventListener('click', toggleTheme);

// ── Toast 提示 ────────────────────────────────────────────────────────────────
function toast(msg) {
  const el = document.createElement('div');
  el.className = 'toast';
  el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 2600);
}

// ── 隐藏欢迎页 ───────────────────────────────────────────────────────────────
function hideWelcome() {
  if (welcome && !welcome.hidden) welcome.hidden = true;
}

// ── 滚动到底部 ───────────────────────────────────────────────────────────────
function scrollBottom() {
  requestAnimationFrame(() => {
    messagesWrap.scrollTop = messagesWrap.scrollHeight;
  });
}

// ── 设置忙碌状态 ─────────────────────────────────────────────────────────────
function setBusy(val) {
  busy = val;
  sendBtn.disabled = val || textInput.value.trim().length === 0;
  textInput.disabled = val;
  fileInput.disabled = val;
  updateProcessBtn();
  updateAnalyzeBtn();
}

// ── Feature 1: 会话管理 ───────────────────────────────────────────────────────

function loadSessions() {
  try {
    const raw = localStorage.getItem(HISTORY_KEY);
    sessions = raw ? JSON.parse(raw) : [];
    if (!Array.isArray(sessions)) sessions = [];
  } catch {
    sessions = [];
  }
}

function saveSessions() {
  // 只保留最近 MAX_SESSIONS 条
  if (sessions.length > MAX_SESSIONS) {
    sessions = sessions.slice(0, MAX_SESSIONS);
  }
  try {
    localStorage.setItem(HISTORY_KEY, JSON.stringify(sessions));
  } catch {
    // localStorage 满时静默失败
  }
}

function getCurrentSession() {
  return sessions.find(s => s.id === currentSessionId) || null;
}

function createNewSession() {
  const id = generateId();
  const session = {
    id,
    conversationId: null,
    title: '新对话',
    createdAt: Date.now(),
    messages: [],
  };
  sessions.unshift(session);
  currentSessionId = id;
  currentConversationId = null;
  saveSessions();
  return session;
}

function appendToCurrentSession(role, content, time) {
  const session = getCurrentSession();
  if (!session) return;
  session.messages.push({ role, content, time });
  // 用第一条用户消息作为标题（最多 20 字）
  if (role === 'user' && session.title === '新对话') {
    session.title = content.replace(/[\n\r]/g, ' ').slice(0, 20) || '新对话';
  }
  saveSessions();
}

function updateSessionConversationId(convId) {
  if (!convId) return;
  currentConversationId = convId;
  const session = getCurrentSession();
  if (session) {
    session.conversationId = convId;
    saveSessions();
  }
}

function renderHistoryList() {
  historyList.innerHTML = '';

  if (sessions.length === 0) {
    const empty = document.createElement('div');
    empty.className = 'history-empty';
    empty.innerHTML = `
      <div class="welcome-card"><span class="wc-icon">📎</span><div><strong>上传 PDF</strong><small>点击输入框左侧按钮</small></div></div>
      <div class="welcome-card"><span class="wc-icon">🖱️</span><div><strong>拖拽上传</strong><small>拖 PDF 到页面任意位置</small></div></div>
      <div class="welcome-card"><span class="wc-icon">💬</span><div><strong>文字提问</strong><small>直接输入问题对话</small></div></div>
    `;
    historyList.appendChild(empty);
    return;
  }

  for (const s of sessions) {
    const item = document.createElement('div');
    item.className = `history-item${s.id === currentSessionId ? ' active' : ''}`;
    item.dataset.id = s.id;

    const title = document.createElement('span');
    title.className = 'history-title';
    title.textContent = s.title;

    // 三点菜单按钮：hover 时显示，点击时弹出操作菜单
    const menuBtn = document.createElement('button');
    menuBtn.className = 'history-menu-btn';
    menuBtn.title = '更多操作';
    menuBtn.setAttribute('aria-label', '更多操作');
    menuBtn.innerHTML = `<svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="5" r="1.8"/><circle cx="12" cy="12" r="1.8"/><circle cx="12" cy="19" r="1.8"/></svg>`;
    menuBtn.addEventListener('click', e => {
      e.stopPropagation();   // 防止触发 switchToSession
      openHistoryDropdown(s.id, menuBtn, item);
    });

    item.appendChild(title);
    item.appendChild(menuBtn);
    item.addEventListener('click', () => switchToSession(s.id));
    historyList.appendChild(item);
  }
}

function restoreMessage(msg) {
  const time = msg.time || Date.now();
  if (msg.role === 'user') {
    addMessage('user', msg.content, undefined, time);
  } else if (msg.role === 'ai') {
    addMessage('ai', msg.content, undefined, time);
  }
}

function switchToSession(id) {
  const session = sessions.find(s => s.id === id);
  if (!session) return;

  currentSessionId = id;
  currentConversationId = session.conversationId || null;

  // 清空消息列表
  messagesList.innerHTML = '';
  welcome.hidden = session.messages.length > 0;
  hideProgress();
  setBusy(false);
  textInput.value = '';
  textInput.style.height = 'auto';
  sendBtn.disabled = true;

  // 恢复消息
  for (const msg of session.messages) {
    restoreMessage(msg);
  }

  renderHistoryList();
}

// ── 多文档队列管理 ────────────────────────────────────────────────────────────

function generateUUID() {
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
    const r = Math.random() * 16 | 0;
    return (c === 'x' ? r : (r & 0x3 | 0x8)).toString(16);
  });
}

function getOrCreateMultiSessionId() {
  if (!multiSessionId) multiSessionId = generateUUID();
  return multiSessionId;
}

function addToFileQueue(files) {
  for (const f of files) {
    if (!f.name.toLowerCase().endsWith('.pdf')) {
      toast(`${f.name} 不是 PDF，已跳过`);
      continue;
    }
    if (fileQueue.some(item => item.name === f.name)) {
      toast(`${f.name} 已在队列中`);
      continue;
    }
    fileQueue.push({ name: f.name, file: f, status: 'pending', party: '甲方' });
  }
  if (fileQueue.length > 0) fileQueuePanel.hidden = false;
  renderFileQueue();
  updateProcessBtn();
  updateAnalyzeBtn();
  // 不自动处理 — 等用户点击"开始文档分析"
}

function removeFromQueue(name) {
  if (busy) return;
  const idx = fileQueue.findIndex(f => f.name === name);
  if (idx === -1 || fileQueue[idx].status === 'processing') return;
  fileQueue.splice(idx, 1);
  if (fileQueue.length === 0) fileQueuePanel.hidden = true;
  renderFileQueue();
  updateProcessBtn();
  updateAnalyzeBtn();
}

function updateFqCount() {
  const total = fileQueue.length;
  if (total === 0) { fqCount.textContent = ''; return; }
  const done       = fileQueue.filter(f => f.status === 'done').length;
  const failed     = fileQueue.filter(f => f.status === 'failed').length;
  const processing = fileQueue.filter(f => f.status === 'processing').length;
  if (fqCollapsed) {
    if (done === total)       fqCount.textContent = `${total} 份 · 全部完成`;
    else if (failed > 0)      fqCount.textContent = `${total} 份 · ${failed} 个失败`;
    else if (processing > 0)  fqCount.textContent = `${total} 份 · 处理中`;
    else                      fqCount.textContent = `${total} 份`;
  } else {
    fqCount.textContent = `${total} 份`;
  }
}

function setFqCollapsed(collapsed) {
  fqCollapsed = collapsed;
  if (fqBody)   fqBody.classList.toggle('collapsed', collapsed);
  if (fqToggle) fqToggle.textContent = collapsed ? '▲' : '▼';
  updateFqCount();
}

if (fqHeader) fqHeader.addEventListener('click', () => setFqCollapsed(!fqCollapsed));

function renderFileQueue() {
  fqList.innerHTML = '';
  updateFqCount();

  const statusMap = {
    pending:    { label: '待处理',  cls: 'fq-status-pending' },
    processing: { label: '处理中',  cls: 'fq-status-processing' },
    done:       { label: '已完成',  cls: 'fq-status-done' },
    failed:     { label: '失败',    cls: 'fq-status-failed' },
  };

  for (const item of fileQueue) {
    const s = statusMap[item.status] || statusMap.pending;
    const el = document.createElement('div');
    el.className = 'fq-item';
    el.dataset.name = item.name;

    const nameEl = document.createElement('span');
    nameEl.className = 'fq-name';
    nameEl.title = item.name;
    const sizeKB = item.file ? `（${(item.file.size / 1024).toFixed(0)} KB）` : '';
    nameEl.textContent = `📄 ${item.name}${sizeKB}`;

    const statusEl = document.createElement('span');
    statusEl.className = `fq-status ${s.cls}`;
    statusEl.textContent = s.label;

    el.appendChild(nameEl);

    // 党派选择器：待处理时显示下拉框，其他状态显示标签
    if (item.status === 'pending' && !busy) {
      const partySelect = document.createElement('select');
      partySelect.className = 'fq-party-select';
      partySelect.title = '选择文件所属方';
      for (const opt of ['甲方', '乙方', '通用']) {
        const option = document.createElement('option');
        option.value = opt;
        option.textContent = opt;
        if (opt === (item.party || '甲方')) option.selected = true;
        partySelect.appendChild(option);
      }
      partySelect.addEventListener('change', () => { item.party = partySelect.value; });
      el.appendChild(partySelect);
    } else {
      const partyBadge = document.createElement('span');
      const p = item.party || '通用';
      partyBadge.className = `fq-party-badge fq-party-${p === '甲方' ? 'a' : p === '乙方' ? 'b' : 'g'}`;
      partyBadge.textContent = p;
      el.appendChild(partyBadge);
    }

    el.appendChild(statusEl);

    if (item.status === 'failed') {
      const retryBtn = document.createElement('button');
      retryBtn.className = 'fq-retry';
      retryBtn.textContent = '重试';
      retryBtn.addEventListener('click', () => retryFile(item.name));
      el.appendChild(retryBtn);
    }

    if (item.status === 'pending' && !busy) {
      const removeBtn = document.createElement('button');
      removeBtn.className = 'fq-remove';
      removeBtn.title = '移除';
      removeBtn.textContent = '×';
      removeBtn.addEventListener('click', () => removeFromQueue(item.name));
      el.appendChild(removeBtn);
    }

    fqList.appendChild(el);
  }
}

function updateFileStatus(name, status) {
  const item = fileQueue.find(f => f.name === name);
  if (item) {
    item.status = status;
    renderFileQueue();
    updateProcessBtn();
    updateAnalyzeBtn();
  }
}

function updateProcessBtn() {
  if (!processBtn) return;
  const hasPending = fileQueue.some(f => f.status === 'pending');
  if (hasPending) {
    processBtn.hidden = false;
    processBtn.disabled = busy;
    processBtn.textContent = busy ? '处理中...' : '开始文档分析';
  } else {
    processBtn.hidden = true;
  }
}

function updateAnalyzeBtn() {
  if (!analyzeBtn) return;
  const allSettled = fileQueue.length > 0 &&
    fileQueue.every(f => f.status === 'done' || f.status === 'failed');
  const hasDone = fileQueue.some(f => f.status === 'done');
  const shouldShow = allSettled && hasDone;
  analyzeBtn.hidden = !shouldShow;
  analyzeBtn.disabled = busy || !shouldShow;
  // Auto-collapse the panel once all files are done processing
  if (allSettled && fileQueue.length > 0 && !fqCollapsed) {
    setFqCollapsed(true);
  }
}

function retryFile(name) {
  if (busy) return;
  updateFileStatus(name, 'pending');
  // 不自动开始 — 等用户点"开始文档分析"
}

function processNextPending() {
  if (busy) return;
  const next = fileQueue.find(f => f.status === 'pending');
  if (!next) return;
  processPdfMulti(next.file);
}

function startProcessing() {
  if (busy) return;
  const hasPending = fileQueue.some(f => f.status === 'pending');
  if (!hasPending) return;
  processNextPending();
}

processBtn.addEventListener('click', startProcessing);

async function processPdfMulti(file) {
  const sessionId = getOrCreateMultiSessionId();

  const userMsg = `📄 ${file.name}（${(file.size / 1024).toFixed(0)} KB）`;
  const time = Date.now();
  addMessage('user', userMsg, undefined, time);
  appendToCurrentSession('user', userMsg, time);
  renderHistoryList();

  setBusy(true);
  updateFileStatus(file.name, 'processing');
  showProgress('准备上传...', 0, 0);
  progressDots.innerHTML = '';

  const queueItem = fileQueue.find(f => f.name === file.name);
  const party = queueItem ? (queueItem.party || '甲方') : '甲方';

  const form = new FormData();
  form.append('pdf_file', file);
  form.append('session_id', sessionId);
  form.append('party', party);
  if (currentConversationId) form.append('conversation_id', currentConversationId);

  const pageResults = [];
  let docDone = false;

  try {
    const res = await fetch(`${API_BASE}/pdf/pages/chat`, { method: 'POST', body: form });

    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      addMessage('ai', `❌ 上传失败（${res.status}）：${err.detail || res.statusText}`);
      updateFileStatus(file.name, 'failed');
      return;
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const parts = buf.split('\n\n');
      buf = parts.pop();

      for (const part of parts) {
        if (!part.startsWith('data: ')) continue;
        let ev;
        try { ev = JSON.parse(part.slice(6)); } catch { continue; }

        if (ev.type === 'doc_complete') {
          updateFileStatus(file.name, 'done');
          const msg = `✅ 《${file.name}》处理完成（${ev.success_pages}/${ev.total_pages} 页）`;
          addMessage('ai', msg);
          appendToCurrentSession('ai', msg, Date.now());
          saveSessions();
          docDone = true;
        } else if (ev.type === 'error') {
          addMessage('ai', `❌ 处理失败：${ev.message}`);
          updateFileStatus(file.name, 'failed');
        } else {
          handleSseEvent(ev, pageResults, `summary-${Date.now()}`, () => {});
        }
      }
    }

    if (!docDone) updateFileStatus(file.name, 'failed');

  } catch (e) {
    addMessage('ai', `❌ 处理出错：${e.message}`);
    updateFileStatus(file.name, 'failed');
  } finally {
    hideProgress();
    setBusy(false);
    setTimeout(() => processNextPending(), 200);
  }
}

analyzeBtn.addEventListener('click', startAnalysis);

// 注册所有下载按钮（party + format 组合）
const _reportBtnMap = {
  'A-docx': reportBtnWordA, 'A-pdf': reportBtnPdfA,
  'B-docx': reportBtnWordB, 'B-pdf': reportBtnPdfB,
  'G-docx': reportBtnWordG, 'G-pdf': reportBtnPdfG,
};
const _reportPartyMap = { A: '甲方', B: '乙方', G: '通用' };

if (reportBtnWordA) reportBtnWordA.addEventListener('click', () => downloadReport('甲方', 'docx'));
if (reportBtnPdfA)  reportBtnPdfA.addEventListener ('click', () => downloadReport('甲方', 'pdf'));
if (reportBtnWordB) reportBtnWordB.addEventListener('click', () => downloadReport('乙方', 'docx'));
if (reportBtnPdfB)  reportBtnPdfB.addEventListener ('click', () => downloadReport('乙方', 'pdf'));
if (reportBtnWordG) reportBtnWordG.addEventListener('click', () => downloadReport('通用', 'docx'));
if (reportBtnPdfG)  reportBtnPdfG.addEventListener ('click', () => downloadReport('通用', 'pdf'));

/**
 * 按方 + 格式下载报告。
 * @param {'甲方'|'乙方'|'通用'} party
 * @param {'pdf'|'docx'} format
 */
async function downloadReport(party, format) {
  if (!multiSessionId) { toast('请先完成综合分析'); return; }

  const keyMap = { '甲方': 'A', '乙方': 'B', '通用': 'G' };
  const key = `${keyMap[party] || 'G'}-${format}`;
  const btn = _reportBtnMap[key];
  if (btn) btn.disabled = true;
  toast('正在生成报告，请稍候...');

  const form = new FormData();
  form.append('session_id', multiSessionId);
  form.append('party', party);
  form.append('output_format', format);

  try {
    const res = await fetch(`${API_BASE}/pdf/session/report`, { method: 'POST', body: form });

    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      toast(`报告生成失败：${err.detail || res.statusText}`);
      return;
    }

    // 服务端可能降级返回 docx，以 Content-Disposition 文件名为准
    const cd = res.headers.get('Content-Disposition') || '';
    const nameMatch = cd.match(/filename[^;=\n]*=["']?([^"'\n]+)["']?/);
    const downloadName = nameMatch ? nameMatch[1] : `ebram_analysis_${party}.${format}`;

    const blob = await res.blob();
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement('a');
    a.href     = url;
    a.download = downloadName;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    toast('报告已下载');
  } catch (e) {
    toast(`报告下载失败：${e.message}`);
  } finally {
    if (btn) btn.disabled = false;
  }
}

/** 显示指定方的下载按钮 */
function showReportButtons(party) {
  const keyMap = { '甲方': 'A', '乙方': 'B', '通用': 'G' };
  const k = keyMap[party] || 'G';
  const wordBtn = _reportBtnMap[`${k}-docx`];
  const pdfBtn  = _reportBtnMap[`${k}-pdf`];
  if (wordBtn) { wordBtn.hidden = false; wordBtn.disabled = false; }
  if (pdfBtn)  { pdfBtn.hidden  = false; pdfBtn.disabled  = false; }
}

/** 隐藏所有下载按钮 */
function hideAllReportButtons() {
  for (const btn of Object.values(_reportBtnMap)) {
    if (btn) { btn.hidden = true; btn.disabled = true; }
  }
}

/**
 * 重置当前页面状态（等同于"新对话"，但不创建历史记录条目）。
 * 供右上角重置按钮使用。
 */
function resetCurrentView() {
  messagesList.innerHTML = '';
  welcome.hidden = false;
  hideProgress();
  setBusy(false);
  textInput.value = '';
  textInput.style.height = 'auto';
  sendBtn.disabled = true;
  // 重置多文档会话
  multiSessionId = null;
  fileQueue = [];
  fileQueuePanel.hidden = true;
  fqCollapsed = false;
  if (fqBody)   fqBody.classList.remove('collapsed');
  if (fqToggle) fqToggle.textContent = '▼';
  updateProcessBtn();
  updateAnalyzeBtn();
  // 重置分析状态和下载按钮
  analysisResults = {};
  conversationIdPartyA   = null;
  conversationIdPartyB   = null;
  followupConversationId = null;
  hideAllReportButtons();
  // 断开与历史会话的关联（不新增条目）
  currentConversationId = null;
  currentSessionId      = null;
}

// ── 历史记录三点下拉菜单 ─────────────────────────────────────────────────────

/** 关闭所有打开的历史下拉菜单 */
function closeAllDropdowns() {
  document.querySelectorAll('.history-dropdown').forEach(d => d.remove());
  document.querySelectorAll('.history-item.menu-open').forEach(el => el.classList.remove('menu-open'));
}

// 点击页面任意位置关闭菜单
document.addEventListener('click', e => {
  if (!e.target.closest('.history-menu-btn') && !e.target.closest('.history-dropdown')) {
    closeAllDropdowns();
  }
});

/**
 * 在指定按钮旁弹出历史操作下拉菜单。
 * @param {string} sessionId
 * @param {HTMLElement} menuBtn
 * @param {HTMLElement} historyItem
 */
function openHistoryDropdown(sessionId, menuBtn, historyItem) {
  closeAllDropdowns();

  const rect = menuBtn.getBoundingClientRect();
  const DROPDOWN_W = 140;
  const left = Math.max(4, rect.right - DROPDOWN_W);

  const dropdown = document.createElement('div');
  dropdown.className = 'history-dropdown';
  dropdown.style.top  = `${rect.bottom + 4}px`;
  dropdown.style.left = `${left}px`;

  const deleteItem = document.createElement('button');
  deleteItem.className = 'history-dropdown-item danger';
  deleteItem.innerHTML = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a1 1 0 011-1h4a1 1 0 011 1v2"/></svg>删除`;
  deleteItem.addEventListener('click', e => {
    e.stopPropagation();
    closeAllDropdowns();
    deleteSession(sessionId);
  });

  dropdown.appendChild(deleteItem);
  document.body.appendChild(dropdown);
  historyItem.classList.add('menu-open');
}

/**
 * 删除指定历史对话（含 localStorage），若删除的是当前对话则重置视图。
 * @param {string} id - 要删除的 session id
 */
function deleteSession(id) {
  if (!confirm('确定删除这条对话记录吗？')) return;
  const wasActive = (id === currentSessionId);
  sessions = sessions.filter(s => s.id !== id);
  saveSessions();
  if (wasActive) resetCurrentView();
  renderHistoryList();
}

async function startAnalysis() {
  if (!multiSessionId || busy) return;

  setBusy(true);
  showProgress('正在进行综合分析...', 0, 0);

  // 重置上一轮分析状态
  analysisResults = {};
  conversationIdPartyA = null;
  conversationIdPartyB = null;
  followupConversationId = null;
  hideAllReportButtons();

  const form = new FormData();
  form.append('session_id', multiSessionId);

  // 追踪每方是否已创建消息气泡
  const partyMsgIds = {};    // { "甲方": "analysis-甲方-ts", ... }
  const partyCreated = {};   // { "甲方": true, ... }

  try {
    const res = await fetch(`${API_BASE}/pdf/session/analyze`, { method: 'POST', body: form });

    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      addMessage('ai', `❌ 综合分析失败（${res.status}）：${err.detail || res.statusText}`);
      return;
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const parts = buf.split('\n\n');
      buf = parts.pop();

      for (const part of parts) {
        if (!part.startsWith('data: ')) continue;
        let ev;
        try { ev = JSON.parse(part.slice(6)); } catch { continue; }

        switch (ev.type) {
          case 'analysis_start': {
            const party = ev.party || '';
            showProgress(ev.message, 0, 0);
            // 为该方创建等待气泡（若尚未创建）
            if (party && !partyMsgIds[party]) {
              const msgId = `analysis-${party}-${Date.now()}`;
              partyMsgIds[party] = msgId;
              const partyLabel = party === '通用' ? '' : `${party} `;
              addMessage('ai', `⏳ 正在综合分析${partyLabel}材料，请稍候...`, msgId);
            }
            break;
          }
          case 'analysis_result': {
            const party = ev.party || '通用';
            const aiTime = Date.now();
            const content = ev.status === 'failed'
              ? `❌ ${party}综合分析失败：${ev.content}`
              : ev.content;

            // 更新或新建该方的气泡
            const msgId = partyMsgIds[party];
            if (msgId && document.getElementById(msgId)) {
              updateMessage(msgId, content);
            } else {
              const newId = `analysis-${party}-${Date.now()}`;
              partyMsgIds[party] = newId;
              addMessage('ai', content, newId, aiTime);
            }

            if (ev.status !== 'failed') {
              analysisResults[party] = content;
              showReportButtons(party);
            }
            appendToCurrentSession('ai', content, aiTime);
            saveSessions();
            partyCreated[party] = true;
            break;
          }
          case 'analysis_complete': {
            showProgress(ev.message || '综合分析完成', 1, 1);
            // 保存各方 conversation_id
            if (ev.conversation_ids) {
              conversationIdPartyA = ev.conversation_ids['甲方'] || null;
              conversationIdPartyB = ev.conversation_ids['乙方'] || null;
            }
            break;
          }
          case 'error':
            addMessage('ai', `❌ 综合分析出错：${ev.message}`);
            break;
        }
      }
    }

    // 处理未收到 result 事件的方（异常中断）
    for (const [party, msgId] of Object.entries(partyMsgIds)) {
      if (!partyCreated[party]) {
        const el = document.getElementById(msgId);
        if (el) updateMessage(msgId, `⚠️ ${party}分析未完成，请重试。`);
      }
    }

  } catch (e) {
    addMessage('ai', `❌ 综合分析出错：${e.message}`);
  } finally {
    hideProgress();
    setBusy(false);
    updateAnalyzeBtn();
  }
}

// ── Feature 2: 复制按钮 ───────────────────────────────────────────────────────

function addCopyButton(bubble) {
  // 幂等：已有则不重复添加
  if (bubble.querySelector('.copy-btn')) return;

  const btn = document.createElement('button');
  btn.className = 'copy-btn';
  btn.title = '复制';
  btn.innerHTML = COPY_ICON;

  btn.addEventListener('click', async () => {
    const text = bubble.innerText || bubble.textContent || '';
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      // 回退：execCommand
      const ta = document.createElement('textarea');
      ta.value = text;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      ta.remove();
    }
    btn.innerHTML = CHECK_ICON;
    btn.classList.add('copied');
    setTimeout(() => {
      btn.innerHTML = COPY_ICON;
      btn.classList.remove('copied');
    }, 1500);
  });

  bubble.appendChild(btn);
}

// ── 创建消息元素 ──────────────────────────────────────────────────────────────
/**
 * @param {'user'|'ai'|'system'} role
 * @param {string} content
 * @param {string} [id]
 * @param {number} [msgTime]
 * @returns {HTMLElement}
 */
function addMessage(role, content, id, msgTime = Date.now()) {
  hideWelcome();

  const wrap = document.createElement('div');
  wrap.className = `message message-${role}`;
  if (id) wrap.id = id;

  // Feature 3: 时间戳
  wrap.dataset.time = formatTime(msgTime);

  if (role !== 'system') {
    const av = document.createElement('div');
    av.className = `avatar avatar-${role}`;
    av.textContent = role === 'user' ? 'U' : '🤖';
    wrap.appendChild(av);
  }

  const bubble = document.createElement('div');
  bubble.className = 'bubble';
  setBubble(bubble, role, content);
  wrap.appendChild(bubble);

  // Feature 2: 只对 AI 非思考消息添加复制按钮
  if (role === 'ai' && content && !content.includes('thinking-dots')) {
    addCopyButton(bubble);
  }

  messagesList.appendChild(wrap);
  scrollBottom();
  return wrap;
}

function setBubble(bubble, role, content) {
  if (role === 'ai') {
    bubble.innerHTML = marked.parse(content);
  } else {
    bubble.textContent = content;
  }
}

function updateMessage(id, content, role = 'ai') {
  const wrap = document.getElementById(id);
  if (!wrap) return;
  const bubble = wrap.querySelector('.bubble');
  if (!bubble) return;

  setBubble(bubble, role, content);

  // Feature 3: 更新时间戳
  wrap.dataset.time = formatTime(Date.now());

  // Feature 2: 更新后补充复制按钮（之前是 thinking 占位）
  if (role === 'ai' && content) {
    addCopyButton(bubble);
  }

  scrollBottom();
}

function addThinking() {
  const uid = `thinking-${Date.now()}`;
  const wrap = addMessage('ai', '', uid);
  wrap.querySelector('.bubble').innerHTML =
    '<div class="thinking-dots"><span></span><span></span><span></span></div>';
  return uid;
}

// ── 进度面板 ──────────────────────────────────────────────────────────────────
function showProgress(msg, current, total) {
  progressPanel.hidden = false;
  progressStage.textContent = msg;
  if (total > 0) {
    const pct = Math.min(100, Math.round((current / total) * 100));
    progressFill.style.width = pct + '%';
    progressRatio.textContent = `${current} / ${total}`;
  } else {
    progressFill.style.width = '5%';
    progressRatio.textContent = '';
  }
}

function hideProgress() {
  progressPanel.hidden = true;
  progressFill.style.width = '0%';
  progressDots.innerHTML = '';
  progressRatio.textContent = '';
  totalPages = 0;
}

function initDots(n) {
  progressDots.innerHTML = '';
  totalPages = n;
  for (let i = 1; i <= n; i++) {
    const dot = document.createElement('span');
    dot.className = 'pdot pdot-pending';
    dot.id = `dot-${i}`;
    dot.title = `第 ${i} 页`;
    progressDots.appendChild(dot);
  }
}

function updateDot(page, status) {
  const dot = document.getElementById(`dot-${page}`);
  if (!dot) return;
  dot.className = `pdot pdot-${status}`;
  dot.title = `第 ${page} 页：${status}`;
}

// ── 从 GPTBots 响应中提取文本 ─────────────────────────────────────────────────
function extractReply(data) {
  if (!data || typeof data !== 'object') return String(data ?? '');

  if (Array.isArray(data.output) && data.output.length > 0) {
    const first = data.output[0];
    if (first && typeof first === 'object') {
      const content = first.content;
      if (content && typeof content === 'object' && typeof content.text === 'string') {
        return content.text;
      }
      if (typeof content === 'string' && content) return content;
    }
  }

  for (const k of ['answer', 'text', 'message', 'reply']) {
    if (data[k] && typeof data[k] === 'string') return data[k];
  }

  if (data.data && typeof data.data === 'object') return extractReply(data.data);

  return JSON.stringify(data, null, 2);
}

// ── 文字聊天 ──────────────────────────────────────────────────────────────────

/**
 * 判断是否应该走「综合分析追问」路径。
 * 条件：本次会话已完成综合分析（analysisResults 非空且 multiSessionId 有值）。
 */
function shouldUseAnalysisChat() {
  return multiSessionId && Object.keys(analysisResults).length > 0;
}

async function sendText(text) {
  const time = Date.now();
  addMessage('user', text, undefined, time);
  appendToCurrentSession('user', text, time);
  renderHistoryList();

  setBusy(true);
  const tid = addThinking();

  try {
    if (shouldUseAnalysisChat()) {
      // ── 综合分析追问：路由到 /pdf/session/chat（Agent B）────────────
      const form = new FormData();
      form.append('session_id', multiSessionId);
      form.append('text', text);
      if (followupConversationId) form.append('followup_conversation_id', followupConversationId);

      const res = await fetch(`${API_BASE}/pdf/session/chat`, { method: 'POST', body: form });

      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        updateMessage(tid, `❌ 请求失败（${res.status}）：${err.detail || res.statusText}`);
        return;
      }

      const data = await res.json();
      if (data.conversation_id) followupConversationId = data.conversation_id;

      const aiTime = Date.now();
      updateMessage(tid, data.reply || '（无回复）');
      appendToCurrentSession('ai', data.reply || '', aiTime);
      saveSessions();

    } else {
      // ── 普通文字聊天：路由到 /chat（Agent A）────────────────────────
      const res = await fetch(`${API_BASE}/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          text,
          user_id: 'web_user',
          conversation_id: currentConversationId,
        }),
      });

      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        updateMessage(tid, `❌ 请求失败（${res.status}）：${err.detail || res.statusText}`);
        return;
      }

      const data = await res.json();
      if (data.conversation_id) updateSessionConversationId(data.conversation_id);

      const reply = extractReply(data.message_response) || extractReply(data);
      const aiTime = Date.now();
      updateMessage(tid, reply);
      appendToCurrentSession('ai', reply, aiTime);
      saveSessions();
    }

  } catch (e) {
    updateMessage(tid, `❌ 网络错误：${e.message}`);
  } finally {
    setBusy(false);
  }
}

// ── PDF 处理（SSE 流）────────────────────────────────────────────────────────
async function processPdf(file) {
  if (!file.name.toLowerCase().endsWith('.pdf')) {
    toast('仅支持 PDF 文件');
    return;
  }

  const userMsg = `📄 ${file.name}（${(file.size / 1024).toFixed(0)} KB）`;
  const time = Date.now();
  addMessage('user', userMsg, undefined, time);
  appendToCurrentSession('user', userMsg, time);
  renderHistoryList();

  setBusy(true);
  showProgress('准备上传...', 0, 0);
  progressDots.innerHTML = '';

  const form = new FormData();
  form.append('pdf_file', file);
  if (currentConversationId) {
    form.append('conversation_id', currentConversationId);
  }

  const pageResults = [];
  const summaryId = `summary-${Date.now()}`;
  let summaryCreated = false;

  try {
    const res = await fetch(`${API_BASE}/pdf/pages/chat`, {
      method: 'POST',
      body: form,
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      addMessage('ai', `❌ 上传失败（${res.status}）：${err.detail || res.statusText}`);
      return;
    }

    const reader  = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buf += decoder.decode(value, { stream: true });

      const parts = buf.split('\n\n');
      buf = parts.pop();

      for (const part of parts) {
        if (!part.startsWith('data: ')) continue;
        let ev;
        try { ev = JSON.parse(part.slice(6)); }
        catch { continue; }

        handleSseEvent(ev, pageResults, summaryId, () => {
          summaryCreated = true;
        });
      }
    }

    if (!summaryCreated) {
      // Agent B 结果未到达（异常中断）时更新占位消息
      const placeholder = document.getElementById(summaryId);
      if (placeholder) {
        updateMessage(summaryId, '⚠️ 综合分析未完成，请重试。');
      }
    }

  } catch (e) {
    addMessage('ai', `❌ 处理出错：${e.message}`);
  } finally {
    hideProgress();
    setBusy(false);
  }
}

function handleSseEvent(ev, pageResults, summaryId, onSummaryCreated) {
  switch (ev.type) {

    case 'stage':
      showProgress(ev.message, 0, 0);
      break;

    case 'start':
      showProgress(ev.message, 0, ev.total_pages);
      initDots(ev.total_pages);
      break;

    case 'ocr_done':
      showProgress(ev.message, ev.success, ev.success + ev.failed);
      break;

    case 'progress':
      if (ev.stage === 'ocr') {
        showProgress(ev.message, ev.extracted ?? 0, ev.total ?? 0);
        // OCR 阶段无单页概念，不调用 updateDot
      } else {
        // stage === 'agent'
        showProgress(ev.message, ev.page - 1, ev.total);
        updateDot(ev.page, 'processing');
      }
      break;

    case 'page_result': {
      pageResults.push(ev);
      updateDot(ev.page, ev.status);
      const done = pageResults.length;
      showProgress(
        ev.status === 'success'
          ? `第 ${ev.page} 页分析完成`
          : `第 ${ev.page} 页处理失败（${ev.status}）`,
        done,
        totalPages
      );
      break;
    }

    case 'agent_b_start':
      showProgress(ev.message, 0, 0);
      addMessage('ai', '⏳ 正在进行综合分析，请稍候...', summaryId);
      break;

    case 'agent_b_result': {
      const aiTime = Date.now();
      const content = ev.status === 'failed'
        ? `❌ 综合分析失败：${ev.content}`
        : ev.content;
      updateMessage(summaryId, content);
      appendToCurrentSession('ai', content, aiTime);
      saveSessions();
      onSummaryCreated();
      break;
    }

    case 'complete':
      showProgress(ev.message, ev.total_pages, ev.total_pages);
      if (ev.conversation_id) {
        updateSessionConversationId(ev.conversation_id);
      }
      break;

    case 'error':
      addMessage('ai', `❌ 处理失败：${ev.message}`);
      break;
  }
}

function renderSummary(results, filename, id) {
  if (!results || results.length === 0) return;

  const total   = results.length;
  const success = results.filter(r => r.status === 'success').length;

  let md = '';
  if (filename) md += `## 📄 ${filename}\n\n`;
  md += `共 **${total}** 页，成功分析 **${success}** 页。\n\n---\n\n`;

  for (const r of results) {
    if (r.status === 'success') {
      md += `### 第 ${r.page} 页\n\n${r.agent_response}\n\n---\n\n`;
    } else {
      const label = r.status === 'ocr_failed' ? 'OCR 识别失败' : 'AI 分析失败';
      md += `### 第 ${r.page} 页 ⚠️ ${label}\n\n`;
      if (r.error) md += `> ${r.error}\n\n`;
      md += `---\n\n`;
    }
  }

  const aiTime = Date.now();
  if (document.getElementById(id)) {
    updateMessage(id, md);
  } else {
    addMessage('ai', md, id, aiTime);
  }

  appendToCurrentSession('ai', md, aiTime);
  saveSessions();
}

// ── 输入框自动撑高 ────────────────────────────────────────────────────────────
textInput.addEventListener('input', () => {
  textInput.style.height = 'auto';
  textInput.style.height = Math.min(textInput.scrollHeight, 200) + 'px';
  sendBtn.disabled = busy || textInput.value.trim().length === 0;
});

textInput.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    if (!sendBtn.disabled) doSend();
  }
});

sendBtn.addEventListener('click', doSend);

function doSend() {
  const text = textInput.value.trim();
  if (!text || busy) return;
  textInput.value = '';
  textInput.style.height = 'auto';
  sendBtn.disabled = true;
  sendText(text);
}

// ── 文件选择上传 ──────────────────────────────────────────────────────────────
fileInput.addEventListener('change', e => {
  const files = Array.from(e.target.files || []);
  if (files.length > 0) addToFileQueue(files);
  fileInput.value = '';
});

// ── 拖拽上传 ──────────────────────────────────────────────────────────────────
document.addEventListener('dragenter', e => {
  e.preventDefault();
  if ([...e.dataTransfer.types].includes('Files')) {
    dragCount++;
    dragOverlay.hidden = false;
  }
});

document.addEventListener('dragleave', () => {
  dragCount = Math.max(0, dragCount - 1);
  if (dragCount === 0) dragOverlay.hidden = true;
});

document.addEventListener('dragover', e => e.preventDefault());

document.addEventListener('drop', e => {
  e.preventDefault();
  dragCount = 0;
  dragOverlay.hidden = true;

  const files = [...e.dataTransfer.files].filter(
    f => f.type === 'application/pdf' || f.name.toLowerCase().endsWith('.pdf')
  );
  if (files.length === 0) {
    toast('请上传 PDF 文件');
  } else {
    addToFileQueue(files);
  }
});

// ── 新对话按钮 ────────────────────────────────────────────────────────────────
newChatBtn.addEventListener('click', () => {
  messagesList.innerHTML = '';
  welcome.hidden = false;
  hideProgress();
  setBusy(false);
  textInput.value = '';
  textInput.style.height = 'auto';
  sendBtn.disabled = true;
  // 重置多文档会话状态
  multiSessionId = null;
  fileQueue = [];
  fileQueuePanel.hidden = true;
  fqCollapsed = false;
  if (fqBody) fqBody.classList.remove('collapsed');
  if (fqToggle) fqToggle.textContent = '▼';
  updateProcessBtn();
  updateAnalyzeBtn();
  // 重置分析状态和下载按钮
  analysisResults = {};
  conversationIdPartyA = null;
  conversationIdPartyB = null;
  followupConversationId = null;
  hideAllReportButtons();
  createNewSession();
  renderHistoryList();
});

// ── 初始化 ────────────────────────────────────────────────────────────────────
loadSessions();
if (sessions.length > 0) {
  switchToSession(sessions[0].id);
} else {
  createNewSession();
  renderHistoryList();
}
