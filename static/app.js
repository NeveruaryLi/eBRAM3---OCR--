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

// ── 状态 ──────────────────────────────────────────────────────────────────────
let busy = false;
let dragCount = 0;
let totalPages = 0;
let currentConversationId = null;

// Feature 1: 会话状态
let sessions = [];          // Session[]
let currentSessionId = null;

// 多文档队列状态
let multiSessionId = null;  // 后端 session_store key（UUID）
let fileQueue = [];         // [{name, file, status: 'pending'|'processing'|'done'|'failed'}]

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

    item.appendChild(title);
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
    fileQueue.push({ name: f.name, file: f, status: 'pending' });
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

function renderFileQueue() {
  fqList.innerHTML = '';
  fqCount.textContent = `${fileQueue.length} 份`;

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

  const form = new FormData();
  form.append('pdf_file', file);
  form.append('session_id', sessionId);
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

async function startAnalysis() {
  if (!multiSessionId || busy) return;

  setBusy(true);
  showProgress('正在进行综合分析...', 0, 0);

  const form = new FormData();
  form.append('session_id', multiSessionId);
  if (currentConversationId) form.append('agent_b_conversation_id', currentConversationId);

  const summaryId = `analysis-${Date.now()}`;
  let analysisCreated = false;

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
          case 'analysis_start':
            showProgress(ev.message, 0, ev.doc_count + 1);
            addMessage('ai', `⏳ 正在综合分析 ${ev.doc_count} 份材料，请稍候...`, summaryId);
            break;
          case 'analysis_sending':
            showProgress(ev.message, ev.doc_index - 1, ev.doc_count + 1);
            break;
          case 'analysis_result': {
            const aiTime = Date.now();
            const content = ev.status === 'failed'
              ? `❌ 综合分析失败：${ev.content}`
              : ev.content;
            updateMessage(summaryId, content);
            appendToCurrentSession('ai', content, aiTime);
            saveSessions();
            analysisCreated = true;
            break;
          }
          case 'analysis_complete':
            showProgress(ev.message, ev.doc_count, ev.doc_count);
            if (ev.conversation_id) {
              updateSessionConversationId(ev.conversation_id);
            }
            break;
          case 'error':
            addMessage('ai', `❌ 综合分析出错：${ev.message}`);
            break;
        }
      }
    }

    if (!analysisCreated) {
      const placeholder = document.getElementById(summaryId);
      if (placeholder) updateMessage(summaryId, '⚠️ 综合分析未完成，请重试。');
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
async function sendText(text) {
  const time = Date.now();
  addMessage('user', text, undefined, time);
  appendToCurrentSession('user', text, time);
  renderHistoryList();

  setBusy(true);
  const tid = addThinking();

  try {
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

    if (data.conversation_id) {
      updateSessionConversationId(data.conversation_id);
    }

    const reply = extractReply(data.message_response) || extractReply(data);
    const aiTime = Date.now();
    updateMessage(tid, reply);
    appendToCurrentSession('ai', reply, aiTime);
    saveSessions();

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
  updateProcessBtn();
  updateAnalyzeBtn();
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
