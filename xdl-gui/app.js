"use strict";

const DEFAULTS = {
  output: "/opt/X_Download/downloads",
  cookies: "/opt/X_Download/data/cookies.txt",
  cookiesSource: "file",
  cookiesBrowser: "chrome",
  proxyMode: "off",
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
  cloudSyncEnabled: false,
  cloudSyncDeleteLocal: true,
  cloudSyncTarget: "gdrive:X_Download",
};

const DONE_PAGE_SIZE = 240;
const MAX_LOCAL_LOGS = 2000;

const REASONS = {
  timeout: { label: "连接超时", hint: "CDN 响应超时，重试通常能成功", retryable: true },
  network: { label: "网络中断", hint: "网络异常，检查连通性后重试", retryable: true },
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

const ICON_TWEET = '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M14 4h6v6"/><path d="M20 4l-8.5 8.5"/><path d="M18 14v4a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4"/></svg>';
const ICON_LOGS = '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/></svg>';
const ICON_ZIP = '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>';
const ICON_DRIVE = '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l7 12-3.5 6h-7L5 15 12 3z"/><path d="M5 15h14"/></svg>';
const ICON_COPY = '<rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>';
const ICON_CHECK = '<polyline points="20 6 9 17 4 12"/>';

const state = {
  inputMode: "accounts",
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
  doneTotals: { all: 0, images: 0, videos: 0, local: 0, cloud: 0 },
  doneFilter: "all",
  doneStorage: "all",
  doneViewMode: "grid",
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
  quickForm: $("quick-form"),
  cmdInput: $("cmd-input"),
  pasteInput: $("paste-input"),
  parseStats: $("parse-stats"),
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
  setCloudEnabled: $("set-cloud-enabled"),
  setCloudDeleteLocal: $("set-cloud-delete-local"),
  setCloudTarget: $("set-cloud-target"),
  cloudStatusTag: $("cloud-status-tag"),
  cloudSetupGuide: $("cloud-setup-guide"),
  cloudSyncSummary: $("cloud-sync-summary"),
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
    credentials: "same-origin",
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let data = null;
  try { data = await response.json(); } catch {}
  if (response.status === 401) {
    stopPolling();
    if (envTimer) {
      clearTimeout(envTimer);
      envTimer = null;
    }
    if (!window.location.pathname.startsWith("/login")) {
      window.location.replace("/login?expired=1");
    }
    throw new Error((data && data.error) || "登录状态已失效，请重新登录");
  }
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
  pollTimer = setInterval(refreshFromServer, 800);
  refreshFromServer();
}

async function refreshFromServer() {
  if (pollBusy) return;
  pollBusy = true;
  try {
    const prevStoreMedia = Number(state.env?.store?.media || 0);
    const wasThumbBackfilling = Boolean(state.env?.thumbs?.backfilling);
    const data = await api("/api/state");
    const wasSyncing = Boolean(state.env?.cloudSync?.syncing);
    const wasRunning = state.running;
    applyServerState(data);
    if (data && (data.logSeq > state.logSeq || state.view === "logs")) {
      await fetchLogs();
    }
    const nowSyncing = Boolean(data?.env?.cloudSync?.syncing);
    const nowThumbBackfilling = Boolean(data?.env?.thumbs?.backfilling);
    const nowStoreMedia = Number(data?.env?.store?.media || 0);
    if (
      (wasSyncing && !nowSyncing)
      || (wasThumbBackfilling && !nowThumbBackfilling)
      || (wasRunning && !data.running)
      || nowStoreMedia !== prevStoreMedia
    ) {
      loadCompleted(true);
    }
    if (wasSyncing && !nowSyncing) {
      const last = data?.env?.cloudSync?.lastSync;
      if (last && last.status === "ok") {
        toast(last.summary || "Google Drive 同步已完成", "ok");
      }
    }
    if (wasThumbBackfilling && !nowThumbBackfilling) {
      toast("本地微缩略图缓存已补全", "ok");
    }
  } catch {
    stopPolling();
  } finally {
    pollBusy = false;
  }
}

function scheduleEnvRefresh() {
  const ready = Boolean(state.env && state.env.store && state.env.store.ready);
  const syncing = Boolean(state.env && state.env.cloudSync && state.env.cloudSync.syncing);
  if (ready && !syncing) {
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
  document.body.classList.toggle("is-cloud", Boolean(state.env.cloudMode));
  const authEnabled = Boolean(state.env.auth && state.env.auth.enabled);
  document.body.classList.toggle("is-auth", authEnabled);
  if (authEnabled && state.env.auth.username) {
    if ($("auth-username-text")) $("auth-username-text").textContent = state.env.auth.username;
    if ($("auth-panel-user")) $("auth-panel-user").textContent = state.env.auth.username;
    if ($("auth-new-user")) $("auth-new-user").placeholder = `当前：${state.env.auth.username}（留空不变）`;
  }
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

  const syncing = Boolean(state.env?.cloudSync?.syncing);
  const thumbBackfilling = Boolean(state.env?.thumbs?.backfilling);
  if (data.running || data.busy || syncing || thumbBackfilling || (data.paused && data.activeCount > 0)) startPolling();
  else stopPolling();
}

/* ---------- Formatting & Helpers ---------- */

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

function mediaFileUrl(user, filename, download = false) {
  return `/api/media/file?user=${encodeURIComponent(user)}&filename=${encodeURIComponent(filename)}${download ? "&download=1" : ""}`;
}

/* ---------- Parsing & Quick Import ---------- */

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

function showParseStats(result, added, skipped, sourceLabel) {
  const parts = [`<span class="ps-new">新增 <b>${added}</b></span>`];
  const dupTotal = result.duplicatesInText + skipped;
  if (dupTotal > 0) parts.push(`<span class="ps-dup">重复 <b>${dupTotal}</b></span>`);
  if (result.invalid > 0) parts.push(`<span class="ps-bad">无效 <b>${result.invalid}</b></span>`);
  if (sourceLabel) parts.push(`<span>来源：${sourceLabel}</span>`);
  els.parseStats.innerHTML = parts.join("");
  els.parseStats.classList.remove("hidden");
}

async function handleQuickImport(andStart) {
  const text = els.pasteInput.value;
  if (!text.trim()) {
    toast("请输入或粘贴 @用户名 / X 主页链接", "warn");
    els.pasteInput.focus();
    return;
  }
  const result = parseText(text);
  if (!result.valid.length) {
    showParseStats(result, 0, 0, "输入");
    toast("未识别到有效的 X 用户名或链接", "warn");
    return;
  }
  const { added, skipped } = await addToQueue(result.valid);
  showParseStats(result, added, skipped, "快速导入");
  if (added > 0 || andStart) {
    els.pasteInput.value = "";
    autoGrowPaste();
    if (andStart) {
      await startRun();
    } else {
      toast(`已加入 ${added} 个账号到队列`, "ok");
    }
  } else {
    toast("账号已在队列中", "warn");
  }
}

function autoGrowPaste() {
  const el = els.pasteInput;
  if (!el) return;
  el.style.height = "";
  if (el.value) {
    el.style.height = `${Math.min(el.scrollHeight, 120)}px`;
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
    showParseStats(result, added, skipped, escapeHtml(file.name));
    toast(added > 0 ? `已从 ${file.name} 加入 ${added} 个账号` : "没有新增账号", added > 0 ? "ok" : "warn");
  };
  reader.onerror = () => toast("读取文件失败", "bad");
  reader.readAsText(file, "utf-8");
}

function switchInputMode(mode) {
  state.inputMode = mode === "cmd" ? "cmd" : "accounts";
  const isCmd = state.inputMode === "cmd";
  $("mode-accounts").classList.toggle("active", !isCmd);
  $("mode-cmd").classList.toggle("active", isCmd);
  $("mode-accounts").setAttribute("aria-selected", String(!isCmd));
  $("mode-cmd").setAttribute("aria-selected", String(isCmd));
  $("input-accounts-box").classList.toggle("hidden", isCmd);
  $("input-cmd-box").classList.toggle("hidden", !isCmd);
  if (window.matchMedia("(max-width: 639px)").matches && (state.mobileTab !== "queue" || state.view !== "accounts")) {
    setView("accounts");
  }
  if (isCmd) els.cmdInput.focus();
  else els.pasteInput.focus();
}

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

/* ---------- Logs ---------- */

async function fetchLogs() {
  try {
    const data = await api(`/api/logs?since=${state.logSeq}&limit=500`);
    if (!data || !Array.isArray(data.entries)) return;
    if (data.entries.length > 0) {
      state.logs = state.logs.concat(data.entries);
      if (state.logs.length > MAX_LOCAL_LOGS) state.logs = state.logs.slice(-MAX_LOCAL_LOGS);
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
  els.logTerminal.querySelectorAll(".log-line").forEach((el) => el.remove());

  if (!rows.length) return;
  const frag = document.createDocumentFragment();
  rows.forEach((entry) => {
    const row = document.createElement("div");
    row.className = "log-line";
    row.innerHTML = `
      <span class="log-ts">${escapeHtml(entry.ts || "--:--:--")}</span>
      <span class="log-lvl lvl-${escapeHtml(entry.level)}">${LOG_LVL_LABEL[entry.level] || "信息"}</span>
      ${entry.user ? `<span class="log-user">@${escapeHtml(entry.user)}</span>` : '<span class="log-user"></span>'}
      <span class="log-text">${escapeHtml(entry.text || "")}</span>
    `;
    frag.appendChild(row);
  });
  els.logTerminal.appendChild(frag);
  if (els.logAutoscroll.checked) els.logTerminal.scrollTop = els.logTerminal.scrollHeight;
  updateMobileViewHeader();
}

/* ---------- Queue Rendering ---------- */

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
  if (failCount > 0) bits.push(`<span class="fbadge">${failCount}</span>`);
  return bits.join("");
}

function itemMetaText(item) {
  const total = Number(item.downloadedTotal ?? item.files) || 0;
  const parts = [];
  if (total > 0) parts.push(`${total} 个媒体`);
  if (item.lastRunAt) parts.push(String(item.lastRunAt).slice(5, 16));
  return parts.join(" · ") || (item.status === "waiting" ? "待抓取" : "—");
}

function itemFilterPillHtml(item) {
  const label = item.filterSummary || "";
  return label ? `<span class="q-filter-pill">${escapeHtml(label)}</span>` : "";
}

function itemKindPillHtml(item) {
  return item.taskKind === "tweet" ? '<span class="q-kind-pill">单条推文</span>' : "";
}

function rowHtml(item) {
  const checked = state.selected.has(item.id) ? "checked" : "";
  const pct = Math.round(item.progress || 0);
  const fillClass = item.status === "done" ? "done" : item.status === "failed" ? "failed" : "";
  const statusText = item.status === "failed" && item.error
    ? `<span class="st st-failed" title="${escapeHtml(item.error)}">失败</span>`
    : `<span class="st st-${item.status}">${STATUS_LABEL[item.status] || item.status}</span>`;
  const retryBtn = item.status === "failed"
    ? `<button class="iconbtn" data-act="retry" data-id="${item.id}" type="button" title="重试" aria-label="重试 ${escapeHtml(item.user)}">
         <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M20 11a8 8 0 1 0-2.3 6.3"/><path d="M20 4v7h-7"/></svg>
       </button>`
    : "";
  const initial = (item.user || "?").slice(0, 1);
  return `<div class="qrow${item.status === "active" ? " is-active" : ""}" data-id="${item.id}" draggable="true">
    <span class="qcell q-grip" title="拖拽调整顺序" aria-hidden="true">
      <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"><path d="M9 6h.01M15 6h.01M9 12h.01M15 12h.01M9 18h.01M15 18h.01"/></svg>
    </span>
    <label class="qcell q-check"><input class="m-check" type="checkbox" data-act="select" data-id="${item.id}" ${checked} aria-label="选择 ${escapeHtml(item.user)}"></label>
    <button class="qcell q-user" data-act="detail" data-id="${item.id}" type="button" title="查看 @${escapeHtml(item.user)} 详情">
      <span class="avatar" aria-hidden="true">${escapeHtml(initial)}</span>
      <span class="q-who">
        <span class="q-name"><span class="at">@</span>${escapeHtml(item.user)}</span>
        <span class="q-subline"><span class="q-meta">${escapeHtml(itemMetaText(item))}</span>${itemKindPillHtml(item)}${itemFilterPillHtml(item)}</span>
      </span>
    </button>
    <span class="qcell q-status">${statusText}</span>
    <span class="qcell q-progress"><span class="track"><span class="fill ${fillClass}" style="transform:scaleX(${pct / 100})"></span></span><span class="pct">${pct}%</span></span>
    <span class="qcell q-files">${filesCellHtml(item)}</span>
    <span class="qcell q-ops">
      <button class="iconbtn" data-act="zip" data-id="${item.id}" type="button" title="打包 ZIP 下载" aria-label="打包下载 ${escapeHtml(item.user)}">${ICON_ZIP}</button>
      <button class="iconbtn" data-act="logs" data-id="${item.id}" type="button" title="查看日志" aria-label="查看 ${escapeHtml(item.user)} 日志">${ICON_LOGS}</button>
      ${retryBtn}
      <button class="iconbtn danger" data-act="remove" data-id="${item.id}" type="button" title="移除" aria-label="移除 ${escapeHtml(item.user)}">
        <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg>
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
  const wantSub = `<span class="q-meta">${escapeHtml(itemMetaText(item))}</span>${itemKindPillHtml(item)}${itemFilterPillHtml(item)}`;
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
  els.btnStart.classList.toggle("primary", !state.running);
  els.btnStart.classList.toggle("ghost", state.running);
  els.btnStart.classList.toggle("btn-is-running", state.running);
  els.btnStartLabel.textContent = state.running ? "暂停下载" : "开始下载";
  $("btn-start-icon").innerHTML = state.running
    ? '<path d="M9 5v14M15 5v14"/>'
    : '<path d="M8 5.5l11 6.5-11 6.5z"/>';
  els.btnStopRun.disabled = !state.running && !state.busy && !state.paused;

  $("round-tag").textContent = `第 ${state.round} 轮`;
  $("log-live-dot").classList.toggle("hidden", !state.running && !state.env?.cloudSync?.syncing);

  renderStatusbar(counts);
  renderCmdPreview();
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

  const files = state.queue.reduce((acc, item) => acc + (Number(item.downloadedTotal ?? item.files) || 0), 0);
  const pending = pendingFailures().length;
  const parts = [`完成 ${counts.done}/${total}`, `${files} 个媒体`];
  if (pending > 0) parts.push(`失败 ${pending}`);
  els.sbStats.textContent = parts.join(" · ");

  const filt = describeFilter(state.settings);
  els.sbFilter.textContent = filt ? `筛选: ${filt}` : "筛选: 不限";

  const cloudSync = state.env?.cloudSync || {};
  els.sbNote.textContent = state.settings.cloudSyncEnabled && cloudSync.targetConfigured
    ? `Google Drive 自动同步已启用 (${state.settings.cloudSyncTarget})`
    : "华为云香港直连 · 支持桌面与手机浏览器";
}

function renderClock() {
  const sec = Math.floor((state.elapsedBase || 0) / 1000);
  const mm = String(Math.floor(sec / 60)).padStart(2, "0");
  const ss = String(sec % 60).padStart(2, "0");
  els.sbTime.textContent = `已用 ${mm}:${ss}`;
}

/* ---------- Failures & Rounds ---------- */

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
  els.rbTitle.textContent = clean ? `第 ${summary.round} 轮结束，全部成功` : `第 ${summary.round} 轮结束，${summary.failedFiles} 项失败`;
  const parts = [`新增 ${summary.okFiles} 个`];
  if (Number(summary.skippedFiles) > 0) parts.push(`跳过 ${summary.skippedFiles} 个`);
  parts.push(`完成账号 ${summary.okAccounts}`);
  els.rbDetail.textContent = parts.join(" · ");
  els.btnRoundRetry.disabled = summary.retryable === 0;
  els.btnRoundRetryLabel.textContent = summary.retryable > 0 ? `重试 ${summary.retryable} 个` : "无重试项";
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
  return `<div class="frow" data-fid="${failure.id}">
    <label class="fcell f-check"><input class="m-check" type="checkbox" data-fact="select" data-fid="${failure.id}" ${checked} aria-label="选择 ${escapeHtml(failure.name)}"></label>
    <button class="fcell f-file" data-fact="detail" data-fid="${failure.id}" type="button">
      <span class="f-name">${escapeHtml(failure.name)}</span>
      <span class="f-owner">@${escapeHtml(failure.user)} · ${escapeHtml(failure.size)}</span>
    </button>
    <span class="fcell f-reason"><span class="reason reason-${escapeHtml(failure.reason)}">${reason.label}</span></span>
    <span class="fcell f-tries${exhausted ? " at-limit" : ""}">${failure.tries}/${state.settings.maxRetries}</span>
    <span class="fcell f-state">${failureStateHtml(failure)}</span>
    <span class="fcell f-ops">
      <button class="iconbtn" data-fact="retry" data-fid="${failure.id}" type="button" ${retryDisabled} title="重试" aria-label="重试 ${escapeHtml(failure.name)}">
        <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M20 11a8 8 0 1 0-2.3 6.3"/><path d="M20 4v7h-7"/></svg>
      </button>
      <button class="iconbtn danger" data-fact="discard" data-fid="${failure.id}" type="button" title="放弃" aria-label="放弃 ${escapeHtml(failure.name)}">
        <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg>
      </button>
    </span>
  </div>`;
}

function renderFailed() {
  const items = visibleFailures();
  els.failedList.innerHTML = items.map(failureRowHtml).join("");
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

  [["file", "fsort-file-arrow"], ["reason", "fsort-reason-arrow"], ["tries", "fsort-tries-arrow"]].forEach(([key, id]) => {
    const el = $(id);
    if (el) el.textContent = state.failedSortKey === key ? (state.failedSortDir === 1 ? "▲" : "▼") : "";
  });

  const retryable = retryableFailures();
  els.btnFailedRetry.disabled = retryable.length === 0;
  els.btnFailedRetryLabel.textContent = retryable.length > 0 ? `重试全部（${retryable.length}）` : "没有可重试项";
  els.btnFailedRetrySelected.disabled = state.failedSelected.size === 0;
  els.btnFailedDiscard.disabled = state.failedSelected.size === 0;
  const visibleIds = items.map((f) => f.id);
  els.failedSelectAll.checked = visibleIds.length > 0 && visibleIds.every((id) => state.failedSelected.has(id));
}

async function retryFailures(ids, auto) {
  if (drawerId !== null) closeDrawer();
  const targets = state.failures.filter((f) => ids.includes(f.id) && f.status === "failed");
  const usable = targets.filter((f) => !isExhausted(f));
  if (!usable.length) {
    toast("所选文件已达重试上限或不可重试", "warn");
    return;
  }
  const data = await postState("/api/retry", { ids: usable.map((f) => f.id) });
  if (!data) return;
  state.failedSelected.clear();
  startPolling();
  if (!auto) toast(`已重新排队 ${data.queued || 0} 个文件`, "ok");
}

/* ---------- Media Library (Gallery + List + Scheme A Cloud Redirect) ---------- */

async function loadCompleted(force) {
  if (state.doneLoaded && !force) return;
  if (state.doneLoading) return;
  state.doneLoading = true;
  try {
    const params = new URLSearchParams({ limit: String(DONE_PAGE_SIZE), offset: "0" });
    if (state.doneFilter !== "all") params.set("kind", state.doneFilter);
    if (state.doneStorage !== "all") params.set("storage", state.doneStorage);
    if (state.doneSearch.trim()) params.set("q", state.doneSearch.trim());
    const data = await api(`/api/completed?${params.toString()}`);
    state.doneRows = Array.isArray(data.rows) ? data.rows : [];
    state.doneTotals = data.totals || { all: 0, images: 0, videos: 0, local: 0, cloud: 0 };
    state.doneHasMore = Boolean(data.hasMore);
    state.doneLoaded = true;
    renderCompleted();
  } catch (error) {
    toast(error.message || "读取媒体库失败", "bad");
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
    if (state.doneStorage !== "all") params.set("storage", state.doneStorage);
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
    if (state.doneStorage === "local" && !row.localExists) return false;
    if (state.doneStorage === "cloud" && !row.cloudSynced) return false;
    if (!keyword) return true;
    return (
      String(row.filename || "").toLowerCase().includes(keyword)
      || String(row.username || "").toLowerCase().includes(keyword)
    );
  });
}

function completedGroups(rows) {
  const groups = new Map();
  for (const row of rows) {
    const key = String(row.username || "").toLowerCase();
    let group = groups.get(key);
    if (!group) {
      group = { user: row.username || "", rows: [], bytes: 0, latest: "", localCount: 0 };
      groups.set(key, group);
    }
    group.rows.push(row);
    group.bytes += Number(row.size) || 0;
    if (row.localExists) group.localCount += 1;
    const stamp = String(row.downloadedAt || "");
    if (stamp > group.latest) group.latest = stamp;
  }
  return [...groups.values()].sort((a, b) => String(b.latest).localeCompare(String(a.latest)));
}

function completedCardHtml(row) {
  const user = row.username || "";
  const filename = row.filename || "";
  const tweetId = row.tweetId || "";
  const isVideo = isVideoFile(filename);
  const localOk = Boolean(row.localExists);
  const cloudUrl = row.cloudUrl || "";
  const fileUrl = mediaFileUrl(user, filename);
  const thumbUrl = row.thumbUrl || "";
  const playOverlay = isVideo
    ? '<span class="mcard-play-overlay" aria-hidden="true"><svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor"><polygon points="8 5 19 12 8 19 8 5"/></svg></span>'
    : "";

  let thumbHtml = "";
  let badgeHtml = "";
  let primaryAction = "";

  if (localOk) {
    if (thumbUrl) {
      thumbHtml = `<button class="mcard-thumb" data-dact="preview" type="button" aria-label="${isVideo ? "播放" : "预览"} ${escapeHtml(filename)}">
           <img src="${escapeHtml(thumbUrl)}" alt="${escapeHtml(filename)}" loading="lazy" decoding="async">
           ${playOverlay}
           ${isVideo ? '<span class="mcard-badge is-video">视频</span>' : ""}
         </button>`;
    } else {
      thumbHtml = isVideo
        ? `<button class="mcard-thumb" data-dact="preview" type="button" aria-label="播放 ${escapeHtml(filename)}">
             <video src="${escapeHtml(fileUrl)}#t=0.1" muted preload="metadata" playsinline></video>
             <span class="mcard-badge is-video">视频</span>
           </button>`
        : `<button class="mcard-thumb" data-dact="preview" type="button" aria-label="预览 ${escapeHtml(filename)}">
             <img src="${escapeHtml(fileUrl)}" alt="${escapeHtml(filename)}" loading="lazy">
           </button>`;
    }
    primaryAction = cloudUrl
      ? `<a class="linkbtn" href="${escapeHtml(cloudUrl)}" target="_blank" rel="noopener noreferrer">云端</a>`
      : `<a class="linkbtn" href="${escapeHtml(mediaFileUrl(user, filename, true))}" download="${escapeHtml(filename)}">下载</a>`;
  } else if (cloudUrl) {
    // Scheme A + Scheme 1: Local micro-thumbnail cover + direct Google Drive playback
    if (thumbUrl) {
      thumbHtml = `<a class="mcard-thumb has-cloud-thumb" href="${escapeHtml(cloudUrl)}" target="_blank" rel="noopener noreferrer" aria-label="在 Google Drive 播放 ${escapeHtml(filename)}">
          <img src="${escapeHtml(thumbUrl)}" alt="${escapeHtml(filename)}" loading="lazy" decoding="async">
          ${playOverlay}
          <span class="mcard-badge is-cloud">${isVideo ? "云端视频 ↗" : "云端图片 ↗"}</span>
        </a>`;
    } else {
      thumbHtml = `<a class="mcard-thumb" href="${escapeHtml(cloudUrl)}" target="_blank" rel="noopener noreferrer" aria-label="在 Google Drive 播放 ${escapeHtml(filename)}">
          <div class="mcard-cloud-stage">
            ${ICON_DRIVE}
            <span class="mcard-cloud-cta">在 Google Drive 打开播放 ↗</span>
          </div>
          <span class="mcard-badge is-cloud">${isVideo ? "云端视频" : "云端图片"}</span>
        </a>`;
    }
    primaryAction = `<a class="linkbtn" href="${escapeHtml(cloudUrl)}" target="_blank" rel="noopener noreferrer">Drive 播放 ↗</a>`;
  } else {
    thumbHtml = `<div class="mcard-thumb"><div class="mcard-cloud-stage"><span class="mcard-cloud-cta is-muted">本地文件已清理</span></div></div>`;
    primaryAction = tweetId
      ? `<button class="linkbtn" data-dact="tweet" type="button">原推文</button>`
      : "";
  }

  return `<article class="mcard" data-user="${escapeHtml(user)}" data-file="${escapeHtml(filename)}" data-tweet="${escapeHtml(tweetId)}" data-size="${Number(row.size) || 0}" data-cloud-url="${escapeHtml(cloudUrl)}" data-local="${localOk ? "1" : "0"}">
    ${thumbHtml}
    ${badgeHtml}
    <div class="mcard-body">
      <div class="mcard-name" title="${escapeHtml(filename)}">${escapeHtml(filename)}</div>
      <div class="mcard-meta">
        <span>${formatBytes(row.size)}</span>
        <span>${escapeHtml(shortWhen(row.downloadedAt))}</span>
      </div>
      <div class="mcard-foot">
        ${primaryAction}
        <div class="mcard-foot-icons">
          ${tweetId ? `<button class="iconbtn" data-dact="tweet" type="button" title="打开原推文" aria-label="打开原推文">${ICON_TWEET}</button>` : ""}
          ${localOk ? `<button class="iconbtn danger" data-dact="delete-file" type="button" title="清理本地文件" aria-label="删除本地文件 ${escapeHtml(filename)}"><svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg></button>` : ""}
        </div>
      </div>
    </div>
  </article>`;
}

function completedRowHtml(row) {
  const user = row.username || "";
  const filename = row.filename || "";
  const tweetId = row.tweetId || "";
  const isVideo = isVideoFile(filename);
  const localOk = Boolean(row.localExists);
  const cloudUrl = row.cloudUrl || "";
  const thumbUrl = row.thumbUrl || "";
  const leadThumb = thumbUrl
    ? `<img class="crow-thumb" src="${escapeHtml(thumbUrl)}" alt="" loading="lazy" decoding="async">`
    : `<span class="kdot ${isVideo ? "v" : "i"}" title="${isVideo ? "视频" : "图片"}"></span>`;

  let opsHtml = "";
  if (localOk) {
    opsHtml += `<button class="btn ghost btn-sm" data-dact="preview" type="button">${isVideo ? "播放" : "预览"}</button>`;
    opsHtml += `<a class="btn ghost btn-sm" href="${escapeHtml(mediaFileUrl(user, filename, true))}" download="${escapeHtml(filename)}">下载</a>`;
  }
  if (cloudUrl) {
    opsHtml += `<a class="btn primary btn-sm" href="${escapeHtml(cloudUrl)}" target="_blank" rel="noopener noreferrer">Drive 打开 ↗</a>`;
  }
  if (tweetId) {
    opsHtml += `<button class="iconbtn" data-dact="tweet" type="button" title="打开原推文" aria-label="打开原推文">${ICON_TWEET}</button>`;
  }
  if (localOk) {
    opsHtml += `<button class="iconbtn danger" data-dact="delete-file" type="button" title="清理本地" aria-label="删除本地文件 ${escapeHtml(filename)}"><svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg></button>`;
  }

  return `<div class="crow" data-user="${escapeHtml(user)}" data-file="${escapeHtml(filename)}" data-tweet="${escapeHtml(tweetId)}" data-size="${Number(row.size) || 0}" data-cloud-url="${escapeHtml(cloudUrl)}" data-local="${localOk ? "1" : "0"}">
    ${leadThumb}
    <span class="cname" title="${escapeHtml(filename)}">${escapeHtml(filename)}</span>
    <span class="cid">${cloudUrl && !localOk ? "Google Drive" : (localOk ? "本地" : "已移除")}</span>
    <span class="csize">${formatBytes(row.size)}</span>
    <span class="ctime">${escapeHtml(shortWhen(row.downloadedAt))}</span>
    <span class="ops">${opsHtml}</span>
  </div>`;
}

function groupHeadHtml(group) {
  const initial = (group.user || "?").slice(0, 1);
  const cs = state.env?.cloudSync || {};
  const userKey = String(group.user || "").toLowerCase();
  const driveDirUrl = (cs.userFolders && cs.userFolders[userKey]) || cs.folderUrl || "";
  const zipBtn = group.localCount > 0
    ? `<a class="btn ghost btn-sm" href="/api/media/zip?user=${encodeURIComponent(group.user)}" download="${escapeHtml(group.user)}_media.zip">打包 ZIP (${group.localCount})</a>`
    : `<span class="tag">已全量转存云盘</span>`;
  const driveBtn = driveDirUrl
    ? `<a class="btn ghost btn-sm" href="${escapeHtml(driveDirUrl)}" target="_blank" rel="noopener noreferrer">Drive 目录 ↗</a>`
    : "";
  const cleanBtn = group.localCount > 0
    ? `<button class="btn danger-ghost btn-sm" data-dact="delete-user" type="button">清理本地</button>`
    : "";
  return `<div class="cgroup" data-user="${escapeHtml(group.user)}">
    <div class="cgroup-left">
      <span class="avatar" aria-hidden="true">${escapeHtml(initial)}</span>
      <span class="gname">@${escapeHtml(group.user)}</span>
      <span class="gmeta">${group.rows.length} 个媒体 · ${formatBytes(group.bytes)}</span>
    </div>
    <div class="cgroup-actions">
      ${driveBtn}
      ${zipBtn}
      ${cleanBtn}
    </div>
  </div>`;
}

function renderCompleted() {
  const list = $("done-list");
  if (!list) return;
  const rows = visibleCompleted();
  const groups = completedGroups(rows);
  const isGrid = state.doneViewMode === "grid";

  list.innerHTML = groups.map((group) => {
    const inner = isGrid
      ? `<div class="media-grid">${group.rows.map(completedCardHtml).join("")}</div>`
      : `<div class="done-rows-list">${group.rows.map(completedRowHtml).join("")}</div>`;
    return `<div class="done-group" data-user="${escapeHtml(group.user)}">${groupHeadHtml(group)}${inner}</div>`;
  }).join("");

  $("done-empty").classList.toggle("hidden", rows.length > 0);
  const totals = state.doneTotals || {};
  const allCount = totals.all || 0;
  $("done-count").textContent = String(allCount);
  $("d-all").textContent = allCount;
  $("d-images").textContent = totals.images || 0;
  $("d-videos").textContent = totals.videos || 0;
  $("d-local").textContent = totals.local || 0;
  $("d-cloud").textContent = totals.cloud || 0;
  updateMobileBadges();

  const moreRow = $("done-more");
  if (moreRow) {
    const more = state.doneHasMore && rows.length > 0;
    moreRow.classList.toggle("hidden", !more);
    if (more) $("done-more-note").textContent = `已显示 ${rows.length} 条记录`;
  }
  updateMobileViewHeader();
}

/* ---------- Media Lightbox Preview Modal ---------- */

function openMediaModal({ user, filename, tweetId, size, cloudUrl }) {
  if (!user || !filename) return;
  if (els.mediaModal.classList.contains("hidden")) mediaReturnFocus = document.activeElement;
  const flatList = visibleCompleted().filter((r) => r.localExists);
  const idx = flatList.findIndex((r) => r.username === user && r.filename === filename);
  currentMediaPreview = { user, filename, tweetId, size, cloudUrl: cloudUrl || "", idx, list: flatList };

  const fileUrl = mediaFileUrl(user, filename);
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
  const driveBtn = $("media-modal-drive");
  if (cloudUrl) {
    driveBtn.href = cloudUrl;
    driveBtn.classList.remove("hidden");
  } else {
    driveBtn.classList.add("hidden");
  }
  $("media-modal-download").href = mediaFileUrl(user, filename, true);
  $("media-modal-download").download = filename;

  $("media-modal-prev").disabled = idx <= 0;
  $("media-modal-next").disabled = idx < 0 || idx >= flatList.length - 1;

  els.mediaModal.classList.remove("hidden");
  $("media-modal-close").focus();
}

function stepMediaModal(delta) {
  if (!currentMediaPreview || !currentMediaPreview.list.length) return;
  const nextIdx = currentMediaPreview.idx + delta;
  if (nextIdx < 0 || nextIdx >= currentMediaPreview.list.length) return;
  const next = currentMediaPreview.list[nextIdx];
  openMediaModal({
    user: next.username,
    filename: next.filename,
    tweetId: next.tweetId,
    size: next.size,
    cloudUrl: next.cloudUrl,
  });
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

/* ---------- View & Mobile Navigation ---------- */

const mqTablet = window.matchMedia("(min-width: 640px) and (max-width: 1023px)");

function syncSideDrawerInert() {
  const side = $("workspace-side");
  if (!side) return;
  side.inert = mqTablet.matches && !document.body.classList.contains("side-open");
}

function updateMobileViewHeader() {
  const titleEl = $("mob-view-title");
  const metaEl = $("mob-view-meta");
  if (!titleEl || !metaEl) return;
  if (state.view === "done") {
    titleEl.textContent = "媒体库";
    metaEl.textContent = `${state.doneTotals.all || 0} 个媒体`;
  } else if (state.view === "logs") {
    titleEl.textContent = "实时日志";
    metaEl.textContent = `${visibleLogs().length} 条日志`;
  } else {
    titleEl.textContent = "";
    metaEl.textContent = "";
  }
}

function setView(view) {
  state.view = view;
  document.body.dataset.view = view;
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
  if (view === "done") loadCompleted(true);

  if (window.matchMedia("(max-width: 639px)").matches) {
    if (view === "logs") syncMobileNav("logs", false);
    else if (view === "done") syncMobileNav("done", false);
    else syncMobileNav("queue", false);
  }
  updateMobileViewHeader();
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
    if (tab === "queue" && state.view !== "accounts") setView("accounts");
    else if (tab === "logs" && state.view !== "logs") setView("logs");
    else if (tab === "done" && state.view !== "done") setView("done");
  }
  if (tab === "logs") {
    state.logUnread = false;
    updateMobileBadges();
  }
}

function openSideDrawer(scrollToSelector) {
  if (window.matchMedia("(max-width: 639px)").matches) {
    syncMobileNav("settings", false);
  } else if (mqTablet.matches) {
    document.body.classList.add("side-open");
    $("side-backdrop").classList.remove("hidden");
    $("btn-open-side").setAttribute("aria-expanded", "true");
    syncSideDrawerInert();
    if (!scrollToSelector) $("btn-close-side")?.focus();
  }
  if (scrollToSelector) {
    const target = document.querySelector(scrollToSelector);
    if (target) setTimeout(() => target.scrollIntoView({ behavior: "smooth", block: "nearest" }), 60);
  }
}

function closeSideDrawer() {
  const wasOpen = document.body.classList.contains("side-open");
  document.body.classList.remove("side-open");
  $("side-backdrop").classList.add("hidden");
  $("btn-open-side").setAttribute("aria-expanded", "false");
  syncSideDrawerInert();
  if (wasOpen && mqTablet.matches) $("btn-open-side")?.focus();
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
  if (fDot) fDot.classList.toggle("hidden", !describeFilter(state.settings) && !state.settings.cloudSyncEnabled);
}

/* ---------- Settings, Duration Filter & Cloud Sync UI ---------- */

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
  els.durationControls.querySelectorAll("button, input").forEach((c) => { c.disabled = !enabled; });

  if (document.activeElement !== els.durMin) els.durMin.value = String(mins);
  if (document.activeElement !== els.durSec) els.durSec.value = String(secs);
  if (document.activeElement !== els.durSlider) els.durSlider.value = String(Math.min(600, totalSec));
  els.durTotalBadge.textContent = `= ${totalSec} 秒 · --max-duration ${totalSec}`;

  document.querySelectorAll(".preset-btn[data-duration-preset]").forEach((btn) => {
    const active = enabled && Number(btn.dataset.durationPreset) === totalSec;
    btn.classList.toggle("active", active);
    btn.setAttribute("aria-pressed", String(active));
  });

  const summaryLabel = describeFilter(s);
  const hasAny = Boolean(summaryLabel);
  els.filterStatusTag.textContent = summaryLabel || "不限";
  els.filterStatusTag.classList.toggle("tag-accent", hasAny);

  const ruleBox = els.filterRuleSummary;
  ruleBox.classList.toggle("is-active", hasAny);
  if (isImagesOnly) ruleBox.textContent = "仅下载图片，视频将跳过且不写入已下载档案";
  else if (mediaType === "videos" && enabled) ruleBox.textContent = `仅下载 ≤ ${formatDuration(totalSec)} 的视频，超时视频不写入档案`;
  else if (mediaType === "videos") ruleBox.textContent = "仅下载视频，不限制单条视频时长";
  else if (enabled) ruleBox.textContent = `下载全部图片 + 时长 ≤ ${formatDuration(totalSec)} 的视频`;
  else ruleBox.textContent = "未启用过滤：抓取账号全部图片与全部时长视频";

  document.querySelectorAll(".cmd-chip[data-quick-preset]").forEach((btn) => {
    const p = btn.dataset.quickPreset;
    const isOn = (p === "short-video" && mediaType === "videos" && enabled && totalSec === 60)
      || (p === "videos" && mediaType === "videos" && !enabled)
      || (p === "images" && mediaType === "images");
    btn.classList.toggle("active", isOn);
    btn.setAttribute("aria-pressed", String(isOn));
  });
}

function syncSettingsInputs() {
  const s = state.settings;
  if (document.activeElement !== els.setOutput) els.setOutput.value = s.output || DEFAULTS.output;
  if (document.activeElement !== els.setCookies) els.setCookies.value = s.cookies || DEFAULTS.cookies;
  els.setCookiesBrowser.value = s.cookiesBrowser || "chrome";
  const isBrowser = s.cookiesSource === "browser" && !state.env?.cloudMode;
  $("cookies-file-box").classList.toggle("hidden", isBrowser);
  $("cookies-browser-box").classList.toggle("hidden", !isBrowser);

  els.setProxyMode.value = s.proxyMode || "off";
  if (document.activeElement !== els.setProxyAddr) els.setProxyAddr.value = s.proxyAddr || DEFAULTS.proxyAddr;
  els.setProxyAddr.classList.toggle("hidden", s.proxyMode !== "manual");

  els.setIncremental.checked = s.incremental !== false;
  if (document.activeElement !== els.setStopAfterSkips) els.setStopAfterSkips.value = s.stopAfterSkips ?? 20;
  els.setStopAfterSkips.disabled = !els.setIncremental.checked;

  if (document.activeElement !== els.setLimit) els.setLimit.value = s.limit ?? 0;
  if (document.activeElement !== els.setConcurrency) els.setConcurrency.value = s.concurrency ?? 2;
  els.setMediaOnly.checked = s.mediaOnly !== false;
  if (document.activeElement !== els.setMaxRetries) els.setMaxRetries.value = s.maxRetries ?? 3;
  els.setAutoRetry.checked = Boolean(s.autoRetry);

  els.setCloudEnabled.checked = Boolean(s.cloudSyncEnabled);
  els.setCloudDeleteLocal.checked = s.cloudSyncDeleteLocal !== false;
  if (document.activeElement !== els.setCloudTarget) els.setCloudTarget.value = s.cloudSyncTarget || "gdrive:X_Download";

  const per = s.layout !== "flat";
  $("layout-per-user").classList.toggle("active", per);
  $("layout-flat").classList.toggle("active", !per);
  $("layout-per-user").setAttribute("aria-pressed", String(per));
  $("layout-flat").setAttribute("aria-pressed", String(!per));

  syncDurationUI();
  renderCmdPreview();
}

function readSettingsFromInputs() {
  const minVal = clampInt(Number(els.durMin.value), 0, 120, 1);
  const secVal = clampInt(Number(els.durSec.value), 0, 59, 0);
  let totalDur = clampInt(minVal * 60 + secVal, 5, 7200, 60);

  state.settings = {
    ...state.settings,
    output: els.setOutput.value.trim() || DEFAULTS.output,
    cookies: els.setCookies.value.trim() || DEFAULTS.cookies,
    cookiesBrowser: els.setCookiesBrowser.value || "chrome",
    proxyMode: els.setProxyMode.value,
    proxyAddr: els.setProxyAddr.value.trim() || DEFAULTS.proxyAddr,
    limit: clampInt(Number(els.setLimit.value), 0, 10000, 0),
    concurrency: clampInt(Number(els.setConcurrency.value), 1, 5, 2),
    mediaOnly: els.setMediaOnly.checked,
    maxRetries: clampInt(Number(els.setMaxRetries.value), 1, 5, 3),
    autoRetry: els.setAutoRetry.checked,
    incremental: els.setIncremental.checked,
    stopAfterSkips: clampInt(Number(els.setStopAfterSkips.value), 0, 500, 20),
    durationFilterEnabled: els.setDurationEnabled.checked,
    maxVideoDuration: totalDur,
    cloudSyncEnabled: els.setCloudEnabled.checked,
    cloudSyncDeleteLocal: els.setCloudDeleteLocal.checked,
    cloudSyncTarget: els.setCloudTarget.value.trim() || "gdrive:X_Download",
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
  if (!state.env?.cloudMode) {
    lines.push(`  -o "${s.output}"`);
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

function renderEnvChips() {
  const env = state.env || {};

  // Cookies
  const ckChip = $("env-cookies");
  ckChip.querySelector(".dot").className = "dot " + (env.cookiesFound ? "dot-ok" : "dot-bad");
  $("env-cookies-text").textContent = env.cookiesFound ? "Cookie 已配置" : "未配置 Cookie";
  const ckTag = $("cookie-status-tag");
  ckTag.textContent = env.cookiesFound ? "已就绪" : "未配置";
  ckTag.className = "tag " + (env.cookiesFound ? "tag-ok" : "tag-bad");

  // Proxy / Network
  const proxy = $("env-proxy");
  const mode = state.settings.proxyMode;
  let proxyText = env.cloudMode ? "香港直连" : "直连模式";
  let proxyClass = "dot-ok";
  if (mode === "manual") {
    proxyText = "手动代理";
    proxyClass = "dot-ok";
  } else if (mode === "auto") {
    proxyText = env.proxyOnline ? "代理在线" : "直连模式";
    proxyClass = env.proxyOnline ? "dot-ok" : "dot-warn";
  }
  proxy.querySelector(".dot").className = "dot " + proxyClass;
  $("env-proxy-text").textContent = proxyText;

  // Cloud Sync (Google Drive)
  const cs = env.cloudSync || {};
  const cloudChip = $("env-cloud");
  const cloudDot = cloudChip.querySelector(".dot");
  if (cs.syncing) {
    cloudDot.className = "dot dot-warn";
    $("env-cloud-text").textContent = "云盘同步中…";
    els.cloudStatusTag.textContent = "同步中…";
    els.cloudStatusTag.className = "tag tag-warn";
  } else if (!cs.targetConfigured) {
    cloudDot.className = "dot dot-warn";
    $("env-cloud-text").textContent = "Drive 未授权";
    els.cloudStatusTag.textContent = "待授权";
    els.cloudStatusTag.className = "tag tag-warn";
  } else if (state.settings.cloudSyncEnabled) {
    cloudDot.className = "dot dot-cloud";
    $("env-cloud-text").textContent = "Drive 自动同步";
    els.cloudStatusTag.textContent = "自动同步中";
    els.cloudStatusTag.className = "tag tag-ok";
  } else {
    cloudDot.className = "dot dot-ok";
    $("env-cloud-text").textContent = "Drive 已连接";
    els.cloudStatusTag.textContent = "已连接 (未开启)";
    els.cloudStatusTag.className = "tag";
  }

  els.cloudSetupGuide.classList.toggle("hidden", Boolean(cs.targetConfigured));
  $("btn-cloud-run").disabled = Boolean(cs.syncing);
  const openDriveBtn = $("btn-cloud-open-drive");
  if (openDriveBtn) {
    const folderUrl = String(cs.folderUrl || "");
    openDriveBtn.classList.toggle("hidden", !folderUrl);
    if (folderUrl) openDriveBtn.href = folderUrl;
  }
  const verifiedCount = Number(cs.verifiedCount || 0);
  const verifiedBytes = Number(cs.verifiedBytes || 0);
  const verifiedTail = verifiedCount > 0
    ? ` · 云端档案共 ${verifiedCount} 个 (${formatBytes(verifiedBytes)})`
    : "";
  const last = cs.lastSync || {};
  if (cs.syncing) {
    els.cloudSyncSummary.textContent = "正在将媒体上传至 Google Drive 并回写预览直链…";
    els.cloudSyncSummary.classList.add("is-active");
  } else if (last.at && last.summary) {
    const baseSummary = `${last.at} · ${last.summary}`;
    els.cloudSyncSummary.textContent = verifiedCount > 0 && !String(last.summary).includes("核对")
      ? `${baseSummary}${verifiedTail}`
      : (verifiedCount > 0 && verifiedBytes > 0 ? `${baseSummary}（共 ${formatBytes(verifiedBytes)}）` : baseSummary);
    els.cloudSyncSummary.classList.toggle("is-active", last.status === "ok");
  } else if (cs.targetConfigured) {
    const baseText = state.settings.cloudSyncEnabled
      ? `每次下载完成后自动同步至 ${state.settings.cloudSyncTarget}`
      : "Google Drive 已连接，打开上方开关即可自动同步并释放空间";
    els.cloudSyncSummary.textContent = `${baseText}${verifiedTail}`;
    els.cloudSyncSummary.classList.toggle("is-active", Boolean(state.settings.cloudSyncEnabled));
  } else {
    els.cloudSyncSummary.textContent = "完成服务器 rclone 授权后即可开启“自动同步到云盘”";
    els.cloudSyncSummary.classList.remove("is-active");
  }

  // Storage
  const st = env.storage || {};
  if (st.totalBytes > 0) {
    const freeStr = formatBytes(st.freeBytes);
    const usedPct = Math.min(100, Math.round(((st.totalBytes - st.freeBytes) / st.totalBytes) * 100));
    $("env-storage-text").textContent = `余 ${freeStr}`;
    $("storage-free-label").textContent = `${freeStr} 可用 / ${formatBytes(st.totalBytes)}`;
    $("storage-fill").style.transform = `scaleX(${usedPct / 100})`;
    $("storage-detail-label").textContent = st.outputDir || "/opt/X_Download/downloads";
  }

  const store = env.store || {};
  $("store-badge").textContent = store.ready ? `${Number(store.media) || 0} 归档` : "扫描中…";

  const thumbs = env.thumbs || {};
  const thumbBtn = $("btn-backfill-thumbs");
  if (thumbBtn) {
    const missingThumbs = Number(thumbs.missing || 0);
    const thumbBusy = Boolean(thumbs.backfilling);
    const showThumbBtn = thumbBusy || (missingThumbs > 0 && Boolean(thumbs.ffmpeg));
    thumbBtn.classList.toggle("hidden", !showThumbBtn);
    thumbBtn.disabled = thumbBusy;
    thumbBtn.textContent = thumbBusy ? "生成缩略图中…" : `补全缩略图 (${missingThumbs})`;
  }

  const filterChip = $("env-filter");
  const filtText = describeFilter(state.settings);
  if (filtText) {
    $("env-filter-text").textContent = filtText;
    filterChip.classList.remove("hidden");
  } else {
    filterChip.classList.add("hidden");
  }

  $("server-url-text").textContent = window.location.host || "XDL";
}

/* ---------- Drawers ---------- */

function drawerCountText(item) {
  const total = Number(item.downloadedTotal ?? item.files) || 0;
  const tweets = drawerHistory && drawerHistory.summary ? Number(drawerHistory.summary.tweets) : null;
  let text = `累计 ${total} 个媒体`;
  if (tweets !== null) text += ` · ${tweets} 条推文`;
  return text;
}

function mediaRowHtml(file) {
  const name = String(file.filename || "媒体文件");
  const tweet = String(file.tweetId || "");
  const when = String(file.downloadedAt || "").slice(0, 16);
  const cloudUrl = file.cloudUrl || "";
  const localOk = Boolean(file.localExists);
  const tag = cloudUrl && !localOk ? "Google Drive ↗" : (localOk ? "本地可播" : "已清理");
  const meta = [when, tag].filter(Boolean).join(" · ");
  if (!localOk && cloudUrl) {
    return `<li><a class="media-row" href="${escapeHtml(cloudUrl)}" target="_blank" rel="noopener noreferrer">
      <span class="media-main"><span class="media-name">${escapeHtml(name)}</span><span class="media-meta">${escapeHtml(meta)}</span></span>
      <span class="fsize">${formatBytes(file.size)}</span>
    </a></li>`;
  }
  return `<li><button class="media-row" type="button" data-drawer-act="preview-media" data-file="${escapeHtml(name)}" data-tweet="${escapeHtml(tweet)}" data-size="${Number(file.size) || 0}" data-cloud-url="${escapeHtml(cloudUrl)}">
    <span class="media-main"><span class="media-name">${escapeHtml(name)}</span><span class="media-meta">${escapeHtml(meta)}</span></span>
    <span class="fsize">${formatBytes(file.size)}</span>
  </button></li>`;
}

function refreshDrawer(item) {
  const body = els.drawerBody;
  const statusValue = body.querySelector("[data-drawer-status]");
  const progressValue = body.querySelector("[data-drawer-progress]");
  const countsValue = body.querySelector("[data-drawer-counts]");
  if (statusValue) statusValue.textContent = item.status === "failed" && item.error ? `失败 · ${item.error}` : (STATUS_LABEL[item.status] || item.status);
  if (progressValue) progressValue.textContent = `${Math.round(item.progress || 0)}%`;
  if (countsValue) countsValue.textContent = drawerCountText(item);
}

function renderDrawer(item) {
  const isTweet = item.taskKind === "tweet";
  const url = (isTweet && item.targetUrl)
    ? item.targetUrl
    : `https://x.com/${item.user}${state.settings.mediaOnly ? "/media" : ""}`;
  const statusLine = item.status === "failed" && item.error ? `失败 · ${escapeHtml(item.error)}` : (STATUS_LABEL[item.status] || item.status);
  const media = drawerHistoryOpen ? ((drawerHistory && drawerHistory.history) || []) : (Array.isArray(item.recentMedia) ? item.recentMedia : []);
  const filt = item.filterSummary || describeFilter(state.settings) || "不限";
  const scopeRow = isTweet
    ? `<dt>任务范围</dt><dd>单条推文${item.tweetId ? ` (${escapeHtml(item.tweetId)})` : ""}</dd>`
    : "";

  els.drawerBody.innerHTML = `
    <dl class="kv">
      <dt>状态</dt><dd data-drawer-status>${statusLine}</dd>
      <dt>进度</dt><dd data-drawer-progress>${Math.round(item.progress || 0)}%</dd>
      ${scopeRow}
      <dt>筛选规则</dt><dd>${escapeHtml(filt)}</dd>
      <dt>归档统计</dt><dd data-drawer-counts>${drawerCountText(item)}</dd>
      <dt>目标页面</dt><dd><a class="linkbtn" href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(url)}</a></dd>
    </dl>
    <div class="drawer-section-head">
      <p class="drawer-section-title">${drawerHistoryOpen ? "最近 50 条记录" : "最近下载的媒体"}</p>
      <button class="linkbtn" type="button" data-drawer-act="history-toggle">${drawerHistoryOpen ? "收起" : "查看全部"}</button>
    </div>
    ${media.length > 0 ? `<ul class="filelist">${media.map(mediaRowHtml).join("")}</ul>` : '<p class="drawer-note">暂无媒体记录。</p>'}
    <div class="drawer-actions">
      <button class="btn ghost btn-sm" data-drawer-act="view-logs" type="button">查看日志</button>
      <a class="btn ghost btn-sm" href="/api/media/zip?user=${encodeURIComponent(item.user)}" download="${escapeHtml(item.user)}_media.zip">打包 ZIP</a>
      <button class="btn ghost btn-sm" data-drawer-act="cloud-sync" type="button">同步到云盘</button>
      ${item.status === "failed" ? '<button class="btn primary btn-sm" data-drawer-act="retry" type="button">重试账号</button>' : ""}
      <button class="btn danger-ghost btn-sm" data-drawer-act="remove" type="button">移出队列</button>
    </div>`;
}

function openDrawer(id) {
  const item = state.queue.find((entry) => entry.id === id);
  if (!item) return;
  if (els.drawer.classList.contains("hidden")) drawerReturnFocus = document.activeElement;
  drawerId = id;
  drawerHistory = null;
  drawerHistoryOpen = false;
  els.drawerTitle.textContent = item.taskKind === "tweet" ? `@${item.user} · 单条推文` : `@${item.user}`;
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
  els.drawerTitle.textContent = failure.name.length > 28 ? `${failure.name.slice(0, 26)}…` : failure.name;
  els.drawerBody.innerHTML = `
    <dl class="kv">
      <dt>博主</dt><dd>@${escapeHtml(failure.user)}</dd>
      <dt>文件名</dt><dd class="mono">${escapeHtml(failure.name)}</dd>
      <dt>失败原因</dt><dd>${reason.label}</dd>
      <dt>错误信息</dt><dd>${escapeHtml(failure.note || "未记录")}</dd>
      <dt>已重试</dt><dd>${failure.tries} / ${state.settings.maxRetries} 次</dd>
    </dl>
    <p class="drawer-note">${escapeHtml(reason.hint)}</p>
    <div class="drawer-actions">
      <button class="btn primary btn-sm" data-fdrawer-act="retry" data-fid="${failure.id}" type="button" ${exhausted || failure.status === "queued" ? "disabled" : ""}>重试此文件</button>
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

/* ---------- Modals & Toasts ---------- */

function openQuitModal() {
  if (els.closeModal.classList.contains("hidden")) quitReturnFocus = document.activeElement;
  els.closeModal.classList.remove("hidden");
  $("btn-modal-cancel").focus();
}

function closeQuitModal() {
  const wasOpen = !els.closeModal.classList.contains("hidden");
  const returnFocus = quitReturnFocus;
  quitReturnFocus = null;
  els.closeModal.classList.add("hidden");
  if (wasOpen) restoreFocus(returnFocus);
}

window.__xdlQuitPrompt = function () { openQuitModal(); return false; };

function toast(message, kind) {
  const el = document.createElement("div");
  el.className = `toast${kind ? " " + kind : ""}`;
  el.textContent = message;
  els.toasts.appendChild(el);
  setTimeout(() => el.remove(), 3600);
  return el;
}

/* ---------- Drag Reorder ---------- */

function bindDragReorder() {
  els.queueList.addEventListener("dragstart", (event) => {
    const row = event.target.closest(".qrow");
    if (!row) return;
    dragId = Number(row.dataset.id);
    row.classList.add("dragging");
    event.dataTransfer.effectAllowed = "move";
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
        render();
        postState("/api/reorder", { ids: state.queue.map((entry) => entry.id) });
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

/* ---------- Actions ---------- */

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
  toast("已暂停，当前任务完成后停止调度", "warn");
}

async function stopRun() {
  const data = await postState("/api/stop");
  if (!data) return;
  await fetchLogs();
  toast("已停止进行中的任务", "warn");
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

/* ---------- Event Binding ---------- */

function bindEvents() {
  $("server-url-chip").addEventListener("click", async () => {
    const text = window.location.origin;
    try { await navigator.clipboard.writeText(text); toast(`已复制访问地址：${text}`, "ok"); }
    catch { toast(`访问地址：${text}`, "ok"); }
  });

  $("env-cookies").addEventListener("click", () => openSideDrawer("#cookie-panel"));
  $("env-cloud").addEventListener("click", () => openSideDrawer("#cloud-panel"));
  $("env-filter").addEventListener("click", () => openSideDrawer("#filter-panel"));
  $("btn-open-side").addEventListener("click", () => openSideDrawer());
  $("btn-close-side").addEventListener("click", closeSideDrawer);
  $("side-backdrop").addEventListener("click", closeSideDrawer);

  $("btn-open-root").addEventListener("click", () => postState("/api/open", { kind: "dir" }));
  $("btn-stop-service").addEventListener("click", openQuitModal);

  // Account & Session Security
  async function performLogout(endpoint) {
    try {
      stopPolling();
      await api(endpoint, {});
    } catch {}
    window.location.replace("/login?logged_out=1");
  }

  $("btn-logout")?.addEventListener("click", () => performLogout("/api/auth/logout"));
  $("btn-panel-logout")?.addEventListener("click", () => performLogout("/api/auth/logout"));

  let logoutAllArmTimer = null;
  $("btn-logout-all")?.addEventListener("click", () => {
    const btn = $("btn-logout-all");
    if (!btn.classList.contains("confirming")) {
      btn.classList.add("confirming");
      btn.textContent = "再次点击确认注销全部";
      clearTimeout(logoutAllArmTimer);
      logoutAllArmTimer = setTimeout(() => {
        btn.classList.remove("confirming");
        btn.textContent = "注销全部设备";
      }, 4000);
      return;
    }
    clearTimeout(logoutAllArmTimer);
    performLogout("/api/auth/logout-all");
  });

  const EYE_OPEN_SVG = '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>';
  const EYE_OFF_SVG = '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M17.94 17.94A10.94 10.94 0 0 1 12 20c-7 0-11-8-11-8a21.77 21.77 0 0 1 5.06-6.94"/><path d="M9.9 4.24A10.94 10.94 0 0 1 12 4c7 0 11 8 11 8a21.78 21.78 0 0 1-3.17 4.73"/><line x1="1" y1="1" x2="23" y2="23"/></svg>';
  document.querySelectorAll(".pw-toggle[data-pw-target]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const target = $(btn.dataset.pwTarget);
      if (!target) return;
      const show = target.type === "password";
      target.type = show ? "text" : "password";
      btn.setAttribute("aria-pressed", show ? "true" : "false");
      btn.setAttribute("aria-label", show ? "隐藏密码" : "显示密码");
      btn.innerHTML = show ? EYE_OFF_SVG : EYE_OPEN_SVG;
    });
  });

  $("auth-change-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const currentPassword = $("auth-current-pw").value;
    const newUsername = $("auth-new-user").value.trim();
    const newPassword = $("auth-new-pw").value;
    const confirmPassword = $("auth-confirm-pw").value;
    if (!currentPassword) {
      toast("请先输入当前密码", "warn");
      $("auth-current-pw").focus();
      return;
    }
    if (newPassword.length < 6) {
      toast("新密码至少需要 6 位字符", "warn");
      $("auth-new-pw").focus();
      return;
    }
    if (newPassword !== confirmPassword) {
      toast("两次输入的新密码不一致", "warn");
      $("auth-confirm-pw").focus();
      return;
    }
    const btn = $("btn-change-password");
    btn.disabled = true;
    try {
      const res = await postState("/api/auth/change-password", {
        currentPassword,
        newUsername,
        newPassword,
      });
      if (res) {
        $("auth-current-pw").value = "";
        $("auth-new-user").value = "";
        $("auth-new-pw").value = "";
        $("auth-confirm-pw").value = "";
        toast(res.message || "凭据已更新", "ok");
      }
    } finally {
      btn.disabled = false;
    }
  });

  // Unified Quick Input
  $("mode-accounts").addEventListener("click", () => switchInputMode("accounts"));
  $("mode-cmd").addEventListener("click", () => switchInputMode("cmd"));

  els.quickForm.addEventListener("submit", (event) => {
    event.preventDefault();
    if (state.inputMode === "cmd") executeCommand(els.cmdInput.value);
    else handleQuickImport(true);
  });

  $("btn-add-only").addEventListener("click", () => handleQuickImport(false));
  $("btn-import-txt").addEventListener("click", () => els.fileInput.click());
  els.fileInput.addEventListener("change", () => {
    handleFile(els.fileInput.files[0]);
    els.fileInput.value = "";
  });

  els.pasteInput.addEventListener("input", autoGrowPaste);
  els.pasteInput.addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
      event.preventDefault();
      handleQuickImport(true);
    }
  });

  els.cmdInput.addEventListener("keydown", (event) => {
    if (event.key === "ArrowUp") {
      if (!state.cmdHistory.length) return;
      event.preventDefault();
      if (state.cmdHistIdx === -1) {
        state.cmdDraft = els.cmdInput.value;
        state.cmdHistIdx = state.cmdHistory.length - 1;
      } else if (state.cmdHistIdx > 0) state.cmdHistIdx -= 1;
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
      switchInputMode("cmd");
      els.cmdInput.value = btn.dataset.cmd || "";
      executeCommand(els.cmdInput.value);
    });
  });

  document.querySelectorAll(".cmd-chip[data-quick-preset]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const p = btn.dataset.quickPreset;
      if (p === "short-video") {
        await commitSettings({ mediaType: "videos", durationFilterEnabled: true, maxVideoDuration: 60 });
        toast("已切换：仅视频 ≤ 01:00", "ok");
      } else if (p === "videos") {
        await commitSettings({ mediaType: "videos", durationFilterEnabled: false });
        toast("已切换：仅视频", "ok");
      } else if (p === "images") {
        await commitSettings({ mediaType: "images", durationFilterEnabled: false });
        toast("已切换：仅图片", "ok");
      }
    });
  });

  // Main View Tabs
  $("view-accounts").addEventListener("click", () => setView("accounts"));
  $("view-done").addEventListener("click", () => setView("done"));
  $("view-logs").addEventListener("click", () => setView("logs"));
  $("view-failed").addEventListener("click", () => setView("failed"));

  // Mobile Bottom Nav
  document.querySelectorAll(".mob-btn[data-mobile-tab]").forEach((btn) => {
    btn.addEventListener("click", () => syncMobileNav(btn.dataset.mobileTab, true));
  });

  // Queue Filters & Actions
  document.querySelectorAll(".chip[data-filter]").forEach((chip) => {
    chip.addEventListener("click", () => {
      state.filter = chip.dataset.filter;
      document.querySelectorAll(".chip[data-filter]").forEach((o) => {
        const active = o === chip;
        o.classList.toggle("active", active);
        o.setAttribute("aria-pressed", String(active));
      });
      render();
    });
  });
  els.search.addEventListener("input", () => { state.search = els.search.value; render(); });

  [["sort-user", "user"], ["sort-progress", "progress"], ["sort-files", "files"]].forEach(([id, key]) => {
    $(id).addEventListener("click", () => {
      if (state.sortKey === key) {
        if (state.sortDir === 1) state.sortDir = -1;
        else { state.sortKey = null; state.sortDir = 1; }
      } else { state.sortKey = key; state.sortDir = 1; }
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
      postState("/api/remove", { ids: [id] }).then((data) => { if (data) toast(`已移除 @${item.user}`, "ok"); });
    } else if (act === "retry") {
      postState("/api/retry-account", { id: item.id, user: item.user }).then((data) => {
        if (data) toast(`@${item.user} 已重新排队`, "ok");
        startPolling();
      });
    } else if (act === "logs") {
      state.logUser = item.user;
      setView("logs");
    } else if (act === "zip") {
      const a = document.createElement("a");
      a.href = `/api/media/zip?user=${encodeURIComponent(item.user)}`;
      a.download = `${item.user}_media.zip`;
      document.body.appendChild(a);
      a.click();
      a.remove();
    } else if (act === "detail") {
      openDrawer(id);
    }
  });

  els.btnStart.addEventListener("click", () => (state.running ? pauseRun() : startRun()));
  els.btnStopRun.addEventListener("click", stopRun);
  $("btn-retry-all").addEventListener("click", () => {
    const r = retryableFailures();
    if (!r.length) { toast("没有可重试的失败文件", "warn"); return; }
    retryFailures(r.map((f) => f.id));
  });
  $("btn-clear-done").addEventListener("click", () => {
    if (!state.queue.some((i) => i.status === "done")) { toast("没有已完成的账号", "warn"); return; }
    postState("/api/clear-done").then((data) => { if (data) toast("已清除完成的账号", "ok"); });
  });
  els.btnDeleteSelected.addEventListener("click", () => {
    const ids = [...state.selected];
    if (ids.length) postState("/api/remove", { ids }).then((data) => { if (data) toast(`已移除 ${ids.length} 个账号`, "ok"); });
  });

  // Logs Toolbar
  document.querySelectorAll(".chip[data-log-level]").forEach((chip) => {
    chip.addEventListener("click", () => {
      state.logLevel = chip.dataset.logLevel || "all";
      document.querySelectorAll(".chip[data-log-level]").forEach((o) => {
        const active = o === chip;
        o.classList.toggle("active", active);
        o.setAttribute("aria-pressed", String(active));
      });
      renderLogs();
    });
  });
  els.logUserFilter.addEventListener("change", () => { state.logUser = els.logUserFilter.value || ""; renderLogs(); });
  els.logSearch.addEventListener("input", () => { state.logSearch = els.logSearch.value || ""; renderLogs(); });
  $("btn-log-copy").addEventListener("click", async () => {
    const text = visibleLogs().map((e) => `[${e.ts}] [${e.level}] ${e.user ? "@" + e.user + " " : ""}${e.text}`).join("\n");
    if (!text) { toast("没有日志可复制", "warn"); return; }
    try { await navigator.clipboard.writeText(text); toast("日志已复制", "ok"); }
    catch { toast("复制失败", "bad"); }
  });
  $("btn-log-export").addEventListener("click", () => {
    const text = visibleLogs().map((e) => `[${e.ts}] [${e.level}] ${e.user ? "@" + e.user + " " : ""}${e.text}`).join("\n");
    if (!text) { toast("没有日志可导出", "warn"); return; }
    downloadTextFile(`xdl-logs-${Date.now()}.log`, text);
  });
  $("btn-log-clear").addEventListener("click", async () => {
    state.logs = [];
    await postState("/api/logs/clear");
    await fetchLogs();
    renderLogs();
  });

  // Roundbar & Failed
  $("btn-round-retry").addEventListener("click", () => {
    const r = retryableFailures();
    if (r.length) retryFailures(r.map((f) => f.id));
  });
  $("btn-round-review").addEventListener("click", () => setView("failed"));
  $("btn-round-dismiss").addEventListener("click", () => { state.roundSummary = null; renderRoundbar(); });

  els.btnFailedRetry.addEventListener("click", () => {
    const r = retryableFailures();
    if (!r.length) { toast("没有可重试的失败文件", "warn"); return; }
    retryFailures(r.map((f) => f.id));
  });
  els.btnFailedRetrySelected.addEventListener("click", () => retryFailures([...state.failedSelected]));
  els.btnFailedDiscard.addEventListener("click", () => {
    const ids = [...state.failedSelected];
    if (!ids.length) return;
    state.failedSelected.clear();
    postState("/api/discard", { ids }).then((data) => { if (data) toast(`已放弃 ${ids.length} 项`, "ok"); });
  });
  $("btn-failed-export").addEventListener("click", () => {
    const pending = pendingFailures();
    if (!pending.length) { toast("没有失败项可导出", "warn"); return; }
    const lines = pending.map((f) => `${f.user}\t${f.name}\t${f.reason}\t${f.note || ""}`);
    downloadTextFile(`xdl-failed-${Date.now()}.txt`, lines.join("\n"));
  });
  els.failedSearch.addEventListener("input", () => { state.failedSearch = els.failedSearch.value; renderFailed(); });

  document.querySelectorAll(".chip[data-reason]").forEach((chip) => {
    chip.addEventListener("click", () => {
      state.failedFilter = chip.dataset.reason;
      document.querySelectorAll(".chip[data-reason]").forEach((o) => {
        const active = o === chip;
        o.classList.toggle("active", active);
        o.setAttribute("aria-pressed", String(active));
      });
      renderFailed();
    });
  });

  [["fsort-file", "file"], ["fsort-reason", "reason"], ["fsort-tries", "tries"]].forEach(([id, key]) => {
    $(id)?.addEventListener("click", () => {
      if (state.failedSortKey === key) {
        if (state.failedSortDir === 1) state.failedSortDir = -1;
        else { state.failedSortKey = null; state.failedSortDir = 1; }
      } else { state.failedSortKey = key; state.failedSortDir = 1; }
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
    } else if (act === "retry") retryFailures([id]);
    else if (act === "discard") {
      state.failedSelected.delete(id);
      postState("/api/discard", { ids: [id] });
    } else if (act === "detail") openFailureDrawer(failure);
  });

  // Completed Media Library
  document.querySelectorAll(".chip[data-done]").forEach((chip) => {
    chip.addEventListener("click", () => {
      state.doneFilter = chip.dataset.done;
      document.querySelectorAll(".chip[data-done]").forEach((o) => {
        const on = o === chip;
        o.classList.toggle("active", on);
        o.setAttribute("aria-pressed", String(on));
      });
      state.doneLoaded = false;
      loadCompleted(true);
    });
  });

  document.querySelectorAll(".chip[data-storage]").forEach((chip) => {
    chip.addEventListener("click", () => {
      state.doneStorage = chip.dataset.storage;
      document.querySelectorAll(".chip[data-storage]").forEach((o) => {
        const on = o === chip;
        o.classList.toggle("active", on);
        o.setAttribute("aria-pressed", String(on));
      });
      state.doneLoaded = false;
      loadCompleted(true);
    });
  });

  $("done-mode-grid").addEventListener("click", () => {
    state.doneViewMode = "grid";
    $("done-mode-grid").classList.add("active");
    $("done-mode-list").classList.remove("active");
    $("done-mode-grid").setAttribute("aria-pressed", "true");
    $("done-mode-list").setAttribute("aria-pressed", "false");
    renderCompleted();
  });
  $("done-mode-list").addEventListener("click", () => {
    state.doneViewMode = "list";
    $("done-mode-list").classList.add("active");
    $("done-mode-grid").classList.remove("active");
    $("done-mode-list").setAttribute("aria-pressed", "true");
    $("done-mode-grid").setAttribute("aria-pressed", "false");
    renderCompleted();
  });

  let doneSearchTimer = null;
  $("done-search").addEventListener("input", (event) => {
    state.doneSearch = event.target.value || "";
    clearTimeout(doneSearchTimer);
    doneSearchTimer = setTimeout(() => { state.doneLoaded = false; loadCompleted(true); }, 250);
  });

  $("btn-backfill-thumbs")?.addEventListener("click", async () => {
    const res = await postState("/api/thumbs/backfill", {});
    if (res) {
      startPolling();
      toast("正在后台补全本地微缩略图缓存…", "ok");
    }
  });
  $("btn-done-refresh").addEventListener("click", () => {
    state.doneLoaded = false;
    loadCompleted(true).then(() => toast("已刷新媒体库", "ok"));
  });
  $("btn-done-more").addEventListener("click", loadMoreCompleted);

  $("btn-done-export").addEventListener("click", () => {
    const rows = visibleCompleted();
    if (!rows.length) { toast("没有可导出的记录", "warn"); return; }
    const header = "博主,文件名,大小(字节),下载时间,推文链接,GoogleDrive链接\n";
    const body = rows.map((row) => [
      row.username,
      `"${String(row.filename || "").replace(/"/g, '""')}"`,
      row.size,
      row.downloadedAt,
      row.tweetId ? `https://x.com/${row.username}/status/${row.tweetId}` : "",
      row.cloudUrl || "",
    ].join(",")).join("\n");
    downloadTextFile(`xdl-media-${Date.now()}.csv`, `\ufeff${header}${body}`, "text/csv;charset=utf-8");
  });

  $("done-list").addEventListener("click", async (event) => {
    const button = event.target.closest("[data-dact]");
    if (!button) return;
    const card = button.closest("[data-file]") || button.closest("[data-user]");
    if (!card) return;
    const user = card.dataset.user;
    const tweetId = card.dataset.tweet;
    const filename = card.dataset.file;
    const size = Number(card.dataset.size) || 0;
    const cloudUrl = card.dataset.cloudUrl || "";
    const act = button.dataset.dact;

    if (act === "preview" && user && filename) {
      openMediaModal({ user, filename, tweetId, size, cloudUrl });
    } else if (act === "tweet" && tweetId) {
      window.open(`https://x.com/${encodeURIComponent(user)}/status/${encodeURIComponent(tweetId)}`, "_blank", "noopener");
    } else if (act === "delete-file" && user && filename) {
      const res = await postState("/api/media/delete", { user, filename });
      if (res) {
        toast(`已清理本地文件 ${filename}`, "ok");
        loadCompleted(true);
      }
    } else if (act === "delete-user" && user) {
      const res = await postState("/api/media/delete", { user });
      if (res) {
        toast(`已清理 @${user} 的本地媒体文件`, "ok");
        loadCompleted(true);
      }
    }
  });

  // Cloud Sync (Google Drive · 方案 A)
  $("btn-copy-setup-cmd").addEventListener("click", async () => {
    const cmd = $("cloud-setup-cmd").textContent.trim();
    try { await navigator.clipboard.writeText(cmd); toast("已复制授权命令", "ok"); }
    catch { toast(cmd, "info"); }
  });

  $("btn-cloud-test").addEventListener("click", async () => {
    const btn = $("btn-cloud-test");
    btn.disabled = true;
    btn.textContent = "测试中…";
    try {
      const data = await api("/api/cloud-sync/test", {});
      let msg = data.message || "Google Drive 连接正常";
      if (data.quota && data.quota.free) msg += `（云盘剩余 ${formatBytes(data.quota.free)}）`;
      toast(msg, "ok");
      await refreshFromServer();
    } catch (error) {
      toast(error.message || "Google Drive 连接失败", "bad");
    } finally {
      btn.disabled = false;
      btn.textContent = "测试连接";
    }
  });

  $("btn-cloud-run").addEventListener("click", async () => {
    const res = await postState("/api/cloud-sync/run", {});
    if (res) {
      startPolling();
      toast("正在同步并核对 Google Drive 文件…", "ok");
    }
  });

  // Cookie Dual-Tab Import
  $("ck-tab-token").addEventListener("click", () => {
    $("ck-tab-token").classList.add("active");
    $("ck-tab-file").classList.remove("active");
    $("ck-tab-token").setAttribute("aria-selected", "true");
    $("ck-tab-file").setAttribute("aria-selected", "false");
    $("ck-pane-token").classList.remove("hidden");
    $("ck-pane-file").classList.add("hidden");
  });
  $("ck-tab-file").addEventListener("click", () => {
    $("ck-tab-file").classList.add("active");
    $("ck-tab-token").classList.remove("active");
    $("ck-tab-file").setAttribute("aria-selected", "true");
    $("ck-tab-token").setAttribute("aria-selected", "false");
    $("ck-pane-file").classList.remove("hidden");
    $("ck-pane-token").classList.add("hidden");
  });

  $("btn-save-tokens").addEventListener("click", async () => {
    const authToken = $("ck-auth-token").value.trim();
    const ct0 = $("ck-ct0").value.trim();
    if (!authToken || !ct0) {
      toast("请同时填写 auth_token 和 ct0", "warn");
      return;
    }
    const res = await postState("/api/upload-cookies", { authToken, ct0 });
    if (res) {
      $("ck-auth-token").value = "";
      $("ck-ct0").value = "";
      toast("Token 已加密生成并保存为服务器 cookies.txt", "ok");
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
      if (data) toast("cookies.txt 已上传并保存", "ok");
    };
    reader.readAsText(file, "utf-8");
  });

  // Media & Duration Filters
  document.querySelectorAll(".seg-btn[data-media-type]").forEach((btn) => {
    btn.addEventListener("click", () => commitSettings({ mediaType: btn.dataset.mediaType }));
  });
  els.setDurationEnabled.addEventListener("change", () => commitSettings({ durationFilterEnabled: els.setDurationEnabled.checked }));
  document.querySelectorAll(".preset-btn[data-duration-preset]").forEach((btn) => {
    btn.addEventListener("click", () => commitSettings({ durationFilterEnabled: true, maxVideoDuration: Number(btn.dataset.durationPreset) || 60 }));
  });
  document.querySelectorAll("[data-dur-step]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const field = btn.dataset.durField;
      const step = Number(btn.dataset.durStep) || 0;
      const current = clampInt(Number(state.settings.maxVideoDuration), 5, 7200, 60);
      const next = clampInt(current + (field === "min" ? step * 60 : step), 5, 7200, 60);
      commitSettings({ durationFilterEnabled: true, maxVideoDuration: next });
    });
  });
  [els.durMin, els.durSec].forEach((input) => {
    input.addEventListener("change", () => {
      let mins = Math.max(0, Math.round(Number(els.durMin.value) || 0));
      let secs = Math.max(0, Math.round(Number(els.durSec.value) || 0));
      if (secs >= 60) { mins += Math.floor(secs / 60); secs %= 60; }
      commitSettings({ durationFilterEnabled: true, maxVideoDuration: clampInt(mins * 60 + secs, 5, 7200, 60) });
    });
  });
  els.durSlider.addEventListener("input", () => {
    state.settings.durationFilterEnabled = true;
    state.settings.maxVideoDuration = clampInt(Number(els.durSlider.value), 5, 600, 60);
    syncDurationUI();
    renderCmdPreview();
  });
  els.durSlider.addEventListener("change", () => {
    commitSettings({ durationFilterEnabled: true, maxVideoDuration: clampInt(Number(els.durSlider.value), 5, 600, 60) });
  });
  $("btn-preset-short-video").addEventListener("click", async () => {
    await commitSettings({ mediaType: "videos", durationFilterEnabled: true, maxVideoDuration: 60 });
    toast("已套用：仅下载 ≤ 01:00 视频", "ok");
  });

  // General Settings
  [
    els.setOutput, els.setCookies, els.setCookiesBrowser, els.setProxyMode, els.setProxyAddr,
    els.setLimit, els.setConcurrency, els.setMediaOnly, els.setMaxRetries,
    els.setAutoRetry, els.setIncremental, els.setStopAfterSkips,
    els.setCloudEnabled, els.setCloudDeleteLocal, els.setCloudTarget,
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

  $("layout-per-user").addEventListener("click", () => commitSettings({ layout: "per-user" }));
  $("layout-flat").addEventListener("click", () => commitSettings({ layout: "flat" }));
  $("btn-backfill").addEventListener("click", async () => {
    const res = await postState("/api/backfill", {});
    if (res) {
      toast(`已扫描并更新档案 (${res.media || 0} 条)`, "ok");
      loadCompleted(true);
    }
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
      }, 1500);
      toast("命令已复制", "ok");
    } catch { toast("复制失败", "bad"); }
  });
  $("btn-fill-cmd").addEventListener("click", () => {
    switchInputMode("cmd");
    els.cmdInput.value = els.cmdPreview.textContent.replace(/ \\\n\s+/g, " ").replace("<用户名>", "NASA");
    els.cmdInput.focus();
  });
  $("btn-reset-settings").addEventListener("click", async () => {
    await commitSettings({ ...DEFAULTS });
    toast("已恢复默认参数", "ok");
  });

  // Modals & Drawers
  $("btn-modal-cancel").addEventListener("click", closeQuitModal);
  $("btn-modal-ok").addEventListener("click", async () => {
    await postState("/api/quit");
    closeQuitModal();
    stopPolling();
  });
  els.closeModal.addEventListener("click", (e) => { if (e.target === els.closeModal) closeQuitModal(); });

  $("media-modal-close").addEventListener("click", closeMediaModal);
  $("media-modal-prev").addEventListener("click", () => stepMediaModal(-1));
  $("media-modal-next").addEventListener("click", () => stepMediaModal(1));
  els.mediaModal.addEventListener("click", (e) => { if (e.target === els.mediaModal) closeMediaModal(); });
  $("media-modal-tweet").addEventListener("click", () => {
    if (currentMediaPreview && currentMediaPreview.tweetId) {
      window.open(`https://x.com/${encodeURIComponent(currentMediaPreview.user)}/status/${encodeURIComponent(currentMediaPreview.tweetId)}`, "_blank", "noopener");
    }
  });
  $("media-modal-delete").addEventListener("click", async () => {
    if (!currentMediaPreview) return;
    const { user, filename } = currentMediaPreview;
    const res = await postState("/api/media/delete", { user, filename });
    if (res) {
      toast(`已清理本地文件 ${filename}`, "ok");
      closeMediaModal();
      loadCompleted(true);
    }
  });

  $("drawer-close").addEventListener("click", closeDrawer);
  els.drawerBackdrop.addEventListener("click", closeDrawer);
  els.drawerBody.addEventListener("click", async (event) => {
    const fBtn = event.target.closest("[data-fdrawer-act]");
    if (fBtn) {
      const id = Number(fBtn.dataset.fid);
      const act = fBtn.dataset.fdrawerAct;
      if (act === "retry") { closeDrawer(); retryFailures([id]); }
      else if (act === "discard") { closeDrawer(); postState("/api/discard", { ids: [id] }); }
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
        cloudUrl: button.dataset.cloudUrl || "",
      });
    } else if (act === "view-logs") {
      closeDrawer();
      state.logUser = item.user;
      setView("logs");
    } else if (act === "cloud-sync") {
      const res = await postState("/api/cloud-sync/run", { user: item.user });
      if (res) { startPolling(); toast(`已触发 @${item.user} 同步至 Google Drive`, "ok"); }
    } else if (act === "history-toggle") {
      drawerHistoryOpen = !drawerHistoryOpen;
      renderDrawer(item);
    } else if (act === "retry") {
      closeDrawer();
      postState("/api/retry-account", { id: item.id, user: item.user }).then(() => startPolling());
    } else if (act === "remove") {
      state.selected.delete(item.id);
      closeDrawer();
      postState("/api/remove", { ids: [item.id] });
    }
  });

  document.addEventListener("keydown", (event) => {
    const topOverlay = getTopOverlay();
    if (event.key === "Tab" && topOverlay) {
      trapOverlayFocus(event, topOverlay);
      return;
    }
    if (!els.mediaModal.classList.contains("hidden")) {
      if (event.key === "ArrowLeft") { event.preventDefault(); stepMediaModal(-1); return; }
      if (event.key === "ArrowRight") { event.preventDefault(); stepMediaModal(1); return; }
    }
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      switchInputMode("cmd");
      els.cmdInput.select();
    } else if (event.key === "Escape") {
      if (!els.mediaModal.classList.contains("hidden")) closeMediaModal();
      else if (!els.closeModal.classList.contains("hidden")) closeQuitModal();
      else if (!els.drawer.classList.contains("hidden")) closeDrawer();
      else if (document.body.classList.contains("side-open")) closeSideDrawer();
    }
  });

  mqTablet.addEventListener("change", () => {
    if (!mqTablet.matches && document.body.classList.contains("side-open")) {
      closeSideDrawer();
    } else {
      syncSideDrawerInert();
    }
  });

  bindDragReorder();
}

