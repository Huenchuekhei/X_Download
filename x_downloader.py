#!/usr/bin/env python3
"""
X (Twitter) 博主媒体下载器
============================
输入一个 X/Twitter 博主主页链接，自动下载该博主的所有图片和视频。

依赖安装:
    pip install gallery-dl

注意: X/Twitter 需要登录才能查看内容，请配置 Cookie。

Cookie 配置方式 (三选一):
    1. 从浏览器直接读取 (推荐):
       python x_downloader.py https://x.com/username --cookies-from-browser chrome

    2. 使用 cookies.txt 文件:
       安装浏览器扩展 "Get cookies.txt LOCALLY" 导出 cookies.txt
       python x_downloader.py https://x.com/username --cookies cookies.txt

    3. 首次运行时自动检测当前目录下的 cookies.txt

使用示例:
    python x_downloader.py https://x.com/elonmusk
    python x_downloader.py https://x.com/username -o ./my_downloads
    python x_downloader.py https://x.com/username --media-only
    python x_downloader.py https://x.com/username --limit 50
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import shutil
import sqlite3
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# Windows 终端默认 GBK 编码，emoji 会报错；强制 UTF-8
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ============================================================
# 常量
# ============================================================

def _default_output_dir() -> str:
    env_dir = os.environ.get("XDL_OUTPUT_DIR", "").strip()
    if env_dir:
        return env_dir
    if sys.platform != "win32":
        return str(Path(__file__).resolve().parent / "downloads")
    return str(Path.home() / "OneDrive" / "文档" / "XDL")


DEFAULT_OUTPUT_DIR = _default_output_dir()
DEFAULT_PROXY = "socks5h://127.0.0.1:7890"
DEFAULT_PROXY_HOST = "127.0.0.1"
DEFAULT_PROXY_PORT = 7890
SUPPORTED_BROWSERS = ["chrome", "firefox", "edge", "safari", "opera", "brave", "chromium"]

XDL_HOME = Path(os.environ.get("XDL_HOME") or (Path.home() / ".xdl"))
QUEUE_FILE = XDL_HOME / "retry_queue.json"
ARCHIVE_FILE = XDL_HOME / "archive.sqlite3"
LEDGER_FILE = XDL_HOME / "downloads.sqlite3"
DEFAULT_STOP_AFTER_SKIPS = 20
LAST_ERROR = ""

THUMB_KEY_RE = re.compile(r"^[A-Za-z0-9_]{1,80}$")
VIDEO_EXTS = {".mp4", ".webm", ".m4v", ".mov", ".mkv"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}

MEDIA_FILE_RE = re.compile(
    r"^(?P<user>[A-Za-z0-9_]{1,15})_"
    r"(?P<date>\d{4}-\d{2}-\d{2})"
    r"(?:[ T]\d{2}[:_-]\d{2}[:_-]\d{2})?_"
    r"(?P<tweet>\d{15,20})_"
    r"(?P<num>\d+)\.(?P<ext>[A-Za-z0-9]+)$"
)
ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def clean_line(line: str) -> str:
    return ANSI_RE.sub("", line).replace("\r", "").strip()


def parse_duration_seconds(raw: str | int | float | None) -> int:
    """Parse duration like '60', '1m', '1m30s', '01:30' into integer seconds."""
    if raw is None:
        return 0
    if isinstance(raw, bool):
        raise ValueError(f"无效的时长参数: {raw}")
    if isinstance(raw, (int, float)):
        if raw < 0:
            raise ValueError("视频时长限制不能为负数")
        return int(round(float(raw)))

    text = str(raw).strip()
    if not text or text.lower() in {"0", "off", "none"}:
        return 0

    if re.fullmatch(r"\d+(?:\.\d+)?", text):
        return int(round(float(text)))

    if re.fullmatch(r"\d+(?::\d+){1,2}", text):
        parts = [int(p) for p in text.split(":")]
        if len(parts) == 2:
            minutes, secs = parts
            if secs >= 60:
                raise ValueError(f"无效的分秒格式: {raw}（秒数应小于 60）")
            return minutes * 60 + secs
        hours, minutes, secs = parts
        if minutes >= 60 or secs >= 60:
            raise ValueError(f"无效的时分秒格式: {raw}（分与秒应小于 60）")
        return hours * 3600 + minutes * 60 + secs

    compact = re.sub(r"\s+", "", text)
    match = re.fullmatch(
        r"(?:(?P<h>\d+(?:\.\d+)?)h)?(?:(?P<m>\d+(?:\.\d+)?)m)?(?:(?P<s>\d+(?:\.\d+)?)s)?",
        compact,
        re.I,
    )
    if match and any(match.group(k) is not None for k in ("h", "m", "s")):
        total = (
            float(match.group("h") or 0) * 3600
            + float(match.group("m") or 0) * 60
            + float(match.group("s") or 0)
        )
        return int(round(total))

    raise ValueError(
        f"无法解析时长 '{raw}'，支持秒数(60)、分秒(01:30)或单位格式(1m / 1m30s)"
    )


def format_duration(seconds: int) -> str:
    """Format duration seconds as '不限', 'MM:SS', or 'H:MM:SS'."""
    secs = max(0, int(seconds or 0))
    if secs <= 0:
        return "不限"
    if secs < 3600:
        minutes, rem = divmod(secs, 60)
        return f"{minutes:02d}:{rem:02d}"
    hours, rem = divmod(secs, 3600)
    minutes, rem = divmod(rem, 60)
    return f"{hours}:{minutes:02d}:{rem:02d}"


def normalize_media_type(value: str | None) -> str:
    """Normalize media type filter into 'all', 'videos', or 'images'."""
    token = str(value or "all").strip().lower()
    if token in {"video", "videos"}:
        return "videos"
    if token in {"image", "images", "photo", "photos"}:
        return "images"
    return "all"


def build_file_filter(
    media_type: str = "all",
    max_video_duration: int = 0,
) -> str | None:
    """Build a gallery-dl Python file-filter expression for media type & video duration."""
    kind = normalize_media_type(media_type)
    max_dur = max(0, int(max_video_duration or 0))

    if kind == "images":
        return "(locals().get('type') == 'photo' or extension in exts_image)"
    if kind == "videos":
        if max_dur > 0:
            return (
                "((locals().get('type') in ('video', 'animated_gif') "
                "or extension in exts_video) "
                f"and (locals().get('duration') or 0) <= {max_dur})"
            )
        return "(locals().get('type') in ('video', 'animated_gif') or extension in exts_video)"
    if max_dur > 0:
        return (
            "((locals().get('type') != 'video' and extension not in exts_video) "
            f"or (locals().get('duration') or 0) <= {max_dur})"
        )
    return None


def _db_connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path), timeout=60)
    con.execute("PRAGMA busy_timeout=60000")
    return con


def ensure_download_store() -> None:
    """Create gallery-dl's archive table and XDL's local media ledger."""
    with _db_connect(ARCHIVE_FILE) as con:
        try:
            con.execute(
                "CREATE TABLE IF NOT EXISTS archive "
                "(entry TEXT PRIMARY KEY) WITHOUT ROWID"
            )
        except sqlite3.OperationalError:
            con.execute(
                "CREATE TABLE IF NOT EXISTS archive "
                "(entry TEXT PRIMARY KEY)"
            )
    with _db_connect(LEDGER_FILE) as con:
        con.execute(
            "CREATE TABLE IF NOT EXISTS media ("
            "key TEXT PRIMARY KEY, "
            "username TEXT NOT NULL, "
            "tweet_id TEXT NOT NULL, "
            "media_index INTEGER NOT NULL, "
            "filename TEXT NOT NULL, "
            "path TEXT NOT NULL, "
            "size INTEGER NOT NULL DEFAULT 0, "
            "downloaded_at TEXT NOT NULL, "
            "source TEXT NOT NULL DEFAULT 'download')"
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_media_user "
            "ON media(username, downloaded_at)"
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_media_tweet "
            "ON media(tweet_id)"
        )
        existing_cols = {
            row[1] for row in con.execute("PRAGMA table_info(media)").fetchall()
        }
        migrations = [
            ("cloud_synced", "INTEGER NOT NULL DEFAULT 0"),
            ("cloud_file_id", "TEXT NOT NULL DEFAULT ''"),
            ("cloud_url", "TEXT NOT NULL DEFAULT ''"),
            ("cloud_remote", "TEXT NOT NULL DEFAULT ''"),
            ("local_deleted", "INTEGER NOT NULL DEFAULT 0"),
        ]
        for col_name, col_def in migrations:
            if col_name not in existing_cols:
                con.execute(f"ALTER TABLE media ADD COLUMN {col_name} {col_def}")
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_media_cloud "
            "ON media(cloud_synced, local_deleted)"
        )


def archive_entry(tweet_id: str, media_index: int, retweet_id: int = 0) -> str:
    return f"twitter{int(tweet_id)}_{int(retweet_id)}_{int(media_index)}"


def parse_media_name(
    filename: str,
    username: str | None = None,
) -> dict | None:
    match = MEDIA_FILE_RE.match(filename)
    if not match:
        return None
    data = match.groupdict()
    if username and data["user"].lower() != username.lower():
        return None
    data["num"] = int(data["num"])
    return data


def _write_media_rows(rows: list[tuple]) -> tuple[int, int]:
    """Insert archive entries and media rows. Returns (archive_added, ledger_added)."""
    if not rows:
        return 0, 0
    ensure_download_store()
    archive_added = 0
    ledger_added = 0
    with _db_connect(ARCHIVE_FILE) as con:
        before = con.total_changes
        con.executemany(
            "INSERT OR IGNORE INTO archive (entry) VALUES (?)",
            ((row[0],) for row in rows),
        )
        archive_added = con.total_changes - before
    with _db_connect(LEDGER_FILE) as con:
        before = con.total_changes
        con.executemany(
            "INSERT INTO media "
            "(key, username, tweet_id, media_index, filename, path, size, "
            " downloaded_at, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET "
            "path = excluded.path, size = excluded.size, local_deleted = 0",
            rows,
        )
        ledger_added = con.total_changes - before
    return archive_added, ledger_added


def mark_media_cloud_synced(entries: list[dict]) -> int:
    """Update or insert Google Drive sync metadata and local deletion state on media rows."""
    if not entries:
        return 0
    ensure_download_store()
    updated = 0
    archive_rows: list[tuple[str]] = []
    with _db_connect(LEDGER_FILE) as con:
        for entry in entries:
            filename = str(entry.get("filename") or "").strip()
            if not filename:
                continue
            username = str(entry.get("username") or "").strip().lstrip("@")
            file_id = str(entry.get("cloudFileId") or "").strip()
            cloud_url = str(entry.get("cloudUrl") or "").strip()
            cloud_remote = str(entry.get("cloudRemote") or "").strip()
            local_deleted = 1 if entry.get("localDeleted") else 0
            size = max(0, int(entry.get("size") or 0))
            if username:
                cur = con.execute(
                    "UPDATE media SET cloud_synced = 1, "
                    "size = CASE WHEN size = 0 AND ? > 0 THEN ? ELSE size END, "
                    "cloud_file_id = CASE WHEN ? != '' THEN ? ELSE cloud_file_id END, "
                    "cloud_url = CASE WHEN ? != '' THEN ? ELSE cloud_url END, "
                    "cloud_remote = CASE WHEN ? != '' THEN ? ELSE cloud_remote END, "
                    "local_deleted = ? "
                    "WHERE lower(username) = lower(?) AND filename = ?",
                    (
                        size, size,
                        file_id, file_id,
                        cloud_url, cloud_url,
                        cloud_remote, cloud_remote,
                        local_deleted,
                        username, filename,
                    ),
                )
            else:
                cur = con.execute(
                    "UPDATE media SET cloud_synced = 1, "
                    "size = CASE WHEN size = 0 AND ? > 0 THEN ? ELSE size END, "
                    "cloud_file_id = CASE WHEN ? != '' THEN ? ELSE cloud_file_id END, "
                    "cloud_url = CASE WHEN ? != '' THEN ? ELSE cloud_url END, "
                    "cloud_remote = CASE WHEN ? != '' THEN ? ELSE cloud_remote END, "
                    "local_deleted = ? "
                    "WHERE filename = ?",
                    (
                        size, size,
                        file_id, file_id,
                        cloud_url, cloud_url,
                        cloud_remote, cloud_remote,
                        local_deleted,
                        filename,
                    ),
                )
            if cur.rowcount and cur.rowcount > 0:
                updated += cur.rowcount
                continue

            parsed = parse_media_name(filename, username or None)
            if parsed is None:
                continue
            key = archive_entry(parsed["tweet"], parsed["num"])
            row_user = username or parsed["user"]
            downloaded_at = str(entry.get("downloadedAt") or "").strip() or f"{parsed['date']} 00:00:00"
            local_path = str(entry.get("localPath") or "").strip()
            con.execute(
                "INSERT INTO media "
                "(key, username, tweet_id, media_index, filename, path, size, "
                " downloaded_at, source, cloud_synced, cloud_file_id, cloud_url, "
                " cloud_remote, local_deleted) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'cloud', 1, ?, ?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET "
                "filename = excluded.filename, "
                "size = CASE WHEN media.size = 0 AND excluded.size > 0 THEN excluded.size ELSE media.size END, "
                "cloud_synced = 1, "
                "cloud_file_id = CASE WHEN excluded.cloud_file_id != '' THEN excluded.cloud_file_id ELSE media.cloud_file_id END, "
                "cloud_url = CASE WHEN excluded.cloud_url != '' THEN excluded.cloud_url ELSE media.cloud_url END, "
                "cloud_remote = CASE WHEN excluded.cloud_remote != '' THEN excluded.cloud_remote ELSE media.cloud_remote END, "
                "local_deleted = excluded.local_deleted",
                (
                    key,
                    row_user,
                    parsed["tweet"],
                    parsed["num"],
                    filename,
                    local_path,
                    size,
                    downloaded_at,
                    file_id,
                    cloud_url,
                    cloud_remote,
                    local_deleted,
                ),
            )
            archive_rows.append((key,))
            updated += 1
    if archive_rows:
        with _db_connect(ARCHIVE_FILE) as acon:
            acon.executemany(
                "INSERT OR IGNORE INTO archive (entry) VALUES (?)",
                archive_rows,
            )
    return updated


def mark_media_local_deleted(
    username: str | None = None,
    filename: str | None = None,
    remove_if_unsynced: bool = True,
) -> int:
    """Mark cloud-synced media as locally deleted, or remove unsynced rows from ledger."""
    ensure_download_store()
    clauses = ["1=1"]
    params: list = []
    if username:
        clauses.append("lower(username) = lower(?)")
        params.append(username.lstrip("@"))
    if filename:
        clauses.append("filename = ?")
        params.append(filename)
    where_sql = " AND ".join(clauses)
    affected = 0
    deleted_keys: list[str] = []
    with _db_connect(LEDGER_FILE) as con:
        cur1 = con.execute(
            f"UPDATE media SET local_deleted = 1 WHERE cloud_synced = 1 AND {where_sql}",
            params,
        )
        affected += cur1.rowcount or 0
        if remove_if_unsynced:
            deleted_keys = [
                str(r[0])
                for r in con.execute(
                    f"SELECT key FROM media WHERE cloud_synced = 0 AND {where_sql}",
                    params,
                ).fetchall()
                if r and r[0]
            ]
            cur2 = con.execute(
                f"DELETE FROM media WHERE cloud_synced = 0 AND {where_sql}",
                params,
            )
            affected += cur2.rowcount or 0
    for key in deleted_keys:
        delete_thumb_for_key(key)
    return affected


def backfill_directory(
    directory: Path | str,
    username: str | None = None,
) -> dict:
    """Mark existing complete media files as downloaded.

    Only files whose name still carries the tweet ID and media index are
    trusted. Anything uncertain is left unmarked so it can be downloaded
    again instead of being skipped by mistake.
    """
    directory = Path(directory)
    username = username or directory.name
    if not directory.is_dir():
        return {"user": username, "files": 0, "media": 0, "added": 0}
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    rows: list[tuple] = []
    seen: set[str] = set()
    found = 0
    for path in directory.rglob("*"):
        if not path.is_file() or path.suffix.lower() in {".part", ".tmp"}:
            continue
        parsed = parse_media_name(path.name, username)
        if parsed is None:
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size <= 0:
            continue
        key = archive_entry(parsed["tweet"], parsed["num"])
        if key in seen:
            continue
        seen.add(key)
        found += 1
        rows.append((
            key,
            username,
            parsed["tweet"],
            parsed["num"],
            path.name,
            str(path),
            size,
            now,
            "existing",
        ))
    archive_added, ledger_added = _write_media_rows(rows)
    if rows and shutil.which("ffmpeg"):
        for row in rows:
            try:
                if thumb_path_for_key(row[0]) is None:
                    generate_thumbnail(row[5], row[0])
            except Exception:
                pass
    return {
        "user": username,
        "files": found,
        "media": len(rows),
        "added": ledger_added,
        "archiveAdded": archive_added,
    }


def backfill_output(output_dir: Path | str) -> dict:
    """Backfill every per-user folder under an XDL output directory."""
    output_dir = Path(output_dir)
    result = {"users": 0, "files": 0, "media": 0, "added": 0}
    if not output_dir.is_dir():
        return result
    for child in output_dir.iterdir():
        if not child.is_dir():
            continue
        if not re.fullmatch(r"[A-Za-z0-9_]{1,15}", child.name):
            continue
        item = backfill_directory(child, child.name)
        result["users"] += 1
        result["files"] += item["files"]
        result["media"] += item["media"]
        result["added"] += item["added"]
    return result


def snapshot_file_map(directory: Path | str) -> dict[str, tuple[int, int]]:
    directory = Path(directory)
    result: dict[str, tuple[int, int]] = {}
    if not directory.is_dir():
        return result
    for path in directory.rglob("*"):
        if not path.is_file():
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        result[str(path)] = (stat.st_size, stat.st_mtime_ns)
    return result


def collect_new_files(
    directory: Path | str,
    username: str,
    before: dict[str, tuple[int, int]],
    source: str = "download",
) -> list[dict]:
    """Record media downloaded since the snapshot and return their details."""
    directory = Path(directory)
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    rows: list[tuple] = []
    new_files: list[dict] = []
    if not directory.is_dir():
        return new_files
    for path in directory.rglob("*"):
        if not path.is_file() or path.suffix.lower() in {".part", ".tmp"}:
            continue
        parsed = parse_media_name(path.name, username)
        if parsed is None:
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        if stat.st_size <= 0:
            continue
        old = before.get(str(path))
        if old and old == (stat.st_size, stat.st_mtime_ns):
            continue
        key = archive_entry(parsed["tweet"], parsed["num"])
        rows.append((
            key,
            username,
            parsed["tweet"],
            parsed["num"],
            path.name,
            str(path),
            stat.st_size,
            now,
            source,
        ))
        new_files.append({
            "tweetId": parsed["tweet"],
            "mediaIndex": parsed["num"],
            "filename": path.name,
            "path": str(path),
            "size": stat.st_size,
            "extension": parsed["ext"],
        })
    _write_media_rows(rows)
    if new_files and shutil.which("ffmpeg"):
        for item in new_files:
            try:
                generate_thumbnail(
                    item["path"],
                    archive_entry(item["tweetId"], item["mediaIndex"]),
                )
            except Exception:
                pass
    return new_files


def count_skipped_from_log(text: str) -> int:
    """Count gallery-dl's output lines that mean an item already existed."""
    seen: set[str] = set()
    for raw in text.splitlines():
        line = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", raw).strip()
        if not line:
            continue
        match = re.match(r"^#\s+(.+)$", line)
        if match:
            seen.add(match.group(1).strip())
            continue
        if re.search(r"already\s+exists|in\s+archive|skipping", line, re.I):
            seen.add(line)
    return len(seen)


