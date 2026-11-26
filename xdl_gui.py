#!/usr/bin/env python3
"""XDL Web & Desktop Server: responsive web workspace plus a real gallery-dl backend."""

from __future__ import annotations

import argparse
import collections
import concurrent.futures
import configparser
import getpass
import json
import mimetypes
import os
import re
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.error
import urllib.request
import webbrowser
import zipfile
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, quote, unquote, urlencode, urlparse


BASE_DIR = Path(__file__).resolve().parent
APP_DIR = BASE_DIR / "xdl-gui"
DEFAULT_PORT = 8765
CLOUD_MODE_ENV = os.environ.get("XDL_CLOUD_MODE", "").strip().lower() in {"1", "true", "yes"}

sys.path.insert(0, str(BASE_DIR))
import x_downloader as xdl  # noqa: E402
import xdl_auth  # noqa: E402

STATE_FILE = xdl.XDL_HOME / "gui_state.json"
AUTH_FILE = xdl.XDL_HOME / "auth.json"
AUTH_DB_FILE = xdl.XDL_HOME / "auth.sqlite3"
RCLONE_CONFIG_FILE = xdl.XDL_HOME / "rclone.conf"
CLOUD_TARGET_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}:[A-Za-z0-9_./-]{0,128}$")


def sanitize_cloud_target(raw: str | None) -> str:
    text = str(raw or "").strip().rstrip("/")
    if (
        not text
        or text.startswith("-")
        or ".." in text
        or not CLOUD_TARGET_RE.fullmatch(text)
    ):
        return "gdrive:X_Download"
    return text


ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
FILE_RE = re.compile(
    r"([A-Za-z0-9_]{1,15})_(\d{4}-\d{2}-\d{2})"
    r"(?:[ T]\d{2}[:_-]\d{2}[:_-]\d{2})?_(\d{15,20})_(\d+)\.([A-Za-z0-9]+)"
)
USER_RE = re.compile(r"^[A-Za-z0-9_]{1,15}$")
REASON_LABELS = {
    "timeout": "连接超时",
    "network": "网络中断",
    "forbidden": "权限受限",
    "notfound": "文件已删除",
}
SHORTCUT_DURATION_OPTIONS = [
    "不限时长",
    "≤ 1 分钟",
    "≤ 3 分钟",
    "≤ 5 分钟",
    "≤ 10 分钟",
    "自定义时长",
]
SHORTCUT_RESERVED_PATHS = {
    "home", "explore", "notifications", "messages", "i", "search",
    "settings", "compose", "login", "signup", "tos", "privacy",
    "hashtag", "intent", "share", "account", "download", "about",
}
SHORTCUT_X_HOSTS = {
    "x.com", "www.x.com", "mobile.x.com",
    "twitter.com", "www.twitter.com", "mobile.twitter.com",
    "fxtwitter.com", "vxtwitter.com", "fixupx.com", "fixvx.com",
}


def clean_line(line: str) -> str:
    return ANSI_RE.sub("", line).replace("\r", "").strip()


def human_size(num_bytes: int) -> str:
    if num_bytes <= 0:
        return "—"
    if num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.0f} KB"
    return f"{num_bytes / (1024 * 1024):.1f} MB"


def effective_max_duration(settings: dict) -> int:
    """Return active max video duration in seconds, or 0 when disabled/images-only."""
    if xdl.normalize_media_type(settings.get("mediaType")) == "images":
        return 0
    if not settings.get("durationFilterEnabled"):
        return 0
    return max(5, min(7200, int(settings.get("maxVideoDuration") or 60)))


def describe_filter(settings: dict) -> str:
    """Return human-readable label describing the active media & duration filter."""
    media_type = xdl.normalize_media_type(settings.get("mediaType"))
    max_dur = effective_max_duration(settings)
    if media_type == "images":
        return "仅图片"
    if media_type == "videos" and max_dur > 0:
        return f"仅视频 · ≤ {xdl.format_duration(max_dur)}"
    if media_type == "videos":
        return "仅视频"
    if max_dur > 0:
        return f"视频 ≤ {xdl.format_duration(max_dur)}"
    return ""


def classify_log_line(line: str) -> str:
    stripped = line.strip()
    if stripped.startswith("#"):
        return "skip"
    low = stripped.lower()
    if any(token in low for token in ("error", "failed", "exception", "traceback")) or "失败" in stripped:
        return "error"
    if "warning" in low or "warn" in low or "警告" in stripped:
        return "warn"
    if FILE_RE.search(stripped) or stripped.lower().endswith(
        (".mp4", ".webm", ".m4v", ".mov", ".jpg", ".jpeg", ".png", ".gif", ".webp")
    ):
        return "ok"
    return "info"


def is_user_media_file(path: Path, user: str, flat: bool) -> bool:
    if not path.is_file() or path.suffix.lower() in {".part", ".tmp"}:
        return False
    if not flat:
        return True
    return path.name.lower().startswith(f"{user.lower()}_")


def count_files(directory: Path, user: str | None = None, flat: bool = False) -> int:
    try:
        if flat and user:
            return sum(
                1 for path in directory.iterdir()
                if is_user_media_file(path, user, flat=True)
            )
        return sum(
            1 for path in directory.rglob("*")
            if path.is_file() and path.suffix.lower() not in {".part", ".tmp"}
        )
    except OSError:
        return 0


def recent_files(directory: Path, limit: int = 6, user: str | None = None, flat: bool = False) -> list[dict]:
    try:
        if flat and user:
            files = [
                path for path in directory.iterdir()
                if is_user_media_file(path, user, flat=True)
            ]
        else:
            files = [
                path for path in directory.rglob("*")
                if path.is_file() and path.suffix.lower() not in {".part", ".tmp"}
            ]
    except OSError:
        return []
    entries = []
    for path in files:
        try:
            entries.append((path.stat().st_mtime, path))
        except OSError:
            pass
    entries.sort(reverse=True)
    result = []
    for _, path in entries[:limit]:
        try:
            size = human_size(path.stat().st_size)
        except OSError:
            size = "—"
        result.append({"name": path.name, "size": size})
    return result


def classify_reason(lines: list[str]) -> tuple[str, str]:
    text = "\n".join(lines).lower()
    error_lines = [
        line for line in lines
        if ("error" in line.lower() or "failed" in line.lower())
        and "Failed to download" not in line
    ]
    note = clean_line(error_lines[-1])[:260] if error_lines else ""
    if re.search(r"\b(?:404|410)\b|not\s*found|notfound|gone|deleted", text):
        return "notfound", note
    if re.search(r"\b(?:401|403)\b|forbidden|unauthorized|age[-\s]?restricted|private|protected", text):
        return "forbidden", note
    if re.search(r"timed?\s*out|timeout|read timeout|connect timeout", text):
        return "timeout", note
    return "network", note


def parse_failures(
    text: str,
    username: str,
    output_dir: str,
    returncode: int,
    flat: bool = False,
) -> list[dict]:
    """Turn gallery-dl's log into file-level failure records."""
    lines = [clean_line(line) for line in text.splitlines() if clean_line(line)]
    failures: list[dict] = []
    seen: set[str] = set()
    for index, line in enumerate(lines):
        match = re.search(r"Failed to download (.+)$", line)
        if not match:
            continue
        raw = match.group(1).strip().strip('"')
        if "://" in raw:
            name = raw.rsplit("/", 1)[-1].split("?", 1)[0]
        else:
            name = Path(raw).name
        if not name or name in seen:
            continue
        seen.add(name)
        reason, note = classify_reason(lines[max(0, index - 12):index + 1])
        file_match = FILE_RE.search(name)
        tweet_id = file_match.group(3) if file_match else None
        path = (Path(output_dir) / name) if flat else (Path(output_dir) / username / name)
        size = "—"
        try:
            if path.exists():
                size = human_size(path.stat().st_size)
        except OSError:
            pass
        failures.append({
            "user": username,
            "name": name,
            "reason": reason,
            "note": note or REASON_LABELS.get(reason, reason),
            "tweetId": tweet_id,
            "size": size,
        })

    if not failures and returncode != 0:
        reason, note = classify_reason(lines[-50:])
        failures.append({
            "user": username,
            "name": f"@{username} 账号级失败",
            "reason": reason,
            "note": note or REASON_LABELS.get(reason, reason),
            "tweetId": None,
            "size": "—",
        })
    return failures


def _run_tk_dialog(script: str) -> str | None:
    """Run a native Tk file/folder dialog in an isolated subprocess."""
    try:
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    chosen = (result.stdout or "").strip()
    return str(Path(chosen)) if chosen else None


def pick_folder_dialog(initial_dir: str = "") -> str | None:
    safe_init = json.dumps(initial_dir or str(Path.home()))
    script = (
        "import tkinter as tk\n"
        "from tkinter import filedialog\n"
        "from pathlib import Path\n"
        "root = tk.Tk()\n"
        "root.withdraw()\n"
        "root.attributes('-topmost', True)\n"
        f"init_dir = {safe_init}\n"
        "kw = {'title': '选择 XDL 下载目录'}\n"
        "if Path(init_dir).is_dir(): kw['initialdir'] = init_dir\n"
        "picked = filedialog.askdirectory(**kw)\n"
        "root.destroy()\n"
        "if picked: print(picked)\n"
    )
    return _run_tk_dialog(script)


def pick_file_dialog(initial_path: str = "") -> str | None:
    safe_init = json.dumps(initial_path or "")
    script = (
        "import tkinter as tk\n"
        "from tkinter import filedialog\n"
        "from pathlib import Path\n"
        "root = tk.Tk()\n"
        "root.withdraw()\n"
        "root.attributes('-topmost', True)\n"
        f"p = Path({safe_init}) if {safe_init} else Path.home()\n"
        "init_dir = str(p.parent if p.is_file() or not p.is_dir() else p)\n"
        "kw = {'title': '选择 cookies.txt 文件', 'filetypes': [('Cookie 文件', '*.txt'), ('所有文件', '*.*')]}\n"
        "if Path(init_dir).is_dir(): kw['initialdir'] = init_dir\n"
        "picked = filedialog.askopenfilename(**kw)\n"
        "root.destroy()\n"
        "if picked: print(picked)\n"
    )
    return _run_tk_dialog(script)


def detect_lan_ip() -> str | None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(0.3)
            sock.connect(("8.8.8.8", 80))
            ip = sock.getsockname()[0]
            if ip and not ip.startswith("127."):
                return ip
    except OSError:
        return None
    return None


def detect_running_instance(port: int) -> dict | None:
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/health", method="GET")
        with urllib.request.urlopen(req, timeout=0.45) as resp:
            if resp.status != 200:
                return None
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
            if isinstance(data, dict) and data.get("app") == "xdl-web":
                return data
    except Exception:
        return None
    return None


def _strip_quotes(token: str) -> str:
    if len(token) >= 2 and token[0] == token[-1] and token[0] in {"'", '"'}:
        return token[1:-1]
    return token


def _extract_cmd_user(token: str) -> str | None:
    raw = token.strip().lstrip("@")
    if not raw:
        return None
    match = re.match(
        r"^(?:https?://)?(?:www\.)?(?:x\.com|twitter\.com)/([A-Za-z0-9_]{1,15})(?:[/?#].*)?$",
        raw,
        re.I,
    )
    if match:
        return match.group(1)
    if USER_RE.fullmatch(raw):
        return raw
    return None


def _resolve_proxy_from_settings(settings: dict) -> tuple[str | None, str]:
    """Resolve (proxy_addr, proxy_mode) from current settings."""
    mode = str(settings.get("proxyMode") or "off").lower()
    proxy = None
    if mode == "manual":
        proxy = settings.get("proxyAddr")
    elif mode == "auto" and xdl.default_proxy_available():
        proxy = xdl.DEFAULT_PROXY
    return (proxy or None), mode


def _expand_short_url(url: str, *, proxy: str | None = None, proxy_mode: str = "off") -> str:
    """Resolve shortener links such as https://t.co/... to their canonical destination."""
    if proxy and str(proxy).lower().startswith(("http://", "https://")):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    elif proxy_mode == "off":
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    else:
        opener = urllib.request.build_opener()
    for method in ("HEAD", "GET"):
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)"},
                method=method,
            )
            with opener.open(req, timeout=5) as resp:
                resolved = str(resp.geturl() or "").strip()
                if resolved:
                    return resolved
        except urllib.error.HTTPError as exc:
            resolved = str(exc.geturl() or "").strip()
            if resolved and resolved != url:
                return resolved
            if method == "HEAD" and exc.code in {403, 405, 501}:
                continue
            break
        except Exception:
            break
    return url


def _parse_shortcut_input(raw: str, *, proxy: str | None = None, proxy_mode: str = "off") -> dict:
    """Extract and classify an X/Twitter profile URL, tweet URL, or @username."""
    text = str(raw or "").strip()
    if not text:
        raise ValueError("未收到链接或博主用户名")

    url_match = re.search(r"https?://\S+", text, re.I)
    if url_match:
        url = url_match.group(0).rstrip(".,!?;:)]}>\"'。，！？、")
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if host in {"t.co", "www.t.co"}:
            url = _expand_short_url(url, proxy=proxy, proxy_mode=proxy_mode)
            parsed = urlparse(url)
            host = (parsed.hostname or "").lower()
        if host not in SHORTCUT_X_HOSTS:
            raise ValueError("请分享 X / Twitter 推文或博主主页链接")
        path = re.sub(r"/+$", "", parsed.path or "")
        tweet_match = re.fullmatch(
            r"/(?:([A-Za-z0-9_]{1,15})|i(?:/web)?)/status(?:es)?/(\d{5,25})(?:/.*)?",
            path,
            re.I,
        )
        if tweet_match:
            url_user = tweet_match.group(1) or ""
            tweet_id = tweet_match.group(2)
            canonical_user = url_user or "i"
            return {
                "kind": "tweet",
                "url": f"https://x.com/{canonical_user}/status/{tweet_id}",
                "urlUser": None if url_user.lower() in SHORTCUT_RESERVED_PATHS else (url_user or None),
                "tweetId": tweet_id,
            }
        profile_match = re.fullmatch(
            r"/([A-Za-z0-9_]{1,15})(?:/(?:media|likes|with_replies|highlights|header_photo|photo)?)?",
            path,
            re.I,
        )
        if profile_match:
            user = profile_match.group(1)
            if user.lower() not in SHORTCUT_RESERVED_PATHS:
                return {
                    "kind": "account",
                    "url": f"https://x.com/{user}",
                    "targetUser": user,
                }
        raise ValueError("未识别到有效的 X 推文或博主主页链接")

    bare = text.lstrip("@").strip()
    if USER_RE.fullmatch(bare) and bare.lower() not in SHORTCUT_RESERVED_PATHS:
        return {
            "kind": "account",
            "url": f"https://x.com/{bare}",
            "targetUser": bare,
        }
    raise ValueError("未识别到有效的 X / Twitter 推文或博主链接")


def _parse_shortcut_duration(choice: str | None, custom: str | None = None) -> tuple[bool, int]:
    """Parse Shortcut duration picker labels or custom text into (enabled, seconds)."""
    raw_choice = str(choice or "").strip()
    raw_custom = str(custom or "").strip()
    presets = {
        "": (False, 0),
        "不限时长": (False, 0),
        "不限": (False, 0),
        "0": (False, 0),
        "off": (False, 0),
        "none": (False, 0),
        "unlimited": (False, 0),
        "≤ 1 分钟": (True, 60),
        "≤1分钟": (True, 60),
        "1分钟": (True, 60),
        "≤ 3 分钟": (True, 180),
        "≤3分钟": (True, 180),
        "3分钟": (True, 180),
        "≤ 5 分钟": (True, 300),
        "≤5分钟": (True, 300),
        "5分钟": (True, 300),
        "≤ 10 分钟": (True, 600),
        "≤10分钟": (True, 600),
        "10分钟": (True, 600),
    }
    low_choice = raw_choice.lower()
    if low_choice in presets:
        return presets[low_choice]

    candidate = (
        raw_custom
        if raw_choice in {"自定义时长", "自定义"} or low_choice == "custom"
        else raw_choice
    ).strip()
    if not candidate or candidate.lower() in {"0", "off", "none", "不限", "不限时长", "unlimited"}:
        return (False, 0)

    normalized = candidate.replace("：", ":")
    normalized = re.sub(r"^[≤<=<\s]+", "", normalized)
    normalized = (
        normalized.replace("小时", "h")
        .replace("分钟", "m")
        .replace("分", "m")
        .replace("秒钟", "s")
        .replace("秒", "s")
    )
    secs = xdl.parse_duration_seconds(normalized)
    if secs <= 0:
        return (False, 0)
    return (True, max(5, min(7200, secs)))


