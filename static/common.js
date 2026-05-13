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