def _is_local_file_present(path_str: str, local_deleted: bool) -> bool:
    if local_deleted or not path_str:
        return False
    try:
        return Path(path_str).is_file()
    except OSError:
        return False


def get_thumbs_dir() -> Path:
    """Return the local micro-thumbnail directory derived dynamically from XDL_HOME."""
    return XDL_HOME / "thumbs"


def thumb_path_for_key(key: str) -> Path | None:
    """Return existing non-empty thumbnail Path (.webp or .jpg) for a validated archive key."""
    if not key or not THUMB_KEY_RE.fullmatch(str(key)):
        return None
    thumbs_dir = get_thumbs_dir()
    for ext in (".webp", ".jpg"):
        candidate = thumbs_dir / f"{key}{ext}"
        try:
            if candidate.is_file() and candidate.stat().st_size > 0:
                return candidate
        except OSError:
            continue
    return None


def delete_thumb_for_key(key: str) -> None:
    """Remove any cached thumbnail file for the given key."""
    if not key or not THUMB_KEY_RE.fullmatch(str(key)):
        return
    thumbs_dir = get_thumbs_dir()
    for ext in (".webp", ".jpg"):
        try:
            (thumbs_dir / f"{key}{ext}").unlink(missing_ok=True)
        except OSError:
            pass


