/**
 * eBRAM AI 文档助手 — Case 5 HKLII 案例检索
 *
 * 核心流程：
 *   1. 用户输入关键词 → POST /case5/scrape（SSE）→ 预览 10 条案例卡片
 *   2. 点击"开始生成摘要" → POST /pdf/session/analyze（SSE）→ Agent J 回复
 *   3. 追问 → POST /pdf/session/chat（复用 analyze conversation_id）
 *   4. 下载 → POST /pdf/session/report
 *
 * 与 Case 2 的差异：
 *   - 无文件上传 / 文件队列 / 拖拽功能
 *   - 关键词搜索区取代文件上传区
 *   - analyzeBtn 由 scrape_complete 后激活（而非文件处理完成后）
 *
 * 依赖：common.js（主题管理、toast、generateId、formatTime）
 */

'use strict';

// ── 配置 ──────────────────────────────────────────────────────────────────────
const API_BASE    = '';
const HISTORY_KEY = 'ebram_c5_history';
const MAX_SESSIONS = 50;

// ── SVG 图标常量 ──────────────────────────────────────────────────────────────
const COPY_ICON  = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/></svg>`;
const CHECK_ICON = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>`;

// ── DOM 引用 ──────────────────────────────────────────────────────────────────
const messagesWrap  = document.getElementById('messagesWrap');
const messagesList  = document.getElementById('messagesList');
const welcome       = document.getElementById('welcome');
const textInput     = document.getElementById('textInput');
const sendBtn       = document.getElementById('sendBtn');
const progressPanel = document.getElementById('progressPanel');
const progressStage = document.getElementById('progressStage');
const progressRatio = document.getElementById('progressRatio');
const progressFill  = document.getElementById('progressFill');
const progressDots  = document.getElementById('progressDots');
const newChatBtn    = document.getElementById('newChatBtn');
const resetConversationBtn = document.getElementById('resetConversationBtn');
const historyList   = document.getElementById('historyList');

// 搜索区
const keywordInput   = document.getElementById('keywordInput');
const searchBtn      = document.getElementById('searchBtn');
const resultsList    = document.getElementById('resultsList');
const analyzeBtn     = document.getElementById('analyzeBtn');
const c5WordBtn      = document.getElementById('c5WordBtn');
const c5PdfBtn       = document.getElementById('c5PdfBtn');
const searchBody     = document.getElementById('searchBody');
const searchTitle    = document.getElementById('searchTitle');
const compactKeyword = document.getElementById('compactKeyword');
const searchToggle   = document.getElementById('searchToggle');

// ── 状态 ──────────────────────────────────────────────────────────────────────
let busy = false;
let currentConversationId = null;

// 会话状态
let sessions = [];
let currentSessionId = null;

// Case 5 专属状态
let scrapeSessionId = null;         // 由 /case5/scrape 创建后写入 session_store 的 ID
let scrapedResults  = [];           // 预览数据（来自 scrape_complete 事件）
let summaryResult   = null;         // Agent J 摘要文本
let followupConversationId = null;  // 追问 conversation_id
let isSearchCollapsed = false;      // 搜索区折叠状态

// ── marked.js ─────────────────────────────────────────────────────────────────
marked.use({ breaks: true, gfm: true });

// ── 工具函数 ──────────────────────────────────────────────────────────────────
function hideWelcome() {
  if (welcome && !welcome.hidden) welcome.hidden = true;
}

function scrollBottom() {
  requestAnimationFrame(() => { messagesWrap.scrollTop = messagesWrap.scrollHeight; });
}

function generateUUID() {
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
    const r = Math.random() * 16 | 0;
    return (c === 'x' ? r : (r & 0x3 | 0x8)).toString(16);
  });
}

function getOrCreateScrapeSessionId() {
  if (!scrapeSessionId) scrapeSessionId = generateUUID();
  const session = getCurrentSession();
  if (session) {
    session.backendSessionId = scrapeSessionId;
    saveSessions();
  }
  return scrapeSessionId;
}

