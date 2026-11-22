"use strict";

const DEFAULTS = {
  output: "C:\\Users\\Huenchuekhei\\OneDrive\\文档\\XDL",
  cookies: "C:\\Users\\Huenchuekhei\\Downloads\\cookies.txt",
  cookiesSource: "file",
  cookiesBrowser: "chrome",
  proxyMode: "auto",
  proxyAddr: "socks5h://127.0.0.1:7890",
  limit: 0,
  concurrency: 2,
  mediaOnly: true,
  maxRetries: 3,
  autoRetry: false,
  incremental: true,
  stopAfterSkips: 20,
  mediaType: "all",
  durationFilterEnabled: false,
  maxVideoDuration: 60,
  layout: "per-user",
};

const DONE_PAGE_SIZE = 300;
const MAX_LOCAL_LOGS = 2000;

const REASONS = {
  timeout: { label: "连接超时", hint: "CDN 响应超时，重试通常能成功", retryable: true },
  network: { label: "网络中断", hint: "代理或连接中断，检查代理后重试", retryable: true },
  forbidden: { label: "权限受限", hint: "账号受年龄或隐私限制，需更新 Cookie", retryable: true },
  notfound: { label: "文件已删除", hint: "源文件已不存在，重试无效", retryable: false },
};

const STATUS_LABEL = {
  waiting: "等待中",
  active: "下载中",
  done: "已完成",
  failed: "失败",
  paused: "已暂停",
};

const LOG_LVL_LABEL = {
  cmd: "CMD",
  ok: "成功",
  skip: "跳过",
  warn: "警告",
  error: "错误",
  info: "信息",
};

const ICON_TWEET = '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M14 4h6v6"/><path d="M20 4l-8.5 8.5"/><path d="M18 14v4a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4"/></svg>';
const ICON_FOLDER = '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>';
const ICON_PREVIEW = '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7-11-7-11-7z"/><circle cx="12" cy="12" r="3"/></svg>';
const ICON_LOGS = '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/></svg>';
const ICON_COPY = '<rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>';
const ICON_CHECK = '<polyline points="20 6 9 17 4 12"/>';

const state = {
  queue: [],
  filter: "all",
  search: "",
  selected: new Set(),
  running: false,
  paused: false,
  busy: false,
  elapsedBase: 0,
  round: 1,
  roundSummary: null,
  settings: { ...DEFAULTS },
  env: {},
  sortKey: null,
  sortDir: 1,
  view: "accounts",
  mobileTab: "queue",
  failures: [],
  failedFilter: "all",
  failedSearch: "",
  failedSelected: new Set(),
  failedSortKey: null,
  failedSortDir: 1,
  doneRows: [],
  doneTotals: { all: 0, images: 0, videos: 0 },
  doneFilter: "all",
  doneSearch: "",
  doneLoaded: false,
  doneHasMore: false,
  doneLoading: false,
  logs: [],
  logSeq: 0,
  logLevel: "all",
  logUser: "",
  logSearch: "",
  logUnread: false,
  cmdHistory: [],
  cmdHistIdx: -1,
  cmdDraft: "",
};

const $ = (id) => document.getElementById(id);

const els = {
  cmdForm: $("cmd-form"),
  cmdInput: $("cmd-input"),
  pasteInput: $("paste-input"),
  charCount: $("char-count"),
  parseStats: $("parse-stats"),
  fileStats: $("file-stats"),
  queueList: $("queue-list"),
  queueEmpty: $("queue-empty"),
  queueCount: $("queue-count"),
  search: $("queue-search"),
  selectAll: $("select-all"),
  btnStart: $("btn-start"),
  btnStartLabel: $("btn-start-label"),
  btnStopRun: $("btn-stop-run"),
  btnDeleteSelected: $("btn-delete-selected"),
  overallFill: $("overall-fill"),
  overallPct: $("overall-pct"),
  sbStats: $("sb-stats"),
  sbFilter: $("sb-filter"),
  sbTime: $("sb-time"),
  sbNote: $("sb-note"),
  toasts: $("toasts"),
  fileInput: $("file-input"),
  cookiesUploadInput: $("cookies-upload-input"),
  dropzone: $("dropzone"),
  setOutput: $("set-output"),
  setCookies: $("set-cookies"),
  setCookiesBrowser: $("set-cookies-browser"),
  setProxyMode: $("set-proxy-mode"),
  setProxyAddr: $("set-proxy-addr"),
  setLimit: $("set-limit"),
  setConcurrency: $("set-concurrency"),
  setMediaOnly: $("set-media-only"),
  setIncremental: $("set-incremental"),
  setStopAfterSkips: $("set-stop-after-skips"),
  setMaxRetries: $("set-max-retries"),
  setAutoRetry: $("set-auto-retry"),
  setDurationEnabled: $("set-duration-enabled"),
  durMin: $("dur-min"),
  durSec: $("dur-sec"),
  durSlider: $("dur-slider"),
  durTotalBadge: $("dur-total-badge"),
  durationControls: $("duration-controls"),
  filterPanel: $("filter-panel"),
  filterStatusTag: $("filter-status-tag"),
  filterRuleSummary: $("filter-rule-summary"),
  cmdPreview: $("cmd-preview-text"),
  closeModal: $("close-modal"),
  mediaModal: $("media-modal"),
  drawer: $("drawer"),
  drawerBackdrop: $("drawer-backdrop"),
  drawerTitle: $("drawer-title"),
  drawerBody: $("drawer-body"),
  logTerminal: $("log-terminal"),
  logEmpty: $("log-empty"),
  logUserFilter: $("log-user-filter"),
  logSearch: $("log-search"),
  logAutoscroll: $("log-autoscroll"),
  logCounter: $("log-counter"),
  failedList: $("failed-list"),
  failedEmpty: $("failed-empty"),
  failedCount: $("failed-count"),
  failedSearch: $("failed-search"),
  failedSelectAll: $("failed-select-all"),
  btnFailedRetry: $("btn-failed-retry"),
  btnFailedRetryLabel: $("btn-failed-retry-label"),
  btnFailedRetrySelected: $("btn-failed-retry-selected"),
  btnFailedDiscard: $("btn-failed-discard"),
  roundbar: $("roundbar"),
  rbTitle: $("rb-title"),
  rbDetail: $("rb-detail"),
  btnRoundRetry: $("btn-round-retry"),
  btnRoundRetryLabel: $("btn-round-retry-label"),
};

let pollTimer = null;
let pollBusy = false;
let envTimer = null;
let drawerId = null;
let drawerHistory = null;
let drawerHistoryOpen = false;
let currentMediaPreview = null;
let dragId = null;
let quitReturnFocus = null;
let mediaReturnFocus = null;
let drawerReturnFocus = null;

/* ---------- API helpers ---------- */