def _run_ffmpeg_thumb(
    ffmpeg_bin: str,
    src: Path,
    tmp_out: Path,
    *,
    is_video: bool,
    seek_sec: int,
    codec: str,
) -> bool:
    cmd = [ffmpeg_bin, "-hide_banner", "-loglevel", "error", "-y"]
    if is_video and seek_sec > 0:
        cmd.extend(["-ss", f"00:00:{seek_sec:02d}"])
    cmd.extend(["-i", str(src), "-frames:v", "1"])
    if codec == "webp":
        cmd.extend([
            "-vf", "scale='min(480,iw)':-2",
            "-c:v", "libwebp",
            "-quality", "75",
            "-compression_level", "4",
        ])
    else:
        cmd.extend([
            "-vf", "scale='min(480,iw)':-2,format=yuvj420p",
            "-c:v", "mjpeg",
            "-q:v", "5",
        ])
    cmd.append(str(tmp_out))
    timeout = 20 if is_video else 15
    try:
        res = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
        if res.returncode != 0 or not tmp_out.is_file() or tmp_out.stat().st_size < 32:
            return False
        with tmp_out.open("rb") as fh:
            hdr = fh.read(12)
        if codec == "webp":
            return len(hdr) >= 12 and hdr[:4] == b"RIFF" and hdr[8:12] == b"WEBP"
        return len(hdr) >= 3 and hdr[:2] == b"\xff\xd8"
    except Exception:
        return False