// ── 忙碌状态 ──────────────────────────────────────────────────────────────────
function setBusy(val) {
  busy = val;
  sendBtn.disabled  = val || textInput.value.trim().length === 0;
  textInput.disabled = val;
  searchBtn.disabled = val;
  keywordInput.disabled = val;
  resetConversationBtn.disabled = val;
  updateAnalyzeBtnState();
}

function updateAnalyzeBtnState() {
  // analyzeBtn 仅在有预览结果且非忙碌时可用
  if (analyzeBtn) {
    const hasResults = scrapedResults.length > 0;
    analyzeBtn.hidden = !hasResults;
    analyzeBtn.disabled = busy || !hasResults;
  }
}

// ── 会话管理 ──────────────────────────────────────────────────────────────────
function loadSessions() {
  try {
    const raw = localStorage.getItem(HISTORY_KEY);
    sessions = raw ? JSON.parse(raw) : [];
    if (!Array.isArray(sessions)) sessions = [];
  } catch { sessions = []; }
}

function saveSessions() {
  if (sessions.length > MAX_SESSIONS) sessions = sessions.slice(0, MAX_SESSIONS);
  try { localStorage.setItem(HISTORY_KEY, JSON.stringify(sessions)); } catch {}
}

function getCurrentSession() {
  return sessions.find(s => s.id === currentSessionId) || null;
}

function createNewSession() {
  const id = generateId();
  const session = { id, conversationId: null, title: '新对话', createdAt: Date.now(), messages: [] };
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
  if (role === 'user' && session.title === '新对话') {
    session.title = content.replace(/[\n\r]/g, ' ').slice(0, 20) || '新对话';
  }
  saveSessions();
}

function updateSessionConversationId(convId) {
  if (!convId) return;
  currentConversationId = convId;
  const session = getCurrentSession();
  if (session) { session.conversationId = convId; saveSessions(); }
}

function renderHistoryList() {
  historyList.innerHTML = '';

  if (sessions.length === 0) {
    const empty = document.createElement('div');
    empty.className = 'history-empty';
    empty.innerHTML = `
      <div class="welcome-card"><span class="wc-icon">🔍</span><div><strong>搜索案例</strong><small>输入法律关键词</small></div></div>
      <div class="welcome-card"><span class="wc-icon">📋</span><div><strong>生成摘要</strong><small>AI 自动整理案例要点</small></div></div>
      <div class="welcome-card"><span class="wc-icon">💬</span><div><strong>深度追问</strong><small>针对案例细节对话</small></div></div>
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
    title.title = s.title;
    const menuBtn = createHistoryMenu({
      label: s.title,
      isDisabled: () => busy,
      onDelete: () => deleteSession(s.id),
    });

    item.appendChild(title);
    item.appendChild(menuBtn);
    item.addEventListener('click', () => switchToSession(s.id));
    historyList.appendChild(item);
  }
}

function restoreMessage(msg) {
  const time = msg.time || Date.now();
  if (msg.role === 'user') addMessage('user', msg.content, undefined, time);
  else if (msg.role === 'ai') addMessage('ai', msg.content, undefined, time);
}

function switchToSession(id) {
  if (busy) return;
  if (id === currentSessionId) return;
  const session = sessions.find(s => s.id === id);
  if (!session) return;
  resetCurrentView({ preserveHistory: true });
  currentSessionId = id;
  currentConversationId = session.conversationId || null;
  scrapeSessionId = session.backendSessionId || null;
  welcome.hidden = session.messages.length > 0;
  for (const msg of session.messages) restoreMessage(msg);
  renderHistoryList();
}

// ── 历史记录三点菜单 ──────────────────────────────────────────────────────────
function closeAllDropdowns() {
  document.querySelectorAll('.history-dropdown').forEach(d => d.remove());
  document.querySelectorAll('.history-item.menu-open').forEach(el => el.classList.remove('menu-open'));
}

document.addEventListener('click', e => {
  if (!e.target.closest('.history-menu-btn') && !e.target.closest('.history-dropdown')) {
    closeAllDropdowns();
  }
});

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

async function deleteSession(id) {
  if (busy) return;
  const target = sessions.find(s => s.id === id);
  const backendSessionId = target?.backendSessionId || null;
  const wasActive = (id === currentSessionId);
  sessions = sessions.filter(s => s.id !== id);
  saveSessions();
  if (wasActive) {
    resetCurrentView();
    if (sessions.length) switchToSession(sessions[0].id);
    else createNewSession();
  }
  renderHistoryList();
  if (backendSessionId) {
    try {
      const response = await fetch(`/pdf/session/${encodeURIComponent(backendSessionId)}`, {
        method: 'DELETE',
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
    } catch {
      toast('对话已从本地删除；服务端缓存将在两小时内自动清理');
    }
  }
}

// ── Case 5：关键词搜索 ────────────────────────────────────────────────────────

/**
 * 渲染案例预览列表（scrape_complete 后调用）。
 * @param {Array} results  [{index, title, doc_type, date, url, content_length}]
 * @param {string} keyword 搜索关键词
 */
function renderResultsPreview(results, keyword) {
  resultsList.innerHTML = '';
  if (!results || results.length === 0) {
    resultsList.hidden = true;
    return;
  }

  for (const r of results) {
    const card = document.createElement('div');
    card.className = 'c5-result-card';

    const badge = document.createElement('span');
    badge.className = `c5-type-badge c5-type-${r.doc_type === 'case' ? 'case' : 'legis'}`;
    badge.textContent = r.doc_type || 'unknown';

    const indexSpan = document.createElement('span');
    indexSpan.className = 'c5-result-index';
    indexSpan.textContent = `#${r.index}`;

    const titleEl = document.createElement('a');
    titleEl.className = 'c5-result-title';
    titleEl.href = r.url;
    titleEl.target = '_blank';
    titleEl.rel = 'noopener noreferrer';
    titleEl.textContent = r.title || '（无标题）';

    const meta = document.createElement('div');
    meta.className = 'c5-result-meta';
    const chars = r.content_length > 0
      ? `${r.content_length.toLocaleString()} chars`
      : 'N/A (scraping failed)';
    meta.textContent = `${r.date || '—'}  ·  ${chars}`;

    card.appendChild(indexSpan);
    card.appendChild(badge);
    card.appendChild(titleEl);
    card.appendChild(meta);
    resultsList.appendChild(card);
  }

  resultsList.hidden = false;
  updateAnalyzeBtnState();
}