async function api(path, body) {
  const response = await fetch(path, {
    method: body === undefined ? "GET" : "POST",
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let data = null;
  try { data = await response.json(); } catch {}
  if (!response.ok || (data && data.ok === false)) {
    throw new Error((data && data.error) || `请求失败 (${response.status})`);
  }
  return data;
}

async function postState(path, body) {
  try {
    const data = await api(path, body || {});
    if (data && data.state) applyServerState(data.state);
    return data;
  } catch (error) {
    toast(error.message || "操作失败", "bad");
    return null;
  }
}

function stopPolling() {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = null;
}

function startPolling() {
  if (pollTimer) return;
  pollTimer = setInterval(refreshFromServer, 700);
  refreshFromServer();
}

async function refreshFromServer() {
  if (pollBusy) return;
  pollBusy = true;
  try {
    const data = await api("/api/state");
    applyServerState(data);
    if (data && (data.logSeq > state.logSeq || state.view === "logs")) {
      await fetchLogs();
    }
  } catch {
    stopPolling();
  } finally {
    pollBusy = false;
  }
}

function scheduleEnvRefresh() {
  const ready = Boolean(state.env && state.env.store && state.env.store.ready);
  if (ready) {
    if (envTimer) { clearTimeout(envTimer); envTimer = null; }
    return;
  }
  if (envTimer) return;
  envTimer = setTimeout(async () => {
    envTimer = null;
    try {
      const data = await api("/api/state");
      if (data) applyServerState(data);
    } catch {
      scheduleEnvRefresh();
    }
  }, 1500);
}

function applyServerState(data) {
  if (!data) return;
  const prevLogSeq = state.logSeq;
  state.queue = Array.isArray(data.queue) ? data.queue : [];
  state.failures = Array.isArray(data.failures) ? data.failures : [];
  state.round = Number(data.round) || 1;
  state.running = Boolean(data.running) && !data.paused;
  state.paused = Boolean(data.paused);
  state.busy = Boolean(data.busy);
  state.roundSummary = data.roundSummary || null;
  state.elapsedBase = Number(data.elapsedMs) || 0;
  state.env = data.env || {};
  if (Array.isArray(data.cmdHistory)) state.cmdHistory = data.cmdHistory;
  if (data.settings) {
    state.settings = { ...DEFAULTS, ...data.settings };
    syncSettingsInputs();
  }

  state.selected = new Set([...state.selected].filter((id) => state.queue.some((item) => item.id === id)));
  state.failedSelected = new Set([...state.failedSelected].filter((id) => state.failures.some((f) => f.id === id)));

  render();
  renderEnvChips();
  renderClock();
  scheduleEnvRefresh();

  if (Number(data.logSeq) > prevLogSeq && state.view !== "logs") {
    state.logUnread = true;
    updateMobileBadges();
  }
  if (Number(data.logSeq) > state.logSeq) {
    fetchLogs();
  }

  if (data.running || data.busy || (data.paused && data.activeCount > 0)) startPolling();
  else stopPolling();
}

/* ---------- formatting & helpers ---------- */

function escapeHtml(text) {
  return String(text == null ? "" : text)
    .replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
}

function clampInt(value, min, max, fallback) {
  if (!Number.isFinite(value)) return fallback;
  return Math.min(max, Math.max(min, Math.round(value)));
}

function formatDuration(seconds) {
  const secs = Math.max(0, Math.round(Number(seconds) || 0));
  if (secs <= 0) return "不限";
  if (secs < 3600) {
    const m = Math.floor(secs / 60);
    const s = secs % 60;
    return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  }
  const h = Math.floor(secs / 3600);
  const m = Math.floor((secs % 3600) / 60);
  const s = secs % 60;
  return `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

function effectiveMaxDuration(settings = state.settings) {
  if (settings.mediaType === "images" || !settings.durationFilterEnabled) return 0;
  return clampInt(Number(settings.maxVideoDuration), 5, 7200, 60);
}

function describeFilter(settings = state.settings) {
  const kind = settings.mediaType || "all";
  const maxDur = effectiveMaxDuration(settings);
  if (kind === "images") return "仅图片";
  if (kind === "videos" && maxDur > 0) return `仅视频 · ≤ ${formatDuration(maxDur)}`;
  if (kind === "videos") return "仅视频";
  if (maxDur > 0) return `视频 ≤ ${formatDuration(maxDur)}`;
  return "";
}

function formatBytes(bytes) {
  const value = Number(bytes) || 0;
  if (value <= 0) return "—";
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  if (value < 1024 * 1024 * 1024) return `${(value / 1024 / 1024).toFixed(1)} MB`;
  return `${(value / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

function formatWhen(stamp) {
  if (!stamp) return "—";
  return String(stamp).replace("T", " ").slice(0, 16);
}

function shortWhen(stamp) {
  const text = formatWhen(stamp);
  return text === "—" ? "—" : text.slice(5);
}

function isVideoFile(name) {
  return /\.(mp4|webm|m4v|mov)$/i.test(String(name || ""));
}

/* ---------- parsing & batch import ---------- */

function extractUsername(token) {
  const raw = token.trim().replace(/^@+/, "");
  if (!raw) return null;
  const urlMatch = raw.match(/^(?:https?:\/\/)?(?:www\.)?(?:x\.com|twitter\.com)\/([A-Za-z0-9_]{1,15})(?:[\/?#].*)?$/i);
  if (urlMatch) return urlMatch[1];
  if (/^[A-Za-z0-9_]{1,15}$/.test(raw)) return raw;
  return null;
}

function parseText(text) {
  const tokens = text.split(/[\s,，;；]+/).filter(Boolean);
  const valid = [];
  let invalid = 0;
  const seen = new Set();
  let duplicatesInText = 0;
  for (const token of tokens) {
    const user = extractUsername(token);
    if (!user) { invalid += 1; continue; }
    const key = user.toLowerCase();
    if (seen.has(key)) { duplicatesInText += 1; continue; }
    seen.add(key);
    valid.push(user);
  }
  return { valid, invalid, duplicatesInText };
}

async function addToQueue(users) {
  if (!users.length) return { added: 0, skipped: 0 };
  const data = await postState("/api/add", { users });
  if (!data) return { added: 0, skipped: 0 };
  return { added: data.added || 0, skipped: data.skipped || 0 };
}

function showParseStats(el, result, added, skipped, sourceLabel) {
  const parts = [];
  parts.push(`<span class="ps-new">新增 <b>${added}</b></span>`);
  const dupTotal = result.duplicatesInText + skipped;
  if (dupTotal > 0) parts.push(`<span class="ps-dup">重复跳过 <b>${dupTotal}</b></span>`);
  if (result.invalid > 0) parts.push(`<span class="ps-bad">无效忽略 <b>${result.invalid}</b></span>`);
  if (sourceLabel) parts.push(`<span>来源：${sourceLabel}</span>`);
  el.innerHTML = parts.join("");
  el.classList.remove("hidden");
}

async function handlePasteImport() {
  const text = els.pasteInput.value;
  if (!text.trim()) {
    toast("先粘贴一些用户名或主页链接", "warn");
    return;
  }
  const result = parseText(text);
  const { added, skipped } = await addToQueue(result.valid);
  showParseStats(els.parseStats, result, added, skipped, "粘贴文本");
  if (added > 0) {
    toast(`已加入 ${added} 个账号到队列`, "ok");
    els.pasteInput.value = "";
    updateCharCount();
  } else {
    toast("没有新增账号（全部重复或无效）", "warn");
  }
}

function handleFile(file) {
  if (!file) return;
  if (!/\.txt$/i.test(file.name)) {
    toast("只支持 .txt 文件", "bad");
    return;
  }
  const reader = new FileReader();
  reader.onload = async () => {
    const result = parseText(String(reader.result));
    const { added, skipped } = await addToQueue(result.valid);
    showParseStats(els.fileStats, result, added, skipped, escapeHtml(file.name));
    toast(added > 0 ? `从 ${file.name} 加入 ${added} 个账号` : "文件里没有新增账号", added > 0 ? "ok" : "warn");
  };
  reader.onerror = () => toast("读取文件失败", "bad");
  reader.readAsText(file, "utf-8");
}

function updateCharCount() {
  els.charCount.textContent = `${els.pasteInput.value.length} 字符`;
}

function switchTab(which) {
  const paste = which === "paste";
  $("tab-paste").classList.toggle("active", paste);
  $("tab-file").classList.toggle("active", !paste);
  $("tab-paste").setAttribute("aria-selected", String(paste));
  $("tab-file").setAttribute("aria-selected", String(!paste));
  $("pane-paste").classList.toggle("hidden", !paste);
  $("pane-file").classList.toggle("hidden", paste);
}

function setImportCollapsed(collapsed) {
  const panel = document.querySelector(".import-panel");
  if (!panel) return;
  panel.classList.toggle("is-collapsed", collapsed);
  const button = $("btn-import-toggle");
  button.setAttribute("aria-expanded", String(!collapsed));
  const label = collapsed ? "展开导入区" : "收起导入区";
  button.title = label;
  button.setAttribute("aria-label", label);
}

/* ---------- interactive CMD bar ---------- */

async function executeCommand(rawCmd) {
  const cmd = String(rawCmd || "").trim();
  if (!cmd) {
    toast("请输入 xdl 命令", "warn");
    els.cmdInput.focus();
    return;
  }
  try {
    const data = await api("/api/command", { command: cmd });
    if (data && data.state) applyServerState(data.state);
    await fetchLogs();
    els.cmdInput.value = "";
    state.cmdHistIdx = -1;
    state.cmdDraft = "";
    if (data.action === "help" || data.action === "status") {
      setView("logs");
    }
    toast(data.message || "命令已执行", "ok");
  } catch (error) {
    await fetchLogs();
    toast(error.message || "命令执行失败", "bad");
  }
}

/* ---------- real-time logs ---------- */

async function fetchLogs() {
  try {
    const data = await api(`/api/logs?since=${state.logSeq}&limit=500`);
    if (!data || !Array.isArray(data.entries)) return;
    if (data.entries.length > 0) {
      state.logs = state.logs.concat(data.entries);
      if (state.logs.length > MAX_LOCAL_LOGS) {
        state.logs = state.logs.slice(-MAX_LOCAL_LOGS);
      }
      state.logSeq = Number(data.lastSeq) || state.logSeq;
      if (state.view === "logs") renderLogs();
    } else if (Number(data.lastSeq) > state.logSeq) {
      state.logSeq = Number(data.lastSeq);
    }
  } catch {}
}

function syncLogUserOptions() {
  const select = els.logUserFilter;
  if (!select) return;
  const current = state.logUser;
  const users = new Set();
  state.queue.forEach((item) => users.add(item.user));
  state.logs.forEach((entry) => { if (entry.user) users.add(entry.user); });
  const sorted = [...users].sort((a, b) => a.toLowerCase().localeCompare(b.toLowerCase()));
  const options = ['<option value="">全部账号</option>'].concat(
    sorted.map((u) => `<option value="${escapeHtml(u)}">@${escapeHtml(u)}</option>`)
  );
  const html = options.join("");
  if (select.innerHTML !== html) select.innerHTML = html;
  select.value = sorted.some((u) => u.toLowerCase() === current.toLowerCase()) ? current : "";
}

function visibleLogs() {
  const q = state.logSearch.trim().toLowerCase();
  const user = state.logUser.trim().toLowerCase();
  const lvl = state.logLevel;
  return state.logs.filter((entry) => {
    if (lvl !== "all" && entry.level !== lvl) return false;
    if (user && String(entry.user || "").toLowerCase() !== user) return false;
    if (q && !String(entry.text || "").toLowerCase().includes(q) && !String(entry.user || "").toLowerCase().includes(q)) {
      return false;
    }
    return true;
  });
}

function renderLogs() {
  syncLogUserOptions();
  const rows = visibleLogs();
  els.logCounter.textContent = `${rows.length} 条日志`;
  els.logEmpty.classList.toggle("hidden", rows.length > 0);
  const oldLines = els.logTerminal.querySelectorAll(".log-line");
  oldLines.forEach((el) => el.remove());

  if (rows.length === 0) return;
  const frag = document.createDocumentFragment();
  rows.forEach((entry) => {
    const row = document.createElement("div");
    row.className = `log-line is-${entry.level}`;
    row.innerHTML = `
      <span class="log-ts">${escapeHtml(entry.ts || "--:--:--")}</span>
      <span class="log-lvl lvl-${escapeHtml(entry.level)}">${LOG_LVL_LABEL[entry.level] || "信息"}</span>
      ${entry.user ? `<span class="log-user">@${escapeHtml(entry.user)}</span>` : '<span class="log-user"></span>'}
      <span class="log-text">${escapeHtml(entry.text || "")}</span>
    `;
    frag.appendChild(row);
  });
  els.logTerminal.appendChild(frag);
  if (els.logAutoscroll.checked) {
    els.logTerminal.scrollTop = els.logTerminal.scrollHeight;
  }
}

/* ---------- queue rendering ---------- */

function visibleItems() {
  const query = state.search.trim().toLowerCase();
  const items = state.queue.filter((item) => {
    if (state.filter === "waiting") {
      if (item.status !== "waiting" && item.status !== "paused") return false;
    } else if (state.filter !== "all" && item.status !== state.filter) {
      return false;
    }
    if (query && !item.user.toLowerCase().includes(query)) return false;
    return true;
  });
  if (state.sortKey) {
    const key = state.sortKey;
    const dir = state.sortDir;
    items.sort((a, b) => {
      if (key === "user") return dir * a.user.toLowerCase().localeCompare(b.user.toLowerCase());
      return dir * ((a[key] || 0) - (b[key] || 0));
    });
  }
  return items;
}

function failuresFor(item) {
  return state.failures.filter((f) => f.ownerId === item.id && f.status !== "done");
}

function filesCellHtml(item) {
  const total = Number(item.downloadedTotal ?? item.files) || 0;
  const added = Number(item.newFiles) || 0;
  const skipped = Number(item.skippedFiles) || 0;
  const failCount = failuresFor(item).length;
  if (!total && !added && !skipped && !failCount) return '<span class="qskip">—</span>';
  const bits = [];
  if (added > 0) bits.push(`<span class="qnew">+${added} 新增</span>`);
  if (skipped > 0) bits.push(`<span class="qskip">${skipped} 跳过</span>`);
  if (!added && !skipped) bits.push('<span class="qskip">0 新增</span>');
  if (failCount > 0) bits.push(`<span class="fbadge" title="${failCount} 个文件失败">${failCount}</span>`);
  return bits.join("");
}

function itemMetaText(item) {
  const total = Number(item.downloadedTotal ?? item.files) || 0;
  const parts = [];
  if (total > 0) parts.push(`${total} 个文件`);
  if (item.lastRunAt) parts.push(String(item.lastRunAt).slice(5, 16));
  return parts.join(" · ") || (item.status === "waiting" ? "待下载" : "—");
}

function itemFilterPillHtml(item) {
  const label = item.filterSummary || "";
  return label ? `<span class="q-filter-pill">${escapeHtml(label)}</span>` : "";
}

function rowHtml(item) {
  const checked = state.selected.has(item.id) ? "checked" : "";
  const pct = Math.round(item.progress || 0);
  const fillClass = item.status === "done" ? "done" : item.status === "failed" ? "failed" : "";
  const statusText = item.status === "failed" && item.error
    ? `<span class="st st-failed" title="${escapeHtml(item.error)}">失败</span>`
    : `<span class="st st-${item.status}">${STATUS_LABEL[item.status] || item.status}</span>`;
  const retryBtn = item.status === "failed"
    ? `<button class="iconbtn" data-act="retry" data-id="${item.id}" title="重试" aria-label="重试 ${escapeHtml(item.user)}">
         <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M20 11a8 8 0 1 0-2.3 6.3"/><path d="M20 4v7h-7"/></svg>
       </button>`
    : "";
  const initial = (item.user || "?").slice(0, 1);
  return `<div class="qrow${item.status === "active" ? " is-active" : ""}" data-id="${item.id}" draggable="true">
    <span class="qcell q-grip" title="拖拽调整下载顺序" aria-hidden="true">
      <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><path d="M9 6h.01M15 6h.01M9 12h.01M15 12h.01M9 18h.01M15 18h.01"/></svg>
    </span>
    <span class="qcell q-check"><input type="checkbox" data-act="select" data-id="${item.id}" ${checked} aria-label="选择 ${escapeHtml(item.user)}"></span>
    <button class="qcell q-user" data-act="detail" data-id="${item.id}" type="button" title="查看 @${escapeHtml(item.user)} 详情">
      <span class="avatar" aria-hidden="true">${escapeHtml(initial)}</span>
      <span class="q-who">
        <span class="q-name"><span class="at">@</span>${escapeHtml(item.user)}</span>
        <span class="q-subline"><span class="q-meta" data-meta>${escapeHtml(itemMetaText(item))}</span>${itemFilterPillHtml(item)}</span>
      </span>
    </button>
    <span class="qcell q-status">${statusText}</span>
    <span class="qcell q-progress"><span class="track"><span class="fill ${fillClass}" style="transform:scaleX(${pct / 100})"></span></span><span class="pct">${pct}%</span></span>
    <span class="qcell q-files">${filesCellHtml(item)}</span>
    <span class="qcell q-ops">
      <button class="iconbtn" data-act="logs" data-id="${item.id}" type="button" title="查看实时日志" aria-label="查看 ${escapeHtml(item.user)} 日志">${ICON_LOGS}</button>
      ${retryBtn}
      <button class="iconbtn danger" data-act="remove" data-id="${item.id}" type="button" title="移出队列" aria-label="移除 ${escapeHtml(item.user)}">
        <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg>
      </button>
    </span>
  </div>`;
}

function reconcileRows(items) {
  const existing = new Map();
  els.queueList.querySelectorAll(".qrow").forEach((row) => existing.set(Number(row.dataset.id), row));
  const wanted = new Set(items.map((item) => item.id));
  existing.forEach((row, id) => { if (!wanted.has(id)) row.remove(); });

  items.forEach((item, index) => {
    let row = existing.get(item.id);
    if (!row) {
      const holder = document.createElement("div");
      holder.innerHTML = rowHtml(item);
      row = holder.firstElementChild;
    } else {
      row = patchRow(row, item);
    }
    const atPosition = els.queueList.children[index];
    if (atPosition !== row) els.queueList.insertBefore(row, atPosition || null);
  });
}

function patchRow(row, item) {
  const pct = Math.round(item.progress || 0);
  const fill = row.querySelector(".fill");
  fill.style.transform = `scaleX(${pct / 100})`;
  fill.className = "fill" + (item.status === "done" ? " done" : item.status === "failed" ? " failed" : "");
  row.querySelector(".pct").textContent = `${pct}%`;
  row.classList.toggle("is-active", item.status === "active");

  const statusCell = row.querySelector(".q-status");
  const statusHtml = item.status === "failed" && item.error
    ? `<span class="st st-failed" title="${escapeHtml(item.error)}">失败</span>`
    : `<span class="st st-${item.status}">${STATUS_LABEL[item.status] || item.status}</span>`;
  if (statusCell.innerHTML !== statusHtml) statusCell.innerHTML = statusHtml;

  const filesCell = row.querySelector(".q-files");
  const filesHtml = filesCellHtml(item);
  if (filesCell.innerHTML !== filesHtml) filesCell.innerHTML = filesHtml;

  const subline = row.querySelector(".q-subline");
  const wantSub = `<span class="q-meta" data-meta>${escapeHtml(itemMetaText(item))}</span>${itemFilterPillHtml(item)}`;
  if (subline && subline.innerHTML !== wantSub) subline.innerHTML = wantSub;

  row.querySelector("input[data-act=select]").checked = state.selected.has(item.id);
  const hasRetry = Boolean(row.querySelector("[data-act=retry]"));
  if ((item.status === "failed") !== hasRetry) {
    const holder = document.createElement("div");
    holder.innerHTML = rowHtml(item);
    const fresh = holder.firstElementChild;
    row.replaceWith(fresh);
    if (drawerId === item.id) refreshDrawer(item);
    return fresh;
  }
  if (drawerId === item.id) refreshDrawer(item);
  return row;
}

function render() {
  const items = visibleItems();
  reconcileRows(items);
  els.queueEmpty.classList.toggle("hidden", state.queue.length > 0);
  els.queueCount.textContent = String(state.queue.length);

  const counts = { all: state.queue.length, waiting: 0, active: 0, done: 0, failed: 0 };
  for (const item of state.queue) {
    if (item.status === "paused") counts.waiting += 1;
    else if (counts[item.status] !== undefined) counts[item.status] += 1;
  }
  $("c-all").textContent = counts.all;
  $("c-waiting").textContent = counts.waiting;
  $("c-active").textContent = counts.active;
  $("c-done").textContent = counts.done;
  $("c-failed").textContent = counts.failed;

  els.btnDeleteSelected.disabled = state.selected.size === 0;
  const visibleIds = items.map((item) => item.id);
  els.selectAll.checked = visibleIds.length > 0 && visibleIds.every((id) => state.selected.has(id));

  [["user", "sort-user-arrow"], ["progress", "sort-progress-arrow"], ["files", "sort-files-arrow"]].forEach(([key, id]) => {
    $(id).textContent = state.sortKey === key ? (state.sortDir === 1 ? "▲" : "▼") : "";
  });

  const startable = state.queue.some((item) => item.status === "waiting" || item.status === "paused");
  els.btnStart.disabled = !state.running && !startable;
  els.btnStartLabel.textContent = state.running ? "暂停下载" : "开始下载";
  $("btn-start-icon").innerHTML = state.running
    ? '<path d="M9 5v14M15 5v14"/>'
    : '<path d="M8 5.5l11 6.5-11 6.5z"/>';
  els.btnStopRun.disabled = !state.running && !state.busy && !state.paused;

  $("round-tag").textContent = `第 ${state.round} 轮`;
  $("log-live-dot").classList.toggle("hidden", !state.running);

  renderStatusbar(counts);
  renderCmdPreview();
  renderTree();
  renderFailed();
  renderRoundbar();
  updateMobileBadges();
}

function renderStatusbar(counts) {
  const total = state.queue.length;
  const settled = (item) => item.status === "done" || item.status === "failed";
  const sum = state.queue.reduce((acc, item) => acc + (settled(item) ? 100 : (item.progress || 0)), 0);
  const pct = total === 0 ? 0 : Math.round(sum / total);
  els.overallFill.style.transform = `scaleX(${pct / 100})`;
  els.overallPct.textContent = `${pct}%`;

  const files = state.queue.reduce((acc, item) => acc + (Number(item.files) || 0), 0);
  const pending = pendingFailures().length;
  const recovered = state.failures.filter((f) => f.status === "done").length;
  const parts = [`完成 ${counts.done}/${total}`, `${files} 个文件`, `失败文件 ${pending}`];
  if (recovered > 0) parts.push(`重试恢复 ${recovered}`);
  els.sbStats.textContent = parts.join(" · ");

  const filt = describeFilter(state.settings);
  els.sbFilter.textContent = filt ? `筛选: ${filt}` : "筛选: 不限";

  const lanUrl = state.env && state.env.lanUrl;
  els.sbNote.textContent = lanUrl
    ? `局域网可访问 ${lanUrl} · 真实下载至设置目录`
    : "本地网页工作台运行中 · 真实下载至设置目录";
}

function renderClock() {
  const sec = Math.floor((state.elapsedBase || 0) / 1000);
  const mm = String(Math.floor(sec / 60)).padStart(2, "0");
  const ss = String(sec % 60).padStart(2, "0");
  els.sbTime.textContent = `已用 ${mm}:${ss}`;
}

/* ---------- failures & rounds ---------- */

function pendingFailures() {
  return state.failures.filter((f) => f.status === "failed" || f.status === "queued");
}

function retryableFailures() {
  const max = state.settings.maxRetries;
  return state.failures.filter((f) => {
    const info = REASONS[f.reason] || REASONS.network;
    return f.status === "failed" && info.retryable && f.tries < max;
  });
}

function isExhausted(failure) {
  const info = REASONS[failure.reason] || REASONS.network;
  return !info.retryable || failure.tries >= state.settings.maxRetries;
}

function renderRoundbar() {
  const summary = state.roundSummary;
  if (!summary) {
    els.roundbar.classList.add("hidden");
    return;
  }
  const clean = summary.failedFiles === 0;
  els.roundbar.classList.remove("hidden");
  els.roundbar.classList.toggle("is-clean", clean);
  els.rbTitle.textContent = clean ? `第 ${summary.round} 轮结束，全部成功` : `第 ${summary.round} 轮结束，${summary.failedFiles} 个文件失败`;
  const parts = [`本次新增 ${summary.okFiles} 个文件`];
  if (Number(summary.skippedFiles) > 0) parts.push(`跳过 ${summary.skippedFiles} 个已下载`);
  parts.push(`完成账号 ${summary.okAccounts}`);
  if (!clean) {
    parts.push(`可重试 ${summary.retryable}`);
    const stuck = summary.failedFiles - summary.retryable;
    if (stuck > 0) parts.push(`需处理 ${stuck}`);
  }
  els.rbDetail.textContent = parts.join(" · ");
  els.btnRoundRetry.disabled = summary.retryable === 0;
  els.btnRoundRetryLabel.textContent = summary.retryable > 0 ? `一键重试 ${summary.retryable} 个` : "没有可重试项";
  $("btn-round-review").classList.toggle("hidden", clean);
}

function visibleFailures() {
  const query = state.failedSearch.trim().toLowerCase();
  const items = state.failures.filter((failure) => {
    if (failure.status === "done") return false;
    if (state.failedFilter === "exhausted") {
      if (!isExhausted(failure)) return false;
    } else if (state.failedFilter !== "all" && failure.reason !== state.failedFilter) {
      return false;
    }
    if (query && !failure.name.toLowerCase().includes(query) && !failure.user.toLowerCase().includes(query)) return false;
    return true;
  });
  if (state.failedSortKey) {
    const key = state.failedSortKey;
    const dir = state.failedSortDir;
    items.sort((a, b) => {
      if (key === "file") return dir * a.name.localeCompare(b.name);
      if (key === "reason") return dir * (REASONS[a.reason]?.label || "").localeCompare(REASONS[b.reason]?.label || "");
      return dir * (a.tries - b.tries);
    });
  }
  return items;
}

function failureStateHtml(failure) {
  const info = REASONS[failure.reason] || REASONS.network;
  if (failure.status === "queued") return '<span class="st st-active">重试中</span>';
  if (!info.retryable) return '<span class="st st-failed">不可重试</span>';
  if (failure.tries >= state.settings.maxRetries) return '<span class="st st-failed">已达上限</span>';
  return '<span class="st st-waiting">待重试</span>';
}

function failureRowHtml(failure) {
  const checked = state.failedSelected.has(failure.id) ? "checked" : "";
  const reason = REASONS[failure.reason] || REASONS.network;
  const exhausted = isExhausted(failure);
  const retryDisabled = exhausted || failure.status === "queued" ? "disabled" : "";
  return `<div class="frow${failure.status === "queued" ? " is-active" : ""}" data-fid="${failure.id}">
    <span class="fcell f-check"><input type="checkbox" data-fact="select" data-fid="${failure.id}" ${checked} aria-label="选择 ${escapeHtml(failure.name)}"></span>
    <button class="fcell f-file" data-fact="detail" data-fid="${failure.id}" type="button" title="${escapeHtml(reason.hint)}">
      <span class="f-name">${escapeHtml(failure.name)}</span>
      <span class="f-owner">@${escapeHtml(failure.user)} · ${escapeHtml(failure.size)}</span>
    </button>
    <span class="fcell f-reason"><span class="reason reason-${escapeHtml(failure.reason)}">${reason.label}</span></span>
    <span class="fcell f-tries${exhausted ? " at-limit" : ""}">${failure.tries}/${state.settings.maxRetries}</span>
    <span class="fcell f-state">${failureStateHtml(failure)}</span>
    <span class="fcell f-ops">
      <button class="iconbtn" data-fact="retry" data-fid="${failure.id}" type="button" ${retryDisabled} title="重试这个文件" aria-label="重试 ${escapeHtml(failure.name)}">
        <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M20 11a8 8 0 1 0-2.3 6.3"/><path d="M20 4v7h-7"/></svg>
      </button>
      <button class="iconbtn danger" data-fact="discard" data-fid="${failure.id}" type="button" title="放弃这个文件" aria-label="放弃 ${escapeHtml(failure.name)}">
        <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg>
      </button>
    </span>
  </div>`;
}

function renderFailed() {
  const items = visibleFailures();
  const existing = new Map();
  els.failedList.querySelectorAll(".frow").forEach((row) => existing.set(Number(row.dataset.fid), row));
  const wanted = new Set(items.map((f) => f.id));
  existing.forEach((row, id) => { if (!wanted.has(id)) row.remove(); });
  items.forEach((failure, index) => {
    let row = existing.get(failure.id);
    if (!row) {
      const holder = document.createElement("div");
      holder.innerHTML = failureRowHtml(failure);
      row = holder.firstElementChild;
    } else {
      const exhausted = isExhausted(failure);
      row.classList.toggle("is-active", failure.status === "queued");
      const triesCell = row.querySelector(".f-tries");
      triesCell.textContent = `${failure.tries}/${state.settings.maxRetries}`;
      triesCell.classList.toggle("at-limit", exhausted);
      const stateCell = row.querySelector(".f-state");
      const stateHtml = failureStateHtml(failure);
      if (stateCell.innerHTML !== stateHtml) stateCell.innerHTML = stateHtml;
      row.querySelector("input[data-fact=select]").checked = state.failedSelected.has(failure.id);
      row.querySelector("[data-fact=retry]").disabled = exhausted || failure.status === "queued";
    }
    const atPosition = els.failedList.children[index];
    if (atPosition !== row) els.failedList.insertBefore(row, atPosition || null);
  });

  const pending = pendingFailures();
  els.failedEmpty.classList.toggle("hidden", pending.length > 0);
  els.failedCount.textContent = String(pending.length);
  els.failedCount.classList.toggle("count-zero", pending.length === 0);

  const byReason = { all: pending.length, timeout: 0, network: 0, forbidden: 0, exhausted: 0 };
  pending.forEach((f) => {
    if (byReason[f.reason] !== undefined) byReason[f.reason] += 1;
    if (isExhausted(f)) byReason.exhausted += 1;
  });
  $("f-all").textContent = byReason.all;
  $("f-timeout").textContent = byReason.timeout;
  $("f-network").textContent = byReason.network;
  $("f-forbidden").textContent = byReason.forbidden;
  $("f-exhausted").textContent = byReason.exhausted;

  const retryable = retryableFailures();
  els.btnFailedRetry.disabled = retryable.length === 0;
  els.btnFailedRetryLabel.textContent = retryable.length > 0 ? `重试全部失败（${retryable.length}）` : "没有可重试项";
  els.btnFailedRetrySelected.disabled = state.failedSelected.size === 0;
  els.btnFailedDiscard.disabled = state.failedSelected.size === 0;
  const visibleIds = items.map((f) => f.id);
  els.failedSelectAll.checked = visibleIds.length > 0 && visibleIds.every((id) => state.failedSelected.has(id));

  [["file", "fsort-file-arrow"], ["reason", "fsort-reason-arrow"], ["tries", "fsort-tries-arrow"]].forEach(([key, id]) => {
    $(id).textContent = state.failedSortKey === key ? (state.failedSortDir === 1 ? "▲" : "▼") : "";
  });
}

async function retryFailures(ids, auto) {
  if (drawerId !== null) closeDrawer();
  const targets = state.failures.filter((f) => ids.includes(f.id) && f.status === "failed");
  const usable = targets.filter((f) => !isExhausted(f));
  if (usable.length === 0) {
    toast("所选文件已达重试上限或原因不可重试", "warn");
    return;
  }
  const data = await postState("/api/retry", { ids: usable.map((f) => f.id) });
  if (!data) return;
  state.failedSelected.clear();
  startPolling();
  if (!auto) {
    toast(
      data.queued > 0 ? `第 ${state.round} 轮：重试 ${data.queued} 个失败文件` : "没有可重试的文件",
      data.queued > 0 ? "ok" : "warn"
    );
  }
}

/* ---------- completed media ---------- */

async function loadCompleted(force) {
  if (state.doneLoaded && !force) return;
  if (state.doneLoading) return;
  state.doneLoading = true;
  try {
    const params = new URLSearchParams({ limit: String(DONE_PAGE_SIZE), offset: "0" });
    if (state.doneFilter !== "all") params.set("kind", state.doneFilter);
    if (state.doneSearch.trim()) params.set("q", state.doneSearch.trim());
    const data = await api(`/api/completed?${params.toString()}`);
    state.doneRows = Array.isArray(data.rows) ? data.rows : [];
    state.doneTotals = data.totals || { all: 0, images: 0, videos: 0 };
    state.doneHasMore = Boolean(data.hasMore);
    state.doneLoaded = true;
    renderCompleted();
  } catch (error) {
    toast(error.message || "读取下载记录失败", "bad");
  } finally {
    state.doneLoading = false;
  }
}

async function loadMoreCompleted() {
  if (state.doneLoading || !state.doneHasMore) return;
  state.doneLoading = true;
  const button = $("btn-done-more");
  if (button) { button.disabled = true; button.textContent = "加载中…"; }
  try {
    const params = new URLSearchParams({
      limit: String(DONE_PAGE_SIZE),
      offset: String(state.doneRows.length),
    });
    if (state.doneFilter !== "all") params.set("kind", state.doneFilter);
    if (state.doneSearch.trim()) params.set("q", state.doneSearch.trim());
    const data = await api(`/api/completed?${params.toString()}`);
    const rows = Array.isArray(data.rows) ? data.rows : [];
    const seen = new Set(state.doneRows.map((row) => row.path || `${row.username}/${row.filename}`));
    const fresh = rows.filter((row) => !seen.has(row.path || `${row.username}/${row.filename}`));
    state.doneRows = state.doneRows.concat(fresh);
    state.doneHasMore = Boolean(data.hasMore) && fresh.length > 0;
    if (data.totals) state.doneTotals = data.totals;
    renderCompleted();
  } catch (error) {
    toast(error.message || "加载更多失败", "bad");
  } finally {
    state.doneLoading = false;
    if (button) { button.disabled = false; button.textContent = "加载更多"; }
  }
}

function visibleCompleted() {
  const keyword = state.doneSearch.trim().toLowerCase();
  return state.doneRows.filter((row) => {
    if (state.doneFilter === "images" || state.doneFilter === "videos") {
      const isVideo = isVideoFile(row.filename);
      if (state.doneFilter === "images" && isVideo) return false;
      if (state.doneFilter === "videos" && !isVideo) return false;
    }
    if (!keyword) return true;
    return (
      String(row.filename || "").toLowerCase().includes(keyword)
      || String(row.username || "").toLowerCase().includes(keyword)
    );
  });
}

function completedRowHtml(row) {
  const isVideo = isVideoFile(row.filename);
  return `<div class="crow" data-tweet="${escapeHtml(row.tweetId || "")}" data-user="${escapeHtml(row.username || "")}" data-file="${escapeHtml(row.filename || "")}" data-size="${Number(row.size) || 0}">
    <span class="kdot ${isVideo ? "v" : "i"}" title="${isVideo ? "视频" : "图片"}" aria-label="${isVideo ? "视频" : "图片"}"></span>
    <span class="cname" title="${escapeHtml(row.path || row.filename || "")}">${escapeHtml(row.filename || "")}</span>
    <span class="cid" title="${escapeHtml(row.tweetId || "")}">${escapeHtml(row.tweetId || "—")}</span>
    <span class="csize">${formatBytes(row.size)}</span>
    <span class="ctime" title="${escapeHtml(formatWhen(row.downloadedAt))}">${escapeHtml(shortWhen(row.downloadedAt))}</span>
    <span class="ops">
      <button class="iconbtn" data-dact="preview" type="button" title="在线预览" aria-label="预览 ${escapeHtml(row.filename || "")}">${ICON_PREVIEW}</button>
      <button class="iconbtn" data-dact="tweet" type="button" title="打开原推文" aria-label="打开原推文">${ICON_TWEET}</button>
      <button class="iconbtn" data-dact="folder" type="button" title="打开所在文件夹" aria-label="打开所在文件夹">${ICON_FOLDER}</button>
    </span>
  </div>`;
}

function completedGroups(rows) {
  const groups = new Map();
  for (const row of rows) {
    const key = String(row.username || "").toLowerCase();
    let group = groups.get(key);
    if (!group) {
      group = { user: row.username || "", rows: [], bytes: 0, latest: "" };
      groups.set(key, group);
    }
    group.rows.push(row);
    group.bytes += Number(row.size) || 0;
    const stamp = String(row.downloadedAt || "");
    if (stamp > group.latest) group.latest = stamp;
  }
  return [...groups.values()].sort((a, b) => String(b.latest).localeCompare(String(a.latest)));
}

function groupHeadHtml(group) {
  const initial = (group.user || "?").slice(0, 1);
  return `<div class="cgroup" data-user="${escapeHtml(group.user)}">
    <span class="avatar" aria-hidden="true">${escapeHtml(initial)}</span>
    <span class="gname">@${escapeHtml(group.user)}</span>
    <span class="gmeta">${group.rows.length} 个文件 · ${formatBytes(group.bytes)}</span>
    <span class="gtime">最近 ${escapeHtml(shortWhen(group.latest))}</span>
    <span class="right"><button class="iconbtn" data-dact="folder" type="button" title="打开 @${escapeHtml(group.user)} 文件夹" aria-label="打开文件夹">${ICON_FOLDER}</button></span>
  </div>`;
}

function renderCompleted() {
  const list = $("done-list");
  if (!list) return;
  const rows = visibleCompleted();
  const groups = completedGroups(rows);
  list.innerHTML = groups
    .map((group) => groupHeadHtml(group) + group.rows.map(completedRowHtml).join(""))
    .join("");
  $("done-empty").classList.toggle("hidden", rows.length > 0);
  const allCount = state.doneTotals.all || 0;
  $("done-count").textContent = String(allCount);
  $("d-all").textContent = allCount;
  $("d-images").textContent = state.doneTotals.images || 0;
  $("d-videos").textContent = state.doneTotals.videos || 0;
  updateMobileBadges();

  const moreRow = $("done-more");
  if (moreRow) {
    const more = state.doneHasMore && rows.length > 0;
    moreRow.classList.toggle("hidden", !more);
    if (more) {
      const scope = state.doneSearch.trim()
        ? "匹配记录"
        : { all: "条", images: "张图片", videos: "个视频" }[state.doneFilter] || "条";
      $("done-more-note").textContent = `已显示 ${rows.length} ${scope}`;
    }
  }
}

/* ---------- media preview modal ---------- */

function openMediaModal({ user, filename, tweetId, size }) {
  if (!user || !filename) return;
  if (els.mediaModal.classList.contains("hidden")) mediaReturnFocus = document.activeElement;
  currentMediaPreview = { user, filename, tweetId };
  const fileUrl = `/api/media/file?user=${encodeURIComponent(user)}&filename=${encodeURIComponent(filename)}`;
  $("media-modal-title").textContent = filename;
  $("media-modal-sub").textContent = [
    `@${user}`,
    size ? formatBytes(size) : "",
    tweetId ? `推文 ${tweetId}` : "",
  ].filter(Boolean).join(" · ");

  const stage = $("media-stage");
  if (isVideoFile(filename)) {
    stage.innerHTML = `<video src="${escapeHtml(fileUrl)}" controls playsinline autoplay preload="metadata"></video>`;
  } else {
    stage.innerHTML = `<img src="${escapeHtml(fileUrl)}" alt="${escapeHtml(filename)}">`;
  }

  $("media-modal-tweet").disabled = !tweetId;
  $("media-modal-download").href = `${fileUrl}&download=1`;
  $("media-modal-download").download = filename;
  els.mediaModal.classList.remove("hidden");
  $("media-modal-close").focus();
}

function closeMediaModal() {
  const wasOpen = !els.mediaModal.classList.contains("hidden");
  const returnFocus = mediaReturnFocus;
  mediaReturnFocus = null;
  const video = $("media-stage").querySelector("video");
  if (video) { try { video.pause(); video.src = ""; } catch {} }
  $("media-stage").innerHTML = "";
  currentMediaPreview = null;
  els.mediaModal.classList.add("hidden");
  if (wasOpen) restoreFocus(returnFocus);
}

/* ---------- view & mobile tab switching ---------- */

function setView(view) {
  state.view = view;
  const map = { accounts: "view-accounts", logs: "view-logs", failed: "view-failed", done: "view-done" };
  Object.entries(map).forEach(([key, id]) => {
    const on = key === view;
    $(id).classList.toggle("active", on);
    $(id).setAttribute("aria-selected", String(on));
    $(id).tabIndex = on ? 0 : -1;
  });
  $("accounts-view").classList.toggle("hidden", view !== "accounts");
  $("logs-view").classList.toggle("hidden", view !== "logs");
  $("failed-view").classList.toggle("hidden", view !== "failed");
  $("done-view").classList.toggle("hidden", view !== "done");

  if (view === "logs") {
    state.logUnread = false;
    fetchLogs().then(renderLogs);
  }
  if (view === "done") loadCompleted();
  if (view !== "accounts") setImportCollapsed(true);

  if (window.matchMedia("(max-width: 900px)").matches) {
    if (view === "logs") syncMobileNav("logs", false);
    else if (view === "done") syncMobileNav("done", false);
    else syncMobileNav("queue", false);
  }
  render();
}

function syncMobileNav(tab, alsoSwitchView = true) {
  state.mobileTab = tab;
  document.body.dataset.mobileTab = tab;
  document.querySelectorAll(".mob-btn[data-mobile-tab]").forEach((btn) => {
    const on = btn.dataset.mobileTab === tab;
    btn.classList.toggle("active", on);
    btn.setAttribute("aria-pressed", String(on));
  });
  if (alsoSwitchView) {
    if (tab === "queue" && (state.view === "logs" || state.view === "done")) setView("accounts");
    else if (tab === "logs" && state.view !== "logs") setView("logs");
    else if (tab === "done" && state.view !== "done") setView("done");
  }
  if (tab === "logs") {
    state.logUnread = false;
    updateMobileBadges();
  }
}

function updateMobileBadges() {
  const qBadge = $("mob-queue-badge");
  const dBadge = $("mob-done-badge");
  const lDot = $("mob-log-dot");
  const fDot = $("mob-filter-dot");
  if (qBadge) {
    qBadge.textContent = String(state.queue.length);
    qBadge.classList.toggle("hidden", state.queue.length === 0);
  }
  if (dBadge) {
    const cnt = state.doneTotals.all || 0;
    dBadge.textContent = cnt > 999 ? "999+" : String(cnt);
    dBadge.classList.toggle("hidden", cnt === 0);
  }
  if (lDot) lDot.classList.toggle("hidden", !state.logUnread && !state.running);
  if (fDot) fDot.classList.toggle("hidden", !describeFilter(state.settings));
}

/* ---------- settings & video duration filter ---------- */

function syncDurationUI() {
  const s = state.settings;
  const mediaType = s.mediaType || "all";
  const isImagesOnly = mediaType === "images";
  const enabled = Boolean(s.durationFilterEnabled) && !isImagesOnly;
  const totalSec = clampInt(Number(s.maxVideoDuration), 5, 7200, 60);
  const mins = Math.floor(totalSec / 60);
  const secs = totalSec % 60;

  document.querySelectorAll(".seg-btn[data-media-type]").forEach((btn) => {
    const active = btn.dataset.mediaType === mediaType;
    btn.classList.toggle("active", active);
    btn.setAttribute("aria-pressed", String(active));
  });

  els.setDurationEnabled.checked = Boolean(s.durationFilterEnabled);
  els.setDurationEnabled.disabled = isImagesOnly;
  els.durationControls.classList.toggle("is-disabled", !enabled);
  els.durationControls.setAttribute("aria-disabled", String(!enabled));
  els.durationControls.querySelectorAll("button, input").forEach((control) => {
    control.disabled = !enabled;
  });

  if (document.activeElement !== els.durMin) els.durMin.value = String(mins);
  if (document.activeElement !== els.durSec) els.durSec.value = String(secs);
  if (document.activeElement !== els.durSlider) els.durSlider.value = String(Math.min(600, totalSec));
  els.durTotalBadge.textContent = `= ${totalSec} 秒 · --max-duration ${totalSec}`;

  document.querySelectorAll(".preset-btn[data-duration-preset]").forEach((btn) => {
    const val = Number(btn.dataset.durationPreset);
    const active = enabled && val === totalSec;
    btn.classList.toggle("active", active);
    btn.setAttribute("aria-pressed", String(active));
  });

  const summaryLabel = describeFilter(s);
  const hasAnyFilter = Boolean(summaryLabel);
  els.filterPanel.classList.toggle("is-active", hasAnyFilter);
  els.filterStatusTag.textContent = summaryLabel || "不限";
  els.filterStatusTag.classList.toggle("tag-accent", hasAnyFilter);

  const ruleBox = els.filterRuleSummary;
  ruleBox.classList.toggle("is-active", hasAnyFilter);
  if (isImagesOnly) {
    ruleBox.textContent = "当前仅下载图片，所有视频将自动跳过且不写入已下载档案。";
  } else if (mediaType === "videos" && enabled) {
    ruleBox.textContent = `仅下载视频，且单条时长 ≤ ${formatDuration(totalSec)}（${totalSec} 秒）；超时视频自动跳过且不写入档案，放宽限制后可再次补下。`;
  } else if (mediaType === "videos") {
    ruleBox.textContent = "仅下载视频（不限制单条时长），纯图片推文将自动跳过。";
  } else if (enabled) {
    ruleBox.textContent = `下载全部图片 + 时长 ≤ ${formatDuration(totalSec)}（${totalSec} 秒）的视频；超时视频自动跳过且不写入档案。`;
  } else {
    ruleBox.textContent = "未开启媒体过滤：将下载账号下的全部图片与全部时长视频。";
  }
}

function syncSettingsInputs() {
  const s = state.settings;
  if (document.activeElement !== els.setOutput) els.setOutput.value = s.output || DEFAULTS.output;
  if (document.activeElement !== els.setCookies) els.setCookies.value = s.cookies || DEFAULTS.cookies;
  els.setCookiesBrowser.value = s.cookiesBrowser || "chrome";
  const isBrowser = s.cookiesSource === "browser";
  document.querySelectorAll(".seg-btn[data-cookies-source]").forEach((btn) => {
    const on = btn.dataset.cookiesSource === (isBrowser ? "browser" : "file");
    btn.classList.toggle("active", on);
    btn.setAttribute("aria-pressed", String(on));
  });
  $("cookies-file-box").classList.toggle("hidden", isBrowser);
  $("cookies-browser-box").classList.toggle("hidden", !isBrowser);

  els.setProxyMode.value = s.proxyMode || "auto";
  if (document.activeElement !== els.setProxyAddr) els.setProxyAddr.value = s.proxyAddr || DEFAULTS.proxyAddr;
  els.setProxyAddr.disabled = s.proxyMode !== "manual";

  els.setIncremental.checked = s.incremental !== false;
  if (document.activeElement !== els.setStopAfterSkips) {
    els.setStopAfterSkips.value = s.stopAfterSkips ?? DEFAULTS.stopAfterSkips;
  }
  els.setStopAfterSkips.disabled = !els.setIncremental.checked;

  if (document.activeElement !== els.setLimit) els.setLimit.value = s.limit ?? 0;
  if (document.activeElement !== els.setConcurrency) els.setConcurrency.value = s.concurrency ?? 2;
  els.setMediaOnly.checked = s.mediaOnly !== false;
  if (document.activeElement !== els.setMaxRetries) els.setMaxRetries.value = s.maxRetries ?? 3;
  els.setAutoRetry.checked = Boolean(s.autoRetry);

  syncLayoutButtons();
  syncDurationUI();
  renderCmdPreview();
}

function readSettingsFromInputs() {
  const minVal = clampInt(Number(els.durMin.value), 0, 120, 1);
  const secVal = clampInt(Number(els.durSec.value), 0, 59, 0);
  let totalDur = minVal * 60 + secVal;
  if (totalDur < 5) totalDur = 5;
  if (totalDur > 7200) totalDur = 7200;

  state.settings = {
    ...state.settings,
    output: els.setOutput.value.trim() || DEFAULTS.output,
    cookies: els.setCookies.value.trim() || DEFAULTS.cookies,
    cookiesBrowser: els.setCookiesBrowser.value || "chrome",
    proxyMode: els.setProxyMode.value,
    proxyAddr: els.setProxyAddr.value.trim() || DEFAULTS.proxyAddr,
    limit: clampInt(Number(els.setLimit.value), 0, 10000, DEFAULTS.limit),
    concurrency: clampInt(Number(els.setConcurrency.value), 1, 5, DEFAULTS.concurrency),
    mediaOnly: els.setMediaOnly.checked,
    maxRetries: clampInt(Number(els.setMaxRetries.value), 1, 5, DEFAULTS.maxRetries),
    autoRetry: els.setAutoRetry.checked,
    incremental: els.setIncremental.checked,
    stopAfterSkips: clampInt(Number(els.setStopAfterSkips.value), 0, 500, DEFAULTS.stopAfterSkips),
    durationFilterEnabled: els.setDurationEnabled.checked,
    maxVideoDuration: totalDur,
  };
}

async function commitSettings(patch) {
  if (patch) state.settings = { ...state.settings, ...patch };
  syncSettingsInputs();
  renderEnvChips();
  render();
  await postState("/api/settings", state.settings);
}

function renderCmdPreview() {
  const s = state.settings;
  const lines = ["xdl <用户名>"];
  lines.push(`  -o "${s.output}"`);
  if (s.cookiesSource === "browser") {
    lines.push(`  --cookies-from-browser ${s.cookiesBrowser || "chrome"}`);
  } else {
    lines.push(`  --cookies "${s.cookies}"`);
  }
  if (s.proxyMode === "manual") lines.push(`  --proxy ${s.proxyAddr}`);
  if (s.proxyMode === "off") lines.push("  --no-proxy");
  if (s.limit > 0) lines.push(`  --limit ${s.limit}`);
  if (s.mediaOnly) lines.push("  --media-only");
  if (s.mediaType === "videos") lines.push("  --videos-only");
  else if (s.mediaType === "images") lines.push("  --images-only");
  const maxDur = effectiveMaxDuration(s);
  if (maxDur > 0) lines.push(`  --max-duration ${maxDur}`);
  if (!s.incremental) lines.push("  --full");
  else if (s.stopAfterSkips > 0) lines.push(`  --stop-after-skips ${s.stopAfterSkips}`);
  els.cmdPreview.textContent = lines.join(" \\\n");
}

/* ---------- folder tree ---------- */

function syncLayoutButtons() {
  const per = state.settings.layout !== "flat";
  $("layout-per-user").classList.toggle("active", per);
  $("layout-flat").classList.toggle("active", !per);
  $("layout-per-user").setAttribute("aria-pressed", String(per));
  $("layout-flat").setAttribute("aria-pressed", String(!per));
}

function renderTree() {
  const el = $("folder-tree");
  if (!el) return;
  const rootName = (state.settings.output || "XDL").split(/[\\/]/).filter(Boolean).pop() || "XDL";
  const items = state.queue;
  const totalFiles = items.reduce((acc, item) => acc + (Number(item.files) || 0), 0);
  const branch = (text) => `<span class="t-branch">${text}</span>`;
  const lines = [`<span class="t-root">${escapeHtml(rootName)}\\</span>`];
  const flat = state.settings.layout === "flat";

  if (items.length === 0) {
    lines.push('<span class="t-more">队列为空，导入用户名后显示目录预览</span>');
  } else if (flat) {
    const shown = items.slice(0, 5);
    shown.forEach((item, index) => {
      const last = index === shown.length - 1 && items.length <= 5;
      const sampleName = `${item.user}_2026-11-22_1800000000_001.mp4`;
      lines.push(`${branch(last ? "└── " : "├── ")}<span class="t-file">${escapeHtml(sampleName)}</span>`);
    });
    if (items.length > 5) lines.push(`${branch("└── ")}<span class="t-more">其余 ${items.length - 5} 个账号文件</span>`);
  } else {
    const shown = items.slice(0, 6);
    shown.forEach((item, index) => {
      const lastShown = index === shown.length - 1 && items.length <= 6;
      const head = lastShown ? "└── " : "├── ";
      const pad = lastShown ? "    " : "│   ";
      const count = item.files > 0 ? `${item.files} 个文件` : "待下载";
      lines.push(`${branch(head)}<span class="t-dir">${escapeHtml(item.user)}\\</span>  <span class="t-count">${count}</span>`);
      if (item.files > 0) {
        lines.push(`${branch(pad + "└── ")}<span class="t-file">2026-11-22_..._001.mp4</span>`);
      }
    });
    if (items.length > 6) lines.push(`${branch("└── ")}<span class="t-more">其余 ${items.length - 6} 个用户文件夹</span>`);
  }

  el.innerHTML = lines.join("\n");
  $("tree-mode-tag").textContent = flat ? "同一文件夹" : "按用户名分";
  $("tree-note").textContent = flat
    ? `全部媒体平铺存放于下载目录，文件名带用户名前缀。共 ${totalFiles} 个文件`
    : `与 CLI 一致：下载目录\\用户名\\。当前 ${items.length} 个账号 · ${totalFiles} 个文件`;
}

/* ---------- env chips ---------- */

function renderEnvChips() {
  const env = state.env || {};
  const cookies = $("env-cookies");
  cookies.querySelector(".dot").className = "dot " + (env.cookiesFound ? "dot-ok" : "dot-bad");
  $("env-cookies-text").textContent = env.cookiesSource === "browser"
    ? `Cookie (${env.cookiesBrowser || "chrome"})`
    : (env.cookiesFound ? "Cookie 已配置" : "未找到 Cookie");

  const proxy = $("env-proxy");
  const mode = state.settings.proxyMode;
  let proxyText = "直连模式";
  let proxyClass = "dot-warn";
  if (mode === "manual") {
    proxyText = "手动代理";
    proxyClass = "dot-ok";
  } else if (mode === "auto") {
    proxyText = env.proxyOnline ? "代理 7890 在线" : "代理未监听（直连）";
    proxyClass = env.proxyOnline ? "dot-ok" : "dot-warn";
  }
  proxy.querySelector(".dot").className = "dot " + proxyClass;
  $("env-proxy-text").textContent = proxyText;

  const version = env.galleryDl && env.galleryDl !== "未检测到" ? env.galleryDl : "";
  $("env-gdl").textContent = version
    ? (version.toLowerCase().startsWith("gallery-dl") ? version : `gallery-dl ${version}`)
    : "gallery-dl 未检测到";

  const store = env.store || {};
  const storeChip = $("env-store");
  const dot = storeChip.querySelector(".dot");
  if (store.ready) {
    dot.className = "dot dot-ok";
    $("env-store-text").textContent = `档案 ${Number(store.media) || 0} 个媒体`;
  } else {
    dot.className = "dot dot-warn";
    $("env-store-text").textContent = "档案准备中…";
  }
  storeChip.title = store.archive ? `下载档案：${store.archive}` : "下载档案尚未就绪";

  const filterChip = $("env-filter");
  const filtText = describeFilter(state.settings);
  if (filtText) {
    $("env-filter-text").textContent = filtText;
    filterChip.classList.remove("hidden");
  } else {
    filterChip.classList.add("hidden");
  }

  const addrText = env.lanUrl || env.serverUrl || `${window.location.host}`;
  if (addrText) {
    $("server-url-text").textContent = addrText.replace(/^https?:\/\//, "").replace(/\/$/, "");
  }
}

/* ---------- drawers ---------- */

function drawerCountText(item) {
  const store = (state.env && state.env.store) || {};
  const total = Number(item.downloadedTotal ?? item.files) || 0;
  const tweets = drawerHistory && drawerHistory.summary ? Number(drawerHistory.summary.tweets) : null;
  if (!store.ready && total === 0) return "下载档案准备中…";
  let text = `已下载 ${total} 个媒体`;
  if (tweets !== null) text += ` · ${tweets} 条推文`;
  return text;
}

function mediaRowHtml(file) {
  const path = String(file.path || "");
  const name = String(file.filename || path.split(/[\\/]/).pop() || "媒体文件");
  const tweet = String(file.tweetId || "");
  const when = String(file.downloadedAt || "").slice(0, 16);
  const meta = [when, tweet ? `推文 ${tweet}` : "", file.mediaIndex ? `#${file.mediaIndex}` : ""]
    .filter(Boolean)
    .join(" · ");
  return `<li class="media-row" data-drawer-act="preview-media" data-file="${escapeHtml(name)}" data-tweet="${escapeHtml(tweet)}" data-size="${Number(file.size) || 0}" title="点击预览媒体" role="button" tabindex="0" aria-label="预览媒体 ${escapeHtml(name)}">
    <span class="media-main"><span class="media-name">${escapeHtml(name)}</span><span class="media-meta">${escapeHtml(meta)}</span></span>
    <span class="fsize">${formatBytes(file.size)}</span>
  </li>`;
}

function refreshDrawer(item) {
  const body = els.drawerBody;
  const statusValue = body.querySelector("[data-drawer-status]");
  const progressValue = body.querySelector("[data-drawer-progress]");
  const countsValue = body.querySelector("[data-drawer-counts]");
  const runValue = body.querySelector("[data-drawer-run]");
  if (statusValue) {
    statusValue.textContent = item.status === "failed" && item.error ? `失败 · ${item.error}` : (STATUS_LABEL[item.status] || item.status);
  }
  if (progressValue) progressValue.textContent = `${Math.round(item.progress || 0)}% · ${item.files} 个文件`;
  if (countsValue) countsValue.textContent = drawerCountText(item);
  if (runValue) {
    runValue.textContent = item.lastRunAt
      ? `${item.lastRunAt} · 新增 ${Number(item.newFiles) || 0} · 跳过 ${Number(item.skippedFiles) || 0}`
      : "尚未运行";
  }
}

function renderDrawer(item) {
  const dir = state.settings.layout === "flat" ? state.settings.output : `${state.settings.output}\\${item.user}`;
  const url = `https://x.com/${item.user}${state.settings.mediaOnly ? "/media" : ""}`;
  const statusLine = item.status === "failed" && item.error
    ? `失败 · ${escapeHtml(item.error)}`
    : (STATUS_LABEL[item.status] || item.status);
  const media = drawerHistoryOpen
    ? ((drawerHistory && drawerHistory.history) || [])
    : (Array.isArray(item.recentMedia) ? item.recentMedia : []);
  const runText = item.lastRunAt
    ? `${item.lastRunAt} · 新增 ${Number(item.newFiles) || 0} · 跳过 ${Number(item.skippedFiles) || 0}`
    : "尚未运行";
  const filt = item.filterSummary || describeFilter(state.settings) || "不限";

  els.drawerBody.innerHTML = `
    <dl class="kv">
      <dt>状态</dt><dd data-drawer-status>${statusLine}</dd>
      <dt>进度</dt><dd data-drawer-progress>${Math.round(item.progress || 0)}% · ${item.files} 个文件</dd>
      <dt>筛选规则</dt><dd>${escapeHtml(filt)}</dd>
      <dt>本地档案</dt><dd data-drawer-counts>${drawerCountText(item)}</dd>
      <dt>最近一次</dt><dd data-drawer-run>${escapeHtml(runText)}</dd>
      <dt>目标页面</dt><dd>${escapeHtml(url)}</dd>
      <dt>保存目录</dt><dd>${escapeHtml(dir)}</dd>
      <dt>条数上限</dt><dd>${state.settings.limit > 0 ? "最近 " + state.settings.limit + " 条" : "全部"}</dd>
    </dl>
    <div class="drawer-section-head">
      <p class="drawer-section-title">${drawerHistoryOpen ? "全部下载记录（最近 50 个）" : "最近下载的媒体"}</p>
      <button class="linkbtn" type="button" data-drawer-act="history-toggle">${drawerHistoryOpen ? "收起" : "查看全部"}</button>
    </div>
    ${media.length > 0
      ? `<ul class="filelist">${media.map(mediaRowHtml).join("")}</ul>`
      : `<p class="drawer-note">${drawerHistoryOpen ? "还没有下载记录。" : "运行下载后会在此列出最近的媒体。"}</p>`}
    <div class="drawer-actions">
      <button class="btn ghost btn-sm" data-drawer-act="view-logs" type="button">查看日志</button>
      <button class="btn ghost btn-sm" data-drawer-act="open-dir" type="button">打开目录</button>
      <button class="btn ghost btn-sm" data-drawer-act="open-page" type="button">打开主页</button>
      ${item.status === "failed" ? '<button class="btn primary btn-sm" data-drawer-act="retry" type="button">重试账号</button>' : ""}
      <button class="btn danger-ghost btn-sm" data-drawer-act="remove" type="button">移出队列</button>
    </div>
    <p class="drawer-note">点击上方任意媒体记录可在网页内直接预览图片或播放视频。</p>`;
}

function openDrawer(id) {
  const item = state.queue.find((entry) => entry.id === id);
  if (!item) return;
  if (els.drawer.classList.contains("hidden")) drawerReturnFocus = document.activeElement;
  drawerId = id;
  drawerHistory = null;
  drawerHistoryOpen = false;
  els.drawerTitle.textContent = `@${item.user}`;
  renderDrawer(item);
  els.drawer.classList.remove("hidden");
  els.drawerBackdrop.classList.remove("hidden");
  $("drawer-close").focus();
  api(`/api/history?user=${encodeURIComponent(item.user)}&limit=50`)
    .then((data) => {
      if (drawerId !== id || !data) return;
      drawerHistory = data;
      const current = state.queue.find((entry) => entry.id === id);
      if (current) renderDrawer(current);
    })
    .catch(() => {});
}

function openFailureDrawer(failure) {
  if (els.drawer.classList.contains("hidden")) drawerReturnFocus = document.activeElement;
  drawerId = null;
  const reason = REASONS[failure.reason] || REASONS.network;
  const exhausted = isExhausted(failure);
  const dir = state.settings.layout === "flat" ? state.settings.output : `${state.settings.output}\\${failure.user}`;
  const stateLabel = failure.status === "queued" ? "重试中" : !reason.retryable ? "不可重试" : exhausted ? "已达重试上限" : "待重试";
  els.drawerTitle.textContent = failure.name.length > 28 ? `${failure.name.slice(0, 26)}…` : failure.name;
  els.drawerBody.innerHTML = `
    <dl class="kv">
      <dt>博主</dt><dd>@${escapeHtml(failure.user)}</dd>
      <dt>文件名</dt><dd>${escapeHtml(failure.name)}</dd>
      <dt>大小</dt><dd>${escapeHtml(failure.size)}</dd>
      <dt>失败原因</dt><dd>${reason.label}</dd>
      <dt>错误信息</dt><dd>${escapeHtml(failure.note || "未记录")}</dd>
      <dt>当前状态</dt><dd>${stateLabel}</dd>
      <dt>已重试</dt><dd>${failure.tries} / ${state.settings.maxRetries} 次</dd>
      <dt>首次失败</dt><dd>第 ${failure.round} 轮</dd>
      <dt>保存目录</dt><dd>${escapeHtml(dir)}</dd>
    </dl>
    <p class="drawer-section-title">处理建议</p>
    <p class="drawer-note">${escapeHtml(reason.hint)}${!reason.retryable ? "，建议放弃该文件" : exhausted ? "。可调高重试上限后再试" : ""}</p>
    <div class="drawer-actions">
      <button class="btn primary btn-sm" data-fdrawer-act="retry" data-fid="${failure.id}" type="button" ${exhausted || failure.status === "queued" ? "disabled" : ""}>重试这个文件</button>
      <button class="btn ghost btn-sm" data-fdrawer-act="owner" data-fid="${failure.id}" type="button">查看该博主</button>
      <button class="btn danger-ghost btn-sm" data-fdrawer-act="discard" data-fid="${failure.id}" type="button">放弃</button>
    </div>`;
  els.drawer.classList.remove("hidden");
  els.drawerBackdrop.classList.remove("hidden");
  $("drawer-close").focus();
}

function closeDrawer() {
  const wasOpen = !els.drawer.classList.contains("hidden");
  const returnFocus = drawerReturnFocus;
  drawerReturnFocus = null;
  drawerId = null;
  els.drawer.classList.add("hidden");
  els.drawerBackdrop.classList.add("hidden");
  if (wasOpen) restoreFocus(returnFocus);
}

/* ---------- modals & toasts ---------- */

function openQuitModal() {
  if (els.closeModal.classList.contains("hidden")) quitReturnFocus = document.activeElement;
  const active = state.queue.filter((item) => item.status === "active");
  const waiting = state.queue.filter((item) => item.status === "waiting" || item.status === "paused");
  const alertBox = $("close-modal-alert");
  const alertText = $("close-modal-alert-text");
  const title = $("close-modal-title");
  const body = $("close-modal-text");
  const okBtn = $("btn-modal-ok");
  const cancelBtn = $("btn-modal-cancel");
  const taskList = $("close-modal-tasks");

  const busy = state.running || active.length > 0 || waiting.length > 0;
  if (busy) {
    title.textContent = `还有 ${active.length + waiting.length} 个任务未完成`;
    body.textContent = "停止服务会立即终止正在进行的下载进程，但已下载的文件和本地档案安全保留。";
    const parts = [];
    if (active.length) parts.push(`${active.length} 个正在下载`);
    if (waiting.length) parts.push(`${waiting.length} 个排队/暂停`);
    alertText.textContent = `${parts.join("，")}。下次启动 xdl 可继续增量抓取。`;
    alertBox.classList.remove("hidden");
    const shown = [...active, ...waiting].slice(0, 4);
    taskList.innerHTML = shown.map((item) => {
      const pct = Math.max(0, Math.min(100, Math.round(item.progress || 0)));
      return `<li><span class="tn">@${escapeHtml(item.user)}</span><span class="bar"><i style="width:${item.status === "active" ? pct : 0}%"></i></span><span class="pct">${item.status === "active" ? pct + "%" : "等待"}</span></li>`;
    }).join("");
    taskList.hidden = shown.length === 0;
    okBtn.textContent = "停止并关闭服务";
    okBtn.classList.remove("primary");
    okBtn.classList.add("danger");
    cancelBtn.textContent = "继续运行";
  } else {
    title.textContent = "停止 XDL 网页服务";
    body.textContent = "当前没有进行中的下载。确定停止本地网页后端吗？";
    alertBox.classList.add("hidden");
    taskList.innerHTML = "";
    taskList.hidden = true;
    okBtn.textContent = "停止服务";
    okBtn.classList.add("primary");
    okBtn.classList.remove("danger");
    cancelBtn.textContent = "取消";
  }
  els.closeModal.classList.remove("hidden");
  cancelBtn.focus();
}

function closeQuitModal() {
  const wasOpen = !els.closeModal.classList.contains("hidden");
  const returnFocus = quitReturnFocus;
  quitReturnFocus = null;
  els.closeModal.classList.add("hidden");
  if (wasOpen) restoreFocus(returnFocus);
}

window.__xdlQuitPrompt = function () {
  openQuitModal();
  return false;
};

function toast(message, kind) {
  const el = document.createElement("div");
  el.className = `toast${kind ? " " + kind : ""}`;
  el.textContent = message;
  els.toasts.appendChild(el);
  setTimeout(() => el.remove(), 3600);
  return el;
}

/* ---------- drag reorder ---------- */

function bindDragReorder() {
  els.queueList.addEventListener("dragstart", (event) => {
    const row = event.target.closest(".qrow");
    if (!row) return;
    dragId = Number(row.dataset.id);
    row.classList.add("dragging");
    event.dataTransfer.effectAllowed = "move";
    try { event.dataTransfer.setData("text/plain", String(dragId)); } catch {}
  });
  els.queueList.addEventListener("dragover", (event) => {
    if (dragId === null) return;
    event.preventDefault();
    const row = event.target.closest(".qrow");
    els.queueList.querySelectorAll(".drop-above, .drop-below").forEach((el) => el.classList.remove("drop-above", "drop-below"));
    if (!row || Number(row.dataset.id) === dragId) return;
    const rect = row.getBoundingClientRect();
    row.classList.add(event.clientY < rect.top + rect.height / 2 ? "drop-above" : "drop-below");
  });
  els.queueList.addEventListener("drop", (event) => {
    if (dragId === null) return;
    event.preventDefault();
    const row = event.target.closest(".qrow");
    if (row) {
      const targetId = Number(row.dataset.id);
      if (targetId !== dragId) {
        const rect = row.getBoundingClientRect();
        const before = event.clientY < rect.top + rect.height / 2;
        const from = state.queue.findIndex((entry) => entry.id === dragId);
        const [moved] = state.queue.splice(from, 1);
        let to = state.queue.findIndex((entry) => entry.id === targetId);
        if (!before) to += 1;
        state.queue.splice(to, 0, moved);
        state.sortKey = null;
        state.sortDir = 1;
        render();
        postState("/api/reorder", { ids: state.queue.map((entry) => entry.id) });
        toast(`已调整 @${moved.user} 的下载顺序`, "ok");
      }
    }
    cleanupDrag();
  });
  els.queueList.addEventListener("dragend", cleanupDrag);
}

function cleanupDrag() {
  dragId = null;
  els.queueList.querySelectorAll(".dragging, .drop-above, .drop-below").forEach((el) => el.classList.remove("dragging", "drop-above", "drop-below"));
}

/* ---------- actions ---------- */

async function startRun() {
  const data = await postState("/api/start");
  if (!data) return;
  startPolling();
  toast(`第 ${state.round} 轮开始下载`, "ok");
}

async function pauseRun() {
  const data = await postState("/api/pause");
  if (!data) return;
  startPolling();
  toast("已暂停，当前账号完成后不再启动新任务", "warn");
}

async function stopRun() {
  const data = await postState("/api/stop");
  if (!data) return;
  await fetchLogs();
  toast("已停止当前下载任务", "warn");
}

function exportFailures() {
  const pending = pendingFailures();
  if (pending.length === 0) {
    toast("没有失败文件可导出", "warn");
    return;
  }
  const lines = [
    "# XDL 失败清单",
    `# 第 ${state.round} 轮 · 共 ${pending.length} 个文件`,
    "账号\t文件\t原因\t已试\t错误",
  ];
  pending.forEach((f) => {
    const note = (f.note || "").replace(/\s+/g, " ");
    lines.push(`${f.user}\t${f.name}\t${(REASONS[f.reason] || REASONS.network).label}\t${f.tries}/${state.settings.maxRetries}\t${note}`);
  });
  downloadTextFile(`xdl-failed-round-${state.round}.txt`, lines.join("\n"));
  toast(`已导出 ${pending.length} 条失败清单`, "ok");
}

function downloadTextFile(filename, content, mime = "text/plain;charset=utf-8") {
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/* ---------- events ---------- */

function bindEvents() {
  // Top bar actions
  $("server-url-chip").addEventListener("click", async () => {
    const text = (state.env && (state.env.lanUrl || state.env.serverUrl)) || window.location.origin;
    try {
      await navigator.clipboard.writeText(text);
      toast(`已复制访问地址：${text}`, "ok");
    } catch {
      toast(`服务地址：${text}`, "ok");
    }
  });

  $("env-filter").addEventListener("click", () => {
    if (window.matchMedia("(max-width: 900px)").matches) {
      syncMobileNav("settings", false);
    }
    els.filterPanel.scrollIntoView({ behavior: "smooth", block: "nearest" });
  });

  $("btn-open-root").addEventListener("click", () => postState("/api/open", { kind: "dir" }));
  $("btn-stop-service").addEventListener("click", openQuitModal);

  // Interactive CMD bar
  els.cmdForm.addEventListener("submit", (event) => {
    event.preventDefault();
    executeCommand(els.cmdInput.value);
  });

  els.cmdInput.addEventListener("keydown", (event) => {
    if (event.key === "ArrowUp") {
      if (!state.cmdHistory.length) return;
      event.preventDefault();
      if (state.cmdHistIdx === -1) {
        state.cmdDraft = els.cmdInput.value;
        state.cmdHistIdx = state.cmdHistory.length - 1;
      } else if (state.cmdHistIdx > 0) {
        state.cmdHistIdx -= 1;
      }
      els.cmdInput.value = state.cmdHistory[state.cmdHistIdx] || "";
    } else if (event.key === "ArrowDown") {
      if (state.cmdHistIdx === -1) return;
      event.preventDefault();
      if (state.cmdHistIdx < state.cmdHistory.length - 1) {
        state.cmdHistIdx += 1;
        els.cmdInput.value = state.cmdHistory[state.cmdHistIdx] || "";
      } else {
        state.cmdHistIdx = -1;
        els.cmdInput.value = state.cmdDraft;
      }
    }
  });

  document.querySelectorAll(".cmd-chip[data-cmd]").forEach((btn) => {
    btn.addEventListener("click", () => {
      els.cmdInput.value = btn.dataset.cmd || "";
      els.cmdInput.focus();
    });
  });

  // Batch importer
  $("tab-paste").addEventListener("click", () => switchTab("paste"));
  $("tab-file").addEventListener("click", () => switchTab("file"));
  $("btn-import-toggle").addEventListener("click", () => {
    const panel = document.querySelector(".import-panel");
    setImportCollapsed(!panel.classList.contains("is-collapsed"));
  });

  els.pasteInput.addEventListener("input", updateCharCount);
  els.pasteInput.addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
      event.preventDefault();
      handlePasteImport();
    }
  });
  $("btn-add").addEventListener("click", handlePasteImport);
  $("btn-clear-input").addEventListener("click", () => {
    els.pasteInput.value = "";
    updateCharCount();
    els.parseStats.classList.add("hidden");
  });

  els.dropzone.addEventListener("dragover", (event) => {
    event.preventDefault();
    els.dropzone.classList.add("dragover");
  });
  els.dropzone.addEventListener("dragleave", () => els.dropzone.classList.remove("dragover"));
  els.dropzone.addEventListener("drop", (event) => {
    event.preventDefault();
    els.dropzone.classList.remove("dragover");
    handleFile(event.dataTransfer.files[0]);
  });
  els.fileInput.addEventListener("change", () => {
    handleFile(els.fileInput.files[0]);
    els.fileInput.value = "";
  });

  // Workspace view tabs
  $("view-accounts").addEventListener("click", () => setView("accounts"));
  $("view-logs").addEventListener("click", () => setView("logs"));
  $("view-failed").addEventListener("click", () => setView("failed"));
  $("view-done").addEventListener("click", () => setView("done"));
  const viewTablist = document.querySelector(".viewtabs[role=tablist]");
  viewTablist.addEventListener("keydown", (event) => {
    const tabs = [...viewTablist.querySelectorAll('[role="tab"]')];
    const currentIndex = tabs.indexOf(document.activeElement);
    if (currentIndex < 0) return;
    let nextIndex;
    if (event.key === "ArrowRight") nextIndex = (currentIndex + 1) % tabs.length;
    else if (event.key === "ArrowLeft") nextIndex = (currentIndex - 1 + tabs.length) % tabs.length;
    else if (event.key === "Home") nextIndex = 0;
    else if (event.key === "End") nextIndex = tabs.length - 1;
    else return;
    event.preventDefault();
    tabs[nextIndex].focus();
    tabs[nextIndex].click();
  });

  // Mobile bottom nav
  document.querySelectorAll(".mob-btn[data-mobile-tab]").forEach((btn) => {
    btn.addEventListener("click", () => syncMobileNav(btn.dataset.mobileTab, true));
  });

  // Queue filters, search, sort, selection
  document.querySelectorAll(".chip[data-filter]").forEach((chip) => {
    chip.addEventListener("click", () => {
      state.filter = chip.dataset.filter;
      document.querySelectorAll(".chip[data-filter]").forEach((other) => {
        const active = other === chip;
        other.classList.toggle("active", active);
        other.setAttribute("aria-pressed", String(active));
      });
      render();
    });
  });

  els.search.addEventListener("input", () => {
    state.search = els.search.value;
    render();
  });

  [["sort-user", "user"], ["sort-progress", "progress"], ["sort-files", "files"]].forEach(([id, key]) => {
    $(id).addEventListener("click", () => {
      if (state.sortKey === key) {
        if (state.sortDir === 1) state.sortDir = -1;
        else { state.sortKey = null; state.sortDir = 1; }
      } else {
        state.sortKey = key;
        state.sortDir = 1;
      }
      render();
    });
  });

  els.selectAll.addEventListener("change", () => {
    const items = visibleItems();
    if (els.selectAll.checked) items.forEach((item) => state.selected.add(item.id));
    else items.forEach((item) => state.selected.delete(item.id));
    render();
  });

  els.queueList.addEventListener("click", (event) => {
    const target = event.target.closest("[data-act]");
    if (!target) return;
    const id = Number(target.dataset.id);
    const item = state.queue.find((entry) => entry.id === id);
    if (!item) return;
    const act = target.dataset.act;
    if (act === "select") {
      if (target.checked) state.selected.add(id);
      else state.selected.delete(id);
      render();
    } else if (act === "remove") {
      state.selected.delete(id);
      postState("/api/remove", { ids: [id] }).then((data) => {
        if (data) toast(`已移除 @${item.user}`, "ok");
      });
    } else if (act === "retry") {
      postState("/api/retry-account", { user: item.user }).then((data) => {
        if (data) toast(`@${item.user} 已重新排队`, "ok");
        startPolling();
      });
    } else if (act === "logs") {
      state.logUser = item.user;
      setView("logs");
    } else if (act === "detail") {
      openDrawer(id);
    }
  });

  els.btnStart.addEventListener("click", () => {
    if (state.running) pauseRun();
    else startRun();
  });
  els.btnStopRun.addEventListener("click", stopRun);

  $("btn-retry-all").addEventListener("click", () => {
    const retryable = retryableFailures();
    if (retryable.length === 0) {
      toast("没有可重试的失败文件", "warn");
      return;
    }
    retryFailures(retryable.map((f) => f.id));
  });

  $("btn-clear-done").addEventListener("click", () => {
    if (!state.queue.some((item) => item.status === "done")) {
      toast("没有已完成的账号", "warn");
      return;
    }
    postState("/api/clear-done").then((data) => {
      if (data) toast("已清除完成的账号", "ok");
    });
  });

  els.btnDeleteSelected.addEventListener("click", () => {
    const ids = [...state.selected];
    if (!ids.length) return;
    postState("/api/remove", { ids }).then((data) => {
      if (data) toast(`已移除 ${ids.length} 个账号`, "ok");
    });
  });

  // Logs toolbar
  document.querySelectorAll(".chip[data-log-level]").forEach((chip) => {
    chip.addEventListener("click", () => {
      state.logLevel = chip.dataset.logLevel || "all";
      document.querySelectorAll(".chip[data-log-level]").forEach((other) => {
        const active = other === chip;
        other.classList.toggle("active", active);
        other.setAttribute("aria-pressed", String(active));
      });
      renderLogs();
    });
  });

  els.logUserFilter.addEventListener("change", () => {
    state.logUser = els.logUserFilter.value || "";
    renderLogs();
  });

  els.logSearch.addEventListener("input", () => {
    state.logSearch = els.logSearch.value || "";
    renderLogs();
  });

  $("btn-log-copy").addEventListener("click", async () => {
    const text = visibleLogs().map((e) => `[${e.ts}] [${e.level}] ${e.user ? "@" + e.user + " " : ""}${e.text}`).join("\n");
    if (!text) { toast("当前没有可复制的日志", "warn"); return; }
    try {
      await navigator.clipboard.writeText(text);
      toast("日志已复制到剪贴板", "ok");
    } catch {
      toast("复制失败", "bad");
    }
  });

  $("btn-log-export").addEventListener("click", () => {
    const text = visibleLogs().map((e) => `[${e.ts}] [${e.level}] ${e.user ? "@" + e.user + " " : ""}${e.text}`).join("\n");
    if (!text) { toast("当前没有可导出的日志", "warn"); return; }
    downloadTextFile(`xdl-logs-${Date.now()}.log`, text);
    toast("已导出运行日志", "ok");
  });

  $("btn-log-clear").addEventListener("click", async () => {
    state.logs = [];
    await postState("/api/logs/clear");
    await fetchLogs();
    renderLogs();
    toast("已清空日志", "ok");
  });

  // Roundbar & Failed view
  $("btn-round-retry").addEventListener("click", () => {
    const retryable = retryableFailures();
    if (!retryable.length) { toast("没有可重试的失败文件", "warn"); return; }
    retryFailures(retryable.map((f) => f.id));
  });
  $("btn-round-review").addEventListener("click", () => setView("failed"));
  $("btn-round-dismiss").addEventListener("click", () => {
    state.roundSummary = null;
    renderRoundbar();
  });

  els.btnFailedRetry.addEventListener("click", () => {
    const retryable = retryableFailures();
    if (!retryable.length) { toast("没有可重试的失败文件", "warn"); return; }
    retryFailures(retryable.map((f) => f.id));
  });
  els.btnFailedRetrySelected.addEventListener("click", () => retryFailures([...state.failedSelected]));
  els.btnFailedDiscard.addEventListener("click", () => {
    const ids = [...state.failedSelected];
    if (!ids.length) return;
    state.failedSelected.clear();
    postState("/api/discard", { ids }).then((data) => {
      if (data) toast(`已放弃 ${ids.length} 个失败文件`, "ok");
    });
  });
  $("btn-failed-export").addEventListener("click", exportFailures);
  els.failedSearch.addEventListener("input", () => { state.failedSearch = els.failedSearch.value; renderFailed(); });

  document.querySelectorAll(".chip[data-reason]").forEach((chip) => {
    chip.addEventListener("click", () => {
      state.failedFilter = chip.dataset.reason;
      document.querySelectorAll(".chip[data-reason]").forEach((other) => {
        const active = other === chip;
        other.classList.toggle("active", active);
        other.setAttribute("aria-pressed", String(active));
      });
      renderFailed();
    });
  });

  [["fsort-file", "file"], ["fsort-reason", "reason"], ["fsort-tries", "tries"]].forEach(([id, key]) => {
    $(id).addEventListener("click", () => {
      if (state.failedSortKey === key) {
        if (state.failedSortDir === 1) state.failedSortDir = -1;
        else { state.failedSortKey = null; state.failedSortDir = 1; }
      } else {
        state.failedSortKey = key;
        state.failedSortDir = 1;
      }
      renderFailed();
    });
  });

  els.failedSelectAll.addEventListener("change", () => {
    const items = visibleFailures();
    if (els.failedSelectAll.checked) items.forEach((f) => state.failedSelected.add(f.id));
    else items.forEach((f) => state.failedSelected.delete(f.id));
    renderFailed();
  });

  els.failedList.addEventListener("click", (event) => {
    const target = event.target.closest("[data-fact]");
    if (!target) return;
    const id = Number(target.dataset.fid);
    const failure = state.failures.find((f) => f.id === id);
    if (!failure) return;
    const act = target.dataset.fact;
    if (act === "select") {
      if (target.checked) state.failedSelected.add(id);
      else state.failedSelected.delete(id);
      renderFailed();
    } else if (act === "retry") {
      retryFailures([id]);
    } else if (act === "discard") {
      state.failedSelected.delete(id);
      postState("/api/discard", { ids: [id] }).then((data) => {
        if (data) toast(`已放弃 ${failure.name}`, "ok");
      });
    } else if (act === "detail") {
      openFailureDrawer(failure);
    }
  });

  // Completed view
  document.querySelectorAll(".chip[data-done]").forEach((chip) => {
    chip.addEventListener("click", () => {
      state.doneFilter = chip.dataset.done;
      document.querySelectorAll(".chip[data-done]").forEach((other) => {
        const on = other === chip;
        other.classList.toggle("active", on);
        other.setAttribute("aria-pressed", String(on));
      });
      state.doneLoaded = false;
      loadCompleted(true);
    });
  });

  let doneSearchTimer = null;
  $("done-search").addEventListener("input", (event) => {
    state.doneSearch = event.target.value || "";
    clearTimeout(doneSearchTimer);
    doneSearchTimer = setTimeout(() => {
      state.doneLoaded = false;
      loadCompleted();
    }, 250);
  });

  $("btn-done-refresh").addEventListener("click", () => {
    state.doneLoaded = false;
    loadCompleted(true).then(() => toast("已刷新下载记录", "ok"));
  });

  $("btn-done-more").addEventListener("click", loadMoreCompleted);

  $("btn-done-export").addEventListener("click", () => {
    const rows = visibleCompleted();
    if (!rows.length) { toast("没有可导出的记录", "warn"); return; }
    const header = "博主,文件名,大小(字节),下载时间,推文链接\n";
    const body = rows.map((row) => [
      row.username,
      `"${String(row.filename || "").replace(/"/g, '""')}"`,
      row.size,
      row.downloadedAt,
      row.tweetId ? `https://x.com/${row.username}/status/${row.tweetId}` : "",
    ].join(",")).join("\n");
    downloadTextFile(`xdl-completed-${Date.now()}.csv`, `\ufeff${header}${body}`, "text/csv;charset=utf-8");
    toast(`已导出 ${rows.length} 条记录`, "ok");
  });

  $("done-list").addEventListener("click", (event) => {
    const button = event.target.closest("[data-dact]");
    if (!button) return;
    const holder = button.closest(".crow") || button.closest(".cgroup");
    if (!holder) return;
    const user = holder.dataset.user;
    const tweetId = holder.dataset.tweet;
    const filename = holder.dataset.file;
    const size = Number(holder.dataset.size) || 0;
    const act = button.dataset.dact;
    if (act === "preview" && user && filename) {
      openMediaModal({ user, filename, tweetId, size });
    } else if (act === "tweet" && tweetId) {
      window.open(`https://x.com/${encodeURIComponent(user)}/status/${encodeURIComponent(tweetId)}`, "_blank", "noopener");
    } else if (act === "folder") {
      postState("/api/open", { kind: "dir", user });
    }
  });

  // Video duration & media type filter panel
  document.querySelectorAll(".seg-btn[data-media-type]").forEach((btn) => {
    btn.addEventListener("click", () => commitSettings({ mediaType: btn.dataset.mediaType }));
  });

  els.setDurationEnabled.addEventListener("change", () => {
    commitSettings({ durationFilterEnabled: els.setDurationEnabled.checked });
  });

  document.querySelectorAll(".preset-btn[data-duration-preset]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const secs = Number(btn.dataset.durationPreset) || 60;
      commitSettings({ durationFilterEnabled: true, maxVideoDuration: secs });
    });
  });

  document.querySelectorAll("[data-dur-step]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const field = btn.dataset.durField;
      const step = Number(btn.dataset.durStep) || 0;
      const current = clampInt(Number(state.settings.maxVideoDuration), 5, 7200, 60);
      const delta = field === "min" ? step * 60 : step;
      const next = clampInt(current + delta, 5, 7200, 60);
      commitSettings({ durationFilterEnabled: true, maxVideoDuration: next });
    });
  });

  [els.durMin, els.durSec].forEach((input) => {
    input.addEventListener("change", () => {
      let mins = Math.max(0, Math.round(Number(els.durMin.value) || 0));
      let secs = Math.max(0, Math.round(Number(els.durSec.value) || 0));
      if (secs >= 60) {
        mins += Math.floor(secs / 60);
        secs %= 60;
      }
      const total = clampInt(mins * 60 + secs, 5, 7200, 60);
      commitSettings({ durationFilterEnabled: true, maxVideoDuration: total });
    });
  });

  els.durSlider.addEventListener("input", () => {
    const val = clampInt(Number(els.durSlider.value), 5, 600, 60);
    state.settings.durationFilterEnabled = true;
    state.settings.maxVideoDuration = val;
    syncDurationUI();
    renderCmdPreview();
  });
  els.durSlider.addEventListener("change", () => {
    const val = clampInt(Number(els.durSlider.value), 5, 600, 60);
    commitSettings({ durationFilterEnabled: true, maxVideoDuration: val });
  });

  $("btn-preset-short-video").addEventListener("click", async () => {
    await commitSettings({
      mediaType: "videos",
      durationFilterEnabled: true,
      maxVideoDuration: 60,
    });
    toast("已套用：仅下载 ≤ 01:00 视频", "ok");
  });

  // General settings
  document.querySelectorAll(".seg-btn[data-cookies-source]").forEach((btn) => {
    btn.addEventListener("click", () => commitSettings({ cookiesSource: btn.dataset.cookiesSource }));
  });

  [
    els.setOutput, els.setCookies, els.setCookiesBrowser, els.setProxyMode, els.setProxyAddr,
    els.setLimit, els.setConcurrency, els.setMediaOnly, els.setMaxRetries,
    els.setAutoRetry, els.setIncremental, els.setStopAfterSkips,
  ].forEach((input) => {
    input.addEventListener("change", () => {
      readSettingsFromInputs();
      commitSettings();
    });
  });

  document.querySelectorAll(".stepper button[data-for]").forEach((button) => {
    button.addEventListener("click", () => {
      const input = $(button.dataset.for);
      const step = Number(button.dataset.step);
      const min = Number(input.min || 0);
      const max = Number(input.max || 99999);
      input.value = String(clampInt(Number(input.value) + step, min, max, min));
      readSettingsFromInputs();
      commitSettings();
    });
  });

  $("btn-browse-output").addEventListener("click", async () => {
    if (window.pywebview && window.pywebview.api) {
      const path = await window.pywebview.api.pick_folder();
      if (path) { els.setOutput.value = path; readSettingsFromInputs(); await commitSettings(); }
      return;
    }
    try {
      const data = await api("/api/pick-folder", {});
      if (data && data.path) {
        els.setOutput.value = data.path;
        readSettingsFromInputs();
        await commitSettings();
      }
    } catch {
      toast("请在输入框直接填写下载目录路径", "warn");
    }
  });

  $("btn-browse-cookies").addEventListener("click", async () => {
    if (window.pywebview && window.pywebview.api) {
      const path = await window.pywebview.api.pick_file();
      if (path) { els.setCookies.value = path; readSettingsFromInputs(); await commitSettings(); }
      return;
    }
    try {
      const data = await api("/api/pick-file", {});
      if (data && data.path) {
        els.setCookies.value = data.path;
        readSettingsFromInputs();
        await commitSettings();
      }
    } catch {
      toast("可使用右侧“上传”按钮导入 cookies.txt", "warn");
    }
  });

  $("btn-upload-cookies").addEventListener("click", () => els.cookiesUploadInput.click());
  els.cookiesUploadInput.addEventListener("change", () => {
    const file = els.cookiesUploadInput.files[0];
    els.cookiesUploadInput.value = "";
    if (!file) return;
    const reader = new FileReader();
    reader.onload = async () => {
      const data = await postState("/api/upload-cookies", {
        filename: file.name,
        content: String(reader.result || ""),
      });
      if (data) toast("Cookie 已上传并保存", "ok");
    };
    reader.readAsText(file, "utf-8");
  });

  $("btn-copy-cmd").addEventListener("click", async () => {
    const text = els.cmdPreview.textContent.replace(/ \\\n\s+/g, " ");
    try {
      await navigator.clipboard.writeText(text);
      $("copy-cmd-icon").innerHTML = ICON_CHECK;
      $("copy-cmd-label").textContent = "已复制";
      setTimeout(() => {
        $("copy-cmd-icon").innerHTML = ICON_COPY;
        $("copy-cmd-label").textContent = "复制命令";
      }, 1600);
      toast("命令已复制到剪贴板", "ok");
    } catch {
      toast("复制失败，请手动选择文本", "bad");
    }
  });

  $("btn-fill-cmd").addEventListener("click", () => {
    const text = els.cmdPreview.textContent.replace(/ \\\n\s+/g, " ").replace("<用户名>", "elonmusk");
    els.cmdInput.value = text;
    els.cmdInput.focus();
    els.cmdInput.select();
  });

  $("btn-reset-settings").addEventListener("click", async () => {
    await commitSettings({ ...DEFAULTS });
    toast("已恢复默认设置", "ok");
  });

  $("layout-per-user").addEventListener("click", () => commitSettings({ layout: "per-user" }));
  $("layout-flat").addEventListener("click", () => commitSettings({ layout: "flat" }));

  // Modals & drawers
  $("btn-modal-cancel").addEventListener("click", closeQuitModal);
  $("btn-modal-ok").addEventListener("click", async () => {
    const ok = $("btn-modal-ok");
    ok.disabled = true;
    ok.textContent = "停止中…";
    try {
      await postState("/api/quit");
    } catch {}
    closeQuitModal();
    toast("XDL 本地服务已停止，可关闭此标签页", "warn");
    stopPolling();
  });
  els.closeModal.addEventListener("click", (event) => {
    if (event.target === els.closeModal) closeQuitModal();
  });

  $("media-modal-close").addEventListener("click", closeMediaModal);
  els.mediaModal.addEventListener("click", (event) => {
    if (event.target === els.mediaModal) closeMediaModal();
  });
  $("media-modal-tweet").addEventListener("click", () => {
    if (currentMediaPreview && currentMediaPreview.tweetId) {
      window.open(
        `https://x.com/${encodeURIComponent(currentMediaPreview.user)}/status/${encodeURIComponent(currentMediaPreview.tweetId)}`,
        "_blank",
        "noopener"
      );
    }
  });

  $("drawer-close").addEventListener("click", closeDrawer);
  els.drawerBackdrop.addEventListener("click", closeDrawer);
  els.drawerBody.addEventListener("click", (event) => {
    const fBtn = event.target.closest("[data-fdrawer-act]");
    if (fBtn) {
      const id = Number(fBtn.dataset.fid);
      const failure = state.failures.find((f) => f.id === id);
      if (!failure) return;
      const act = fBtn.dataset.fdrawerAct;
      if (act === "retry") { closeDrawer(); retryFailures([id]); }
      else if (act === "owner") {
        closeDrawer();
        setView("accounts");
        state.search = failure.user;
        els.search.value = failure.user;
        state.filter = "all";
        render();
      } else if (act === "discard") {
        state.failedSelected.delete(id);
        closeDrawer();
        postState("/api/discard", { ids: [id] }).then((data) => {
          if (data) toast(`已放弃 ${failure.name}`, "ok");
        });
      }
      return;
    }

    const button = event.target.closest("[data-drawer-act]");
    if (!button || drawerId === null) return;
    const item = state.queue.find((entry) => entry.id === drawerId);
    if (!item) return;
    const act = button.dataset.drawerAct;
    if (act === "preview-media") {
      openMediaModal({
        user: item.user,
        filename: button.dataset.file,
        tweetId: button.dataset.tweet,
        size: Number(button.dataset.size) || 0,
      });
    } else if (act === "view-logs") {
      closeDrawer();
      state.logUser = item.user;
      setView("logs");
    } else if (act === "open-dir") {
      postState("/api/open", { kind: "dir", user: item.user });
    } else if (act === "open-page") {
      window.open(`https://x.com/${encodeURIComponent(item.user)}`, "_blank", "noopener");
    } else if (act === "history-toggle") {
      drawerHistoryOpen = !drawerHistoryOpen;
      renderDrawer(item);
    } else if (act === "retry") {
      closeDrawer();
      postState("/api/retry-account", { user: item.user }).then((data) => {
        if (data) toast(`@${item.user} 已重新排队`, "ok");
        startPolling();
      });
    } else if (act === "remove") {
      state.selected.delete(item.id);
      closeDrawer();
      postState("/api/remove", { ids: [item.id] }).then((data) => {
        if (data) toast(`已移除 @${item.user}`, "ok");
      });
    }
  });

  els.drawerBody.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    const row = event.target.closest('.media-row[data-drawer-act="preview-media"]');
    if (!row) return;
    event.preventDefault();
    row.click();
  });

  document.addEventListener("keydown", (event) => {
    const topOverlay = getTopOverlay();
    if (event.key === "Tab" && topOverlay) {
      trapOverlayFocus(event, topOverlay);
      return;
    }
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      els.cmdInput.focus();
      els.cmdInput.select();
    } else if (event.key === "Escape") {
      if (!els.mediaModal.classList.contains("hidden")) closeMediaModal();
      else if (!els.closeModal.classList.contains("hidden")) closeQuitModal();
      else if (!els.drawer.classList.contains("hidden")) closeDrawer();
      else return;
      event.preventDefault();
    }
  });

  bindDragReorder();
}