def generate_thumbnail(
    src_path: Path | str,
    key: str,
    *,
    force: bool = False,
) -> Path | None:
    """Generate a compressed <=480px WebP (or fallback JPEG) micro-thumbnail."""
    if not key or not THUMB_KEY_RE.fullmatch(str(key)):
        return None
    if not force:
        existing = thumb_path_for_key(key)
        if existing is not None:
            return existing
    src = Path(src_path)
    try:
        if not src.is_file() or src.stat().st_size <= 0:
            return None
    except OSError:
        return None
    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin:
        return None

    ext = src.suffix.lower()
    if ext not in VIDEO_EXTS and ext not in IMAGE_EXTS:
        return None
    is_video = ext in VIDEO_EXTS
    thumbs_dir = get_thumbs_dir()
    thumbs_dir.mkdir(parents=True, exist_ok=True)

    attempts: list[tuple[str, int]] = []
    if is_video:
        attempts.extend([("webp", 1), ("webp", 0), ("jpg", 1), ("jpg", 0)])
    else:
        attempts.extend([("webp", 0), ("jpg", 0)])

    for codec, seek_sec in attempts:
        suffix = ".webp" if codec == "webp" else ".jpg"
        dest = thumbs_dir / f"{key}{suffix}"
        tmp = thumbs_dir / f".tmp_{key}_{os.getpid()}_{time.time_ns()}{suffix}"
        try:
            if _run_ffmpeg_thumb(
                ffmpeg_bin, src, tmp, is_video=is_video, seek_sec=seek_sec, codec=codec
            ):
                os.replace(tmp, dest)
                other_ext = ".jpg" if suffix == ".webp" else ".webp"
                try:
                    (thumbs_dir / f"{key}{other_ext}").unlink(missing_ok=True)
                except OSError:
                    pass
                return dest
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
    return None


def ensure_thumbs_for_paths(
    paths: list[Path],
    username: str | None = None,
) -> int:
    """Ensure local micro-thumbnails exist for matching local media files."""
    if not paths:
        return 0
    ready = 0
    for path in paths:
        parsed = parse_media_name(Path(path).name, username)
        if parsed is None:
            continue
        key = archive_entry(parsed["tweet"], parsed["num"])
        try:
            if generate_thumbnail(path, key) is not None:
                ready += 1
        except Exception:
            continue
    return ready


def list_media_missing_thumbs(username: str | None = None) -> list[dict]:
    """Return media ledger rows that do not currently have a cached local thumbnail."""
    ensure_download_store()
    sql = [
        "SELECT key, username, filename, path, cloud_file_id, cloud_remote, cloud_synced, local_deleted",
        "FROM media",
    ]
    params: list = []
    if username:
        sql.append("WHERE lower(username) = lower(?)")
        params.append(username.lstrip("@"))
    sql.append("ORDER BY downloaded_at DESC, rowid DESC")
    with _db_connect(LEDGER_FILE) as con:
        rows = con.execute(" ".join(sql), params).fetchall()
    missing: list[dict] = []
    for row in rows:
        key = str(row[0] or "")
        if not THUMB_KEY_RE.fullmatch(key):
            continue
        if thumb_path_for_key(key) is not None:
            continue
        missing.append({
            "key": key,
            "username": row[1] or "",
            "filename": row[2] or "",
            "path": row[3] or "",
            "cloudFileId": row[4] or "",
            "cloudRemote": row[5] or "",
            "cloudSynced": bool(row[6]),
            "localDeleted": bool(row[7]),
        })
    return missing


def thumbs_summary() -> dict:
    """Summarize cached micro-thumbnails count, total bytes, and missing rows."""
    thumbs_dir = get_thumbs_dir()
    count = 0
    total_bytes = 0
    if thumbs_dir.is_dir():
        for entry in thumbs_dir.iterdir():
            if not entry.is_file() or entry.name.startswith("."):
                continue
            if entry.suffix.lower() not in {".webp", ".jpg"}:
                continue
            try:
                sz = entry.stat().st_size
            except OSError:
                continue
            if sz > 0:
                count += 1
                total_bytes += sz
    try:
        missing = len(list_media_missing_thumbs())
    except Exception:
        missing = 0
    return {
        "count": count,
        "bytes": total_bytes,
        "missing": missing,
        "ffmpeg": bool(shutil.which("ffmpeg")),
    }