// ── Case 5：搜索区折叠面板 ────────────────────────────────────────────────────

/**
 * 折叠搜索区，显示紧凑的关键词摘要行。
 * @param {string} keyword 搜索关键词（显示在折叠行）
 */
function collapseSearchPanel(keyword) {
  isSearchCollapsed = true;
  // 使用 CSS class 驱动过渡动画（而非 hidden 属性，hidden 会跳过 max-height 过渡）
  if (searchBody) searchBody.classList.add('collapsed');
  if (searchTitle)    searchTitle.hidden = true;
  if (compactKeyword) {
    compactKeyword.textContent = `⚖️ 搜索: "${keyword}"`;
    compactKeyword.hidden = false;
  }
  if (searchToggle) {
    searchToggle.textContent = '▼ 展开';
    searchToggle.title = '展开搜索区';
  }
}

/**
 * 展开搜索区，恢复完整输入界面。
 */
function expandSearchPanel() {
  isSearchCollapsed = false;
  // 使用 CSS class 驱动过渡动画
  if (searchBody) searchBody.classList.remove('collapsed');
  if (searchTitle)    searchTitle.hidden = false;
  if (compactKeyword) compactKeyword.hidden = true;
  if (searchToggle) {
    searchToggle.textContent = '▲ 收起';
    searchToggle.title = '收起搜索区';
  }
}

/**
 * 在 resultsList 区域渲染搜索提示框（无结果 / 超时 / 错误）。
 * @param {'warning'|'error'} type  决定配色
 * @param {string} message          提示文字
 */
function showSearchNotice(type, message) {
  resultsList.innerHTML = `<div class="c5-notice c5-notice-${type}">${message}</div>`;
  resultsList.hidden = false;
}