function getTopOverlay() {
  if (!els.mediaModal.classList.contains("hidden")) return els.mediaModal.querySelector(".modal");
  if (!els.closeModal.classList.contains("hidden")) return els.closeModal.querySelector(".modal");
  if (!els.drawer.classList.contains("hidden")) return els.drawer;
  if (document.body.classList.contains("side-open")) return $("workspace-side");
  return null;
}

function trapOverlayFocus(event, overlay) {
  const focusable = [...overlay.querySelectorAll(
    'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), video[controls], [tabindex]:not([tabindex="-1"])'
  )].filter((element) => element.getClientRects().length > 0);
  if (!focusable.length) { event.preventDefault(); return; }
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

/* ---------- iOS Shortcut Setup Card ---------- */

function copyTextWithToast(text, okMsg) {
  if (!text) { toast("暂无可复制内容", "warn"); return; }
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text)
      .then(() => toast(okMsg || "已复制", "ok"))
      .catch(() => toast(text, "info"));
  } else {
    toast(text, "info");
  }
}

function applyShortcutConfig(data) {
  if (!data) return;
  const origin = window.location.origin || "";
  if ($("shortcut-inspect-url")) {
    $("shortcut-inspect-url").textContent = `${origin}${data.inspectPath || "/api/shortcut/inspect"}`;
  }
  if ($("shortcut-submit-url")) {
    $("shortcut-submit-url").textContent = `${origin}${data.submitPath || "/api/shortcut/submit"}`;
  }
  const authOn = data.authRequired !== false;
  $("shortcut-token-wrap")?.classList.toggle("hidden", !authOn);
  $("shortcut-noauth-note")?.classList.toggle("hidden", authOn);
  if ($("shortcut-token-input")) $("shortcut-token-input").value = data.token || "";
  const tag = $("shortcut-status-tag");
  if (tag) {
    tag.textContent = authOn ? "已就绪" : "免 Token";
    tag.className = "tag tag-ok";
  }
}