def recent_media(username: str, limit: int = 8) -> list[dict]:
    ensure_download_store()
    with _db_connect(LEDGER_FILE) as con:
        cursor = con.execute(
            "SELECT key, tweet_id, media_index, filename, path, size, "
            "       downloaded_at, source, cloud_synced, cloud_file_id, "
            "       cloud_url, cloud_remote, local_deleted "
            "FROM media WHERE lower(username)=lower(?) "
            "ORDER BY downloaded_at DESC, rowid DESC LIMIT ?",
            (username, max(1, min(200, int(limit)))),
        )
        rows = cursor.fetchall()
    result: list[dict] = []
    for row in rows:
        key = str(row[0] or "")
        has_thumb = thumb_path_for_key(key) is not None
        result.append({
            "key": key,
            "tweetId": row[1],
            "mediaIndex": row[2],
            "filename": row[3],
            "path": row[4],
            "size": row[5],
            "downloadedAt": row[6],
            "source": row[7],
            "cloudSynced": bool(row[8]),
            "cloudFileId": row[9] or "",
            "cloudUrl": row[10] or "",
            "cloudRemote": row[11] or "",
            "localDeleted": bool(row[12]),
            "localExists": _is_local_file_present(row[4] or "", bool(row[12])),
            "hasThumb": has_thumb,
            "thumbUrl": f"/api/media/thumb?key={key}" if has_thumb else "",
        })
    return result


def list_media(
    username: str | None = None,
    limit: int = 500,
    offset: int = 0,
    kind: str | None = None,
    keyword: str | None = None,
    storage: str | None = None,
) -> list[dict]:
    """Return downloaded media across all users, newest first."""
    ensure_download_store()
    sql = [
        "SELECT key, username, tweet_id, media_index, filename, path, size,",
        "       downloaded_at, source, cloud_synced, cloud_file_id,",
        "       cloud_url, cloud_remote, local_deleted FROM media WHERE 1=1",
    ]
    params: list = []
    if username:
        sql.append("AND lower(username)=lower(?)")
        params.append(username)
    if kind == "images":
        sql.append("AND lower(filename) NOT LIKE '%.mp4'")
        sql.append("AND lower(filename) NOT LIKE '%.webm'")
        sql.append("AND lower(filename) NOT LIKE '%.m4v'")
    elif kind == "videos":
        sql.append("AND (lower(filename) LIKE '%.mp4'")
        sql.append("     OR lower(filename) LIKE '%.webm'")
        sql.append("     OR lower(filename) LIKE '%.m4v')")
    if storage == "local":
        sql.append("AND local_deleted = 0")
    elif storage == "cloud":
        sql.append("AND cloud_synced = 1")
    if keyword:
        sql.append("AND (lower(filename) LIKE ? OR lower(username) LIKE ?)")
        like = f"%{keyword.lower()}%"
        params.extend([like, like])
    sql.append("ORDER BY downloaded_at DESC, rowid DESC LIMIT ? OFFSET ?")
    params.extend([max(1, min(5000, int(limit))), max(0, int(offset))])

    with _db_connect(LEDGER_FILE) as con:
        rows = con.execute(" ".join(sql), params).fetchall()
    result: list[dict] = []
    for row in rows:
        key = str(row[0] or "")
        has_thumb = thumb_path_for_key(key) is not None
        result.append({
            "key": key,
            "username": row[1],
            "tweetId": row[2],
            "mediaIndex": row[3],
            "filename": row[4],
            "path": row[5],
            "size": row[6],
            "downloadedAt": row[7],
            "source": row[8],
            "cloudSynced": bool(row[9]),
            "cloudFileId": row[10] or "",
            "cloudUrl": row[11] or "",
            "cloudRemote": row[12] or "",
            "localDeleted": bool(row[13]),
            "localExists": _is_local_file_present(row[5] or "", bool(row[13])),
            "hasThumb": has_thumb,
            "thumbUrl": f"/api/media/thumb?key={key}" if has_thumb else "",
        })
    return result


def media_kind_counts() -> dict:
    """Count downloaded media by kind in one pass."""
    ensure_download_store()
    video_sql = (
        "SELECT COUNT(*) FROM media WHERE lower(filename) LIKE '%.mp4' "
        "OR lower(filename) LIKE '%.webm' OR lower(filename) LIKE '%.m4v'"
    )
    with _db_connect(LEDGER_FILE) as con:
        total = con.execute("SELECT COUNT(*) FROM media").fetchone()[0]
        videos = con.execute(video_sql).fetchone()[0]
        local = con.execute("SELECT COUNT(*) FROM media WHERE local_deleted = 0").fetchone()[0]
        cloud = con.execute("SELECT COUNT(*) FROM media WHERE cloud_synced = 1").fetchone()[0]
    return {
        "all": total,
        "videos": videos,
        "images": total - videos,
        "local": local,
        "cloud": cloud,
    }


def media_summary(username: str | None = None) -> dict:
    ensure_download_store()
    with _db_connect(LEDGER_FILE) as con:
        if username:
            row = con.execute(
                "SELECT COUNT(*), COALESCE(SUM(size), 0), "
                "       COUNT(DISTINCT tweet_id) "
                "FROM media WHERE lower(username)=lower(?)",
                (username,),
            ).fetchone()
        else:
            row = con.execute(
                "SELECT COUNT(*), COALESCE(SUM(size), 0), "
                "       COUNT(DISTINCT tweet_id) FROM media"
            ).fetchone()
    return {"media": row[0], "bytes": row[1], "tweets": row[2]}


def media_count(username: str) -> int:
    return int(media_summary(username)["media"])


def cloud_media_summary() -> dict:
    """Return total count and bytes for media rows synced to Google Drive."""
    ensure_download_store()
    with _db_connect(LEDGER_FILE) as con:
        row = con.execute(
            "SELECT COUNT(*), COALESCE(SUM(size), 0) "
            "FROM media WHERE cloud_synced = 1"
        ).fetchone()
    return {"files": int(row[0] or 0), "bytes": int(row[1] or 0)}


# ============================================================
# 工具函数
# ============================================================

def extract_username(url: str) -> str:
    """从 X/Twitter URL 或 @用户名 中提取用户名。

    支持格式:
        https://x.com/username
        https://twitter.com/username
        https://x.com/username/media
        https://x.com/username/status/123456
        @username / username
    """
    raw = url.strip().rstrip("/")
    pattern = r"(?:x\.com|twitter\.com)/([A-Za-z0-9_]{1,15})"
    match = re.search(pattern, raw, re.I)
    if match:
        return match.group(1)
    bare = raw.lstrip("@")
    if re.fullmatch(r"[A-Za-z0-9_]{1,15}", bare):
        return bare
    raise ValueError(
        f"无法从输入中提取用户名: {url}\n"
        f"格式应为: https://x.com/用户名 或 @用户名"
    )


def check_gallery_dl() -> bool:
    """检查 gallery-dl 是否可用。"""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "gallery_dl", "--version"],
            capture_output=True, text=True, timeout=10
        )
        return result.returncode == 0
    except Exception:
        return False


