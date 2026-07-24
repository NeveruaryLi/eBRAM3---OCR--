/**
 * eBRAM AI 文档助手 — 公共工具
 *
 * 供所有页面共享的轻量级工具函数：
 *   - 主题管理（深色 / 浅色，localStorage 持久化）
 *   - Toast 通知
 *   - ID 生成与时间格式化
 *
 * 该文件在 index.html / case1.html / case2.html 中均需引入。
 * 不依赖任何第三方库，不包含任何业务逻辑。
 */

'use strict';

// ── 配置 ──────────────────────────────────────────────────────────────────────
const THEME_KEY = 'theme';   // 跨所有页面统一使用同一个 key

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

// 页面加载时立即恢复上次保存的主题
applyTheme(localStorage.getItem(THEME_KEY) || 'dark');

// 绑定主题切换按钮（所有页面均有 id="themeBtn"，脚本在 </body> 底部加载，DOM 已就绪）
const _themeBtn = document.getElementById('themeBtn');
if (_themeBtn) _themeBtn.addEventListener('click', toggleTheme);

// ── Toast 通知 ────────────────────────────────────────────────────────────────
function toast(msg) {
  const el = document.createElement('div');
  el.className = 'toast';
  el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 2600);
}

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

// ── 统一历史会话菜单 ──────────────────────────────────────────────────────────
let activeHistoryMenuButton = null;

function closeHistoryMenus({ restoreFocus = false } = {}) {
  const trigger = activeHistoryMenuButton;
  activeHistoryMenuButton = null;
  document.querySelectorAll('.history-dropdown').forEach(menu => menu.remove());
  document.querySelectorAll('.history-item.menu-open').forEach(item => {
    item.classList.remove('menu-open');
  });
  document.querySelectorAll('.history-menu-btn[aria-expanded="true"]').forEach(button => {
    button.setAttribute('aria-expanded', 'false');
  });
  if (restoreFocus && trigger?.isConnected) trigger.focus();
}

function createHistoryMenu({ label, onDelete, isDisabled = () => false }) {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'history-menu-btn';
  button.title = '更多操作';
  button.setAttribute('aria-label', `更多操作：${label}`);
  button.setAttribute('aria-haspopup', 'menu');
  button.setAttribute('aria-expanded', 'false');
  button.innerHTML = '<svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><circle cx="12" cy="5" r="1.8"/><circle cx="12" cy="12" r="1.8"/><circle cx="12" cy="19" r="1.8"/></svg>';

  button.addEventListener('click', event => {
    event.stopPropagation();
    if (isDisabled()) return;
    const historyItem = button.closest('.history-item');
    const wasOpen = Boolean(historyItem?.classList.contains('menu-open'));
    closeHistoryMenus();
    if (wasOpen) return;
    activeHistoryMenuButton = button;

    const rect = button.getBoundingClientRect();
    const dropdown = document.createElement('div');
    dropdown.className = 'history-dropdown';
    dropdown.setAttribute('role', 'menu');
    dropdown.style.top = `${rect.bottom + 4}px`;
    dropdown.style.left = `${Math.max(4, rect.right - 140)}px`;

    const deleteItem = document.createElement('button');
    deleteItem.type = 'button';
    deleteItem.className = 'history-dropdown-item danger';
    deleteItem.setAttribute('role', 'menuitem');
    deleteItem.innerHTML = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a1 1 0 011-1h4a1 1 0 011 1v2"/></svg>删除会话';
    deleteItem.addEventListener('click', async deleteEvent => {
      deleteEvent.stopPropagation();
      if (isDisabled()) return;
      if (!window.confirm(`确定删除“${label}”吗？`)) return;
      closeHistoryMenus();
      await onDelete();
      requestAnimationFrame(() => {
        const nextControl = document.querySelector(
          '.history-item.active .history-title-button, '
          + '.history-item.active .history-menu-btn, '
          + '.history-item .history-title-button, '
          + '.history-item .history-menu-btn',
        );
        nextControl?.focus();
      });
    });

    dropdown.appendChild(deleteItem);
    document.body.appendChild(dropdown);
    historyItem?.classList.add('menu-open');
    button.setAttribute('aria-expanded', 'true');
    deleteItem.focus();
  });
  return button;
}

document.addEventListener('click', event => {
  if (!event.target.closest('.history-menu-btn')
      && !event.target.closest('.history-dropdown')) {
    closeHistoryMenus();
  }
});
document.addEventListener('keydown', event => {
  if (event.key === 'Escape') closeHistoryMenus({ restoreFocus: true });
});