// Toggle 按钮 + header 区域点击（与 Case 2 保持一致，整行可点）
// 只在 toggle 按钮可见（即 scrape_complete 后）才响应 header 点击
const searchHeader = document.getElementById('searchHeader');
if (searchHeader) {
  searchHeader.addEventListener('click', e => {
    // 仅当 toggle 可见时才响应 header 点击（避免搜索前误触）
    if (searchToggle && !searchToggle.hidden) {
      // 阻止来自 input / button 本身的冒泡（只响应 header 空白区域）
      if (e.target === searchHeader || e.target === searchTitle || e.target === compactKeyword) {
        if (isSearchCollapsed) expandSearchPanel();
        else collapseSearchPanel(keywordInput.value.trim() || '');
      }
    }
  });
}
if (searchToggle) {
  searchToggle.addEventListener('click', e => {
    e.stopPropagation(); // 防止冒泡到 searchHeader
    if (isSearchCollapsed) expandSearchPanel();
    else collapseSearchPanel(keywordInput.value.trim() || '');
  });
}

/**
 * 开始 HKLII 搜索与正文抓取（SSE）。
 */
async function startScrape() {
  const keyword = keywordInput.value.trim();
  if (!keyword || busy) return;

  // 新搜索前：清空上次结果 + 隐藏摘要按钮
  scrapedResults = [];
  summaryResult  = null;
  followupConversationId = null;
  resultsList.innerHTML  = '';
  resultsList.hidden = true;
  hideSummaryButtons();
  updateAnalyzeBtnState();

  // 若搜索区处于折叠状态，先展开
  if (isSearchCollapsed) expandSearchPanel();
  // 隐藏折叠按钮，搜索期间不可折叠
  if (searchToggle) searchToggle.hidden = true;

  // 创建新的 scrape session（每次搜索重新生成）
  scrapeSessionId = generateUUID();
  const activeSession = getCurrentSession();
  if (activeSession) {
    activeSession.backendSessionId = scrapeSessionId;
    saveSessions();
  }

  const userMsg = `🔍 搜索关键词：${keyword}`;
  const msgTime = Date.now();
  hideWelcome();
  addMessage('user', userMsg, undefined, msgTime);
  appendToCurrentSession('user', userMsg, msgTime);
  // 将关键词写入历史标题
  const session = getCurrentSession();
  if (session && session.title === '新对话') {
    session.title = keyword.slice(0, 20);
    saveSessions();
  }
  renderHistoryList();

  setBusy(true);
  showProgress('正在搜索 HKLII...', 0, 0);

  const form = new FormData();
  form.append('session_id', scrapeSessionId);
  form.append('keyword', keyword);
  form.append('max_results', '10');
  form.append('content_concurrency', '3');

  const scrapeMsgId = `scrape-${Date.now()}`;
  addMessage('ai', '⏳ 正在搜索 HKLII，请稍候...', scrapeMsgId);

  try {
    const res = await fetch(`${API_BASE}/case5/scrape`, { method: 'POST', body: form });

    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      updateMessage(scrapeMsgId, `❌ 搜索失败（${res.status}）：${err.detail || res.statusText}`);
      return;
    }

    const reader  = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    let scrapeComplete = false;

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
          case 'progress': {
            showProgress(ev.message || ev.stage, 0, 0);
            if (ev.stage === 'fetching' && ev.count) {
              updateMessage(
                scrapeMsgId,
                `⏳ 已找到 **${ev.count}** 条结果，正在抓取正文（约 ${Math.max(20, ev.count * 5)}–${Math.max(60, ev.count * 10)} 秒）...`
              );
            }
            break;
          }
          case 'scrape_complete': {
            scrapedResults = ev.results || [];
            const n = ev.count || scrapedResults.length;
            const summaryLines = scrapedResults
              .map(r => `${r.index}. **${r.title}** (${r.date || '—'})`)
              .join('\n');
            const completeMsg = (
              `✅ 搜索完成：找到 **${n}** 条关于 "${ev.keyword}" 的结果\n\n` +
              summaryLines +
              `\n\n---\n\n请点击下方 **「开始生成摘要」** 按钮，由 Agent J 为每条案例生成摘要。`
            );
            updateMessage(scrapeMsgId, completeMsg);
            appendToCurrentSession('ai', completeMsg, Date.now());
            saveSessions();
            renderResultsPreview(scrapedResults, keyword);
            // 搜索完成后显示折叠按钮
            if (searchToggle) searchToggle.hidden = false;
            scrapeComplete = true;
            break;
          }
          case 'no_results': {
            const kw = ev.keyword || keyword;
            updateMessage(scrapeMsgId, `未找到关键词 **"${kw}"** 的相关案例，请尝试其他关键词。`);
            showSearchNotice('warning', `未找到 "${kw}" 的相关案例，请尝试其他关键词`);
            scrapedResults = [];
            updateAnalyzeBtnState();
            scrapeComplete = true; // 视为正常终止，不触发通用"未完成"提示
            break;
          }
          case 'search_timeout': {
            updateMessage(scrapeMsgId, `❌ 搜索超时，HKLII 可能暂时无法访问，请稍后重试。`);
            showSearchNotice('error', '搜索超时，请稍后重试');
            scrapeComplete = true;
            break;
          }
          case 'error': {
            updateMessage(scrapeMsgId, `❌ 搜索出错：${ev.message}`);
            showSearchNotice('error', ev.message || '搜索出错，请重试');
            scrapeComplete = true;
            break;
          }
        }
      }
    }

    if (!scrapeComplete) {
      updateMessage(scrapeMsgId, '⚠️ 搜索未完成，请重试。');
    }

  } catch (e) {
    updateMessage(scrapeMsgId, `❌ 网络错误：${e.message}`);
  } finally {
    hideProgress();
    setBusy(false);
  }
}