def read_log_tail(path: Path, limit: int = 800) -> str:
    """Read the most relevant error lines from a gallery-dl log file."""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return ""
    picked = [
        line.strip() for line in lines
        if "error" in line.lower() or "fail" in line.lower()
    ]
    if not picked:
        picked = [line.strip() for line in lines if line.strip()][-5:]
    text = " | ".join(picked[-6:])
    text = re.sub(r"\x1b\[[0-9;]*m", "", text)
    return text[-limit:]


def default_proxy_available() -> bool:
    """检查默认代理端口是否在监听。"""
    try:
        with socket.create_connection((DEFAULT_PROXY_HOST, DEFAULT_PROXY_PORT), timeout=0.5):
            return True
    except OSError:
        return False


def install_gallery_dl() -> bool:
    """安装 gallery-dl。"""
    print("📦 正在安装 gallery-dl ...")
    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "gallery-dl"],
            stdout=sys.stdout, stderr=sys.stderr
        )
        print("✅ gallery-dl 安装成功!\n")
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ gallery-dl 安装失败: {e}")
        print("请手动安装: pip install gallery-dl")
        return False


def find_cookies_file() -> str | None:
    """在常见位置查找 cookies.txt 文件。"""
    search_paths = [
        XDL_HOME / "cookies.txt",
        Path.cwd() / "cookies.txt",
        Path.cwd() / "x_cookies.txt",
        Path.cwd() / "twitter_cookies.txt",
        Path.home() / "Downloads" / "cookies.txt",
    ]
    for path in search_paths:
        if path.exists():
            return str(path)
    return None


def setup_cookies_config(args) -> str | None:
    """配置 Cookie 认证，返回配置字符串或 None。"""
    # 从浏览器读取
    if args.cookies_from_browser:
        browser = args.cookies_from_browser.lower()
        if browser not in SUPPORTED_BROWSERS:
            print(f"⚠️  不支持的浏览器: {browser}")
            print(f"   支持的浏览器: {', '.join(SUPPORTED_BROWSERS)}")
            browser = "chrome"
        return f"cookies-from-browser:{browser}"

    # 指定的 cookies 文件
    if args.cookies:
        if not Path(args.cookies).exists():
            print(f"❌ Cookie 文件不存在: {args.cookies}")
            return None
        return str(Path(args.cookies).absolute())

    # 自动查找
    auto = find_cookies_file()
    if auto:
        print(f"🔍 自动找到 Cookie 文件: {auto}")
        return auto

    # 未找到 - 打印帮助
    print("\n⚠️  未找到 Cookie 配置!")
    print("   X/Twitter 需要登录才能查看内容。请选择以下方式之一:\n")
    print("   方式1: 从浏览器直接读取 (推荐)")
    print(f"     python {sys.argv[0]} URL --cookies-from-browser chrome\n")
    print("   方式2: 导出 cookies.txt 文件")
    print("     1. 安装浏览器扩展 'Get cookies.txt LOCALLY'")
    print("     2. 登录 x.com")
    print("     3. 点击扩展图标导出 cookies.txt")
    print(f"     4. 运行: python {sys.argv[0]} URL --cookies cookies.txt\n")
    return None


# ============================================================
# 核心下载
# ============================================================

def build_config(username: str, output_dir: str, cookies_config: str | None, args) -> dict:
    """构建 gallery-dl 的 JSON 配置。"""
    ensure_download_store()
    incremental = bool(getattr(args, "incremental", True))
    stop_after = max(0, int(getattr(args, "stop_after_skips", DEFAULT_STOP_AFTER_SKIPS) or 0))
    if not incremental:
        skip_value = False
    elif stop_after > 0:
        skip_value = f"abort:{stop_after}"
    else:
        skip_value = True

    extractor_opts = {
        "filename": f"{username}_{{date}}_{{tweet_id}}_{{num:>03}}.{{extension}}",
        "directory": [],
        "retries": 3,
        "videos": True,
        "cards": False,
        "quoted": False,
        "conversations": False,
        "pinned": True,
        "replies": True,
        "retweets": False,
        "text-tweets": False,
    }

    media_type = normalize_media_type(getattr(args, "media_type", "all"))
    max_duration = max(0, int(getattr(args, "max_duration", 0) or 0))
    file_filter = build_file_filter(media_type, max_duration)
    if file_filter:
        extractor_opts["file-filter"] = file_filter

    # Cookie
    if cookies_config:
        if cookies_config.startswith("cookies-from-browser:"):
            browser = cookies_config.split(":", 1)[1]
            extractor_opts["cookies-from-browser"] = browser
        else:
            extractor_opts["cookies"] = cookies_config

    config = {
        "extractor": {
            "twitter": extractor_opts
        },
        "downloader": {
            "rate": "4M",
            "retries": 3,
            "part": True,
        },
        "output": {
            "mode": "terminal",
            "shorten": True,
            "skip": skip_value,
        },
        "skip": skip_value,
    }
    if incremental:
        config["archive"] = str(ARCHIVE_FILE)
    return config


@contextlib.contextmanager
def queue_lock(timeout: float = 5.0):
    QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
    lock_path = QUEUE_FILE.with_suffix(".lock")
    deadline = time.time() + timeout
    handle = None
    while handle is None:
        try:
            handle = os.open(
                lock_path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            )
        except FileExistsError:
            if time.time() > deadline:
                try:
                    lock_path.unlink()
                except OSError:
                    pass
                deadline = time.time() + timeout
            time.sleep(0.05)
    try:
        yield
    finally:
        os.close(handle)
        try:
            lock_path.unlink()
        except OSError:
            pass


def load_retry_queue() -> dict:
    if not QUEUE_FILE.exists():
        return {"version": 1, "items": []}
    try:
        data = json.loads(QUEUE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"version": 1, "items": []}
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        return {"version": 1, "items": []}
    return data


def save_retry_queue(data: dict) -> None:
    QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = QUEUE_FILE.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    os.replace(tmp, QUEUE_FILE)


def queue_key(username: str, output_dir: str) -> str:
    resolved = str(Path(output_dir).resolve()).lower()
    return f"{username.lower()}|{resolved}"


def record_failure(username: str, output_dir: str, args, error: str) -> None:
    media_type = normalize_media_type(getattr(args, "media_type", "all"))
    max_duration = max(0, int(getattr(args, "max_duration", 0) or 0))
    with queue_lock():
        data = load_retry_queue()
        key = queue_key(username, output_dir)
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        for item in data["items"]:
            if item.get("key") == key:
                item["attempts"] = int(item.get("attempts", 1)) + 1
                item["last_error"] = error[:800]
                item["last_attempt"] = now
                item["limit"] = args.limit or 0
                item["media_only"] = bool(args.media_only)
                item["media_type"] = media_type
                item["max_duration"] = max_duration
                break
        else:
            data["items"].append({
                "key": key,
                "username": username,
                "url": f"https://x.com/{username}" + (
                    "/media" if args.media_only else ""
                ),
                "output": str(Path(output_dir).resolve()),
                "media_only": bool(args.media_only),
                "limit": args.limit or 0,
                "media_type": media_type,
                "max_duration": max_duration,
                "attempts": 1,
                "added_at": now,
                "last_attempt": now,
                "last_error": error[:800],
            })
        save_retry_queue(data)