function getTopOverlay() {
  if (!els.mediaModal.classList.contains("hidden")) return els.mediaModal.querySelector(".modal");
  if (!els.closeModal.classList.contains("hidden")) return els.closeModal.querySelector(".modal");
  if (!els.drawer.classList.contains("hidden")) return els.drawer;
  return null;
}

function trapOverlayFocus(event, overlay) {
  const focusable = [...overlay.querySelectorAll(
    'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), video[controls], [tabindex]:not([tabindex="-1"])'
  )].filter((element) => element.getClientRects().length > 0);
  if (!focusable.length) {
    event.preventDefault();
    return;
  }
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  const active = document.activeElement;
  if (event.shiftKey && (active === first || !overlay.contains(active))) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && (active === last || !overlay.contains(active))) {
    event.preventDefault();
    first.focus();
  }
}

function restoreFocus(element) {
  if (element && element.isConnected) element.focus();
}

/* ---------- init ---------- */

async function init() {
  bindEvents();
  syncSettingsInputs();
  updateCharCount();
  try {
    const data = await api("/api/state");
    applyServerState(data);
    setView("accounts");
    await fetchLogs();
    loadCompleted(true);
    if (data.running || data.busy || data.paused) startPolling();
  } catch {
    toast("无法连接本地 XDL 后端，请从命令行运行 xdl 启动", "bad");
  }
}

init();