// ── Case 5：综合分析（SSE）────────────────────────────────────────────────────

async function startAnalysis() {
  if (!scrapeSessionId || busy || scrapedResults.length === 0) return;

  setBusy(true);
  showProgress('正在初始化 Agent J 分析会话...', 0, 0);

  summaryResult = null;
  followupConversationId = null;
  hideSummaryButtons();

  const form = new FormData();
  form.append('session_id', scrapeSessionId);

  const analysisMsgId = `analysis-${Date.now()}`;
  addMessage('ai', '⏳ 正在启动摘要生成，请稍候...', analysisMsgId);

  try {
    const res = await fetch(`${API_BASE}/pdf/session/analyze`, { method: 'POST', body: form });

    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      updateMessage(analysisMsgId, `❌ 综合分析失败（${res.status}）：${err.detail || res.statusText}`);
      return;
    }

    const reader  = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    let resultReceived = false;

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
          case 'progress': {
            showProgress(ev.message, 0, 0);
            break;
          }
          case 'result': {
            const aiTime = Date.now();
            const content = ev.analysis_text || '';
            summaryResult = content;
            if (ev.conversation_id) {
              followupConversationId = ev.conversation_id;
            }
            updateMessage(analysisMsgId, content);
            showSummaryButtons();
            appendToCurrentSession('ai', content, aiTime);
            saveSessions();
            resultReceived = true;
            break;
          }
          case 'analyze_complete':
            showProgress('摘要生成完成', 1, 1);
            break;
          case 'error':
            updateMessage(analysisMsgId, `❌ 生成出错：${ev.message}`);
            break;
          case 'fatal_error':
            updateMessage(analysisMsgId, `❌ 致命错误：${ev.message}`);
            break;
        }
      }
    }

    if (!resultReceived) {
      updateMessage(analysisMsgId, '⚠️ 摘要生成未完成，请重试。');
    }

  } catch (e) {
    updateMessage(analysisMsgId, `❌ 网络错误：${e.message}`);
  } finally {
    hideProgress();
    setBusy(false);
    updateAnalyzeBtnState();
  }
}

// ── 下载摘要报告 ──────────────────────────────────────────────────────────────