def remove_from_queue(username: str, output_dir: str) -> bool:
    with queue_lock():
        data = load_retry_queue()
        key = queue_key(username, output_dir)
        before = len(data["items"])
        data["items"] = [
            item for item in data["items"] if item.get("key") != key
        ]
        if len(data["items"]) != before:
            save_retry_queue(data)
            return True
        return False


def print_retry_queue() -> None:
    items = load_retry_queue()["items"]
    print(f"\n失败队列: {len(items)} 项  ({QUEUE_FILE})")
    if not items:
        print("  队列为空。")
        return
    for index, item in enumerate(items, 1):
        print(
            f"  {index}. @{item.get('username')}  "
            f"尝试 {item.get('attempts', 1)} 次  "
            f"最近 {item.get('last_attempt', '-')}"
        )
        print(f"     输出: {item.get('output')}")
        error = (item.get("last_error") or "").strip().replace("\n", " | ")
        if error:
            print(f"     错误: {error[:300]}")
    print("\n重试全部: xdl --retry")
    print("清空队列: xdl --clear-queue")


def clear_retry_queue() -> None:
    with queue_lock():
        if QUEUE_FILE.exists():
            QUEUE_FILE.unlink()
    print("失败队列已清空。")


def download_with_queue(url: str, args) -> bool:
    global LAST_ERROR
    LAST_ERROR = ""
    username = extract_username(url)
    success = download_media(
        url=url,
        output_dir=args.output,
        cookies_config=args.cookies_config,
        args=args,
    )
    if success:
        if remove_from_queue(username, args.output):
            print(f"已从失败队列移除 @{username}")
    else:
        record_failure(
            username,
            args.output,
            args,
            LAST_ERROR or "gallery-dl 退出码非 0",
        )
        print(f"\n已加入失败队列: @{username}")
        print("重试全部: xdl --retry")
    return success


def retry_all(args, only: str | None = None) -> int:
    items = list(load_retry_queue()["items"])
    if only:
        wanted = only.lower().lstrip("@")
        items = [
            item for item in items
            if str(item.get("username", "")).lower() == wanted
        ]
        if not items:
            print(f"失败队列里没有 @{wanted}")
            return 1
    if not items:
        print("失败队列为空，没有需要重试的任务。")
        return 0

    print(f"开始重试 {len(items)} 个失败账号...\n")
    succeeded = 0
    failed = 0
    for index, item in enumerate(items, 1):
        print(
            f"[{index}/{len(items)}] @{item.get('username')}  "
            f"(之前失败 {item.get('attempts', 1)} 次)"
        )
        cli_media_type = normalize_media_type(getattr(args, "media_type", "all"))
        item_media_type = normalize_media_type(item.get("media_type"))
        cli_max_dur = max(0, int(getattr(args, "max_duration", 0) or 0))
        item_max_dur = max(0, int(item.get("max_duration") or 0))
        retry_args = argparse.Namespace(
            output=item.get("output") or args.output,
            proxy=args.proxy,
            no_proxy=args.no_proxy,
            media_only=bool(item.get("media_only")),
            limit=item.get("limit") or None,
            cookies=args.cookies,
            cookies_from_browser=args.cookies_from_browser,
            cookies_config=args.cookies_config,
            incremental=args.incremental,
            stop_after_skips=args.stop_after_skips,
            media_type=cli_media_type if cli_media_type != "all" else item_media_type,
            max_duration=cli_max_dur if cli_max_dur > 0 else item_max_dur,
        )
        url = item.get("url") or f"https://x.com/{item.get('username')}"
        if download_with_queue(url, retry_args):
            succeeded += 1
        else:
            failed += 1
        print()

    print(f"重试结束: 成功 {succeeded}，仍失败 {failed}")
    if failed:
        print("仍失败的项保留在队列里，可稍后再运行 xdl --retry。")
        print_retry_queue()
    else:
        print("失败队列已清空。")
    return 0 if failed == 0 else 1


def download_media(url: str, output_dir: str, cookies_config: str | None, args) -> bool:
    """使用 gallery-dl 下载指定博主的全部图片和视频。"""
    username = extract_username(url)
    media_path = Path(output_dir).resolve() / username
    incremental = bool(getattr(args, "incremental", True))

    print(f"👤 博主: @{username}")
    print(f"📁 输出目录: {media_path}")
    if args.proxy:
        print(f"🌐 代理: {args.proxy}")
    media_type = normalize_media_type(getattr(args, "media_type", "all"))
    max_duration = max(0, int(getattr(args, "max_duration", 0) or 0))
    if media_type == "videos":
        print("🎞️  媒体筛选: 仅视频")
    elif media_type == "images":
        print("🖼️  媒体筛选: 仅图片")
    if max_duration > 0 and media_type != "images":
        print(f"⏱️  视频时长限制: ≤ {format_duration(max_duration)} ({max_duration} 秒)")
    if incremental:
        ensure_download_store()
        filled = backfill_directory(media_path, username)
        if filled["files"]:
            print(
                f"🗂️  已把 {filled['files']} 个现有文件标记为已下载"
                f"（新增档案 {filled['archiveAdded']} 条）"
            )
    before = snapshot_file_map(media_path)

    # 写入临时配置
    config = build_config(username, output_dir, cookies_config, args)
    config_path = Path(tempfile.gettempdir()) / f"gdl_{username}.json"
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)

    # 目标 URL
    target_url = f"https://x.com/{username}"
    if args.media_only:
        target_url += "/media"

    print(f"🌐 目标: {target_url}")
    print("🚀 开始下载...\n")

    start_time = time.time()
    log_path = Path(tempfile.gettempdir()) / f"gdl_log_{username}_{os.getpid()}.txt"

    try:
        media_dir = str(media_path)
        cmd = [
            sys.executable, "-m", "gallery_dl",
            "--config", str(config_path),
            "--write-log", str(log_path),
            "-d", media_dir,
        ]
        if args.proxy:
            cmd.extend(["--proxy", args.proxy])
        if args.limit:
            cmd.extend(["--range", f"1-{args.limit}"])

        cmd.append(target_url)

        lines: list[str] = []
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
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
        if process.stdout:
            for line in process.stdout:
                print(line, end="")
                stripped = clean_line(line)
                if stripped:
                    lines.append(stripped)
                    if len(lines) > 4000:
                        lines = lines[-2500:]
        returncode = process.wait()

        global LAST_ERROR
        if returncode != 0:
            LAST_ERROR = (
                read_log_tail(log_path)
                or f"gallery-dl 退出码 {returncode}"
            )
        try:
            log_text = log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            log_text = ""
        skipped = count_skipped_from_log(
            "\n".join(lines) + "\n" + log_text
        )
        new_files = collect_new_files(media_path, username, before)
        summary = media_summary(username)

        elapsed = time.time() - start_time

        if returncode == 0:
            print(f"\n{'='*50}")
            print(f"✅ 下载完成! (耗时 {elapsed:.1f} 秒)")

            # 统计：本次新增与目录总量分开，避免把跳过误读成又下载了一遍
            image_exts = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
            video_exts = {".mp4", ".mov", ".webm", ".avi", ".mkv"}
            new_images = sum(
                1 for item in new_files
                if Path(item["filename"]).suffix.lower() in image_exts
            )
            new_videos = sum(
                1 for item in new_files
                if Path(item["filename"]).suffix.lower() in video_exts
            )
            print(
                f"📊 本次新增: {new_images} 张图片, {new_videos} 个视频"
            )
            if media_path.exists():
                files = [f for f in media_path.rglob("*") if f.is_file()]
                total_images = sum(
                    1 for f in files if f.suffix.lower() in image_exts
                )
                total_videos = sum(
                    1 for f in files if f.suffix.lower() in video_exts
                )
                print(
                    f"📁 目录共 {len(files)} 个文件"
                    f"（{total_images} 张图片, {total_videos} 个视频）"
                )
            print(
                f"🆕 本次新增 {len(new_files)} 个 · 跳过 {skipped} 个 · "
                f"已标记 {summary['media']} 个媒体（{summary['tweets']} 条推文）"
            )
            if incremental:
                print(f"🗂️  下载档案: {ARCHIVE_FILE}")
            print(f"{'='*50}")
            return True
        else:
            print(f"\n❌ 下载失败 (退出码: {returncode})")
            return False

    except KeyboardInterrupt:
        print("\n⚠️  下载已中断")
        return False
    except Exception as e:
        print(f"\n❌ 下载出错: {e}")
        return False
    finally:
        if config_path.exists():
            config_path.unlink()
        try:
            log_path.unlink(missing_ok=True)
        except Exception:
            pass


