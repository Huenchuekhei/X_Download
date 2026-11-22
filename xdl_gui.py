#!/usr/bin/env python3
"""XDL Web & Desktop Server: responsive web workspace plus a real gallery-dl backend."""

from __future__ import annotations

import argparse
import collections
import json
import mimetypes
import os
import re
import shlex
import socket
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, quote, unquote, urlparse


BASE_DIR = Path(__file__).resolve().parent
APP_DIR = BASE_DIR / "xdl-gui"
STATE_FILE = Path.home() / ".xdl" / "gui_state.json"
DEFAULT_PORT = 8765

sys.path.insert(0, str(BASE_DIR))
import x_downloader as xdl  # noqa: E402


ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
FILE_RE = re.compile(
    r"([A-Za-z0-9_]{1,15})_(\d{4}-\d{2}-\d{2})"
    r"(?:[ T]\d{2}_\d{2}_\d{2})?_(\d{15,20})_(\d+)\.([A-Za-z0-9]+)"
)
USER_RE = re.compile(r"^[A-Za-z0-9_]{1,15}$")
REASON_LABELS = {
    "timeout": "连接超时",
    "network": "网络中断",
    "forbidden": "权限受限",
    "notfound": "文件已删除",
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


class AppState:
    def __init__(self, state_file: Path = STATE_FILE):
        self.lock = threading.RLock()
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
        self.load()
        self.add_log("info", "XDL Web 工作台已就绪，支持 CLI 命令、实时日志与视频时长过滤")
        threading.Thread(target=self._saver_loop, daemon=True).start()
        threading.Thread(target=self._scheduler_loop, daemon=True).start()
        threading.Thread(target=self._probe_gallery_dl, daemon=True).start()
        threading.Thread(target=self._prepare_store, daemon=True).start()

    def default_settings(self) -> dict:
        cookies = xdl.find_cookies_file()
        return {
            "output": xdl.DEFAULT_OUTPUT_DIR,
            "cookies": cookies or str(Path.home() / "Downloads" / "cookies.txt"),
            "cookiesSource": "file",
            "cookiesBrowser": "chrome",
            "proxyMode": "auto",
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
        proxy_mode = str(s.get("proxyMode") or "auto").lower()
        s["proxyMode"] = proxy_mode if proxy_mode in {"auto", "manual", "off"} else "auto"
        layout = str(s.get("layout") or "per-user").lower()
        s["layout"] = "flat" if layout == "flat" else "per-user"

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
        for item in self.queue:
            if item.get("status") in {"active", "waiting"}:
                item["status"] = "paused"
            item.pop("retryFailureIds", None)
        for failure in self.failures:
            if failure.get("status") == "queued":
                failure["status"] = "failed"

    def add_log(self, level: str, text: str, user: str | None = None) -> dict:
        with self.lock:
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
        return {
            "cookiesFound": cookies_found,
            "cookiesSource": cookies_source,
            "cookiesBrowser": cookies_browser,
            "proxyMode": proxy_mode,
            "proxyOnline": proxy_online,
            "galleryDl": self.gdl_version,
            "filterSummary": describe_filter(self.settings),
            "serverUrl": self.server_url,
            "lanUrl": self.lan_url,
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
            if result["files"]:
                msg = f"XDL 已标记 {result['files']} 个历史文件（{summary['tweets']} 条推文）"
                print(msg, flush=True)
                self.add_log("info", msg)
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
                self.add_log("info", f"已重建本地下载档案：{summary['media']} 个媒体 / {summary['tweets']} 条推文")
                self.mark_dirty()
        return {
            "files": result["files"],
            "media": summary["media"],
            "tweets": summary["tweets"],
            "users": result["users"],
        }

    def mark_dirty(self) -> None:
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
            existing = {item["user"].lower() for item in self.queue}
            for user in users:
                user = str(user).strip().lstrip("@")
                if not USER_RE.fullmatch(user):
                    continue
                if user.lower() in existing:
                    skipped += 1
                    continue
                item = {
                    "id": self.next_id,
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
                        patch["output"] = next_val()
                    elif low in {"-c", "--cookies"}:
                        patch["cookiesSource"] = "file"
                        patch["cookies"] = next_val()
                    elif low == "--cookies-from-browser":
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
                if item["user"].lower() in wanted:
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

    def start(self) -> None:
        with self.lock:
            filt_label = describe_filter(self.settings)
            for item in self.queue:
                if item.get("status") == "paused":
                    item["status"] = "waiting"
                    item["filterSummary"] = filt_label
                elif item.get("status") == "done":
                    item["status"] = "waiting"
                    item["progress"] = 0
                    item["error"] = None
                    item["newFiles"] = 0
                    item["skippedFiles"] = 0
                    item["filterSummary"] = filt_label
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
        settings = dict(self.settings)
        item["filterSummary"] = describe_filter(settings)
        user = item["user"]
        item_id = item["id"]
        self.add_log(
            "info",
            f"开始任务 @{user}" + (f"（{item['filterSummary']}）" if item["filterSummary"] else ""),
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

        targets: list[tuple[str, list[int]]] = []
        if failure_ids:
            groups: dict[str | None, list[int]] = {}
            for failure_id in failure_ids:
                failure = self._find_failure(failure_id)
                if not failure:
                    continue
                groups.setdefault(failure.get("tweetId") or None, []).append(failure_id)
            for tweet_id, ids in groups.items():
                target = (
                    f"https://x.com/{user}/status/{tweet_id}"
                    if tweet_id else
                    f"https://x.com/{user}" + ("/media" if settings["mediaOnly"] else "")
                )
                targets.append((target, ids))
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

            item["files"] = count_user_files(Path(output), user, flat)
            item["recentFiles"] = recent_files(
                Path(output) if flat else (Path(output) / user),
                user=user,
                flat=flat,
            )
            try:
                item["downloadedTotal"] = xdl.media_count(user)
                item["recentMedia"] = xdl.recent_media(user, 6)
            except Exception:
                item["downloadedTotal"] = int(item.get("downloadedTotal") or item["files"])
                if run_stats["recentMedia"]:
                    item["recentMedia"] = run_stats["recentMedia"]
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
                self.add_log(
                    "ok",
                    f"@{user} 完成：新增 {item['newFiles']} · 跳过 {item['skippedFiles']} · 累计 {item['downloadedTotal']} 个媒体",
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

    def _run_gallery(
        self,
        item_id: int,
        user: str,
        target: str,
        settings: dict,
        is_tweet: bool,
    ) -> tuple[bool, list[dict], dict]:
        empty_stats = {"newFiles": 0, "skippedFiles": 0, "recentMedia": []}
        cookies_source = settings.get("cookiesSource", "file")
        cookies_browser = settings.get("cookiesBrowser", "chrome")
        if cookies_source == "browser":
            if cookies_browser not in xdl.SUPPORTED_BROWSERS:
                return False, [{
                    "user": user,
                    "name": f"@{user} 账号级失败",
                    "reason": "forbidden",
                    "note": f"不支持的浏览器 Cookie 来源: {cookies_browser}",
                    "tweetId": None,
                    "size": "—",
                }], empty_stats
            cookies_config = f"cookies-from-browser:{cookies_browser}"
        else:
            cookies = Path(settings["cookies"])
            if not cookies.is_file():
                return False, [{
                    "user": user,
                    "name": f"@{user} 账号级失败",
                    "reason": "forbidden",
                    "note": f"Cookie 文件不存在: {cookies}",
                    "tweetId": None,
                    "size": "—",
                }], empty_stats
            cookies_config = str(cookies)

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

        proxy = None
        if settings["proxyMode"] == "manual":
            proxy = settings["proxyAddr"]
        elif settings["proxyMode"] == "auto" and xdl.default_proxy_available():
            proxy = xdl.DEFAULT_PROXY

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
        config_path = Path(os.environ.get("TEMP", ".")) / f"xdl_gui_{css}_{os.getpid()}_{time.time_ns()}.json"
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

    def retry_account(self, user: str) -> dict:
        with self.lock:
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
            item["filterSummary"] = describe_filter(self.settings)
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

    def _json(self, payload, status=200):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
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

    def do_GET(self):
        parsed = urlparse(self.path)
        route = parsed.path
        query = parse_qs(parsed.query)

        if route == "/api/health":
            self._json({
                "ok": True,
                "app": "xdl-web",
                "version": "2.0",
                "pid": os.getpid(),
                "url": self.app.server_url,
                "lanUrl": self.app.lan_url,
            })
            return
        if route == "/api/state":
            self._json(self.app.snapshot())
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
            self._serve_media_file(query)
            return
        if route == "/api/history":
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
            user = (query.get("user") or [""])[0].lstrip("@") or None
            kind = (query.get("kind") or ["all"])[0]
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
        super().do_GET()

    def do_POST(self):
        route = urlparse(self.path).path
        payload = self._body()
        try:
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
                picked = pick_folder_dialog(self.app.settings.get("output", ""))
                self._json({"ok": True, "path": picked})
            elif route == "/api/pick-file":
                picked = pick_file_dialog(self.app.settings.get("cookies", ""))
                self._json({"ok": True, "path": picked})
            elif route == "/api/upload-cookies":
                content = payload.get("content")
                if not isinstance(content, str) or not content.strip():
                    self._json({"ok": False, "error": "Cookie 内容不能为空"}, 400)
                    return
                if len(content.encode("utf-8", errors="ignore")) > 2 * 1024 * 1024:
                    self._json({"ok": False, "error": "Cookie 文件过大（上限 2 MB）"}, 400)
                    return
                xdl.XDL_HOME.mkdir(parents=True, exist_ok=True)
                target = xdl.XDL_HOME / "cookies.txt"
                target.write_text(content, encoding="utf-8")
                self.app.update_settings({
                    "cookiesSource": "file",
                    "cookies": str(target),
                })
                self.app.add_log("ok", f"已上传并保存 Cookie 至 {target}")
                self._json({"ok": True, "path": str(target), "state": self.app.snapshot()})
            elif route == "/api/retry":
                result = self.app.retry_failures(payload.get("ids") or [])
                self._json({"ok": True, **result, "state": self.app.snapshot()})
            elif route == "/api/retry-account":
                result = self.app.retry_account(payload.get("user") or "")
                self._json({"ok": True, **result, "state": self.app.snapshot()})
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


def serve(app: AppState, host: str = "127.0.0.1", port: int = DEFAULT_PORT) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {"app": app})
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


def main() -> int:
    parser = argparse.ArgumentParser(description="XDL Web & Desktop App")
    parser.add_argument("--web", action="store_true", help="以网页应用模式启动（默认）")
    parser.add_argument("--desktop", action="store_true", help="使用 PyWebView 原生窗口启动")
    parser.add_argument("--headless", action="store_true", help="仅启动服务，不自动打开浏览器")
    parser.add_argument("--host", default="127.0.0.1", help="监听主机地址 (默认 127.0.0.1)")
    parser.add_argument("--lan", action="store_true", help="允许局域网设备（如手机）访问")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"监听端口 (默认 {DEFAULT_PORT})")
    parser.add_argument("--no-open", action="store_true", help="启动网页服务时不自动拉起浏览器")
    parser.add_argument("--state-file", default=str(STATE_FILE))
    args = parser.parse_args()

    host = "0.0.0.0" if args.lan else args.host
    use_desktop = bool(args.desktop)

    if not args.headless and not use_desktop and args.port > 0:
        existing = detect_running_instance(args.port)
        if existing:
            url = existing.get("url") or f"http://127.0.0.1:{args.port}/"
            print(f"XDL Web 已在运行: {url}", flush=True)
            if not args.no_open:
                webbrowser.open(url)
            return 0

    app = AppState(Path(args.state_file))
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
        try:
            while not app.stop.wait(0.5):
                pass
        except KeyboardInterrupt:
            pass
        finally:
            app.shutdown()
            server.shutdown()
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
