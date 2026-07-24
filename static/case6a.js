/** eBRAM Case 6A — 官网服务指导助手。 */

'use strict';

const HISTORY_KEY = 'ebram_c6a_history';
const MAX_SESSIONS = 50;
const SESSION_TTL_MS = 2 * 60 * 60 * 1000;
const MAX_MESSAGE_CHARS = 4000;

const messagesWrap = document.getElementById('messagesWrap');
const messagesList = document.getElementById('messagesList');
const welcome = document.getElementById('welcome');
const textInput = document.getElementById('textInput');
const sendBtn = document.getElementById('sendBtn');
const assistantStatus = document.getElementById('assistantStatus');
const historyList = document.getElementById('historyList');
const newChatBtn = document.getElementById('newChatBtn');
const resetConversationBtn = document.getElementById('resetConversationBtn');
const charCount = document.getElementById('charCount');
const sidebar = document.getElementById('sidebar');
const sidebarToggle = document.getElementById('sidebarToggle');
const sidebarOverlay = document.getElementById('sidebarOverlay');
const mobileResetBtn = document.getElementById('mobileResetBtn');

let sessions = [];
let currentSessionId = null;
let busy = false;

if (window.marked) marked.use({ breaks: true, gfm: true });

function generateLocalId() {
  return `c6_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
}

function normalizeSessions(value) {
  if (!Array.isArray(value)) return [];
  return value.filter(item => item && typeof item.id === 'string').map(item => ({
    id: item.id,
    backendSessionId: typeof item.backendSessionId === 'string' ? item.backendSessionId : null,
    title: typeof item.title === 'string' ? item.title : '新对话',
    createdAt: Number(item.createdAt) || Date.now(),
    updatedAt: Number(item.updatedAt) || Number(item.createdAt) || Date.now(),
    expired: Boolean(item.expired),
    messages: Array.isArray(item.messages)
      ? item.messages.filter(message => message && ['user', 'ai'].includes(message.role) && typeof message.content === 'string')
      : [],
  }));
}

function loadSessions() {
  try {
    sessions = normalizeSessions(JSON.parse(localStorage.getItem(HISTORY_KEY) || '[]'));
  } catch {
    sessions = [];
  }
  const now = Date.now();
  for (const session of sessions) {
    if (session.backendSessionId && now - session.updatedAt > SESSION_TTL_MS) session.expired = true;
  }
}

function saveSessions() {
  sessions = sessions.slice(0, MAX_SESSIONS);
  try { localStorage.setItem(HISTORY_KEY, JSON.stringify(sessions)); } catch { /* storage may be unavailable */ }
}

function getCurrentSession() {
  return sessions.find(session => session.id === currentSessionId) || null;
}

function createLocalSession() {
  const session = {
    id: generateLocalId(),
    backendSessionId: null,
    title: '新对话',
    createdAt: Date.now(),
    updatedAt: Date.now(),
    expired: false,
    messages: [],
  };
  sessions.unshift(session);
  currentSessionId = session.id;
  saveSessions();
  return session;
}

function appendStoredMessage(role, content, time = Date.now()) {
  const session = getCurrentSession();
  if (!session) return;
  session.messages.push({ role, content, time });
  session.updatedAt = time;
  if (role === 'user' && session.title === '新对话') {
    session.title = content.replace(/[\n\r]+/g, ' ').trim().slice(0, 22) || '新对话';
  }
  saveSessions();
}

function formatDisplayTime(timestamp) {
  return typeof formatTime === 'function' ? formatTime(timestamp) : '';
}

function createAvatar(role) {
  const avatar = document.createElement('div');
  avatar.className = `avatar ${role === 'user' ? 'avatar-user' : 'avatar-ai'}`;
  if (role === 'user') {
    avatar.textContent = 'U';
  } else {
    avatar.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M6 3h12v18l-6-4-6 4V3Z"/></svg>';
  }
  return avatar;
}

function isOfficialUrl(rawUrl) {
  try {
    const url = new URL(rawUrl, window.location.origin);
    const host = url.hostname.toLowerCase();
    return url.protocol === 'https:' && (host === 'ebram.org' || host.endsWith('.ebram.org'));
  } catch {
    return false;
  }
}

function renderSafeMarkdown(content) {
  const wrapper = document.createElement('div');
  if (!window.marked || !window.DOMPurify) {
    wrapper.textContent = content;
    return wrapper;
  }
  const rendered = marked.parse(content);
  wrapper.innerHTML = DOMPurify.sanitize(rendered, {
    USE_PROFILES: { html: true },
    FORBID_TAGS: ['style', 'form', 'input', 'button', 'iframe', 'object', 'embed'],
    FORBID_ATTR: ['style'],
  });
  wrapper.querySelectorAll('a').forEach(anchor => {
    if (!isOfficialUrl(anchor.href)) {
      anchor.replaceWith(document.createTextNode(anchor.textContent || anchor.href));
      return;
    }
    anchor.target = '_blank';
    anchor.rel = 'noopener noreferrer';
  });
  return wrapper;
}

function addMessage(role, content, time = Date.now(), options = {}) {
  welcome.hidden = true;
  const message = document.createElement('article');
  message.className = `message message-${role === 'user' ? 'user' : 'ai'}${options.error ? ' message-error' : ''}`;
  message.dataset.time = formatDisplayTime(time);

  const bubble = document.createElement('div');
  bubble.className = 'bubble';
  if (role === 'user') {
    bubble.textContent = content;
  } else {
    bubble.appendChild(renderSafeMarkdown(content));
    const copyButton = document.createElement('button');
    copyButton.className = 'copy-btn';
    copyButton.type = 'button';
    copyButton.title = '复制回答';
    copyButton.setAttribute('aria-label', '复制回答');
    copyButton.innerHTML = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>';
    copyButton.addEventListener('click', async () => {
      try {
        await navigator.clipboard.writeText(content);
        copyButton.classList.add('copied');
        copyButton.textContent = '✓';
        setTimeout(() => {
          copyButton.classList.remove('copied');
          copyButton.innerHTML = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>';
        }, 1400);
      } catch {
        toast('无法复制，请手动选择文字');
      }
    });
    bubble.appendChild(copyButton);
  }

  message.append(createAvatar(role), bubble);
  messagesList.appendChild(message);
  requestAnimationFrame(() => { messagesWrap.scrollTop = messagesWrap.scrollHeight; });
}

function renderCurrentSession() {
  const session = getCurrentSession();
  messagesList.innerHTML = '';
  welcome.hidden = Boolean(session && session.messages.length);
  if (!session) return;
  for (const message of session.messages) addMessage(message.role, message.content, message.time);
  if (session.expired && session.messages.length) {
    addMessage('ai', '此历史对话已过期。请点击“新对话”继续咨询。', Date.now(), { error: true });
  }
  renderHistory();
}

function renderHistory() {
  historyList.innerHTML = '';
  if (!sessions.length) {
    const empty = document.createElement('div');
    empty.className = 'history-empty';
    empty.textContent = '尚无咨询记录';
    historyList.appendChild(empty);
    return;
  }

  for (const session of sessions) {
    const item = document.createElement('div');
    item.className = `history-item${session.id === currentSessionId ? ' active' : ''}`;
    const title = document.createElement('span');
    title.className = 'history-title';
    title.textContent = session.title;
    title.title = session.title;
    const menu = createHistoryMenu({
      label: session.title,
      isDisabled: () => busy,
      onDelete: () => removeSession(session.id),
    });
    item.append(title, menu);
    item.addEventListener('click', () => switchSession(session.id));
    historyList.appendChild(item);
  }
}

async function removeSession(id) {
  const session = sessions.find(item => item.id === id);
  sessions = sessions.filter(item => item.id !== id);
  if (currentSessionId === id) {
    currentSessionId = sessions[0]?.id || createLocalSession().id;
  }
  saveSessions();
  renderCurrentSession();
  if (session && session.backendSessionId) {
    try {
      const response = await fetch(
        `/case6a/session/${encodeURIComponent(session.backendSessionId)}`,
        { method: 'DELETE' },
      );
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
    } catch {
      toast('对话已从本地删除；服务端缓存将在两小时内自动清理');
    }
  }
}

function switchSession(id) {
  if (busy) return;
  if (!sessions.some(item => item.id === id)) return;
  currentSessionId = id;
  renderCurrentSession();
  closeSidebar();
  textInput.focus();
}

function startNewConversation() {
  if (busy) return;
  createLocalSession();
  renderCurrentSession();
  closeSidebar();
  textInput.value = '';
  updateComposer();
  textInput.focus();
}

async function resetCurrentConversation() {
  if (busy) return;
  const session = getCurrentSession();
  if (!session) return;
  const hasContent = session.messages.length > 0 || Boolean(session.backendSessionId);
  if (hasContent && !confirm('确定重置当前对话吗？当前消息和服务上下文将被清空。')) return;

  const backendSessionId = session.backendSessionId;
  session.messages = [];
  session.title = '新对话';
  session.backendSessionId = null;
  session.expired = false;
  session.updatedAt = Date.now();
  saveSessions();
  renderCurrentSession();
  closeSidebar();
  textInput.value = '';
  updateComposer();
  textInput.focus();

  if (backendSessionId) {
    try {
      const response = await fetch(
        `/case6a/session/${encodeURIComponent(backendSessionId)}`,
        { method: 'DELETE' },
      );
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
    } catch {
      toast('当前对话已重置；服务端缓存将在两小时内自动清理');
      return;
    }
  }
  toast('当前对话已重置');
}

async function requestJson(url, options) {
  const response = await fetch(url, options);
  let payload = {};
  try { payload = await response.json(); } catch { /* empty response */ }
  if (!response.ok) {
    const detail = payload.detail || {};
    const error = new Error(detail.message || '服务请求失败，请稍后重试。');
    error.code = detail.code || 'REQUEST_FAILED';
    error.status = response.status;
    throw error;
  }
  return payload;
}

async function ensureBackendSession(session) {
  if (session.backendSessionId && !session.expired) return session.backendSessionId;
  const created = await requestJson('/case6a/session', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: '{}',
  });
  session.backendSessionId = created.session_id;
  session.expired = false;
  session.updatedAt = Date.now();
  saveSessions();
  return session.backendSessionId;
}

function setBusy(value) {
  busy = value;
  textInput.disabled = value;
  assistantStatus.hidden = !value;
  resetConversationBtn.disabled = value;
  mobileResetBtn.disabled = value;
  updateComposer();
}

function errorMessage(error) {
  if (error.code === 'SESSION_EXPIRED') return '此对话已过期。历史内容仍保留，请开启新对话继续咨询。';
  if (error.code === 'SESSION_BUSY') return '上一条问题仍在处理中，请稍候。';
  if (error.code === 'CASE6A_NOT_CONFIGURED') return '服务指导助手暂未配置，请联系管理员。';
  return error.message || '暂时无法取得回答，请稍后重试。';
}

async function sendQuestion(prefilled) {
  if (busy) return;
  const message = (typeof prefilled === 'string' ? prefilled : textInput.value).trim();
  if (!message) return;
  let session = getCurrentSession() || createLocalSession();
  if (session.expired) {
    session = createLocalSession();
    currentSessionId = session.id;
    renderCurrentSession();
  }

  addMessage('user', message);
  appendStoredMessage('user', message);
  textInput.value = '';
  updateComposer();
  renderHistory();
  setBusy(true);

  try {
    const backendSessionId = await ensureBackendSession(session);
    const result = await requestJson('/case6a/session/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: backendSessionId, message }),
    });
    addMessage('ai', result.reply);
    appendStoredMessage('ai', result.reply);
  } catch (error) {
    if (error.code === 'SESSION_EXPIRED') {
      session.expired = true;
      session.backendSessionId = null;
      saveSessions();
    }
    addMessage('ai', errorMessage(error), Date.now(), { error: true });
  } finally {
    setBusy(false);
    textInput.focus();
  }
}

function updateComposer() {
  const length = textInput.value.length;
  sendBtn.disabled = busy || textInput.value.trim().length === 0;
  charCount.textContent = length >= 3200 ? `${length}/${MAX_MESSAGE_CHARS}` : '';
  textInput.style.height = 'auto';
  textInput.style.height = `${Math.min(textInput.scrollHeight, 180)}px`;
}

function openSidebar() {
  sidebar.classList.add('open');
  sidebarOverlay.hidden = false;
  sidebarToggle.setAttribute('aria-expanded', 'true');
}

function closeSidebar() {
  sidebar.classList.remove('open');
  sidebarOverlay.hidden = true;
  sidebarToggle.setAttribute('aria-expanded', 'false');
}

textInput.addEventListener('input', updateComposer);
textInput.addEventListener('keydown', event => {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    sendQuestion();
  }
});
sendBtn.addEventListener('click', () => sendQuestion());
newChatBtn.addEventListener('click', startNewConversation);
resetConversationBtn.addEventListener('click', resetCurrentConversation);
mobileResetBtn.addEventListener('click', resetCurrentConversation);
document.querySelectorAll('.c6-suggestion').forEach(button => {
  button.addEventListener('click', () => sendQuestion(button.dataset.question));
});
sidebarToggle.addEventListener('click', () => sidebar.classList.contains('open') ? closeSidebar() : openSidebar());
sidebarOverlay.addEventListener('click', closeSidebar);
document.addEventListener('keydown', event => { if (event.key === 'Escape') closeSidebar(); });

loadSessions();
if (!sessions.length) createLocalSession();
currentSessionId = sessions[0].id;
renderCurrentSession();
updateComposer();