# ============================================================
# 主入口
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="X (Twitter) 博主媒体下载器",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s elonmusk
  %(prog)s elonmusk --limit 100
  %(prog)s elonmusk --videos-only --max-duration 60
  %(prog)s elonmusk --max-duration 01:00
  %(prog)s --retry
  %(prog)s --status
  %(prog)s --clear-queue
        """,
    )
    parser.add_argument("url", nargs="?",
                        help="X/Twitter 博主主页 URL 或用户名")
    parser.add_argument("-o", "--output", default=DEFAULT_OUTPUT_DIR,
                        help=f"输出目录 (默认: {DEFAULT_OUTPUT_DIR})")
    parser.add_argument("-c", "--cookies", help="cookies.txt 文件路径")
    parser.add_argument("--cookies-from-browser",
                        help=f"从浏览器读取 Cookie ({', '.join(SUPPORTED_BROWSERS)})")
    parser.add_argument("--media-only", action="store_true",
                        help="只下载含媒体的推文页面")
    parser.add_argument("--limit", type=int, help="限制下载的推文数量")
    parser.add_argument("--max-duration", default="0", metavar="SECONDS|MM:SS|1m",
                        help="限制单条视频最长时长，例如 60、1m、01:30；0 表示不限")
    parser.add_argument("--media-type", choices=["all", "videos", "images"],
                        default="all", help="筛选下载的媒体类型 (默认: all)")
    parser.add_argument("--videos-only", action="store_true",
                        help="仅下载视频 (等同 --media-type videos)")
    parser.add_argument("--images-only", action="store_true",
                        help="仅下载图片 (等同 --media-type images)")
    parser.add_argument("--full", action="store_true",
                        help="不使用下载档案，重新扫描全部内容")
    parser.add_argument("--stop-after-skips", type=int,
                        default=DEFAULT_STOP_AFTER_SKIPS,
                        metavar="N",
                        help="连续跳过 N 条后停止本轮扫描，0 = 扫完全部（默认 20）")
    parser.add_argument("--proxy", help="代理地址, 例如 http://127.0.0.1:7890")
    parser.add_argument("--no-proxy", action="store_true",
                        help="不使用默认代理，强制直连")
    parser.add_argument("--install", action="store_true",
                        help="仅安装依赖，不执行下载")
    parser.add_argument("--retry", nargs="?", const=True, default=False,
                        metavar="USERNAME",
                        help="重试失败队列中的全部账号，或只重试指定账号")
    parser.add_argument("--status", "--queue", dest="status",
                        action="store_true", help="查看失败队列")
    parser.add_argument("--queue-json", action="store_true",
                        help="以 JSON 输出失败队列（供 GUI 调用）")
    parser.add_argument("--clear-queue", action="store_true",
                        help="清空失败队列")

    args = parser.parse_args()
    args.incremental = not args.full
    args.stop_after_skips = max(0, int(args.stop_after_skips or 0))
    if args.videos_only and args.images_only:
        print("❌ 不能同时指定 --videos-only 和 --images-only")
        return 2
    if args.videos_only:
        args.media_type = "videos"
    elif args.images_only:
        args.media_type = "images"
    else:
        args.media_type = normalize_media_type(args.media_type)
    try:
        args.max_duration = parse_duration_seconds(args.max_duration)
    except ValueError as exc:
        print(f"❌ {exc}")
        return 2

    if args.queue_json:
        print(json.dumps(load_retry_queue(), indent=2, ensure_ascii=False))
        return 0

    if args.status:
        print_retry_queue()
        return 0

    if args.clear_queue:
        clear_retry_queue()
        return 0

    if not args.retry and not args.url:
        parser.print_help()
        print_retry_queue()
        return 1

    if not args.proxy and not args.no_proxy and default_proxy_available():
        args.proxy = DEFAULT_PROXY

    if args.install:
        return 0 if install_gallery_dl() else 1

    if not check_gallery_dl():
        print("未检测到 gallery-dl")
        resp = input("是否自动安装? [Y/n]: ").strip().lower()
        if resp in ("", "y", "yes"):
            if not install_gallery_dl():
                return 1
        else:
            print("请手动安装: pip install gallery-dl")
            return 1

    cookies_config = setup_cookies_config(args)
    if cookies_config is None:
        print("无法继续: 需要配置 Cookie 才能访问 X/Twitter 内容。")
        return 1
    args.cookies_config = cookies_config

    print()
    if args.retry:
        only = args.retry if isinstance(args.retry, str) else None
        return retry_all(args, only)

    return 0 if download_with_queue(args.url, args) else 1


if __name__ == "__main__":
    sys.exit(main())