async function loadShortcutConfig() {
  try {
    const data = await api("/api/shortcut/config");
    applyShortcutConfig(data);
  } catch {
    applyShortcutConfig({
      authRequired: true,
      token: "",
      inspectPath: "/api/shortcut/inspect",
      submitPath: "/api/shortcut/submit",
    });
  }
}

async function runShortcutInspectTest() {
  const input = $("shortcut-test-url");
  const out = $("shortcut-test-result");
  const btn = $("btn-shortcut-test");
  if (!input || !out || !btn) return;
  const url = input.value.trim();
  if (!url) {
    toast("请先粘贴 X 推文或主页链接", "warn");
    input.focus();
    return;
  }
  btn.disabled = true;
  btn.textContent = "识别中…";
  out.classList.remove("hidden", "is-active");
  out.textContent = "正在通过服务器探测链接结构…";
  try {
    const res = await api("/api/shortcut/inspect", { url });
    out.classList.add("is-active");
    if (res.isQuote === "yes" && Array.isArray(res.scopeOptions) && res.scopeOptions.length) {
      out.textContent = `${res.summary || "引用转发"} · 将弹窗选择：${res.scopeOptions.join(" / ")}`;
    } else {
      out.textContent = `${res.summary || `@${res.targetUser}`} · 将跳过范围菜单，直接弹出视频时长选项`;
    }
  } catch (error) {
    out.classList.remove("is-active");
    out.textContent = error.message || "识别失败";
  } finally {
    btn.disabled = false;
    btn.textContent = "识别";
  }
}