class AppState:
    def __init__(
        self,
        state_file: Path = STATE_FILE,
        cloud_mode: bool | None = None,
        require_auth: bool | None = None,
        idle_timeout: int = 0,
        start_background: bool = True,
    ):
        self.lock = threading.RLock()
        self.cloud_mode = CLOUD_MODE_ENV if cloud_mode is None else bool(cloud_mode)
        self.auth_required = bool(self.cloud_mode or require_auth)
        self.auth = xdl_auth.AuthManager(AUTH_FILE, AUTH_DB_FILE)
        self.state_file = Path(state_file)
        self.queue: list[dict] = []
        self.failures: list[dict] = []
        self.settings = self.default_settings()
        self.next_id = 1
        self.next_failure_id = 1
        self.running = False
        self.paused = False
        self.round = 1
        self.round_summary = None
        self.round_stats = {"ok": 0, "failed": 0, "files": 0, "skipped": 0}
        self.started_at = None
        self.elapsed_base = 0
        self.processes: dict[int, subprocess.Popen] = {}
        self.log_entries: collections.deque = collections.deque(maxlen=3000)
        self.log_seq = 0
        self.cmd_history: list[str] = []
        self._proxy_checked = 0.0
        self._proxy_online = False
        self._rclone_checked = 0.0
        self._rclone_info = {"installed": False, "configured": False, "remotes": []}
        self.cloud_sync_lock = threading.Lock()
        self.cloud_syncing = False
        self.cloud_last_sync = {
            "at": None,
            "status": "idle",
            "summary": "",
            "files": 0,
            "error": None,
        }
        self.cloud_folder_url: str = ""
        self.cloud_folder_target: str = ""
        self.cloud_user_folders: dict[str, str] = {}
        self.idle_timeout = max(0, int(idle_timeout))
        self.last_activity = time.monotonic()
        self.stop = threading.Event()
        self.dirty = threading.Event()
        self.allow_window_close = False
        self.gdl_version = "未检测到"
        self.store_ready = False
        self.server_url = ""
        self.lan_url: str | None = None
        self.store_summary = {
            "media": 0,
            "tweets": 0,
            "bytes": 0,
            "users": 0,
            "archive": str(xdl.ARCHIVE_FILE),
        }
        self.backfilled: set[str] = set()
        self.backfill_lock = threading.Lock()
        self.thumb_backfill_lock = threading.Lock()
        self.thumb_backfilling = False
        self._shortcut_inspect_cache: dict[str, tuple[float, dict]] = {}
        self.load()
        self.add_log("info", "XDL Web 工作台已就绪，支持 CLI 命令、实时日志与视频时长过滤")
        if start_background:
            threading.Thread(target=self._saver_loop, daemon=True).start()
            threading.Thread(target=self._scheduler_loop, daemon=True).start()
            threading.Thread(target=self._probe_gallery_dl, daemon=True).start()
            threading.Thread(target=self._prepare_store, daemon=True).start()
            if self.idle_timeout > 0:
                threading.Thread(target=self._idle_watch_loop, daemon=True).start()

    def default_settings(self) -> dict:
        cookies = xdl.find_cookies_file()
        default_cookies = str(xdl.XDL_HOME / "cookies.txt") if self.cloud_mode else (cookies or str(Path.home() / "Downloads" / "cookies.txt"))
        return {
            "output": xdl.DEFAULT_OUTPUT_DIR,
            "cookies": cookies or default_cookies,
            "cookiesSource": "file",
            "cookiesBrowser": "chrome",
            "proxyMode": "off" if self.cloud_mode else "auto",
            "proxyAddr": xdl.DEFAULT_PROXY,
            "limit": 0,
            "concurrency": 2,
            "mediaOnly": True,
            "maxRetries": 3,
            "autoRetry": False,
            "incremental": True,
            "stopAfterSkips": xdl.DEFAULT_STOP_AFTER_SKIPS,
            "mediaType": "all",
            "durationFilterEnabled": False,
            "maxVideoDuration": 60,
            "layout": "per-user",
            "cloudSyncEnabled": False,
            "cloudSyncDeleteLocal": True,
            "cloudSyncTarget": "gdrive:X_Download",
        }

    def _sanitize_settings_locked(self) -> None:
        s = self.settings
        s["limit"] = max(0, min(10000, int(s.get("limit") or 0)))
        s["concurrency"] = max(1, min(5, int(s.get("concurrency") or 2)))
        s["maxRetries"] = max(1, min(5, int(s.get("maxRetries") or 3)))
        s["mediaOnly"] = bool(s.get("mediaOnly", True))
        s["autoRetry"] = bool(s.get("autoRetry", False))
        s["incremental"] = bool(s.get("incremental", True))
        s["stopAfterSkips"] = max(0, min(500, int(s.get("stopAfterSkips") or 0)))
        s["mediaType"] = xdl.normalize_media_type(s.get("mediaType"))
        s["durationFilterEnabled"] = bool(s.get("durationFilterEnabled", False))
        raw_dur = int(s.get("maxVideoDuration") or 60)
        s["maxVideoDuration"] = max(5, min(7200, raw_dur))
        source = str(s.get("cookiesSource") or "file").lower()
        s["cookiesSource"] = "browser" if source == "browser" else "file"
        browser = str(s.get("cookiesBrowser") or "chrome").lower()
        s["cookiesBrowser"] = browser if browser in xdl.SUPPORTED_BROWSERS else "chrome"
        proxy_mode = str(s.get("proxyMode") or ("off" if self.cloud_mode else "auto")).lower()
        s["proxyMode"] = proxy_mode if proxy_mode in {"auto", "manual", "off"} else ("off" if self.cloud_mode else "auto")
        layout = str(s.get("layout") or "per-user").lower()
        s["layout"] = "flat" if layout == "flat" else "per-user"
        s["cloudSyncEnabled"] = bool(s.get("cloudSyncEnabled", False))
        s["cloudSyncDeleteLocal"] = bool(s.get("cloudSyncDeleteLocal", True))
        s["cloudSyncTarget"] = sanitize_cloud_target(s.get("cloudSyncTarget"))
        if self.cloud_mode:
            s["output"] = str(Path(xdl.DEFAULT_OUTPUT_DIR).resolve())
            s["cookies"] = str((xdl.XDL_HOME / "cookies.txt").resolve())
            s["cookiesSource"] = "file"

    def load(self) -> None:
        if not self.state_file.exists():
            return
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
        except Exception:
            return
        if not isinstance(data, dict):
            return
        self.settings.update(data.get("settings") or {})
        self._sanitize_settings_locked()
        self.queue = data.get("queue") if isinstance(data.get("queue"), list) else []
        self.failures = data.get("failures") if isinstance(data.get("failures"), list) else []
        self.next_id = int(data.get("nextId") or 1)
        self.next_failure_id = int(data.get("nextFailureId") or 1)
        self.round = int(data.get("round") or 1)
        self.round_summary = data.get("roundSummary")
        history = data.get("cmdHistory")
        if isinstance(history, list):
            self.cmd_history = [str(item) for item in history if str(item).strip()][-50:]
        last_sync = data.get("cloudLastSync")
        if isinstance(last_sync, dict):
            self.cloud_last_sync.update(last_sync)
        if isinstance(data.get("cloudFolderUrl"), str):
            self.cloud_folder_url = data["cloudFolderUrl"].strip()
        if isinstance(data.get("cloudFolderTarget"), str):
            self.cloud_folder_target = data["cloudFolderTarget"].strip()
        if isinstance(data.get("cloudUserFolders"), dict):
            self.cloud_user_folders = {
                str(k).lower(): str(v)
                for k, v in data["cloudUserFolders"].items()
                if k and v
            }
        for item in self.queue:
            if item.get("status") in {"active", "waiting"}:
                item["status"] = "paused"
            item.pop("retryFailureIds", None)
        for failure in self.failures:
            if failure.get("status") == "queued":
                failure["status"] = "failed"

    def touch_activity(self) -> None:
        self.last_activity = time.monotonic()

    def _is_work_active(self) -> bool:
        with self.lock:
            if self.cloud_syncing:
                return True
            if any(proc.poll() is None for proc in self.processes.values()):
                return True
            if any(item.get("status") == "active" for item in self.queue):
                return True
            if self.running and not self.paused and any(item.get("status") == "waiting" for item in self.queue):
                return True
            return False

    def _idle_watch_loop(self) -> None:
        while not self.stop.wait(5.0):
            if self._is_work_active():
                self.touch_activity()
                continue
            if self.idle_timeout > 0 and (time.monotonic() - self.last_activity) >= self.idle_timeout:
                mins = max(1, round(self.idle_timeout / 60))
                self.add_log("info", f"已连续 {mins} 分钟无操作变化，服务自动休眠以释放内存")
                self.save()
                self.stop.set()
                break

    def add_log(self, level: str, text: str, user: str | None = None) -> dict:
        with self.lock:
            self.last_activity = time.monotonic()
            self.log_seq += 1
            entry = {
                "seq": self.log_seq,
                "ts": time.strftime("%H:%M:%S"),
                "level": level or "info",
                "user": user or "",
                "text": str(text),
            }
            self.log_entries.append(entry)
            return entry

    def get_logs(
        self,
        since: int = 0,
        user: str | None = None,
        level: str | None = None,
        limit: int = 500,
    ) -> dict:
        with self.lock:
            entries = list(self.log_entries)
            if since > 0:
                entries = [entry for entry in entries if entry["seq"] > since]
            if user:
                wanted_user = user.lstrip("@").lower()
                entries = [entry for entry in entries if entry["user"].lower() == wanted_user]
            if level and level != "all":
                entries = [entry for entry in entries if entry["level"] == level]
            if limit > 0 and len(entries) > limit:
                entries = entries[-limit:]
            return {
                "ok": True,
                "entries": entries,
                "lastSeq": self.log_seq,
                "total": len(self.log_entries),
            }

    def clear_logs(self) -> None:
        with self.lock:
            self.log_entries.clear()
            self.add_log("info", "已清空运行日志")

    def snapshot(self) -> dict:
        with self.lock:
            queue = []
            for item in self.queue:
                queue.append({
                    key: value for key, value in item.items()
                    if not key.startswith("_")
                })
            failures = [dict(failure) for failure in self.failures]
            elapsed = self.elapsed_base
            if self.started_at and self.running:
                elapsed += int((time.time() - self.started_at) * 1000)
            active = sum(1 for item in self.queue if item.get("status") == "active")
            waiting = sum(1 for item in self.queue if item.get("status") == "waiting")
            return {
                "queue": queue,
                "failures": failures,
                "settings": dict(self.settings),
                "running": self.running,
                "paused": self.paused,
                "busy": active > 0 or waiting > 0,
                "activeCount": active,
                "round": self.round,
                "roundSummary": self.round_summary,
                "elapsedMs": elapsed,
                "logSeq": self.log_seq,
                "cmdHistory": list(self.cmd_history[-50:]),
                "env": self.env_snapshot(),
            }

    def probe_rclone(self, force: bool = False) -> dict:
        if not force and time.time() - self._rclone_checked < 5:
            return dict(self._rclone_info)
        rclone_bin = shutil.which("rclone")
        remotes: list[str] = []
        configured = False
        if rclone_bin and RCLONE_CONFIG_FILE.is_file():
            try:
                res = subprocess.run(
                    [rclone_bin, "listremotes", "--config", str(RCLONE_CONFIG_FILE)],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if res.returncode == 0:
                    remotes = [
                        line.strip().rstrip(":")
                        for line in (res.stdout or "").splitlines()
                        if line.strip()
                    ]
                    configured = len(remotes) > 0
            except Exception:
                pass
        self._rclone_info = {
            "installed": bool(rclone_bin),
            "configured": configured,
            "remotes": remotes,
        }
        self._rclone_checked = time.time()
        return dict(self._rclone_info)

    def env_snapshot(self) -> dict:
        cookies_source = self.settings.get("cookiesSource", "file")
        cookies_browser = self.settings.get("cookiesBrowser", "chrome")
        cookies = Path(self.settings.get("cookies", ""))
        cookies_found = (
            cookies_browser in xdl.SUPPORTED_BROWSERS
            if cookies_source == "browser"
            else cookies.is_file()
        )
        proxy_mode = self.settings.get("proxyMode", "auto")
        if proxy_mode == "auto":
            if time.time() - self._proxy_checked > 5:
                self._proxy_online = xdl.default_proxy_available()
                self._proxy_checked = time.time()
            proxy_online = self._proxy_online
        else:
            proxy_online = proxy_mode == "manual"
        try:
            out_path = Path(self.settings.get("output") or xdl.DEFAULT_OUTPUT_DIR)
            out_path.mkdir(parents=True, exist_ok=True)
            du = shutil.disk_usage(str(out_path))
            storage_info = {
                "totalBytes": du.total,
                "usedBytes": du.used,
                "freeBytes": du.free,
                "outputDir": str(out_path),
            }
        except OSError:
            storage_info = {
                "totalBytes": 0,
                "usedBytes": 0,
                "freeBytes": 0,
                "outputDir": str(self.settings.get("output", "")),
            }
        rc = self.probe_rclone()
        cloud_target = sanitize_cloud_target(self.settings.get("cloudSyncTarget"))
        target_remote = cloud_target.split(":", 1)[0]
        try:
            cloud_stats = xdl.cloud_media_summary()
        except Exception:
            cloud_stats = {"files": 0, "bytes": 0}
        try:
            thumbs_info = xdl.thumbs_summary()
        except Exception:
            thumbs_info = {"count": 0, "bytes": 0, "missing": 0, "ffmpeg": False}
        return {
            "cloudMode": self.cloud_mode,
            "auth": {
                "enabled": self.auth_required,
                "configured": self.auth.is_configured(),
                "username": self.auth.username(),
            },
            "cookiesFound": cookies_found,
            "cookiesSource": cookies_source,
            "cookiesBrowser": cookies_browser,
            "proxyMode": proxy_mode,
            "proxyOnline": proxy_online,
            "galleryDl": self.gdl_version,
            "filterSummary": describe_filter(self.settings),
            "serverUrl": self.server_url,
            "lanUrl": self.lan_url,
            "storage": storage_info,
            "cloudSync": {
                "installed": rc["installed"],
                "configured": rc["configured"],
                "remotes": rc["remotes"],
                "target": cloud_target,
                "targetRemote": target_remote,
                "targetConfigured": target_remote in rc["remotes"],
                "folderUrl": self.cloud_folder_url,
                "userFolders": dict(self.cloud_user_folders),
                "verifiedCount": int(cloud_stats.get("files") or 0),
                "verifiedBytes": int(cloud_stats.get("bytes") or 0),
                "syncing": self.cloud_syncing,
                "lastSync": dict(self.cloud_last_sync),
            },
            "thumbs": {
                **thumbs_info,
                "backfilling": self.thumb_backfilling,
            },
            "store": {
                **self.store_summary,
                "ready": self.store_ready,
            },
        }

    def _probe_gallery_dl(self) -> None:
        try:
            result = subprocess.run(
                [sys.executable, "-m", "gallery_dl", "--version"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                self.gdl_version = (result.stdout or result.stderr).strip()
        except Exception:
            self.gdl_version = "未安装"
        self.add_log("info", f"环境检测: gallery-dl {self.gdl_version}")
        self.mark_dirty()

    def _prepare_store(self) -> None:
        try:
            xdl.ensure_download_store()
            result = xdl.backfill_output(self.settings["output"])
            summary = xdl.media_summary()
            with self.lock:
                self.store_summary = {
                    "media": summary["media"],
                    "tweets": summary["tweets"],
                    "bytes": summary["bytes"],
                    "users": result["users"],
                    "archive": str(xdl.ARCHIVE_FILE),
                }
                self._refresh_item_counts_locked()
                self.store_ready = True
                target = sanitize_cloud_target(self.settings.get("cloudSyncTarget"))
                output_root = Path(self.settings["output"]).resolve()
                need_cloud_reconcile = (
                    summary["media"] == 0
                    or not self.cloud_folder_url
                    or not self.cloud_user_folders
                )
            if result["files"]:
                msg = f"XDL 已标记 {result['files']} 个历史文件（{summary['tweets']} 条推文）"
                print(msg, flush=True)
                self.add_log("info", msg)

            if need_cloud_reconcile:
                rclone_bin = shutil.which("rclone")
                rc = self.probe_rclone(force=True)
                remote_name = target.split(":", 1)[0]
                if rclone_bin and remote_name in rc.get("remotes", []):
                    entries: list[dict] = []
                    updated = 0
                    try:
                        entries, updated = self._reconcile_cloud_index(
                            rclone_bin, target, output_root
                        )
                        summary = xdl.media_summary()
                        with self.lock:
                            self.store_summary = {
                                "media": summary["media"],
                                "tweets": summary["tweets"],
                                "bytes": summary["bytes"],
                                "users": max(self.store_summary.get("users", 0), len(self.cloud_user_folders)),
                                "archive": str(xdl.ARCHIVE_FILE),
                            }
                            self._refresh_item_counts_locked()
                            if self.cloud_last_sync.get("status") == "idle" and entries:
                                self.cloud_last_sync = {
                                    "at": time.strftime("%Y-%m-%d %H:%M:%S"),
                                    "status": "ok",
                                    "summary": f"已从 {target} 核对 {len(entries)} 个云端媒体",
                                    "files": len(entries),
                                    "updated": updated,
                                    "error": None,
                                }
                        if entries:
                            self.add_log("info", f"[cloud-sync] 启动时已核对 {len(entries)} 个 Google Drive 媒体（同步 {updated} 条档案）")
                    except Exception as exc:
                        self.add_log("warn", f"[cloud-sync] 启动核对云端文件失败: {exc}")

                    try:
                        self._resolve_root_folder_url(rclone_bin, target, force=False)
                    except Exception as exc:
                        self.add_log("warn", f"[cloud-sync] 启动解析云端根目录失败: {exc}")
            self.mark_dirty()
        except Exception:
            traceback.print_exc()

    def _refresh_item_counts_locked(self) -> None:
        for item in self.queue:
            try:
                count = xdl.media_count(item["user"])
                recent = xdl.recent_media(item["user"], 6)
            except Exception:
                continue
            item["downloadedTotal"] = count
            item["files"] = max(int(item.get("files") or 0), count)
            item["recentMedia"] = recent

    def backfill_all(self) -> dict:
        with self.backfill_lock:
            result = xdl.backfill_output(self.settings["output"])
            cloud_entries: list[dict] = []
            with self.lock:
                target = sanitize_cloud_target(self.settings.get("cloudSyncTarget"))
                output_root = Path(self.settings["output"]).resolve()
            rclone_bin = shutil.which("rclone")
            rc = self.probe_rclone(force=True)
            remote_name = target.split(":", 1)[0]
            if rclone_bin and remote_name in rc.get("remotes", []):
                try:
                    cloud_entries, _ = self._reconcile_cloud_index(rclone_bin, target, output_root)
                except Exception as exc:
                    self.add_log("warn", f"[cloud-sync] 重建档案时核对云端文件失败: {exc}")
                try:
                    self._resolve_root_folder_url(rclone_bin, target, force=False)
                except Exception as exc:
                    self.add_log("warn", f"[cloud-sync] 重建档案时解析根目录失败: {exc}")
            summary = xdl.media_summary()
            with self.lock:
                self.store_summary = {
                    "media": summary["media"],
                    "tweets": summary["tweets"],
                    "bytes": summary["bytes"],
                    "users": result["users"],
                    "archive": str(xdl.ARCHIVE_FILE),
                }
                self._refresh_item_counts_locked()
                self.store_ready = True
                self.add_log(
                    "info",
                    f"已重建下载档案：{summary['media']} 个媒体 / {summary['tweets']} 条推文"
                    + (f"（含云端 {len(cloud_entries)} 个）" if cloud_entries else ""),
                )
                self.mark_dirty()
        return {
            "files": result["files"],
            "cloudFiles": len(cloud_entries),
            "media": summary["media"],
            "tweets": summary["tweets"],
            "users": result["users"],
        }

    def mark_dirty(self) -> None:
        self.last_activity = time.monotonic()
        self.dirty.set()

    def shutdown(self) -> None:
        self.stop.set()
        with self.lock:
            for item in self.queue:
                if item.get("status") == "active":
                    item["status"] = "paused"
            for process in self.processes.values():
                if process.poll() is None:
                    try:
                        process.terminate()
                    except OSError:
                        pass
        self.save()

    def _saver_loop(self) -> None:
        while not self.stop.is_set():
            if self.dirty.wait(0.5):
                self.dirty.clear()
                self.save()

    def save(self) -> None:
        with self.lock:
            data = {
                "settings": self.settings,
                "queue": [
                    {
                        key: value for key, value in item.items()
                        if not key.startswith("_")
                    }
                    for item in self.queue
                ],
                "failures": self.failures,
                "nextId": self.next_id,
                "nextFailureId": self.next_failure_id,
                "round": self.round,
                "roundSummary": self.round_summary,
                "cmdHistory": self.cmd_history[-50:],
                "cloudLastSync": dict(self.cloud_last_sync),
                "cloudFolderUrl": self.cloud_folder_url,
                "cloudFolderTarget": self.cloud_folder_target,
                "cloudUserFolders": dict(self.cloud_user_folders),
            }
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_file.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            os.replace(tmp, self.state_file)
        except OSError:
            pass

    # ---------- queue ----------

    def add_users(self, users: list[str]) -> dict:
        added = 0
        skipped = 0
        with self.lock:
            existing = {
                item["user"].lower()
                for item in self.queue
                if item.get("taskKind", "account") != "tweet"
            }
            for user in users:
                user = str(user).strip().lstrip("@")
                if not USER_RE.fullmatch(user):
                    continue
                if user.lower() in existing:
                    skipped += 1
                    continue
                item = {
                    "id": self.next_id,
                    "taskKind": "account",
                    "user": user,
                    "status": "waiting",
                    "progress": 0,
                    "files": 0,
                    "error": None,
                    "failedCount": 0,
                    "retried": False,
                    "retryOnly": False,
                    "newFiles": 0,
                    "skippedFiles": 0,
                    "downloadedTotal": 0,
                    "filterSummary": describe_filter(self.settings),
                }
                try:
                    known = xdl.media_count(user)
                    item["files"] = known
                    item["downloadedTotal"] = known
                    item["recentMedia"] = xdl.recent_media(user, 6)
                except Exception:
                    item["recentMedia"] = []
                self.queue.append(item)
                self.next_id += 1
                existing.add(user.lower())
                added += 1
            if added:
                self.add_log("info", f"已加入 {added} 个账号到下载队列")
            self.mark_dirty()
            return {"added": added, "skipped": skipped}

    def remove_items(self, ids: list[int]) -> None:
        wanted = {int(value) for value in ids}
        with self.lock:
            for item_id in wanted:
                item = self._find_item(item_id)
                if item is not None:
                    item["_stopped"] = True
                process = self.processes.pop(item_id, None)
                if process and process.poll() is None:
                    try:
                        process.terminate()
                    except OSError:
                        pass
            self.queue = [item for item in self.queue if item["id"] not in wanted]
            self.failures = [
                failure for failure in self.failures
                if failure.get("ownerId") not in wanted
            ]
            self.mark_dirty()

    def clear_done(self) -> None:
        with self.lock:
            keep = [
                item for item in self.queue
                if item.get("status") != "done"
            ]
            removed = {item["id"] for item in self.queue} - {item["id"] for item in keep}
            self.queue = keep
            self.failures = [
                failure for failure in self.failures
                if failure.get("ownerId") not in removed
            ]
            self.mark_dirty()

    def reorder(self, ids: list[int]) -> None:
        wanted = [int(value) for value in ids]
        with self.lock:
            order = {item_id: index for index, item_id in enumerate(wanted)}
            self.queue.sort(key=lambda item: order.get(item["id"], len(order)))
            self.mark_dirty()

    # ---------- settings ----------

    def update_settings(self, payload: dict) -> dict:
        with self.lock:
            for key in self.settings:
                if key in payload:
                    self.settings[key] = payload[key]
            self._sanitize_settings_locked()
            self.mark_dirty()
            return dict(self.settings)

    # ---------- command bar ----------

    def execute_command(self, raw_cmd: str) -> dict:
        cmd_text = str(raw_cmd or "").strip()
        if not cmd_text:
            return {"ok": False, "error": "请输入命令"}

        with self.lock:
            if not self.cmd_history or self.cmd_history[-1] != cmd_text:
                self.cmd_history.append(cmd_text)
                if len(self.cmd_history) > 50:
                    self.cmd_history = self.cmd_history[-50:]
            self.add_log("cmd", f"$ {cmd_text}")
            self.mark_dirty()

        try:
            tokens = [_strip_quotes(t) for t in shlex.split(cmd_text, posix=False)]
        except ValueError as exc:
            err = f"命令语法错误: {exc}"
            self.add_log("error", err)
            return {"ok": False, "error": err}

        if tokens and tokens[0].lower() in {"xdl", "xdl.cmd", "xdl.exe", "x_downloader.py"}:
            tokens = tokens[1:]

        if not tokens:
            msg = "提示: 输入用户名（如 elonmusk --videos-only --max-duration 60）或 --help 查看参数"
            self.add_log("info", msg)
            return {"ok": True, "action": "help", "message": msg, "state": self.snapshot()}

        first = tokens[0].lower()
        if first in {"-h", "--help", "help"}:
            help_lines = [
                "XDL 命令用法:",
                "  xdl <用户名或主页URL...> [选项]   加入队列并开始下载",
                "  常用选项:",
                "    --videos-only / --images-only / --media-type <all|videos|images>",
                "    --max-duration <60|1m|01:30|0>   限制单条视频最长时长（0 为不限）",
                "    --limit <N>                      限制每个账号最近 N 条推文",
                "    --media-only / --no-media-only   仅抓取 /media 页面或全量推文",
                "    --full / --incremental           全量扫描 / 增量跳过已下载",
                "    --stop-after-skips <N>           连续跳过 N 条后停止本轮",
                "    --proxy <URL> / --no-proxy       指定代理或强制直连",
                "    -o <目录>  -c <cookies.txt>  --cookies-from-browser <chrome|edge|...>",
                "    --queue-only                     仅加入队列与更新参数，不立即开始",
                "  管理命令: --retry [用户名] | --status | --clear-queue",
            ]
            for line in help_lines:
                self.add_log("info", line)
            return {"ok": True, "action": "help", "message": "已在实时日志输出命令帮助", "state": self.snapshot()}

        if first in {"--status", "--queue", "status"}:
            snap = self.snapshot()
            pending_fail = sum(1 for f in snap["failures"] if f.get("status") in {"failed", "queued"})
            filt = describe_filter(self.settings) or "不限"
            summary = (
                f"当前状态: 队列 {len(snap['queue'])} 个账号 · 失败 {pending_fail} 条 · "
                f"轮次 第 {snap['round']} 轮 · 筛选 {filt}"
            )
            self.add_log("info", summary)
            return {"ok": True, "action": "status", "message": summary, "state": snap}

        if first == "--clear-queue":
            with self.lock:
                self.failures = [
                    f for f in self.failures if f.get("status") not in {"failed", "queued"}
                ]
                for item in self.queue:
                    item["failedCount"] = len(self._pending_for(item["id"]))
                self.mark_dirty()
            xdl.clear_retry_queue()
            self.add_log("ok", "已清空失败队列")
            return {"ok": True, "action": "clear", "message": "已清空失败队列", "state": self.snapshot()}

        if first == "--retry":
            target_user = tokens[1].lstrip("@") if len(tokens) > 1 and not tokens[1].startswith("-") else None
            if target_user:
                res = self.retry_account(target_user)
                if not res.get("ok") and not res.get("queued"):
                    err = res.get("error") or f"@{target_user} 没有可重试项"
                    self.add_log("warn", err)
                    return {"ok": False, "error": err}
                msg = f"已重新排队重试 @{target_user}"
                self.add_log("info", msg)
                return {"ok": True, "action": "retry", "message": msg, "state": self.snapshot()}
            with self.lock:
                retry_ids = [
                    f["id"] for f in self.failures
                    if f.get("status") == "failed"
                    and f.get("reason") != "notfound"
                    and f.get("tries", 1) < self.settings["maxRetries"]
                ]
            if not retry_ids:
                msg = "失败队列中没有可重试的项目"
                self.add_log("warn", msg)
                return {"ok": True, "action": "retry", "message": msg, "state": self.snapshot()}
            res = self.retry_failures(retry_ids)
            msg = f"已加入 {res.get('queued', 0)} 个失败项重试"
            self.add_log("info", msg)
            return {"ok": True, "action": "retry", "message": msg, "state": self.snapshot()}

        # Parse targets and inline flags
        patch: dict = {}
        targets: list[str] = []
        queue_only = False
        idx = 0
        try:
            while idx < len(tokens):
                tok = tokens[idx]
                if tok.startswith("-") and tok != "-":
                    if "=" in tok:
                        flag, inline_val = tok.split("=", 1)
                        has_inline = True
                    else:
                        flag, inline_val = tok, None
                        has_inline = False
                    low = flag.lower()

                    def next_val() -> str:
                        nonlocal idx
                        if has_inline:
                            return inline_val or ""
                        if idx + 1 >= len(tokens):
                            raise ValueError(f"参数 {flag} 缺少值")
                        idx += 1
                        return tokens[idx]

                    if low in {"-o", "--output"}:
                        if self.cloud_mode:
                            raise ValueError("云端模式已锁定服务器下载目录，禁止修改 -o/--output")
                        patch["output"] = next_val()
                    elif low in {"-c", "--cookies"}:
                        if self.cloud_mode:
                            raise ValueError("云端模式已锁定 Cookie 路径，请在界面中上传或填写 Cookie")
                        patch["cookiesSource"] = "file"
                        patch["cookies"] = next_val()
                    elif low == "--cookies-from-browser":
                        if self.cloud_mode:
                            raise ValueError("云端模式不支持读取客户端浏览器 Cookie，请使用界面导入功能")
                        browser = next_val().lower()
                        if browser not in xdl.SUPPORTED_BROWSERS:
                            raise ValueError(f"不支持的浏览器: {browser}")
                        patch["cookiesSource"] = "browser"
                        patch["cookiesBrowser"] = browser
                    elif low == "--media-only":
                        patch["mediaOnly"] = True
                    elif low == "--no-media-only":
                        patch["mediaOnly"] = False
                    elif low == "--limit":
                        patch["limit"] = max(0, int(next_val()))
                    elif low == "--full":
                        patch["incremental"] = False
                    elif low == "--incremental":
                        patch["incremental"] = True
                    elif low == "--stop-after-skips":
                        patch["stopAfterSkips"] = max(0, int(next_val()))
                    elif low == "--proxy":
                        patch["proxyMode"] = "manual"
                        patch["proxyAddr"] = next_val()
                    elif low == "--no-proxy":
                        patch["proxyMode"] = "off"
                    elif low == "--max-duration":
                        secs = xdl.parse_duration_seconds(next_val())
                        if secs <= 0:
                            patch["durationFilterEnabled"] = False
                        else:
                            patch["durationFilterEnabled"] = True
                            patch["maxVideoDuration"] = max(5, min(7200, secs))
                    elif low == "--media-type":
                        val = next_val().lower()
                        if val not in {"all", "videos", "images", "video", "image", "photo", "photos"}:
                            raise ValueError(f"无效的 --media-type: {val}（可选 all / videos / images）")
                        patch["mediaType"] = xdl.normalize_media_type(val)
                    elif low == "--videos-only":
                        patch["mediaType"] = "videos"
                    elif low == "--images-only":
                        patch["mediaType"] = "images"
                    elif low == "--queue-only":
                        queue_only = True
                    elif low == "--flat":
                        patch["layout"] = "flat"
                    elif low == "--per-user":
                        patch["layout"] = "per-user"
                    else:
                        raise ValueError(f"未知参数: {flag}")
                else:
                    user = _extract_cmd_user(tok)
                    if not user:
                        raise ValueError(f"无效的用户名或链接: {tok}")
                    if user.lower() not in {u.lower() for u in targets}:
                        targets.append(user)
                idx += 1
        except ValueError as exc:
            err = str(exc)
            self.add_log("error", err)
            return {"ok": False, "error": err}

        if patch:
            self.update_settings(patch)

        if not targets:
            if not patch:
                err = "请指定要下载的博主用户名或链接"
                self.add_log("warn", err)
                return {"ok": False, "error": err}
            filt = describe_filter(self.settings) or "全部媒体"
            msg = f"已更新运行配置（当前筛选：{filt}）"
            self.add_log("info", msg)
            return {"ok": True, "action": "settings", "message": msg, "state": self.snapshot()}

        self.add_users(targets)
        filt_label = describe_filter(self.settings)
        with self.lock:
            wanted = {u.lower() for u in targets}
            for item in self.queue:
                if item.get("taskKind", "account") != "tweet" and item["user"].lower() in wanted:
                    item.pop("settingsOverride", None)
                    item["filterSummary"] = filt_label
                    if item.get("status") in {"done", "failed", "paused"}:
                        item["status"] = "waiting"
                        item["progress"] = 0
                        item["error"] = None
                        item["newFiles"] = 0
                        item["skippedFiles"] = 0
            self.mark_dirty()

        if not queue_only:
            self.start()
            msg = f"已启动下载: {', '.join('@' + u for u in targets)}" + (f"（{filt_label}）" if filt_label else "")
        else:
            msg = f"已加入队列: {', '.join('@' + u for u in targets)}" + (f"（{filt_label}）" if filt_label else "")
        self.add_log("info", msg)
        return {"ok": True, "action": "run", "users": targets, "message": msg, "state": self.snapshot()}

    # ---------- scheduling ----------

    def _effective_item_settings_locked(self, item: dict) -> dict:
        settings = dict(self.settings)
        override = item.get("settingsOverride")
        if isinstance(override, dict) and override:
            settings.update(override)
        return settings

    def start(self, *, requeue_done: bool = True, resume_paused: bool = True) -> None:
        with self.lock:
            filt_label = describe_filter(self.settings)
            for item in self.queue:
                if resume_paused and item.get("status") == "paused":
                    item["status"] = "waiting"
                    item["filterSummary"] = describe_filter(self._effective_item_settings_locked(item))
                elif requeue_done and item.get("status") == "done":
                    item.pop("settingsOverride", None)
                    item["status"] = "waiting"
                    item["progress"] = 0
                    item["error"] = None
                    item["newFiles"] = 0
                    item["skippedFiles"] = 0
                    item["filterSummary"] = filt_label
            if not requeue_done and self.running and not self.paused:
                self.mark_dirty()
                return
            if self.round_summary is not None:
                self.round += 1
                self.round_stats = {"ok": 0, "failed": 0, "files": 0, "skipped": 0}
                self.round_summary = None
            self.running = True
            self.paused = False
            if self.started_at is None:
                self.started_at = time.time()
            self.add_log(
                "info",
                f"第 {self.round} 轮开始下载" + (f"（筛选：{filt_label}）" if filt_label else ""),
            )
            self.mark_dirty()

    def pause(self) -> None:
        with self.lock:
            self.paused = True
            if self.started_at:
                self.elapsed_base += int((time.time() - self.started_at) * 1000)
                self.started_at = None
            self.add_log("warn", "已暂停调度：当前活跃账号完成后不再启动新任务")
            self.mark_dirty()

    def stop_all(self) -> dict:
        with self.lock:
            self.running = False
            self.paused = False
            if self.started_at:
                self.elapsed_base += int((time.time() - self.started_at) * 1000)
                self.started_at = None
            for item in self.queue:
                if item.get("status") in {"active", "waiting"}:
                    item["_stopped"] = True
                    item["status"] = "paused"
                    item["error"] = None
            for process in list(self.processes.values()):
                if process.poll() is None:
                    try:
                        process.terminate()
                    except OSError:
                        pass
            self.add_log("warn", "已停止所有进行中的下载任务")
            self.mark_dirty()
            return {"ok": True}

    def _scheduler_loop(self) -> None:
        while not self.stop.is_set():
            try:
                self._scheduler_tick()
            except Exception:
                traceback.print_exc()
            time.sleep(0.4)

    def _scheduler_tick(self) -> None:
        with self.lock:
            if not self.running or self.paused:
                return
            active = sum(1 for item in self.queue if item.get("status") == "active")
            waiting = [item for item in self.queue if item.get("status") == "waiting"]
            if waiting and active < self.settings["concurrency"]:
                room = self.settings["concurrency"] - active
                for item in waiting[:room]:
                    self._launch(item)
            active = sum(1 for item in self.queue if item.get("status") == "active")
            waiting = [item for item in self.queue if item.get("status") == "waiting"]
            if active == 0 and not waiting:
                self._finish_round()

    def _launch(self, item: dict) -> None:
        item["status"] = "active"
        item["progress"] = 2
        item["error"] = None
        item["_startedAt"] = time.time()
        item.pop("_stopped", None)
        failure_ids = list(item.pop("_retryFailureIds", []))
        item["retryOnly"] = bool(failure_ids)
        for failure in self.failures:
            if failure["id"] in failure_ids:
                failure["status"] = "queued"
        settings = self._effective_item_settings_locked(item)
        item["filterSummary"] = describe_filter(settings)
        user = item["user"]
        item_id = item["id"]
        task_kind = item.get("taskKind") or "account"
        task_label = "单条推文任务" if task_kind == "tweet" else "任务"
        self.add_log(
            "info",
            f"开始{task_label} @{user}" + (f"（{item['filterSummary']}）" if item["filterSummary"] else ""),
            user=user,
        )
        self.mark_dirty()
        threading.Thread(
            target=self._run_item_safe,
            args=(item_id, user, failure_ids, settings),
            daemon=True,
        ).start()

    def _run_item_safe(
        self,
        item_id: int,
        user: str,
        failure_ids: list[int],
        settings: dict,
    ) -> None:
        try:
            self._run_item(item_id, user, failure_ids, settings)
        except Exception as exc:
            traceback.print_exc()
            self.add_log("error", f"@{user} 内部异常: {exc}", user=user)
            with self.lock:
                item = self._find_item(item_id)
                if item is not None:
                    item["status"] = "failed"
                    item["progress"] = 100
                    item["error"] = f"内部错误: {exc}"
                    item.pop("_retryFailureIds", None)
                self.processes.pop(item_id, None)
                self.mark_dirty()

    def _run_item(
        self,
        item_id: int,
        user: str,
        failure_ids: list[int],
        settings: dict,
    ) -> None:
        with self.lock:
            item = self._find_item(item_id)
            if item is None:
                return
            output = settings["output"]
            flat = settings.get("layout") == "flat"
            task_kind = item.get("taskKind") or "account"
            target_url = str(item.get("targetUrl") or "").strip()
            include_quoted = bool(item.get("includeQuoted"))

        targets: list[tuple[str, list[int]]] = []
        if failure_ids:
            groups: dict[str | None, list[int]] = {}
            for failure_id in failure_ids:
                failure = self._find_failure(failure_id)
                if not failure:
                    continue
                groups.setdefault(failure.get("tweetId") or None, []).append(failure_id)
            for tweet_id, ids in groups.items():
                if tweet_id:
                    target = f"https://x.com/{user}/status/{tweet_id}"
                elif task_kind == "tweet" and target_url:
                    target = target_url
                else:
                    target = f"https://x.com/{user}" + ("/media" if settings["mediaOnly"] else "")
                targets.append((target, ids))
        elif task_kind == "tweet" and target_url:
            targets.append((target_url, []))
        else:
            target = f"https://x.com/{user}" + ("/media" if settings["mediaOnly"] else "")
            targets.append((target, []))

        new_failures: list[dict] = []
        run_stats = {"newFiles": 0, "skippedFiles": 0, "recentMedia": []}
        ok = True
        for target, ids in targets:
            with self.lock:
                current = self._find_item(item_id)
                if current is None or current.get("_stopped"):
                    break
            target_ok, failures, stats = self._run_gallery(
                item_id, user, target, settings,
                is_tweet="/status/" in target,
                include_quoted=include_quoted,
            )
            ok = ok and target_ok
            run_stats["newFiles"] += int(stats.get("newFiles") or 0)
            run_stats["skippedFiles"] += int(stats.get("skippedFiles") or 0)
            if stats.get("recentMedia"):
                run_stats["recentMedia"] = stats["recentMedia"]
            for failure in failures:
                if not any(
                    known["user"].lower() == failure["user"].lower()
                    and known["name"] == failure["name"]
                    for known in new_failures
                ):
                    failure["_retryIds"] = ids
                    new_failures.append(failure)

        with self.lock:
            item = self._find_item(item_id)
            if item is None:
                return
            if item.pop("_stopped", False):
                item["status"] = "paused"
                item["error"] = None
                item["retryOnly"] = False
                item.pop("_retryFailureIds", None)
                item["newFiles"] = int(run_stats["newFiles"])
                item["skippedFiles"] = int(run_stats["skippedFiles"])
                item["files"] = count_user_files(Path(output), user, flat)
                self.processes.pop(item_id, None)
                self.mark_dirty()
                return

            recovered = 0
            if failure_ids:
                by_name = {failure["name"]: failure for failure in new_failures}
                for failure_id in failure_ids:
                    failure = self._find_failure(failure_id)
                    if not failure:
                        continue
                    replacement = by_name.pop(failure["name"], None)
                    if replacement is None:
                        file_path = (Path(output) / failure["name"]) if flat else (Path(output) / user / failure["name"])
                        account_level = failure["name"].startswith("@")
                        if file_path.exists() or (account_level and ok):
                            failure["status"] = "done"
                            recovered += 1
                            continue
                    if replacement:
                        failure["reason"] = replacement["reason"]
                        failure["note"] = replacement["note"]
                    failure["status"] = "failed"
                    failure["round"] = self.round
                for replacement in by_name.values():
                    self._add_failure_locked(item, replacement)
            else:
                for failure in new_failures:
                    self._add_failure_locked(item, failure)
                if ok and not new_failures:
                    for failure in self.failures:
                        if (
                            failure.get("ownerId") == item["id"]
                            and failure.get("status") in {"failed", "queued"}
                        ):
                            failure["status"] = "done"
                            recovered += 1

            local_files_count = count_user_files(Path(output), user, flat)
            item["recentFiles"] = recent_files(
                Path(output) if flat else (Path(output) / user),
                user=user,
                flat=flat,
            )
            try:
                item["downloadedTotal"] = xdl.media_count(user)
                item["recentMedia"] = xdl.recent_media(user, 6)
            except Exception:
                item["downloadedTotal"] = int(item.get("downloadedTotal") or local_files_count)
                if run_stats["recentMedia"]:
                    item["recentMedia"] = run_stats["recentMedia"]
            item["files"] = max(local_files_count, int(item.get("downloadedTotal") or 0))
            item["newFiles"] = int(run_stats["newFiles"])
            item["skippedFiles"] = int(run_stats["skippedFiles"])
            item["lastRunAt"] = time.strftime("%Y-%m-%d %H:%M:%S")
            item["progress"] = 100
            item["retryOnly"] = False
            item.pop("_retryFailureIds", None)
            pending = self._pending_for(item["id"])
            item["failedCount"] = len(pending)
            if pending:
                item["status"] = "failed"
                item["error"] = pending[0].get("note") or REASON_LABELS.get(
                    pending[0].get("reason"), "下载失败"
                )
            elif ok:
                item["status"] = "done"
                item["error"] = None
            else:
                item["status"] = "failed"
                item["error"] = "下载失败"

            self.round_stats["files"] += int(run_stats["newFiles"])
            self.round_stats["skipped"] += int(run_stats["skippedFiles"])
            if item["status"] == "done":
                self.round_stats["ok"] += 1
                done_prefix = "单条推文完成" if task_kind == "tweet" else "完成"
                self.add_log(
                    "ok",
                    f"@{user} {done_prefix}：新增 {item['newFiles']} · 跳过 {item['skippedFiles']} · 累计 {item['downloadedTotal']} 个媒体",
                    user=user,
                )
            else:
                self.round_stats["failed"] += 1
                self.add_log("error", f"@{user} 结束，存在 {item['failedCount'] or 1} 项失败", user=user)
            if recovered:
                item["retried"] = True
            deferred = item.pop("_deferredRetryIds", None)
            if deferred:
                item["_retryFailureIds"] = deferred
                item["status"] = "waiting"
                item["retryOnly"] = True
            self.processes.pop(item_id, None)
            self.mark_dirty()

        if settings.get("cloudSyncEnabled") and (run_stats["newFiles"] > 0 or local_files_count > 0):
            threading.Thread(
                target=self.sync_to_cloud,
                kwargs={"user": user, "trigger": "auto"},
                daemon=True,
            ).start()

    def _resolve_gallery_env(self, settings: dict) -> tuple[str | None, str | None, str | None]:
        """Resolve (cookies_config, proxy, error_message) from settings."""
        cookies_source = settings.get("cookiesSource", "file")
        cookies_browser = settings.get("cookiesBrowser", "chrome")
        if cookies_source == "browser":
            if cookies_browser not in xdl.SUPPORTED_BROWSERS:
                return None, None, f"不支持的浏览器 Cookie 来源: {cookies_browser}"
            cookies_config = f"cookies-from-browser:{cookies_browser}"
        else:
            cookies = Path(settings["cookies"])
            if not cookies.is_file():
                return None, None, f"Cookie 文件不存在: {cookies}"
            cookies_config = str(cookies)

        proxy, _ = _resolve_proxy_from_settings(settings)
        return cookies_config, proxy, None

    def _run_gallery(
        self,
        item_id: int,
        user: str,
        target: str,
        settings: dict,
        is_tweet: bool,
        include_quoted: bool = False,
    ) -> tuple[bool, list[dict], dict]:
        empty_stats = {"newFiles": 0, "skippedFiles": 0, "recentMedia": []}
        cookies_config, proxy, env_err = self._resolve_gallery_env(settings)
        if env_err:
            return False, [{
                "user": user,
                "name": f"@{user} 账号级失败",
                "reason": "forbidden",
                "note": env_err,
                "tweetId": None,
                "size": "—",
            }], empty_stats

        output_dir = Path(settings["output"])
        flat_layout = settings.get("layout") == "flat"
        media_dir = output_dir if flat_layout else (output_dir / user)
        try:
            media_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return False, [{
                "user": user,
                "name": f"@{user} 账号级失败",
                "reason": "forbidden",
                "note": f"无法创建输出目录: {exc}",
                "tweetId": None,
                "size": "—",
            }], empty_stats

        media_type = xdl.normalize_media_type(settings.get("mediaType"))
        max_duration = effective_max_duration(settings)
        args = SimpleNamespace(
            proxy=proxy,
            media_only=bool(settings.get("mediaOnly")) and not is_tweet,
            limit=settings.get("limit") or 0,
            incremental=bool(settings.get("incremental", True)),
            stop_after_skips=int(settings.get("stopAfterSkips") or 0),
            media_type=media_type,
            max_duration=max_duration,
            quoted=bool(include_quoted),
        )
        backfill_key = f"{user.lower()}|{str(media_dir).lower()}"
        if backfill_key not in self.backfilled:
            self.backfilled.add(backfill_key)
            try:
                xdl.backfill_directory(media_dir, user)
            except Exception:
                self.backfilled.discard(backfill_key)
        before = xdl.snapshot_file_map(media_dir)
        config = xdl.build_config(user, str(output_dir), cookies_config, args)
        css = user.replace("/", "_")
        config_path = Path(tempfile.gettempdir()) / f"xdl_gui_{css}_{os.getpid()}_{time.time_ns()}.json"
        log_path = config_path.with_suffix(".log")
        config_path.write_text(
            json.dumps(config, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        cmd = [
            sys.executable, "-m", "gallery_dl",
            "--config", str(config_path),
            "--write-log", str(log_path),
            "-d", str(media_dir),
        ]
        if proxy:
            cmd.extend(["--proxy", proxy])
        if settings.get("limit") and not is_tweet:
            cmd.extend(["--range", f"1-{settings['limit']}"])
        cmd.append(target)

        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        process = None
        lines: list[str] = []
        last_scan = 0.0
        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=env,
            )
            with self.lock:
                self.processes[item_id] = process
            if process.stdout:
                for line in process.stdout:
                    stripped = clean_line(line)
                    if stripped:
                        lines.append(stripped)
                        if len(lines) > 4000:
                            lines = lines[-2500:]
                        self.add_log(classify_log_line(stripped), stripped, user=user)
                        with self.lock:
                            item = self._find_item(item_id)
                            if item is None or item.get("_stopped"):
                                process.terminate()
                                break
                            elapsed = time.time() - item.get("_startedAt", time.time())
                            item["progress"] = min(92, 3 + elapsed * 1.6)
                            if time.time() - last_scan > 1.5:
                                item["files"] = count_user_files(output_dir, user, flat_layout)
                                last_scan = time.time()
            returncode = process.wait()
        except Exception as exc:
            err_line = f"[xdl][error] {exc}"
            lines.append(err_line)
            self.add_log("error", err_line, user=user)
            returncode = 1
        finally:
            with self.lock:
                self.processes.pop(item_id, None)
            if log_path.exists():
                try:
                    log_text = log_path.read_text(encoding="utf-8", errors="replace")
                    lines.extend(
                        clean_line(line) for line in log_text.splitlines()
                        if clean_line(line)
                    )
                except OSError:
                    pass
            for path in (config_path, log_path):
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass

        with self.lock:
            item = self._find_item(item_id)
            stopped = bool(item and item.get("_stopped"))
        if stopped:
            new_files = xdl.collect_new_files(media_dir, user, before)
            return True, [], {
                "newFiles": len(new_files),
                "skippedFiles": 0,
                "totalMedia": xdl.media_count(user),
                "recentMedia": xdl.recent_media(user, 6),
            }

        failures = parse_failures(
            "\n".join(lines), user, str(output_dir), returncode, flat=flat_layout
        )
        new_files = xdl.collect_new_files(media_dir, user, before)
        skipped = xdl.count_skipped_from_log("\n".join(lines))
        try:
            summary = xdl.media_summary(user)
            recent = xdl.recent_media(user, 6)
        except Exception:
            summary = {"media": 0}
            recent = []
        stats = {
            "newFiles": len(new_files),
            "skippedFiles": skipped,
            "totalMedia": int(summary.get("media") or 0),
            "recentMedia": recent,
        }
        has_filter = media_type != "all" or max_duration > 0
        if (
            returncode == 0
            and not failures
            and not is_tweet
            and count_user_files(output_dir, user, flat_layout) == 0
        ):
            if skipped > 0 or len(new_files) > 0 or int(stats.get("totalMedia") or 0) > 0:
                self.add_log(
                    "info",
                    f"@{user} 增量检查完成，暂无新增媒体（已跳过 {skipped} 条，累计归档 {stats['totalMedia']} 个）",
                    user=user,
                )
                return True, [], stats
            if has_filter:
                self.add_log(
                    "warn",
                    f"@{user} 已扫描完成，当前筛选条件（{describe_filter(settings) or '媒体过滤'}）下没有匹配媒体",
                    user=user,
                )
                return True, [], stats
            failures.append({
                "user": user,
                "name": f"@{user} 账号级失败",
                "reason": "network",
                "note": "未获取到任何文件：账号不存在、没有媒体，或链接不可用",
                "tweetId": None,
                "size": "—",
            })
        return returncode == 0 and not failures, failures, stats

    def _probe_tweet_with_gallery_dl(self, target_url: str, settings: dict) -> dict:
        """Inspect tweet and quoted-tweet metadata using gallery-dl --dump-json."""
        cookies_config, proxy, env_err = self._resolve_gallery_env(settings)
        if env_err:
            return {"error": env_err, "tweets": {}, "order": []}

        probe_args = SimpleNamespace(
            proxy=proxy,
            media_only=False,
            limit=0,
            incremental=False,
            stop_after_skips=0,
            media_type="all",
            max_duration=0,
            quoted=True,
            text_tweets=True,
        )
        config = xdl.build_config("probe", str(settings.get("output") or xdl.DEFAULT_OUTPUT_DIR), cookies_config, probe_args)
        twitter_cfg = config.get("extractor", {}).get("twitter", {})
        twitter_cfg["replies"] = False
        twitter_cfg["pinned"] = False
        twitter_cfg["retweets"] = False
        twitter_cfg["cards"] = False
        config.pop("archive", None)
        config["skip"] = False
        if isinstance(config.get("output"), dict):
            config["output"]["skip"] = False

        config_path = Path(tempfile.gettempdir()) / f"xdl_probe_{os.getpid()}_{time.time_ns()}.json"
        entries: list = []
        probe_err: str | None = None
        try:
            config_path.write_text(
                json.dumps(config, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            cmd = [
                sys.executable, "-m", "gallery_dl",
                "--config", str(config_path),
                "--dump-json",
            ]
            if proxy:
                cmd.extend(["--proxy", proxy])
            cmd.append(target_url)
            env = dict(os.environ)
            env["PYTHONIOENCODING"] = "utf-8"
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=12,
                env=env,
            )
            raw_out = (proc.stdout or "").strip()
            if raw_out:
                try:
                    parsed_out = json.loads(raw_out)
                    if isinstance(parsed_out, list):
                        entries = parsed_out
                except json.JSONDecodeError:
                    for line in raw_out.splitlines():
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            item = json.loads(line)
                            if isinstance(item, list):
                                entries.append(item)
                        except json.JSONDecodeError:
                            continue
            if proc.returncode != 0 and not entries:
                err_tail = clean_line((proc.stderr or "")[-260:])
                probe_err = err_tail or f"gallery-dl 探测退出码 {proc.returncode}"
        except Exception as exc:
            probe_err = str(exc)
        finally:
            try:
                config_path.unlink(missing_ok=True)
            except OSError:
                pass

        tweets: dict[str, dict] = {}
        order: list[str] = []
        for entry in entries:
            if not isinstance(entry, list) or len(entry) < 2 or not isinstance(entry[-1], dict):
                continue
            meta = entry[-1]
            tid = str(meta.get("tweet_id") or "").strip()
            if not tid:
                continue
            author_obj = meta.get("author") if isinstance(meta.get("author"), dict) else {}
            user_obj = meta.get("user") if isinstance(meta.get("user"), dict) else {}
            author = str(author_obj.get("name") or user_obj.get("name") or "").strip().lstrip("@")
            quote_id_raw = meta.get("quote_id")
            quote_id = str(quote_id_raw).strip() if quote_id_raw not in {None, 0, "0", ""} else ""
            retweet_id_raw = meta.get("retweet_id")
            retweet_id = str(retweet_id_raw).strip() if retweet_id_raw not in {None, 0, "0", ""} else ""
            quote_by_raw = meta.get("quote_by")
            quote_by = ""
            if isinstance(quote_by_raw, dict):
                quote_by = str(quote_by_raw.get("name") or "").strip().lstrip("@")
            elif isinstance(quote_by_raw, str):
                quote_by = quote_by_raw.strip().lstrip("@")

            rec = tweets.get(tid)
            if rec is None:
                rec = {
                    "tweetId": tid,
                    "author": author,
                    "hasMedia": False,
                    "hasVideo": False,
                    "quoteId": quote_id,
                    "retweetId": retweet_id,
                    "quoteBy": quote_by,
                }
                tweets[tid] = rec
                order.append(tid)
            elif not rec["author"] and author:
                rec["author"] = author
            if quote_id and not rec["quoteId"]:
                rec["quoteId"] = quote_id
            if retweet_id and not rec["retweetId"]:
                rec["retweetId"] = retweet_id
            if quote_by and not rec["quoteBy"]:
                rec["quoteBy"] = quote_by
            if entry[0] == 3:
                rec["hasMedia"] = True
                if str(meta.get("type") or "").lower() in {"video", "animated_gif"}:
                    rec["hasVideo"] = True

        return {"error": probe_err, "tweets": tweets, "order": order}

    def _get_cached_shortcut_inspect(self, tweet_id: str) -> dict | None:
        now = time.monotonic()
        with self.lock:
            entry = self._shortcut_inspect_cache.get(f"tweet:{tweet_id}")
            if not entry:
                return None
            ts, data = entry
            if now - ts > 600:
                self._shortcut_inspect_cache.pop(f"tweet:{tweet_id}", None)
                return None
            return dict(data)

    def _store_cached_shortcut_inspect(self, tweet_id: str, data: dict) -> None:
        now = time.monotonic()
        with self.lock:
            stale = [k for k, (ts, _) in self._shortcut_inspect_cache.items() if now - ts > 600]
            for k in stale:
                self._shortcut_inspect_cache.pop(k, None)
            if len(self._shortcut_inspect_cache) >= 100:
                oldest_key = min(self._shortcut_inspect_cache, key=lambda k: self._shortcut_inspect_cache[k][0])
                self._shortcut_inspect_cache.pop(oldest_key, None)
            self._shortcut_inspect_cache[f"tweet:{tweet_id}"] = (now, dict(data))

    def inspect_shortcut_url(self, raw_url: str, *, use_cache: bool = True) -> dict:
        """Analyze a shared X link so iOS Shortcuts knows whether to prompt for scope."""
        with self.lock:
            settings = dict(self.settings)
        proxy, proxy_mode = _resolve_proxy_from_settings(settings)
        parsed = _parse_shortcut_input(raw_url, proxy=proxy, proxy_mode=proxy_mode)
        if parsed["kind"] == "account":
            target_user = parsed["targetUser"]
            return {
                "ok": True,
                "kind": "account",
                "url": parsed["url"],
                "targetUser": target_user,
                "tweetUser": None,
                "quotedUser": None,
                "tweetId": None,
                "quotedTweetId": None,
                "quotedUrl": None,
                "singleTargetUser": target_user,
                "singleTargetTweetId": None,
                "singleTargetUrl": parsed["url"],
                "singleIncludeQuoted": False,
                "needsScopeChoice": False,
                "isQuote": "no",
                "scopeOptions": [],
                "durationOptions": list(SHORTCUT_DURATION_OPTIONS),
                "defaultScope": "account",
                "summary": f"@{target_user} 主页（直接下载全部媒体）",
            }

        tweet_id = parsed["tweetId"]
        if use_cache:
            cached = self._get_cached_shortcut_inspect(tweet_id)
            if cached is not None:
                return cached

        probe = self._probe_tweet_with_gallery_dl(parsed["url"], settings)
        tweets = probe.get("tweets") or {}
        order = probe.get("order") or []
        outer = tweets.get(tweet_id)

        quoted_entry = None
        for tid in order:
            if tid != tweet_id:
                cand = tweets[tid]
                if not cand.get("retweetId"):
                    quoted_entry = cand
                    break

        is_quote = bool(
            quoted_entry
            or (outer and outer.get("quoteId"))
            or any(rec.get("quoteBy") for rec in tweets.values())
        )
        tweet_user = (outer and outer.get("author")) or parsed.get("urlUser") or ""
        quoted_user = (quoted_entry and quoted_entry.get("author")) or ""
        quoted_tid = (quoted_entry and quoted_entry.get("tweetId")) or (outer and outer.get("quoteId")) or None
        quoted_url = f"https://x.com/{quoted_user}/status/{quoted_tid}" if (quoted_user and quoted_tid) else None

        if is_quote and quoted_user and (
            (quoted_entry and quoted_entry.get("hasMedia"))
            or not (outer and outer.get("hasMedia"))
        ):
            preferred_creator = quoted_user
        else:
            preferred_creator = tweet_user or quoted_user

        if not preferred_creator:
            detail = probe.get("error") or "无法识别推文作者，请检查服务器 Cookie 与网络状态"
            raise ValueError(f"推文信息解析失败：{detail}")

        if is_quote:
            needs_scope = True
            is_quote_str = "yes"
            scope_options = [
                "只下载这个链接的",
                f"下载 @{preferred_creator} 全部媒体",
            ]
            if (
                quoted_entry
                and quoted_entry.get("hasMedia")
                and not (outer and outer.get("hasMedia"))
                and quoted_user
                and quoted_tid
            ):
                single_target_user = quoted_user
                single_target_tweet_id = quoted_tid
                single_target_url = f"https://x.com/{quoted_user}/status/{quoted_tid}"
                single_include_quoted = False
            else:
                single_target_user = tweet_user or preferred_creator
                single_target_tweet_id = tweet_id
                single_target_url = f"https://x.com/{single_target_user}/status/{tweet_id}"
                single_include_quoted = True
            summary = f"引用转发（原作者 @{preferred_creator}）"
        else:
            needs_scope = False
            is_quote_str = "no"
            scope_options = []
            single_target_user = preferred_creator
            single_target_tweet_id = tweet_id
            single_target_url = f"https://x.com/{preferred_creator}/status/{tweet_id}"
            single_include_quoted = False
            summary = f"普通推文 @{preferred_creator}（直接下载全部媒体）"

        result = {
            "ok": True,
            "kind": "tweet",
            "url": parsed["url"],
            "targetUser": preferred_creator,
            "tweetUser": tweet_user or None,
            "quotedUser": quoted_user or None,
            "tweetId": tweet_id,
            "quotedTweetId": quoted_tid,
            "quotedUrl": quoted_url,
            "singleTargetUser": single_target_user,
            "singleTargetTweetId": single_target_tweet_id,
            "singleTargetUrl": single_target_url,
            "singleIncludeQuoted": single_include_quoted,
            "needsScopeChoice": needs_scope,
            "isQuote": is_quote_str,
            "scopeOptions": scope_options,
            "durationOptions": list(SHORTCUT_DURATION_OPTIONS),
            "defaultScope": "account",
            "summary": summary,
        }
        if tweets:
            self._store_cached_shortcut_inspect(tweet_id, result)
        return result

    def submit_shortcut_task(self, payload: dict) -> dict:
        """Apply global duration filter, enqueue account or single tweet, and start async."""
        raw_url = str(payload.get("url") or "").strip()
        scope_choice = str(payload.get("scopeChoice") or "").strip()
        duration_choice = str(payload.get("durationChoice") or "").strip()
        custom_duration = str(payload.get("customDuration") or "").strip()

        with self.lock:
            base_settings = dict(self.settings)
        proxy, proxy_mode = _resolve_proxy_from_settings(base_settings)
        parsed = _parse_shortcut_input(raw_url, proxy=proxy, proxy_mode=proxy_mode)
        dur_enabled, max_dur = _parse_shortcut_duration(duration_choice, custom_duration)

        settings_patch: dict = {
            "mediaType": "all",
            "durationFilterEnabled": bool(dur_enabled),
        }
        if dur_enabled:
            settings_patch["maxVideoDuration"] = int(max_dur)
        filt_label = describe_filter({**base_settings, **settings_patch})

        want_single_tweet = parsed["kind"] == "tweet" and (
            scope_choice.startswith("只下载")
            or scope_choice.lower() in {"tweet", "single", "this", "only"}
        )

        inspected: dict | None = None
        if parsed["kind"] == "tweet":
            inspected = self._get_cached_shortcut_inspect(parsed["tweetId"])

        handle_match = re.search(r"@([A-Za-z0-9_]{1,15})", scope_choice)
        chosen_handle = handle_match.group(1) if handle_match else ""

        single_tweet_id = ""
        single_url = ""
        include_quoted = False

        if want_single_tweet:
            if inspected is None and not parsed.get("urlUser"):
                inspected = self.inspect_shortcut_url(raw_url)
            if inspected is not None:
                target_user = inspected.get("singleTargetUser") or inspected.get("targetUser")
                single_tweet_id = str(inspected.get("singleTargetTweetId") or parsed["tweetId"])
                single_url = inspected.get("singleTargetUrl") or parsed["url"]
                include_quoted = bool(inspected.get("singleIncludeQuoted"))
            else:
                target_user = parsed.get("urlUser") or chosen_handle
                single_tweet_id = str(parsed["tweetId"])
                single_url = f"https://x.com/{target_user}/status/{single_tweet_id}" if target_user else parsed["url"]
                include_quoted = True
            if not target_user or not USER_RE.fullmatch(str(target_user)):
                raise ValueError("无法识别推文所属博主用户名")
        else:
            if parsed["kind"] == "account":
                target_user = parsed["targetUser"]
            elif chosen_handle and USER_RE.fullmatch(chosen_handle):
                target_user = chosen_handle
            elif inspected and inspected.get("targetUser"):
                target_user = inspected["targetUser"]
            elif parsed.get("urlUser"):
                target_user = parsed["urlUser"]
            else:
                inspected = self.inspect_shortcut_url(raw_url)
                target_user = inspected.get("targetUser") or ""

            if not target_user or not USER_RE.fullmatch(str(target_user)):
                raise ValueError("无法识别博主用户名")

        # Update global settings only after link resolution & validation succeed
        self.update_settings(settings_patch)

        with self.lock:
            was_paused = bool(self.paused)

        if want_single_tweet:
            with self.lock:
                existing_item = next(
                    (
                        item for item in self.queue
                        if item.get("taskKind") == "tweet"
                        and str(item.get("tweetId") or "") == str(single_tweet_id)
                    ),
                    None,
                )
                if existing_item is not None:
                    existing_item["user"] = target_user
                    existing_item["targetUrl"] = single_url
                    existing_item["includeQuoted"] = include_quoted
                    existing_item["settingsOverride"] = dict(settings_patch)
                    existing_item["filterSummary"] = filt_label
                    existing_item["displayNote"] = "单条推文"
                    if existing_item.get("status") != "active":
                        existing_item["status"] = "waiting"
                        existing_item["progress"] = 0
                        existing_item["error"] = None
                        existing_item["newFiles"] = 0
                        existing_item["skippedFiles"] = 0
                else:
                    item = {
                        "id": self.next_id,
                        "taskKind": "tweet",
                        "tweetId": str(single_tweet_id),
                        "targetUrl": single_url,
                        "includeQuoted": include_quoted,
                        "settingsOverride": dict(settings_patch),
                        "displayNote": "单条推文",
                        "user": target_user,
                        "status": "waiting",
                        "progress": 0,
                        "files": 0,
                        "error": None,
                        "failedCount": 0,
                        "retried": False,
                        "retryOnly": False,
                        "newFiles": 0,
                        "skippedFiles": 0,
                        "downloadedTotal": 0,
                        "filterSummary": filt_label,
                    }
                    try:
                        known = xdl.media_count(target_user)
                        item["files"] = known
                        item["downloadedTotal"] = known
                        item["recentMedia"] = xdl.recent_media(target_user, 6)
                    except Exception:
                        item["recentMedia"] = []
                    self.queue.append(item)
                    self.next_id += 1
                self.add_log(
                    "info",
                    f"[iOS 快捷指令] 已加入单条推文 @{target_user} ({single_tweet_id})"
                    + (f"（{filt_label}）" if filt_label else ""),
                    user=target_user,
                )
                self.mark_dirty()
        else:
            self.add_users([target_user])
            with self.lock:
                for item in self.queue:
                    if item.get("taskKind", "account") != "tweet" and item["user"].lower() == target_user.lower():
                        item["settingsOverride"] = dict(settings_patch)
                        item["filterSummary"] = filt_label
                        if item.get("status") in {"done", "failed", "paused"}:
                            item["status"] = "waiting"
                            item["progress"] = 0
                            item["error"] = None
                            item["newFiles"] = 0
                            item["skippedFiles"] = 0
                self.add_log(
                    "info",
                    f"[iOS 快捷指令] 已加入博主 @{target_user}"
                    + (f"（{filt_label}）" if filt_label else ""),
                    user=target_user,
                )
                self.mark_dirty()

        if not was_paused:
            self.start(requeue_done=False, resume_paused=False)

        dur_text = f"视频 ≤ {xdl.format_duration(max_dur)}" if dur_enabled else "视频不限时长"
        mode_text = "单条推文" if want_single_tweet else "全部媒体"
        if was_paused:
            title = "XDL 已加入队列（暂停中）"
            message = f"@{target_user}（{mode_text} · 图片 + {dur_text}）已加入队列，调度暂停中"
        else:
            title = "XDL 已加入下载队列"
            message = f"@{target_user}（{mode_text} · 图片 + {dur_text}）已开始后台下载"
        return {
            "ok": True,
            "status": "success",
            "mode": "tweet" if want_single_tweet else "account",
            "user": target_user,
            "tweetId": str(single_tweet_id) if want_single_tweet else None,
            "paused": was_paused,
            "filterSummary": filt_label or "图片 + 视频（不限时长）",
            "title": title,
            "message": message,
            "state": self.snapshot(),
        }

    # ---------- failures / rounds ----------

    def _find_item(self, item_id: int) -> dict | None:
        for item in self.queue:
            if item["id"] == item_id:
                return item
        return None

    def _find_failure(self, failure_id: int) -> dict | None:
        for failure in self.failures:
            if failure["id"] == failure_id:
                return failure
        return None

    def _pending_for(self, owner_id: int) -> list[dict]:
        return [
            failure for failure in self.failures
            if failure.get("ownerId") == owner_id
            and failure.get("status") in {"failed", "queued"}
        ]

    def _add_failure_locked(self, item: dict, failure: dict) -> dict:
        for existing in self.failures:
            if (
                existing.get("ownerId") == item["id"]
                and existing.get("name") == failure["name"]
                and existing.get("status") != "done"
            ):
                existing["reason"] = failure["reason"]
                existing["note"] = failure["note"]
                existing["round"] = self.round
                existing["size"] = failure.get("size") or existing.get("size", "—")
                existing["status"] = "failed"
                return existing
        record = {
            "id": self.next_failure_id,
            "ownerId": item["id"],
            "user": item["user"],
            "name": failure["name"],
            "reason": failure["reason"],
            "note": failure["note"],
            "tries": 1,
            "status": "failed",
            "round": self.round,
            "size": failure.get("size") or "—",
            "tweetId": failure.get("tweetId"),
        }
        self.next_failure_id += 1
        self.failures.append(record)
        return record

    def retry_failures(self, ids: list[int]) -> dict:
        wanted = {int(value) for value in ids}
        queued = 0
        with self.lock:
            if not self.running:
                self.round += 1
                self.round_stats = {"ok": 0, "failed": 0, "files": 0, "skipped": 0}
                self.round_summary = None
            for failure in self.failures:
                if failure["id"] not in wanted:
                    continue
                if failure.get("status") not in {"failed", "queued"}:
                    continue
                if failure["reason"] == "notfound":
                    continue
                if failure.get("tries", 1) >= self.settings["maxRetries"]:
                    continue
                failure["status"] = "queued"
                failure["tries"] = int(failure.get("tries", 1)) + 1
                item = self._find_item(failure["ownerId"])
                if item is None:
                    continue
                if item.get("status") == "active":
                    item.setdefault("_deferredRetryIds", []).append(failure["id"])
                else:
                    item.setdefault("_retryFailureIds", []).append(failure["id"])
                    item["status"] = "waiting"
                item["retryOnly"] = True
                queued += 1
            if queued:
                self.running = True
                self.paused = False
                if self.started_at is None:
                    self.started_at = time.time()
            self.mark_dirty()
            return {"queued": queued}

    def retry_account(self, user: str, item_id: int | None = None) -> dict:
        with self.lock:
            item = self._find_item(int(item_id)) if item_id is not None else None
            if item is None:
                item = next(
                    (
                        entry for entry in self.queue
                        if entry.get("taskKind", "account") != "tweet"
                        and entry["user"].lower() == user.lower()
                    ),
                    None,
                )
            if item is None:
                item = next(
                    (entry for entry in self.queue if entry["user"].lower() == user.lower()),
                    None,
                )
            if item is None:
                return {"ok": False, "error": "账号不在队列中"}
            pending = self._pending_for(item["id"])
            if pending:
                result = self.retry_failures([failure["id"] for failure in pending])
                if not result.get("queued"):
                    return {"ok": False, "error": "失败项已达上限或不可重试"}
                return result
            if not self.running:
                self.round += 1
                self.round_stats = {"ok": 0, "failed": 0, "files": 0, "skipped": 0}
                self.round_summary = None
            item["status"] = "waiting"
            item["progress"] = 0
            item["error"] = None
            item["retried"] = True
            item["filterSummary"] = describe_filter(self._effective_item_settings_locked(item))
            self.running = True
            self.paused = False
            if self.started_at is None:
                self.started_at = time.time()
            self.mark_dirty()
            return {"ok": True}

    def discard_failures(self, ids: list[int]) -> None:
        wanted = {int(value) for value in ids}
        with self.lock:
            self.failures = [
                failure for failure in self.failures
                if failure["id"] not in wanted
            ]
            for item in self.queue:
                item["failedCount"] = len(self._pending_for(item["id"]))
            self.mark_dirty()

    def _finish_round(self) -> None:
        if self.paused:
            return
        if self.started_at:
            self.elapsed_base += int((time.time() - self.started_at) * 1000)
            self.started_at = None
        pending = [
            failure for failure in self.failures
            if failure.get("status") in {"failed", "queued"}
        ]
        retryable = [
            failure for failure in pending
            if failure.get("reason") != "notfound"
            and failure.get("tries", 1) < self.settings["maxRetries"]
        ]
        self.round_summary = {
            "round": self.round,
            "okFiles": self.round_stats["files"],
            "skippedFiles": self.round_stats["skipped"],
            "okAccounts": self.round_stats["ok"],
            "failedFiles": len(pending),
            "retryable": len(retryable),
        }
        self.running = False
        self.paused = False
        self.add_log(
            "ok" if not pending else "warn",
            f"第 {self.round} 轮结束：新增 {self.round_stats['files']} 个文件 · 跳过 {self.round_stats['skipped']} 个 · 失败 {len(pending)} 个",
        )
        self.mark_dirty()
        if self.settings.get("autoRetry") and retryable:
            self.retry_failures([failure["id"] for failure in retryable])

    # ---------- Google Drive cloud sync (rclone) ----------

    def test_cloud_sync(self) -> dict:
        rclone_bin = shutil.which("rclone")
        if not rclone_bin:
            return {"ok": False, "error": "服务器尚未安装 rclone"}
        rc = self.probe_rclone(force=True)
        target = sanitize_cloud_target(self.settings.get("cloudSyncTarget"))
        remote_name = target.split(":", 1)[0]
        if remote_name not in rc["remotes"]:
            return {
                "ok": False,
                "unconfigured": True,
                "remote": remote_name,
                "target": target,
                "error": f"尚未配置 Google Drive 远端 '{remote_name}'，请在服务器终端运行 bash /opt/X_Download/setup_gdrive.sh",
            }
        quota = None
        try:
            about = subprocess.run(
                [rclone_bin, "about", f"{remote_name}:", "--config", str(RCLONE_CONFIG_FILE), "--json"],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if about.returncode == 0 and about.stdout.strip():
                data = json.loads(about.stdout)
                quota = {
                    "total": data.get("total"),
                    "used": data.get("used"),
                    "free": data.get("free"),
                }
            else:
                lsd = subprocess.run(
                    [rclone_bin, "lsd", f"{remote_name}:", "--config", str(RCLONE_CONFIG_FILE), "--max-depth", "1"],
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                if lsd.returncode != 0:
                    err = clean_line(lsd.stderr or lsd.stdout or "连接测试失败")
                    return {"ok": False, "remote": remote_name, "target": target, "error": err}
        except subprocess.TimeoutExpired:
            return {"ok": False, "remote": remote_name, "target": target, "error": "连接 Google Drive 超时"}
        except Exception as exc:
            return {"ok": False, "remote": remote_name, "target": target, "error": str(exc)}

        folder_url = ""
        try:
            folder_url = self._resolve_root_folder_url(rclone_bin, target, force=False)
        except Exception:
            folder_url = self.cloud_folder_url
        try:
            verified_count = int(xdl.cloud_media_summary().get("files") or 0)
        except Exception:
            verified_count = 0

        self.add_log("ok", f"[cloud-sync] Google Drive ({remote_name}:) 连通性测试通过")
        self.mark_dirty()
        return {
            "ok": True,
            "remote": remote_name,
            "target": target,
            "quota": quota,
            "folderUrl": folder_url,
            "verifiedCount": verified_count,
            "message": f"Google Drive ({remote_name}:) 连接正常",
        }

    def _resolve_root_folder_url(self, rclone_bin: str, target: str, force: bool = False) -> str:
        with self.lock:
            if not force and self.cloud_folder_url and self.cloud_folder_target == target:
                return self.cloud_folder_url

        remote_name, path_part = target.split(":", 1)
        clean_path = path_part.strip("/")
        folder_url = ""
        if not clean_path:
            folder_url = "https://drive.google.com/drive/my-drive"
        else:
            parent_path, leaf = (
                clean_path.rsplit("/", 1) if "/" in clean_path else ("", clean_path)
            )
            parent_target = f"{remote_name}:{parent_path}"
            ls_parent = subprocess.run(
                [
                    rclone_bin, "lsjson",
                    "--config", str(RCLONE_CONFIG_FILE),
                    "--dirs-only", "--no-modtime", "--no-mimetype",
                    parent_target,
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=90,
            )
            if ls_parent.returncode == 0 and ls_parent.stdout.strip():
                for d in json.loads(ls_parent.stdout):
                    if isinstance(d, dict) and d.get("Name") == leaf and d.get("ID"):
                        folder_url = f"https://drive.google.com/drive/folders/{d['ID']}"
                        break

        with self.lock:
            if folder_url:
                self.cloud_folder_url = folder_url
                self.cloud_folder_target = target
            return self.cloud_folder_url

    @staticmethod
    def _parse_rclone_modtime(raw: str | None) -> str:
        text = str(raw or "").strip()
        if not text:
            return ""
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if dt.tzinfo is not None:
                dt = dt.astimezone()
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return ""

    def _reconcile_cloud_index(
        self,
        rclone_bin: str,
        target: str,
        output_root: Path,
        user: str | None = None,
        remote_dest: str | None = None,
    ) -> tuple[list[dict], int]:
        scan_target = remote_dest or target
        entries: list[dict] = []
        found_user_folders: dict[str, str] = {}
        lsjson = subprocess.run(
            [
                rclone_bin, "lsjson",
                "--config", str(RCLONE_CONFIG_FILE),
                "-R", "--fast-list", "--no-mimetype",
                scan_target,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
        )
        if lsjson.returncode == 0 and lsjson.stdout.strip():
            items = json.loads(lsjson.stdout)
            if isinstance(items, list):
                for entry in items:
                    if not isinstance(entry, dict):
                        continue
                    name = str(entry.get("Name") or "").strip()
                    rel_path = str(entry.get("Path") or name).strip()
                    file_id = str(entry.get("ID") or "").strip()
                    if entry.get("IsDir"):
                        if not user and "/" not in rel_path and name and file_id and USER_RE.fullmatch(name):
                            found_user_folders[name.lower()] = (
                                f"https://drive.google.com/drive/folders/{file_id}"
                            )
                        continue
                    if not name:
                        continue
                    cloud_url = f"https://drive.google.com/file/d/{file_id}/view" if file_id else ""
                    item_user = user or ""
                    if not item_user and "/" in rel_path:
                        candidate = rel_path.split("/", 1)[0]
                        if USER_RE.fullmatch(candidate):
                            item_user = candidate
                    if not item_user:
                        parsed_name = xdl.parse_media_name(name)
                        if parsed_name:
                            item_user = parsed_name["user"]
                    local_path = (
                        str(output_root / item_user / name)
                        if item_user
                        else str(output_root / name)
                    )
                    local_exists = (
                        (bool(item_user) and (output_root / item_user / name).is_file())
                        or (output_root / name).is_file()
                    )
                    entries.append({
                        "username": item_user,
                        "filename": name,
                        "cloudFileId": file_id,
                        "cloudUrl": cloud_url,
                        "cloudRemote": f"{scan_target.rstrip('/')}/{rel_path}",
                        "localDeleted": not local_exists,
                        "size": int(entry.get("Size") or 0),
                        "downloadedAt": self._parse_rclone_modtime(entry.get("ModTime")),
                        "localPath": local_path,
                    })
        if found_user_folders:
            with self.lock:
                self.cloud_user_folders.update(found_user_folders)
        updated = xdl.mark_media_cloud_synced(entries) if entries else 0
        return entries, updated

    def sync_to_cloud(self, user: str | None = None, trigger: str = "auto") -> dict:
        if trigger == "manual":
            if not self.cloud_sync_lock.acquire(blocking=False):
                return {"ok": False, "busy": True, "error": "云盘同步正在进行中，请稍候"}
        else:
            self.cloud_sync_lock.acquire()

        self.cloud_syncing = True
        self.mark_dirty()
        try:
            rclone_bin = shutil.which("rclone")
            if not rclone_bin:
                err = "服务器未安装 rclone，无法执行云盘同步"
                self.cloud_last_sync = {
                    "at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "status": "error",
                    "summary": err,
                    "files": 0,
                    "error": err,
                }
                self.add_log("error", f"[cloud-sync] {err}", user=user)
                return {"ok": False, "error": err}

            rc = self.probe_rclone(force=True)
            with self.lock:
                target = sanitize_cloud_target(self.settings.get("cloudSyncTarget"))
                delete_local = bool(self.settings.get("cloudSyncDeleteLocal", True))
                flat = self.settings.get("layout") == "flat"
                output_root = Path(self.settings["output"]).resolve()

            remote_name = target.split(":", 1)[0]
            if remote_name not in rc["remotes"]:
                err = f"Google Drive 远端 '{remote_name}' 尚未授权，请先运行 bash /opt/X_Download/setup_gdrive.sh"
                self.cloud_last_sync = {
                    "at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "status": "unconfigured",
                    "summary": err,
                    "files": 0,
                    "error": err,
                }
                self.add_log("warn", f"[cloud-sync] {err}", user=user)
                return {"ok": False, "unconfigured": True, "error": err}

            if user and not flat:
                local_src = output_root / user
                remote_dest = f"{target.rstrip('/')}/{user}"
                user_filter = None
            else:
                local_src = output_root
                remote_dest = target.rstrip("/")
                user_filter = f"{user}_*" if (user and flat) else None

            pending_files: list[Path] = []
            if local_src.is_dir():
                if user_filter:
                    pending_files = [
                        p for p in local_src.iterdir()
                        if is_user_media_file(p, user, flat=True)
                    ]
                else:
                    pending_files = [
                        p for p in local_src.rglob("*")
                        if p.is_file() and p.suffix.lower() not in {".part", ".tmp"}
                    ]

            if not pending_files:
                if trigger != "manual":
                    return {"ok": True, "synced": 0, "message": "本地暂无待同步文件"}
                folder_url = ""
                try:
                    folder_url = self._resolve_root_folder_url(rclone_bin, target, force=False)
                except Exception as exc:
                    self.add_log("warn", f"[cloud-sync] 解析云端目录 ID 失败: {exc}", user=user)
                entries: list[dict] = []
                updated = 0
                try:
                    entries, updated = self._reconcile_cloud_index(
                        rclone_bin, target, output_root, user=user, remote_dest=remote_dest
                    )
                except Exception as exc:
                    self.add_log("warn", f"[cloud-sync] 核对云端文件失败: {exc}", user=user)
                summary = xdl.media_summary()
                with self.lock:
                    self.store_summary.update({
                        "media": summary["media"],
                        "tweets": summary["tweets"],
                        "bytes": summary["bytes"],
                    })
                    self._refresh_item_counts_locked()
                    self.cloud_last_sync = {
                        "at": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "status": "ok",
                        "summary": f"本地无待传文件 · 云端已核对 {len(entries)} 个文件（更新 {updated} 条档案）",
                        "files": len(entries),
                        "updated": updated,
                        "error": None,
                    }
                    self.add_log(
                        "info",
                        f"[cloud-sync] {self.cloud_last_sync['summary']}",
                        user=user,
                    )
                    self.mark_dirty()
                return {
                    "ok": True,
                    "synced": len(entries),
                    "updated": updated,
                    "folderUrl": folder_url or self.cloud_folder_url,
                    "target": remote_dest,
                }

            try:
                if user and not flat:
                    xdl.backfill_directory(local_src, user)
                else:
                    xdl.backfill_output(output_root)
            except Exception:
                pass

            try:
                thumb_ready = xdl.ensure_thumbs_for_paths(pending_files, username=user)
                if thumb_ready:
                    self.add_log("info", f"[thumbs] 已为 {thumb_ready} 个待同步媒体生成本地微缩略图", user=user)
            except Exception as exc:
                self.add_log("warn", f"[thumbs] 同步前生成微缩略图异常: {exc}", user=user)

            action = "move" if delete_local else "copy"
            self.add_log(
                "info",
                f"[cloud-sync] 开始将 {len(pending_files)} 个文件上传至 {remote_dest}（{'上传后释放本地' if delete_local else '保留本地副本'}）",
                user=user,
            )
            cmd = [
                rclone_bin,
                action,
                str(local_src),
                remote_dest,
                "--config", str(RCLONE_CONFIG_FILE),
            ]
            if user_filter:
                cmd.extend([
                    "--filter", "- *.part",
                    "--filter", "- *.tmp",
                    "--filter", f"+ {user_filter}",
                    "--filter", "- *",
                ])
            else:
                cmd.extend(["--exclude", "*.part", "--exclude", "*.tmp"])
            if delete_local and not user_filter:
                cmd.append("--delete-empty-src-dirs")
            cmd.extend(["--checksum", "--stats=5s", "-v"])

            lines: list[str] = []
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
            if proc.stdout:
                for line in proc.stdout:
                    cleaned = clean_line(line)
                    if cleaned:
                        lines.append(cleaned)
                        if len(lines) > 200:
                            lines = lines[-120:]
                        lvl = "error" if ("error" in cleaned.lower() or "failed" in cleaned.lower()) else "info"
                        self.add_log(lvl, f"[cloud-sync] {cleaned}", user=user)
            rc_code = proc.wait()
            if rc_code != 0:
                err = clean_line(lines[-1]) if lines else f"rclone 退出码 {rc_code}"
                self.cloud_last_sync = {
                    "at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "status": "error",
                    "summary": f"同步失败：{err}",
                    "files": 0,
                    "error": err,
                }
                self.add_log("error", f"[cloud-sync] 同步失败：{err}", user=user)
                self.mark_dirty()
                return {"ok": False, "error": err}

            if delete_local and user and not flat and local_src.is_dir():
                try:
                    if not any(local_src.iterdir()):
                        local_src.rmdir()
                except OSError:
                    pass

            folder_url = ""
            try:
                folder_url = self._resolve_root_folder_url(rclone_bin, target, force=False)
            except Exception as exc:
                self.add_log("warn", f"[cloud-sync] 读取云端目录链接失败: {exc}", user=user)

            entries = []
            updated = 0
            try:
                entries, updated = self._reconcile_cloud_index(
                    rclone_bin, target, output_root, user=user, remote_dest=remote_dest
                )
            except Exception as exc:
                self.add_log("warn", f"[cloud-sync] 读取云端文件 ID 失败: {exc}", user=user)

            summary = xdl.media_summary()
            with self.lock:
                self.store_summary.update({
                    "media": summary["media"],
                    "tweets": summary["tweets"],
                    "bytes": summary["bytes"],
                })
                self._refresh_item_counts_locked()
                self.cloud_last_sync = {
                    "at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "status": "ok",
                    "summary": f"已上传 {len(pending_files)} 个文件，云端已核对 {len(entries)} 个文件" + ("（本地已释放）" if delete_local else "（保留本地）"),
                    "files": len(entries),
                    "updated": updated,
                    "error": None,
                }
                self.add_log(
                    "ok",
                    f"[cloud-sync] 同步完成：本次上传 {len(pending_files)} 个，云端已核对 {len(entries)} 个（更新 {updated} 条链接）" + ("，本地空间已回收" if delete_local else ""),
                    user=user,
                )
                self.mark_dirty()
            return {
                "ok": True,
                "synced": len(entries),
                "uploaded": len(pending_files),
                "updated": updated,
                "folderUrl": folder_url or self.cloud_folder_url,
                "target": remote_dest,
                "deleteLocal": delete_local,
            }
        finally:
            self.cloud_syncing = False
            self.mark_dirty()
            self.cloud_sync_lock.release()

    def _read_gdrive_access_token(
        self,
        remote_name: str,
        rclone_bin: str,
        force_refresh: bool = False,
    ) -> str | None:
        def _parse_token() -> tuple[str | None, bool]:
            if not RCLONE_CONFIG_FILE.is_file():
                return None, False
            cfg = configparser.ConfigParser(interpolation=None)
            try:
                cfg.read(str(RCLONE_CONFIG_FILE), encoding="utf-8")
            except Exception:
                return None, False
            if not cfg.has_section(remote_name):
                return None, False
            if cfg.get(remote_name, "type", fallback="").strip().lower() != "drive":
                return None, False
            raw_tok = cfg.get(remote_name, "token", fallback="").strip()
            if not raw_tok:
                return None, False
            try:
                tok = json.loads(raw_tok)
            except Exception:
                return None, False
            access = str(tok.get("access_token") or "").strip() or None
            expired = False
            exp_raw = str(tok.get("expiry") or "").strip()
            if exp_raw:
                try:
                    exp_dt = datetime.fromisoformat(exp_raw.replace("Z", "+00:00"))
                    if exp_dt.timestamp() <= time.time() + 60:
                        expired = True
                except Exception:
                    pass
            return access, expired

        access_token, is_expired = _parse_token()
        if force_refresh or not access_token or is_expired:
            if rclone_bin and RCLONE_CONFIG_FILE.is_file():
                try:
                    subprocess.run(
                        [
                            rclone_bin, "lsd", f"{remote_name}:",
                            "--config", str(RCLONE_CONFIG_FILE),
                            "--max-depth", "1",
                        ],
                        capture_output=True,
                        text=True,
                        timeout=60,
                    )
                    access_token, _ = _parse_token()
                except Exception:
                    pass
        return access_token

    def _fetch_drive_thumbnail_links(
        self,
        remote_name: str,
        rclone_bin: str,
        rows: list[dict],
    ) -> tuple[dict[str, str], str | None]:
        wanted_ids = {str(r.get("cloudFileId") or "").strip() for r in rows if r.get("cloudFileId")}
        if not wanted_ids:
            return {}, None

        token = self._read_gdrive_access_token(remote_name, rclone_bin, force_refresh=False)
        if not token:
            return {}, None

        def _drive_get_json(url: str) -> dict | None:
            nonlocal token
            for attempt in range(2):
                req = urllib.request.Request(
                    url,
                    headers={"Authorization": f"Bearer {token}"},
                )
                try:
                    with urllib.request.urlopen(req, timeout=20) as resp:
                        return json.loads(resp.read().decode("utf-8"))
                except urllib.error.HTTPError as exc:
                    if exc.code == 401 and attempt == 0:
                        refreshed = self._read_gdrive_access_token(
                            remote_name, rclone_bin, force_refresh=True
                        )
                        if refreshed:
                            token = refreshed
                            continue
                    return None
                except Exception:
                    return None
            return None

        folder_ids: list[str] = []
        seen_folders: set[str] = set()
        with self.lock:
            urls = list(self.cloud_user_folders.values())
            if self.cloud_folder_url:
                urls.append(self.cloud_folder_url)
        for url in urls:
            if "/folders/" not in str(url):
                continue
            fid = str(url).split("/folders/", 1)[1].split("?", 1)[0].strip("/")
            if fid and fid not in seen_folders and re.fullmatch(r"[A-Za-z0-9_-]{8,128}", fid):
                seen_folders.add(fid)
                folder_ids.append(fid)

        links: dict[str, str] = {}
        for fid in folder_ids:
            page_token = ""
            while True:
                params = {
                    "q": f"'{fid}' in parents and trashed = false",
                    "pageSize": "1000",
                    "fields": "nextPageToken,files(id,thumbnailLink)",
                }
                if page_token:
                    params["pageToken"] = page_token
                url = "https://www.googleapis.com/drive/v3/files?" + urlencode(params)
                data = _drive_get_json(url)
                if not isinstance(data, dict):
                    break
                for item in data.get("files") or []:
                    if not isinstance(item, dict):
                        continue
                    item_id = str(item.get("id") or "").strip()
                    tlink = str(item.get("thumbnailLink") or "").strip()
                    if item_id in wanted_ids and tlink:
                        links[item_id] = tlink
                page_token = str(data.get("nextPageToken") or "").strip()
                if not page_token or len(links) >= len(wanted_ids):
                    break
            if len(links) >= len(wanted_ids):
                break

        missing_ids = [fid for fid in wanted_ids if fid not in links]
        for fid in missing_ids:
            if not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", fid):
                continue
            url = f"https://www.googleapis.com/drive/v3/files/{quote(fid, safe='')}?fields=id,thumbnailLink"
            data = _drive_get_json(url)
            if isinstance(data, dict):
                tlink = str(data.get("thumbnailLink") or "").strip()
                if tlink:
                    links[fid] = tlink

        return links, token

    def backfill_cloud_thumbs(self, user: str | None = None) -> dict:
        if not self.thumb_backfill_lock.acquire(blocking=False):
            return {"ok": False, "busy": True, "error": "缩略图补全正在进行中，请稍候"}

        self.thumb_backfilling = True
        self.mark_dirty()
        generated = 0
        try:
            if not shutil.which("ffmpeg"):
                err = "服务器未安装 ffmpeg，无法生成微缩略图"
                self.add_log("warn", f"[thumbs] {err}", user=user)
                return {"ok": False, "error": err, "thumbs": xdl.thumbs_summary()}

            missing = xdl.list_media_missing_thumbs(username=user)
            if not missing:
                summary = xdl.thumbs_summary()
                return {"ok": True, "generated": 0, "remaining": 0, "thumbs": summary}

            with self.lock:
                target = sanitize_cloud_target(self.settings.get("cloudSyncTarget"))
                flat = self.settings.get("layout") == "flat"
                output_root = Path(self.settings["output"]).resolve()

            cloud_pending: list[dict] = []
            for row in missing:
                uname = row.get("username") or ""
                fname = row.get("filename") or ""
                candidates: list[Path] = []
                if row.get("path"):
                    candidates.append(Path(row["path"]))
                if uname and fname:
                    candidates.append(output_root / uname / fname)
                if fname:
                    candidates.append(output_root / fname)
                local_src = None
                for cand in candidates:
                    try:
                        if cand.is_file() and cand.stat().st_size > 0:
                            local_src = cand
                            break
                    except OSError:
                        continue
                if local_src is not None:
                    if xdl.generate_thumbnail(local_src, row["key"]) is not None:
                        generated += 1
                    continue
                if row.get("cloudSynced"):
                    cloud_pending.append(row)

            if cloud_pending:
                rclone_bin = shutil.which("rclone")
                rc = self.probe_rclone(force=True)
                remote_name = target.split(":", 1)[0]
                if rclone_bin and remote_name in rc.get("remotes", []):
                    # Phase 2A: Fast Drive API thumbnailLink batch fetch -> local WebP cache
                    thumb_links, access_token = self._fetch_drive_thumbnail_links(
                        remote_name, rclone_bin, cloud_pending
                    )
                    fast_rows = [
                        r for r in cloud_pending
                        if r.get("cloudFileId") and r["cloudFileId"] in thumb_links
                    ]
                    succeeded_keys: set[str] = set()
                    if fast_rows:
                        self.add_log(
                            "info",
                            f"[thumbs] 正在通过 Drive 缩略图接口快速转换 {len(fast_rows)} 个云端封面…",
                            user=user,
                        )
                        with tempfile.TemporaryDirectory(prefix="xdl_thumb_dl_") as dl_td:
                            dl_root = Path(dl_td)

                            def _fetch_fast_one(row: dict) -> str | None:
                                key = row["key"]
                                fid = row["cloudFileId"]
                                link = re.sub(r"=s\d+$", "=s480", thumb_links[fid])
                                img_bytes = None
                                hdr_sets = (
                                    [{"Authorization": f"Bearer {access_token}"}, {}]
                                    if access_token
                                    else [{}]
                                )
                                for hdrs in hdr_sets:
                                    req = urllib.request.Request(link, headers=hdrs)
                                    try:
                                        with urllib.request.urlopen(req, timeout=20) as resp:
                                            data = resp.read()
                                            if len(data) >= 64:
                                                img_bytes = data
                                                break
                                    except Exception:
                                        continue
                                if not img_bytes:
                                    return None
                                tmp_src = dl_root / f"{key}.jpg"
                                try:
                                    tmp_src.write_bytes(img_bytes)
                                    if xdl.generate_thumbnail(tmp_src, key) is not None:
                                        return key
                                finally:
                                    try:
                                        tmp_src.unlink(missing_ok=True)
                                    except OSError:
                                        pass
                                return None

                            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                                futs = [pool.submit(_fetch_fast_one, r) for r in fast_rows]
                                for fut in concurrent.futures.as_completed(futs):
                                    try:
                                        res_key = fut.result()
                                        if res_key:
                                            succeeded_keys.add(res_key)
                                            generated += 1
                                    except Exception:
                                        pass

                    cloud_pending = [
                        r for r in cloud_pending if r["key"] not in succeeded_keys
                    ]

                # Phase 2B: Batched rclone copy fallback for any remaining cloud files
                if cloud_pending and rclone_bin and remote_name in rc.get("remotes", []):
                    target_prefix = target.rstrip("/") + "/"
                    rel_map: dict[str, dict] = {}
                    name_map: dict[str, dict] = {}
                    unique_rels: list[str] = []
                    seen_rels: set[str] = set()
                    for row in cloud_pending:
                        remote_val = str(row.get("cloudRemote") or "").strip()
                        if remote_val.startswith(target_prefix):
                            rel = remote_val[len(target_prefix):].lstrip("/")
                        elif row.get("username") and not flat:
                            rel = f"{row['username']}/{row['filename']}"
                        else:
                            rel = row["filename"]
                        if not rel:
                            continue
                        rel_map[rel] = row
                        if row.get("filename"):
                            name_map[row["filename"]] = row
                        if rel not in seen_rels:
                            seen_rels.add(rel)
                            unique_rels.append(rel)

                    if unique_rels:
                        batch_size = 15
                        self.add_log(
                            "info",
                            f"[thumbs] 回退拉取剩余 {len(unique_rels)} 个云端文件生成微缩略图…",
                            user=user,
                        )
                        for idx in range(0, len(unique_rels), batch_size):
                            batch = unique_rels[idx:idx + batch_size]
                            with tempfile.TemporaryDirectory(prefix="xdl_thumb_bf_") as td:
                                td_path = Path(td)
                                tmp_media_dir = td_path / "media"
                                tmp_media_dir.mkdir(parents=True, exist_ok=True)
                                files_from = td_path / "files.txt"
                                files_from.write_text("\n".join(batch) + "\n", encoding="utf-8")

                                try:
                                    cp_res = subprocess.run(
                                        [
                                            rclone_bin, "copy",
                                            target, str(tmp_media_dir),
                                            "--config", str(RCLONE_CONFIG_FILE),
                                            "--files-from", str(files_from),
                                            "--fast-list",
                                            "--transfers", "2",
                                        ],
                                        capture_output=True,
                                        text=True,
                                        encoding="utf-8",
                                        errors="replace",
                                        timeout=300,
                                    )
                                    if cp_res.returncode != 0:
                                        err_msg = clean_line(cp_res.stderr or cp_res.stdout or f"rclone exit {cp_res.returncode}")
                                        self.add_log("warn", f"[thumbs] 批次拉取部分文件异常: {err_msg}", user=user)
                                except Exception as exc:
                                    self.add_log("warn", f"[thumbs] 批次拉取超时或失败: {exc}", user=user)

                                work_items: list[tuple[Path, str]] = []
                                for fpath in tmp_media_dir.rglob("*"):
                                    if not fpath.is_file():
                                        continue
                                    rel_found = fpath.relative_to(tmp_media_dir).as_posix()
                                    matched = rel_map.get(rel_found) or name_map.get(fpath.name)
                                    if matched and matched.get("key"):
                                        work_items.append((fpath, matched["key"]))

                                def _build_one(item: tuple[Path, str]) -> bool:
                                    fpath, key = item
                                    try:
                                        return xdl.generate_thumbnail(fpath, key) is not None
                                    finally:
                                        try:
                                            fpath.unlink(missing_ok=True)
                                        except OSError:
                                            pass

                                if work_items:
                                    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                                        futs = [pool.submit(_build_one, it) for it in work_items]
                                        for fut in concurrent.futures.as_completed(futs):
                                            try:
                                                if fut.result():
                                                    generated += 1
                                            except Exception:
                                                pass

            remaining = len(xdl.list_media_missing_thumbs(username=user))
            summary = xdl.thumbs_summary()
            with self.lock:
                self._refresh_item_counts_locked()
            self.add_log(
                "ok" if remaining == 0 else "info",
                f"[thumbs] 已补全 {generated} 张本地微缩略图（现有 {summary['count']} 张 / {human_size(summary['bytes'])}，待补 {remaining} 个）",
                user=user,
            )
            self.mark_dirty()
            return {
                "ok": True,
                "generated": generated,
                "remaining": remaining,
                "thumbs": summary,
            }
        finally:
            self.thumb_backfilling = False
            self.mark_dirty()
            self.thumb_backfill_lock.release()


def count_user_files(output_dir: Path, user: str, flat: bool) -> int:
    if flat:
        return count_files(output_dir, user=user, flat=True)
    return count_files(output_dir / user)


class Bridge:
    def pick_folder(self):
        import webview
        window = webview.windows[0] if webview.windows else None
        if window is None:
            return None
        result = window.create_file_dialog(webview.FileDialog.FOLDER)
        return str(result[0]) if result else None

    def pick_file(self):
        import webview
        window = webview.windows[0] if webview.windows else None
        if window is None:
            return None
        result = window.create_file_dialog(
            webview.FileDialog.OPEN,
            file_types=("Cookie files (*.txt)", "All files (*.*)"),
        )
        return str(result[0]) if result else None


def _synthesize_netscape_cookies(auth_token: str, ct0: str) -> str:
    token = str(auth_token or "").strip()
    csrf = str(ct0 or "").strip()
    if not token or not csrf:
        raise ValueError("auth_token 和 ct0 均不能为空")
    for label, val in (("auth_token", token), ("ct0", csrf)):
        if len(val) > 512 or any(ch in val for ch in ("\r", "\n", "\t", " ")):
            raise ValueError(f"{label} 格式无效，请仅填入 Cookie 值本身")
        if not val.isascii() or not val.isprintable():
            raise ValueError(f"{label} 包含非法字符")
    lines = [
        "# Netscape HTTP Cookie File",
        "# Generated by XDL Web",
    ]
    for domain in (".x.com", ".twitter.com"):
        lines.append(f"{domain}\tTRUE\t/\tTRUE\t2147483647\tauth_token\t{token}")
        lines.append(f"{domain}\tTRUE\t/\tTRUE\t2147483647\tct0\t{csrf}")
    return "\n".join(lines) + "\n"


class Handler(SimpleHTTPRequestHandler):
    app: AppState

    def log_message(self, fmt, *args):
        return

    def end_headers(self):
        if not self.path.startswith("/api/"):
            self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def translate_path(self, path):
        parsed = urlparse(path)
        clean = parsed.path.lstrip("/") or "index.html"
        target = (APP_DIR / clean).resolve()
        try:
            target.relative_to(APP_DIR.resolve())
        except ValueError:
            return str(APP_DIR / "index.html")
        return str(target)

    def _client_ip(self) -> str:
        peer = self.client_address[0] if self.client_address else ""
        return xdl_auth.extract_client_ip(peer, self.headers)

    def _is_https(self) -> bool:
        return xdl_auth.is_https_request(self.headers)

    def _session_token(self) -> str | None:
        cookies = xdl_auth.parse_cookies(self.headers.get("Cookie"))
        return cookies.get(xdl_auth.AUTH_COOKIE_NAME)

    def _current_session(self) -> dict | None:
        if not self.app.auth_required:
            return {"username": "local", "remember": False, "refreshed": False}
        sess = self.app.auth.validate_session(self._session_token())
        if sess and sess.get("refreshed"):
            self._pending_set_cookie = xdl_auth.build_session_cookie(
                self._session_token() or "",
                sess["ttl"],
                self._is_https(),
            )
        return sess

    def _require_auth_json(self) -> dict | None:
        sess = self._current_session()
        if sess is None:
            self._json({"ok": False, "authRequired": True, "error": "请先登录"}, 401)
            return None
        return sess

    def _shortcut_token(self, payload: dict | None = None) -> str | None:
        direct = str(self.headers.get("X-XDL-Token") or "").strip()
        if direct:
            return direct
        auth_hdr = str(self.headers.get("Authorization") or "").strip()
        if auth_hdr.lower().startswith("bearer "):
            bearer = auth_hdr[7:].strip()
            if bearer:
                return bearer
        if isinstance(payload, dict) and isinstance(payload.get("token"), str):
            tok = payload["token"].strip()
            if tok:
                return tok
        return None

    def _require_shortcut_auth(self, payload: dict | None = None) -> dict | None:
        if not self.app.auth_required:
            return {"username": "local", "via": "local"}
        sess = self._current_session()
        if sess is not None:
            return sess
        tok_user = self.app.auth.validate_shortcut_token(self._shortcut_token(payload))
        if tok_user is not None:
            return tok_user
        self._json(
            {
                "ok": False,
                "authRequired": True,
                "status": "error",
                "title": "XDL 认证失败",
                "message": "Shortcut Token 无效或已重置，请前往 XDL 网页端复制最新 Token",
                "error": "Shortcut Token 无效或未提供",
            },
            401,
        )
        return None

    def _redirect(self, location: str, status: int = 302) -> None:
        self.send_response(status)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def _json(self, payload, status=200, extra_headers=None):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        pending_cookie = getattr(self, "_pending_set_cookie", None)
        if pending_cookie and not (extra_headers and any(k.lower() == "set-cookie" for k, _ in extra_headers)):
            self.send_header("Set-Cookie", pending_cookie)
        if extra_headers:
            for key, val in extra_headers:
                self.send_header(key, val)
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return {}
        if length <= 0 or length > 4 * 1024 * 1024:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return {}

    def _serve_media_file(self, query: dict) -> None:
        user = unquote((query.get("user") or [""])[0]).lstrip("@")
        filename = unquote((query.get("filename") or [""])[0])
        download = (query.get("download") or ["0"])[0] == "1"

        if not user or not USER_RE.fullmatch(user):
            self._json({"ok": False, "error": "无效的用户名"}, 400)
            return
        if not filename or filename != Path(filename).name or ".." in filename:
            self._json({"ok": False, "error": "无效的文件名"}, 400)
            return

        output_root = Path(self.app.settings["output"]).resolve()
        candidates = [
            output_root / user / filename,
            output_root / filename,
        ]
        target = None
        for candidate in candidates:
            try:
                resolved = candidate.resolve()
                if resolved.is_relative_to(output_root) and resolved.is_file():
                    target = resolved
                    break
            except OSError:
                continue

        if target is None:
            self._json({"ok": False, "error": "文件不存在或已移动"}, 404)
            return

        ctype, _ = mimetypes.guess_type(str(target))
        ctype = ctype or "application/octet-stream"
        size = target.stat().st_size
        range_header = self.headers.get("Range")
        start = 0
        end = size - 1
        status = 200

        if range_header and size > 0:
            match = re.match(r"bytes=(\d*)-(\d*)", range_header.strip())
            if match:
                raw_start, raw_end = match.group(1), match.group(2)
                if raw_start == "" and raw_end:
                    suffix_len = int(raw_end)
                    start = max(0, size - suffix_len)
                else:
                    start = int(raw_start) if raw_start else 0
                    if raw_end:
                        end = min(size - 1, int(raw_end))
                if start > end or start >= size:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.end_headers()
                    return
                status = 206

        length = max(0, end - start + 1)
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        if download:
            self.send_header(
                "Content-Disposition",
                f"attachment; filename*=UTF-8''{quote(target.name)}",
            )
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

        if length <= 0:
            return
        try:
            with target.open("rb") as fh:
                fh.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = fh.read(min(65536, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            return

    def _serve_media_thumb(self, query: dict) -> None:
        key = unquote((query.get("key") or [""])[0]).strip()
        if not key or not xdl.THUMB_KEY_RE.fullmatch(key):
            self._json({"ok": False, "error": "无效的缩略图索引"}, 400)
            return

        target = xdl.thumb_path_for_key(key)
        if target is None:
            self._json({"ok": False, "error": "缩略图不存在"}, 404)
            return

        thumbs_root = xdl.get_thumbs_dir().resolve()
        try:
            resolved = target.resolve()
            if not resolved.is_relative_to(thumbs_root) or not resolved.is_file():
                self._json({"ok": False, "error": "缩略图不存在"}, 404)
                return
            data = resolved.read_bytes()
        except OSError:
            self._json({"ok": False, "error": "缩略图读取失败"}, 404)
            return

        ctype = "image/webp" if resolved.suffix.lower() == ".webp" else "image/jpeg"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "private, max-age=2592000, immutable")
        pending_cookie = getattr(self, "_pending_set_cookie", None)
        if pending_cookie:
            self.send_header("Set-Cookie", pending_cookie)
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            return

    def do_HEAD(self):
        parsed = urlparse(self.path)
        route = parsed.path
        if self.app.auth_required and route not in {"/login", "/login.html", "/styles.css", "/login.js", "/api/health"}:
            if self._current_session() is None:
                if route in {"/", "/index.html"}:
                    self._redirect("/login")
                    return
                self.send_response(401)
                self.send_header("Content-Length", "0")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return
        if route in {"/login", "/login.html"}:
            self.path = "/login.html"
        super().do_HEAD()

    def do_GET(self):
        parsed = urlparse(self.path)
        route = parsed.path
        query = parse_qs(parsed.query)

        if route == "/api/health":
            if self.app.auth_required:
                self._json({
                    "ok": True,
                    "app": "xdl-web",
                    "version": "2.0",
                    "authRequired": True,
                    "configured": self.app.auth.is_configured(),
                })
            else:
                self._json({
                    "ok": True,
                    "app": "xdl-web",
                    "version": "2.0",
                    "pid": os.getpid(),
                    "url": self.app.server_url,
                    "lanUrl": self.app.lan_url,
                })
            return

        if route == "/api/auth/status":
            sess = self._current_session()
            self._json({
                "ok": True,
                "authRequired": self.app.auth_required,
                "configured": self.app.auth.is_configured(),
                "authenticated": sess is not None,
                "username": sess["username"] if sess else None,
            })
            return

        if route.startswith("/api/"):
            if self._require_auth_json() is None:
                return
        elif self.app.auth_required:
            if route in {"/login", "/login.html"}:
                if self._current_session() is not None:
                    self._redirect("/")
                    return
                self.path = "/login.html"
                super().do_GET()
                return
            if route not in {"/styles.css", "/login.js"}:
                if self._current_session() is None:
                    if route.endswith((".js", ".txt", ".json")):
                        self.send_error(401, "Authentication required")
                    else:
                        self._redirect("/login")
                    return

        if route == "/api/state":
            self._json(self.app.snapshot())
            return
        if route == "/api/shortcut/config":
            token = self.app.auth.get_shortcut_token(create=True) if self.app.auth_required else ""
            self._json({
                "ok": True,
                "authRequired": self.app.auth_required,
                "token": token or "",
                "inspectPath": "/api/shortcut/inspect",
                "submitPath": "/api/shortcut/submit",
                "durationOptions": list(SHORTCUT_DURATION_OPTIONS),
            })
            return
        if route == "/api/logs":
            try:
                since = max(0, int((query.get("since") or ["0"])[0]))
            except ValueError:
                since = 0
            user = (query.get("user") or [""])[0].strip() or None
            level = (query.get("level") or ["all"])[0].strip() or "all"
            try:
                limit = max(1, min(2000, int((query.get("limit") or ["500"])[0])))
            except ValueError:
                limit = 500
            self._json(self.app.get_logs(since=since, user=user, level=level, limit=limit))
            return
        if route == "/api/media/file":
            self.app.touch_activity()
            self._serve_media_file(query)
            return
        if route == "/api/media/thumb":
            self._serve_media_thumb(query)
            return
        if route == "/api/media/zip":
            self.app.touch_activity()
            self._serve_media_zip(query)
            return
        if route == "/api/history":
            self.app.touch_activity()
            user = (query.get("user") or [""])[0].lstrip("@")
            try:
                limit = int((query.get("limit") or ["20"])[0])
            except ValueError:
                limit = 20
            limit = max(1, min(200, limit))
            try:
                summary = xdl.media_summary(user) if user else xdl.media_summary()
                history = xdl.recent_media(user, limit) if user else []
                self._json({
                    "ok": True,
                    "user": user,
                    "summary": summary,
                    "history": history,
                })
            except Exception as exc:
                self._json({"ok": False, "error": str(exc)}, 500)
            return
        if route == "/api/completed":
            self.app.touch_activity()
            user = (query.get("user") or [""])[0].lstrip("@") or None
            kind = (query.get("kind") or ["all"])[0]
            storage = (query.get("storage") or ["all"])[0]
            keyword = (query.get("q") or [""])[0].strip() or None
            try:
                limit = int((query.get("limit") or ["300"])[0])
            except ValueError:
                limit = 300
            limit = max(1, min(2000, limit))
            try:
                offset = int((query.get("offset") or ["0"])[0])
            except ValueError:
                offset = 0
            offset = max(0, offset)
            try:
                rows = xdl.list_media(
                    username=user,
                    limit=limit,
                    offset=offset,
                    kind=kind if kind in {"images", "videos"} else None,
                    keyword=keyword,
                    storage=storage if storage in {"local", "cloud"} else None,
                )
                totals = xdl.media_kind_counts()
                self._json({
                    "ok": True,
                    "rows": rows,
                    "offset": offset,
                    "hasMore": len(rows) >= limit,
                    "totals": totals,
                    "summary": xdl.media_summary(),
                })
            except Exception as exc:
                self._json({"ok": False, "error": str(exc)}, 500)
            return
        if route == "/api/quit":
            if self.app.cloud_mode:
                self._json({"ok": False, "error": "云端服务模式下已禁用远程关停"}, 403)
                return
            self.app.allow_window_close = True
            self._json({"ok": True})
            threading.Thread(target=self.app.stop.set, daemon=True).start()
            return
        if route == "/api/busy":
            snapshot = self.app.snapshot()
            self._json({
                "ok": True,
                "busy": bool(snapshot.get("busy") or snapshot.get("running")),
                "activeCount": snapshot.get("activeCount", 0),
                "running": bool(snapshot.get("running")),
            })
            return
        if route in {"/", "/index.html"}:
            self.app.touch_activity()
        super().do_GET()

    def _serve_media_zip(self, query: dict) -> None:
        user = unquote((query.get("user") or [""])[0]).lstrip("@")
        if not user or not USER_RE.fullmatch(user):
            self._json({"ok": False, "error": "无效的用户名"}, 400)
            return
        output_root = Path(self.app.settings["output"]).resolve()
        files: list[Path] = []
        seen: set[Path] = set()
        user_dir = (output_root / user).resolve()
        if user_dir.is_relative_to(output_root) and user_dir.is_dir():
            for path in sorted(user_dir.rglob("*")):
                resolved = path.resolve()
                if resolved.is_relative_to(output_root) and resolved.is_file() and resolved.suffix.lower() not in {".part", ".tmp"}:
                    if resolved not in seen:
                        seen.add(resolved)
                        files.append(resolved)
        if output_root.is_dir():
            for path in sorted(output_root.iterdir()):
                if is_user_media_file(path, user, flat=True):
                    resolved = path.resolve()
                    if resolved.is_relative_to(output_root) and resolved not in seen:
                        seen.add(resolved)
                        files.append(resolved)

        if not files:
            self._json({"ok": False, "error": f"@{user} 当前没有本地媒体文件可打包（可能已转存至 Google Drive）"}, 404)
            return

        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(prefix=f"xdl_{user}_", suffix=".zip", delete=False) as tf:
                tmp_path = Path(tf.name)
            with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_STORED) as zf:
                for fpath in files:
                    zf.write(fpath, arcname=fpath.name)
            size = tmp_path.stat().st_size
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Length", str(size))
            self.send_header(
                "Content-Disposition",
                f"attachment; filename*=UTF-8''{quote(user + '_media.zip')}",
            )
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            with tmp_path.open("rb") as fh:
                while True:
                    chunk = fh.read(65536)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            return
        finally:
            if tmp_path is not None:
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError:
                    pass

    def do_POST(self):
        route = urlparse(self.path).path
        payload = self._body()
        try:
            if route == "/api/auth/login":
                if not self.app.auth_required:
                    self._json({"ok": True, "authRequired": False, "username": "local"})
                    return
                result = self.app.auth.verify_login(
                    payload.get("username"),
                    payload.get("password"),
                    client_ip=self._client_ip(),
                    user_agent=self.headers.get("User-Agent", ""),
                    remember=bool(payload.get("remember")),
                )
                status = int(result.pop("status", 200 if result.get("ok") else 401))
                headers = []
                if result.get("ok"):
                    self.app.touch_activity()
                    token = result.pop("token")
                    ttl = result.pop("ttl")
                    headers.append(("Set-Cookie", xdl_auth.build_session_cookie(token, ttl, self._is_https())))
                    self.app.add_log("info", f"管理员 {result.get('username')} 已登录 ({self._client_ip()})")
                elif status == 429 and result.get("retryAfter"):
                    headers.append(("Retry-After", str(int(result["retryAfter"]))))
                self._json(result, status=status, extra_headers=headers)
                return

            if route == "/api/auth/logout":
                self.app.auth.logout_session(self._session_token())
                self._json(
                    {"ok": True, "redirect": "/login"},
                    extra_headers=[("Set-Cookie", xdl_auth.build_clear_cookie(self._is_https()))],
                )
                return

            if route in {"/api/shortcut/inspect", "/api/shortcut/submit"}:
                if self._require_shortcut_auth(payload) is None:
                    return
                self.app.touch_activity()
                try:
                    if route == "/api/shortcut/inspect":
                        result = self.app.inspect_shortcut_url(payload.get("url") or "")
                    else:
                        result = self.app.submit_shortcut_task(payload)
                    self._json(result)
                except ValueError as exc:
                    msg = str(exc)
                    self._json(
                        {
                            "ok": False,
                            "status": "error",
                            "title": "XDL 快捷指令处理失败",
                            "message": msg,
                            "error": msg,
                        },
                        400,
                    )
                return

            sess = self._require_auth_json()
            if sess is None:
                return
            self.app.touch_activity()

            if route == "/api/auth/change-password":
                new_password = payload.get("newPassword")
                new_username = payload.get("newUsername")
                current_password = payload.get("currentPassword")
                try:
                    result = self.app.auth.change_credentials(
                        current_password=current_password,
                        new_password=new_password,
                        new_username=new_username,
                        client_ip=self._client_ip(),
                        user_agent=self.headers.get("User-Agent", ""),
                        keep_remember=bool(sess.get("remember")),
                    )
                except ValueError as exc:
                    self._json({"ok": False, "error": str(exc)}, status=400)
                    return
                status = int(result.pop("status", 200 if result.get("ok") else 400))
                headers = []
                if result.get("ok"):
                    token = result.pop("token")
                    ttl = result.pop("ttl")
                    headers.append(("Set-Cookie", xdl_auth.build_session_cookie(token, ttl, self._is_https())))
                    self.app.add_log("ok", f"管理员凭据已更新（用户名：{result.get('username')}）")
                    result["state"] = self.app.snapshot()
                self._json(result, status=status, extra_headers=headers)
                return

            if route == "/api/auth/logout-all":
                self.app.auth.logout_all()
                self.app.add_log("warn", "已注销全部已登录设备的会话")
                self._json(
                    {"ok": True, "redirect": "/login"},
                    extra_headers=[("Set-Cookie", xdl_auth.build_clear_cookie(self._is_https()))],
                )
                return

            if route == "/api/add":
                result = self.app.add_users(payload.get("users") or [])
                self._json({"ok": True, **result, "state": self.app.snapshot()})
            elif route == "/api/start":
                self.app.start()
                self._json({"ok": True, "state": self.app.snapshot()})
            elif route == "/api/pause":
                self.app.pause()
                self._json({"ok": True, "state": self.app.snapshot()})
            elif route == "/api/stop":
                result = self.app.stop_all()
                self._json({**result, "state": self.app.snapshot()})
            elif route == "/api/command":
                result = self.app.execute_command(payload.get("command") or "")
                status = 200 if result.get("ok") else 400
                self._json(result, status=status)
            elif route == "/api/logs/clear":
                self.app.clear_logs()
                self._json({"ok": True, "state": self.app.snapshot()})
            elif route == "/api/pick-folder":
                if self.app.cloud_mode:
                    self._json({"ok": False, "cloudMode": True, "error": "云端服务模式下使用服务器固定存储目录"})
                    return
                picked = pick_folder_dialog(self.app.settings.get("output", ""))
                self._json({"ok": True, "path": picked})
            elif route == "/api/pick-file":
                if self.app.cloud_mode:
                    self._json({"ok": False, "cloudMode": True, "error": "云端服务模式请直接上传或粘贴 Cookie"})
                    return
                picked = pick_file_dialog(self.app.settings.get("cookies", ""))
                self._json({"ok": True, "path": picked})
            elif route == "/api/upload-cookies":
                content = payload.get("content")
                auth_token = payload.get("authToken")
                ct0 = payload.get("ct0")
                if isinstance(auth_token, str) and isinstance(ct0, str) and (auth_token.strip() or ct0.strip()):
                    try:
                        content = _synthesize_netscape_cookies(auth_token, ct0)
                    except ValueError as exc:
                        self._json({"ok": False, "error": str(exc)}, 400)
                        return
                elif not isinstance(content, str) or not content.strip():
                    self._json({"ok": False, "error": "请上传 cookies.txt 或填写 auth_token 与 ct0"}, 400)
                    return
                if len(content.encode("utf-8", errors="ignore")) > 2 * 1024 * 1024:
                    self._json({"ok": False, "error": "Cookie 文件过大（上限 2 MB）"}, 400)
                    return
                xdl.XDL_HOME.mkdir(parents=True, exist_ok=True)
                target = xdl.XDL_HOME / "cookies.txt"
                target.write_text(content, encoding="utf-8")
                try:
                    os.chmod(target, 0o600)
                except OSError:
                    pass
                self.app.update_settings({
                    "cookiesSource": "file",
                    "cookies": str(target),
                })
                self.app.add_log("ok", f"已保存 Cookie 至 {target}")
                self._json({"ok": True, "path": str(target), "state": self.app.snapshot()})
            elif route == "/api/media/delete":
                user = str(payload.get("user") or "").strip().lstrip("@")
                filename = str(payload.get("filename") or "").strip()
                if not user or not USER_RE.fullmatch(user):
                    self._json({"ok": False, "error": "无效的用户名"}, 400)
                    return
                output_root = Path(self.app.settings["output"]).resolve()
                deleted_files = 0
                if filename:
                    if filename != Path(filename).name or ".." in filename:
                        self._json({"ok": False, "error": "无效的文件名"}, 400)
                        return
                    for candidate in (output_root / user / filename, output_root / filename):
                        try:
                            resolved = candidate.resolve()
                            if resolved.is_relative_to(output_root) and resolved.is_file():
                                resolved.unlink()
                                deleted_files += 1
                        except OSError:
                            pass
                    xdl.mark_media_local_deleted(username=user, filename=filename)
                else:
                    user_dir = (output_root / user).resolve()
                    if user_dir.is_relative_to(output_root) and user_dir.is_dir():
                        for path in list(user_dir.rglob("*")):
                            try:
                                resolved = path.resolve()
                                if resolved.is_relative_to(output_root) and resolved.is_file():
                                    resolved.unlink()
                                    deleted_files += 1
                            except OSError:
                                pass
                        try:
                            if not any(user_dir.iterdir()):
                                user_dir.rmdir()
                        except OSError:
                            pass
                    if output_root.is_dir():
                        for path in list(output_root.iterdir()):
                            if is_user_media_file(path, user, flat=True):
                                try:
                                    resolved = path.resolve()
                                    if resolved.is_relative_to(output_root):
                                        resolved.unlink()
                                        deleted_files += 1
                                except OSError:
                                    pass
                    xdl.mark_media_local_deleted(username=user)
                summary = xdl.media_summary()
                with self.app.lock:
                    self.app.store_summary.update({
                        "media": summary["media"],
                        "tweets": summary["tweets"],
                        "bytes": summary["bytes"],
                    })
                    self.app._refresh_item_counts_locked()
                    self.app.add_log("info", f"已清理 @{user} 本地媒体 {deleted_files} 个文件", user=user)
                    self.app.mark_dirty()
                self._json({"ok": True, "deleted": deleted_files, "state": self.app.snapshot()})
            elif route == "/api/cloud-sync/test":
                result = self.app.test_cloud_sync()
                status = 200 if result.get("ok") else 400
                self._json(result, status=status)
            elif route == "/api/cloud-sync/run":
                user = str(payload.get("user") or "").strip().lstrip("@") or None
                if user and not USER_RE.fullmatch(user):
                    self._json({"ok": False, "error": "无效的用户名"}, 400)
                    return
                if self.app.cloud_syncing:
                    self._json({"ok": False, "busy": True, "error": "云盘同步正在进行中，请稍候"}, 409)
                    return
                threading.Thread(
                    target=self.app.sync_to_cloud,
                    kwargs={"user": user, "trigger": "manual"},
                    daemon=True,
                ).start()
                self._json({"ok": True, "started": True, "state": self.app.snapshot()})
            elif route == "/api/retry":
                result = self.app.retry_failures(payload.get("ids") or [])
                self._json({"ok": True, **result, "state": self.app.snapshot()})
            elif route == "/api/retry-account":
                raw_id = payload.get("id")
                item_id = int(raw_id) if raw_id not in {None, ""} else None
                result = self.app.retry_account(payload.get("user") or "", item_id=item_id)
                self._json({"ok": True, **result, "state": self.app.snapshot()})
            elif route == "/api/shortcut/token/rotate":
                if not self.app.auth_required:
                    self._json({"ok": True, "authRequired": False, "token": ""})
                    return
                new_token = self.app.auth.rotate_shortcut_token()
                self.app.add_log("warn", "已重置 iOS 快捷指令 API Token，旧 Token 已立即失效")
                self._json({"ok": True, "authRequired": True, "token": new_token or ""})
            elif route == "/api/remove":
                self.app.remove_items(payload.get("ids") or [])
                self._json({"ok": True, "state": self.app.snapshot()})
            elif route == "/api/clear-done":
                self.app.clear_done()
                self._json({"ok": True, "state": self.app.snapshot()})
            elif route == "/api/discard":
                self.app.discard_failures(payload.get("ids") or [])
                self._json({"ok": True, "state": self.app.snapshot()})
            elif route == "/api/backfill":
                result = self.app.backfill_all()
                self._json({"ok": True, **result, "state": self.app.snapshot()})
            elif route == "/api/thumbs/backfill":
                user = str(payload.get("user") or "").strip().lstrip("@") or None
                if user and not USER_RE.fullmatch(user):
                    self._json({"ok": False, "error": "无效的用户名"}, 400)
                    return
                if self.app.thumb_backfilling:
                    self._json({"ok": False, "busy": True, "error": "缩略图补全正在进行中，请稍候"}, 409)
                    return
                threading.Thread(
                    target=self.app.backfill_cloud_thumbs,
                    kwargs={"user": user},
                    daemon=True,
                ).start()
                self._json({"ok": True, "started": True, "state": self.app.snapshot()})
            elif route == "/api/settings":
                settings = self.app.update_settings(payload)
                self._json({"ok": True, "settings": settings, "state": self.app.snapshot()})
            elif route == "/api/reorder":
                self.app.reorder(payload.get("ids") or [])
                self._json({"ok": True})
            elif route == "/api/open":
                self._open(payload)
                self._json({"ok": True})
            elif route == "/api/quit":
                if self.app.cloud_mode:
                    self._json({"ok": False, "error": "云端服务模式下已禁用远程关停"}, 403)
                    return
                self.app.allow_window_close = True
                self._json({"ok": True})
                threading.Thread(target=self.app.stop.set, daemon=True).start()
                self._close_window_soon()
            else:
                self._json({"ok": False, "error": "unknown route"}, 404)
        except Exception as exc:
            traceback.print_exc()
            self._json({"ok": False, "error": str(exc)}, 500)

    def _close_window_soon(self) -> None:
        def worker() -> None:
            time.sleep(0.25)
            try:
                import webview
                for window in list(getattr(webview, "windows", []) or []):
                    window.destroy()
            except Exception:
                pass

        threading.Thread(target=worker, daemon=True).start()

    def _open(self, payload):
        if self.app.cloud_mode or sys.platform != "win32":
            return
        kind = payload.get("kind")
        settings = self.app.settings
        if kind == "dir":
            user = payload.get("user")
            path = Path(settings["output"])
            if user and settings.get("layout") != "flat":
                path = path / user
            path.mkdir(parents=True, exist_ok=True)
            os.startfile(str(path))
        elif kind == "file":
            user = payload.get("user") or ""
            filename = payload.get("filename") or ""
            if filename and filename == Path(filename).name:
                output_root = Path(settings["output"]).resolve()
                target = output_root / user / filename if user else output_root / filename
                if not target.is_file():
                    target = output_root / filename
                if target.is_file():
                    subprocess.Popen(["explorer", "/select,", str(target)])
        elif kind == "page" and payload.get("user"):
            os.startfile(f"https://x.com/{payload['user']}")
        elif kind == "tweet" and payload.get("user") and payload.get("tweetId"):
            os.startfile(
                f"https://x.com/{payload['user']}/status/{payload['tweetId']}"
            )


class ActivatedThreadingHTTPServer(ThreadingHTTPServer):
    """ThreadingHTTPServer adopting a pre-bound systemd socket fd."""

    def __init__(self, listen_fd: int, RequestHandlerClass):
        super().__init__(("127.0.0.1", 0), RequestHandlerClass, bind_and_activate=False)
        self.socket.close()
        sock = socket.fromfd(listen_fd, self.address_family, self.socket_type)
        sock.setblocking(True)
        self.socket = sock
        self.server_address = sock.getsockname()
        host, port = self.server_address[:2]
        self.server_name = socket.getfqdn(host)
        self.server_port = port


def _adopt_systemd_listen_fd() -> int | None:
    if os.environ.get("LISTEN_PID", "").strip() != str(os.getpid()):
        return None
    try:
        fds = int(os.environ.get("LISTEN_FDS", "0").strip() or "0")
    except ValueError:
        return None
    if fds < 1:
        return None
    os.environ.pop("LISTEN_PID", None)
    os.environ.pop("LISTEN_FDS", None)
    os.environ.pop("LISTEN_FDNAMES", None)
    return 3  # SD_LISTEN_FDS_START


def serve(app: AppState, host: str = "127.0.0.1", port: int = DEFAULT_PORT) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {"app": app})
    listen_fd = _adopt_systemd_listen_fd()
    if listen_fd is not None:
        server = ActivatedThreadingHTTPServer(listen_fd, handler)
        try:
            os.close(listen_fd)
        except OSError:
            pass
    else:
        try:
            server = ThreadingHTTPServer((host, port), handler)
        except OSError:
            if port != 0:
                server = ThreadingHTTPServer((host, 0), handler)
            else:
                raise
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _run_set_auth_cli(args: argparse.Namespace) -> int:
    auth_mgr = xdl_auth.AuthManager(AUTH_FILE, AUTH_DB_FILE)
    existing_user = auth_mgr.username() or "admin"
    username = (args.username or "").strip()
    if not username and args.username_file:
        username = Path(args.username_file).read_text(encoding="utf-8").strip()
    if not username:
        if args.password_file or args.password_stdin:
            username = existing_user
        else:
            entered = input(f"管理员用户名 [{existing_user}]: ").strip()
            username = entered or existing_user

    if args.password_file:
        pw_path = Path(args.password_file)
        password = pw_path.read_text(encoding="utf-8").rstrip("\r\n")
    elif args.password_stdin:
        password = sys.stdin.read().rstrip("\r\n")
    else:
        password = getpass.getpass("设置管理员密码（至少 6 位）: ")
        confirm = getpass.getpass("再次输入密码确认: ")
        if password != confirm:
            print("错误: 两次输入的密码不一致", file=sys.stderr)
            return 1

    try:
        res = auth_mgr.set_credentials(username, password, clear_sessions=True)
    except ValueError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1

    print(f"管理员凭据已保存至 {res['path']}（用户名: {res['username']}，所有旧会话已失效）")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="XDL Web & Desktop App")
    parser.add_argument("--web", action="store_true", help="以网页应用模式启动（默认）")
    parser.add_argument("--desktop", action="store_true", help="使用 PyWebView 原生窗口启动")
    parser.add_argument("--headless", action="store_true", help="仅启动服务，不自动打开浏览器")
    parser.add_argument("--cloud", action="store_true", help="云端服务器模式（锁定本地路径、禁用远程关停、默认直连、开启登录鉴权）")
    parser.add_argument("--require-auth", action="store_true", help="强制开启网页登录鉴权")
    parser.add_argument("--set-auth", action="store_true", help="设置或重置管理员登录用户名与密码后退出")
    parser.add_argument("--backfill-thumbs", action="store_true", help="为已有媒体（含云端文件）补全本地微缩略图后退出")
    parser.add_argument("--username", default="", help="配合 --set-auth 使用：指定用户名")
    parser.add_argument("--username-file", default="", help="配合 --set-auth 使用：从文件读取用户名")
    parser.add_argument("--password-file", default="", help="配合 --set-auth 使用：从文件读取密码")
    parser.add_argument("--password-stdin", action="store_true", help="配合 --set-auth 使用：从标准输入读取密码")
    parser.add_argument("--host", default="127.0.0.1", help="监听主机地址 (默认 127.0.0.1)")
    parser.add_argument("--lan", action="store_true", help="允许局域网设备（如手机）访问")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"监听端口 (默认 {DEFAULT_PORT})")
    parser.add_argument("--no-open", action="store_true", help="启动网页服务时不自动拉起浏览器")
    parser.add_argument("--state-file", default=str(STATE_FILE))
    parser.add_argument("--idle-timeout", type=int, default=0, help="无操作变化自动退出秒数 (0 表示不自动休眠)")
    args = parser.parse_args()

    if args.set_auth:
        return _run_set_auth_cli(args)

    if args.backfill_thumbs:
        xdl.ensure_download_store()
        cloud_mode = bool(args.cloud or CLOUD_MODE_ENV)
        app = AppState(
            Path(args.state_file),
            cloud_mode=cloud_mode,
            require_auth=False,
            start_background=False,
        )
        res = app.backfill_cloud_thumbs()
        app.save()
        print(json.dumps(res, ensure_ascii=False))
        return 0 if res.get("ok") else 1

    host = "0.0.0.0" if args.lan else args.host
    use_desktop = bool(args.desktop)
    cloud_mode = bool(args.cloud or CLOUD_MODE_ENV)
    require_auth = bool(args.require_auth or cloud_mode)

    if not args.headless and not use_desktop and args.port > 0:
        existing = detect_running_instance(args.port)
        if existing:
            url = existing.get("url") or f"http://127.0.0.1:{args.port}/"
            print(f"XDL Web 已在运行: {url}", flush=True)
            if not args.no_open:
                webbrowser.open(url)
            return 0

    idle_timeout = args.idle_timeout or int(os.environ.get("XDL_IDLE_TIMEOUT", "0") or "0")
    app = AppState(Path(args.state_file), cloud_mode=cloud_mode, require_auth=require_auth, idle_timeout=idle_timeout)
    server = serve(app, host=host, port=args.port)
    port = server.server_address[1]
    local_url = f"http://127.0.0.1:{port}/"
    lan_ip = detect_lan_ip() if host == "0.0.0.0" else None
    lan_url = f"http://{lan_ip}:{port}/" if lan_ip else None
    app.server_url = local_url
    app.lan_url = lan_url

    print(f"XDL Web: {local_url}", flush=True)
    if lan_url:
        print(f"LAN URL (手机访问): {lan_url}", flush=True)

    if args.headless:
        def _handle_sig(_signum, _frame):
            app.stop.set()
        signal.signal(signal.SIGTERM, _handle_sig)
        signal.signal(signal.SIGINT, _handle_sig)
        try:
            while not app.stop.wait(0.5):
                pass
        except KeyboardInterrupt:
            pass
        finally:
            app.shutdown()
            server.shutdown()
            server.server_close()
        return 0

    if not use_desktop:
        if not args.no_open:
            webbrowser.open(local_url)
        try:
            while not app.stop.wait(0.5):
                pass
        except KeyboardInterrupt:
            pass
        finally:
            app.shutdown()
            server.shutdown()
        return 0

    try:
        import webview
    except ImportError:
        print("缺少 pywebview，自动切换为浏览器模式...")
        webbrowser.open(local_url)
        try:
            while not app.stop.wait(0.5):
                pass
        except KeyboardInterrupt:
            pass
        app.shutdown()
        server.shutdown()
        return 0

    window = webview.create_window(
        "XDL - X 媒体批量下载器",
        url=local_url,
        width=1280,
        height=860,
        min_size=(980, 640),
        background_color="#090b0f",
        js_api=Bridge(),
    )
    asking = threading.Event()

    def prompt_quit() -> None:
        if asking.is_set():
            return
        asking.set()
        try:
            window.evaluate_js(
                "(function(){"
                "if (typeof window.__xdlQuitPrompt === 'function') "
                "return window.__xdlQuitPrompt();"
                "return null;"
                "})()"
            )
        except Exception:
            traceback.print_exc()
        finally:
            asking.clear()

    def on_closing() -> bool:
        if app.allow_window_close:
            return True
        try:
            snap = app.snapshot()
            busy = bool(snap.get("busy") or snap.get("running"))
        except Exception:
            busy = False
        if not busy:
            app.allow_window_close = True
            return True
        threading.Thread(target=prompt_quit, daemon=True).start()
        return False

    window.events.closing += on_closing
    webview.start()
    app.shutdown()
    server.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