async function downloadReport(resultKey, format) {
  if (!scrapeSessionId) { toast('请先完成摘要生成'); return; }

  const btn = format === 'docx' ? c5WordBtn : c5PdfBtn;
  if (btn) btn.disabled = true;
  toast('正在生成报告，请稍候...');

  const form = new FormData();
  form.append('session_id', scrapeSessionId);
  form.append('party', resultKey);
  form.append('output_format', format);

  try {
    const res = await fetch(`${API_BASE}/pdf/session/report`, { method: 'POST', body: form });

    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      toast(`报告生成失败：${err.detail || res.statusText}`);
      return;
    }

    const cd = res.headers.get('Content-Disposition') || '';
    const nameMatch = cd.match(/filename[^;=\n]*=["']?([^"'\n]+)["']?/);
    const downloadName = nameMatch ? nameMatch[1] : `hklii_summary.${format}`;

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

function showSummaryButtons() {
  if (c5WordBtn) { c5WordBtn.hidden = false; c5WordBtn.disabled = false; }
  if (c5PdfBtn)  { c5PdfBtn.hidden  = false; c5PdfBtn.disabled  = false; }
}

function hideSummaryButtons() {
  if (c5WordBtn) { c5WordBtn.hidden = true; c5WordBtn.disabled = true; }
  if (c5PdfBtn)  { c5PdfBtn.hidden  = true; c5PdfBtn.disabled  = true; }
}

// 下载按钮事件
if (c5WordBtn) c5WordBtn.addEventListener('click', () => downloadReport('HKLII 案例摘要', 'docx'));
if (c5PdfBtn)  c5PdfBtn.addEventListener('click',  () => downloadReport('HKLII 案例摘要', 'pdf'));

// ── 文字聊天（追问） ──────────────────────────────────────────────────────────

/**
 * 只要存在 scrapeSessionId（本轮已爬取），追问就路由到 /pdf/session/chat（Agent J）。
 * 分析未完成时，后端返回 400 "请先完成综合分析"，前端显示在气泡中。
 *
 * 不再用 summaryResult 判断：summaryResult=null 时静默 fallback 到 Agent B 是错误行为，
 * 应让用户明确知道"需要先生成摘要"而非收到 Agent B 的无关回复。
 */
function shouldUseAnalysisChat() {
  return !!scrapeSessionId;
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
      // ── 摘要追问：/pdf/session/chat（Agent J）────────────────────────
      const form = new FormData();
      form.append('session_id', scrapeSessionId);
      form.append('text', text);
      if (followupConversationId) {
        form.append('followup_conversation_id', followupConversationId);
      }

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
      // ── 普通文字聊天：/chat（Agent A）───────────────────────────────
      const res = await fetch(`${API_BASE}/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text, user_id: 'web_user', conversation_id: currentConversationId }),
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

// ── 消息气泡 ──────────────────────────────────────────────────────────────────

function addCopyButton(bubble) {
  if (bubble.querySelector('.copy-btn')) return;
  const btn = document.createElement('button');
  btn.className = 'copy-btn';
  btn.title = '复制';
  btn.innerHTML = COPY_ICON;
  btn.addEventListener('click', async () => {
    const text = bubble.innerText || bubble.textContent || '';
    try { await navigator.clipboard.writeText(text); }
    catch {
      const ta = document.createElement('textarea');
      ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
      document.body.appendChild(ta); ta.select(); document.execCommand('copy'); ta.remove();
    }
    btn.innerHTML = CHECK_ICON;
    btn.classList.add('copied');
    setTimeout(() => { btn.innerHTML = COPY_ICON; btn.classList.remove('copied'); }, 1500);
  });
  bubble.appendChild(btn);
}

function addMessage(role, content, id, msgTime = Date.now()) {
  hideWelcome();
  const wrap = document.createElement('div');
  wrap.className = `message message-${role}`;
  if (id) wrap.id = id;
  wrap.dataset.time = formatTime(msgTime);

  if (role !== 'system') {
    const av = document.createElement('div');
    av.className = `avatar avatar-${role}`;
    av.textContent = role === 'user' ? 'U' : '⚖️';
    wrap.appendChild(av);
  }

  const bubble = document.createElement('div');
  bubble.className = 'bubble';
  setBubble(bubble, role, content);
  wrap.appendChild(bubble);

  if (role === 'ai' && content && !content.includes('thinking-dots')) {
    addCopyButton(bubble);
  }

  messagesList.appendChild(wrap);
  scrollBottom();
  return wrap;
}

function setBubble(bubble, role, content) {
  if (role === 'ai') bubble.innerHTML = marked.parse(content);
  else bubble.textContent = content;
}

function updateMessage(id, content, role = 'ai') {
  const wrap = document.getElementById(id);
  if (!wrap) return;
  const bubble = wrap.querySelector('.bubble');
  if (!bubble) return;
  setBubble(bubble, role, content);
  wrap.dataset.time = formatTime(Date.now());
  if (role === 'ai' && content) addCopyButton(bubble);
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
  if (progressDots) progressDots.innerHTML = '';
  progressRatio.textContent = '';
}

// ── GPTBots 回复提取（普通聊天用） ────────────────────────────────────────────
function extractReply(data) {
  if (!data || typeof data !== 'object') return String(data ?? '');
  if (Array.isArray(data.output) && data.output.length > 0) {
    const first = data.output[0];
    if (first && typeof first === 'object') {
      const content = first.content;
      if (content && typeof content === 'object' && typeof content.text === 'string') return content.text;
      if (typeof content === 'string' && content) return content;
    }
  }
  for (const k of ['answer', 'text', 'message', 'reply']) {
    if (data[k] && typeof data[k] === 'string') return data[k];
  }
  if (data.data && typeof data.data === 'object') return extractReply(data.data);
  return JSON.stringify(data, null, 2);
}

// ── 重置当前视图 ──────────────────────────────────────────────────────────────
function resetCurrentView({ preserveHistory = false } = {}) {
  const preservedSessionId = preserveHistory ? currentSessionId : null;
  messagesList.innerHTML = '';
  welcome.hidden = false;
  hideProgress();
  setBusy(false);
  textInput.value = '';
  textInput.style.height = 'auto';
  sendBtn.disabled = true;

  // Case 5 状态重置
  scrapeSessionId        = null;
  scrapedResults         = [];
  summaryResult          = null;
  followupConversationId = null;
  resultsList.innerHTML  = '';
  resultsList.hidden     = true;
  keywordInput.value     = '';
  hideSummaryButtons();
  updateAnalyzeBtnState();

  // 重置折叠状态（直接设置而非调用 expandSearchPanel，避免改变 toggle 文案）
  isSearchCollapsed = false;
  if (searchBody)     searchBody.classList.remove('collapsed');
  if (searchTitle)    searchTitle.hidden = false;
  if (compactKeyword) compactKeyword.hidden = true;
  if (searchToggle)   searchToggle.hidden = true;

  currentConversationId = null;
  currentSessionId      = preservedSessionId;
}

async function resetCurrentConversation() {
  if (busy) return;
  const session = getCurrentSession();
  if (!session) return;
  const hasContent = (
    session.messages.length > 0
    || Boolean(session.backendSessionId)
    || scrapedResults.length > 0
    || Boolean(summaryResult)
    || keywordInput.value.trim().length > 0
  );
  if (hasContent && !confirm('确定重置当前对话吗？消息、检索结果和摘要将被清空。')) return;

  const backendSessionId = session.backendSessionId || null;
  resetCurrentView({ preserveHistory: true });
  session.messages = [];
  session.title = '新对话';
  session.conversationId = null;
  session.backendSessionId = null;
  session.updatedAt = Date.now();
  saveSessions();
  renderHistoryList();

  if (backendSessionId) {
    try {
      const response = await fetch(`/pdf/session/${encodeURIComponent(backendSessionId)}`, {
        method: 'DELETE',
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
    } catch {
      toast('当前对话已重置；服务端缓存将在两小时内自动清理');
      return;
    }
  }
  toast('当前对话已重置');
}

// ── 事件绑定 ──────────────────────────────────────────────────────────────────

// 搜索
searchBtn.addEventListener('click', startScrape);
keywordInput.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !busy) startScrape();
});

// 分析
analyzeBtn.addEventListener('click', startAnalysis);

// 输入框
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

// 新对话
newChatBtn.addEventListener('click', () => {
  resetCurrentView();
  createNewSession();
  renderHistoryList();
});
resetConversationBtn.addEventListener('click', resetCurrentConversation);

// ── 初始化 ────────────────────────────────────────────────────────────────────
loadSessions();
if (sessions.length > 0) {
  switchToSession(sessions[0].id);
} else {
  createNewSession();
  renderHistoryList();
}