function bindShortcutEvents() {
  const panel = $("shortcut-panel");
  if (!panel) return;

  $("btn-copy-shortcut-token")?.addEventListener("click", () => {
    const token = ($("shortcut-token-input")?.value || "").trim();
    copyTextWithToast(token, "Shortcut API Token 已复制");
  });

  let rotateArmTimer = null;
  $("btn-rotate-shortcut-token")?.addEventListener("click", async () => {
    const btn = $("btn-rotate-shortcut-token");
    if (!btn.classList.contains("confirming")) {
      btn.classList.add("confirming");
      btn.textContent = "再次点击确认重置";
      clearTimeout(rotateArmTimer);
      rotateArmTimer = setTimeout(() => {
        btn.classList.remove("confirming");
        btn.textContent = "重置 Token";
      }, 4000);
      return;
    }
    clearTimeout(rotateArmTimer);
    btn.classList.remove("confirming");
    btn.textContent = "重置中…";
    btn.disabled = true;
    try {
      const res = await api("/api/shortcut/token/rotate", {});
      applyShortcutConfig({
        authRequired: res.authRequired,
        token: res.token,
        inspectPath: "/api/shortcut/inspect",
        submitPath: "/api/shortcut/submit",
      });
      toast("已生成新 Token，请在 iPhone 快捷指令中同步更新", "ok");
    } catch (error) {
      toast(error.message || "重置 Token 失败", "bad");
    } finally {
      btn.disabled = false;
      btn.textContent = "重置 Token";
    }
  });

  panel.addEventListener("click", (event) => {
    const copyBtn = event.target.closest("[data-copy-target], [data-copy-text]");
    if (!copyBtn) return;
    const text = copyBtn.dataset.copyTarget
      ? ($(copyBtn.dataset.copyTarget)?.textContent || "").trim()
      : (copyBtn.dataset.copyText || "");
    copyTextWithToast(text, `已复制：${text}`);
  });

  $("btn-shortcut-test")?.addEventListener("click", runShortcutInspectTest);
  $("shortcut-test-url")?.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      runShortcutInspectTest();
    }
  });
}

/* ---------- Init ---------- */

async function init() {
  bindEvents();
  bindShortcutEvents();
  syncSettingsInputs();
  syncSideDrawerInert();
  try {
    const data = await api("/api/state");
    applyServerState(data);
    setView("accounts");
    await fetchLogs();
    loadCompleted(true);
    loadShortcutConfig();
    const syncing = Boolean(data?.env?.cloudSync?.syncing);
    if (data.running || data.busy || data.paused || syncing) startPolling();
  } catch {
    toast("无法连接 XDL 后端服务", "bad");
  }
}

init();
