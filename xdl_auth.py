#!/usr/bin/env python3
"""Authentication, salted scrypt credentials, SQLite sessions, and IP rate limiting."""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import sqlite3
import tempfile
import threading
import time
from pathlib import Path

AUTH_COOKIE_NAME = "xdl_session"
SESSION_TTL_SHORT = 86400
SESSION_TTL_REMEMBER = 30 * 86400
SLIDING_REFRESH_SECONDS = 300
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_SECONDS = 15 * 60

SCRYPT_N = 16384
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32

USERNAME_RE = re.compile(r"^[A-Za-z0-9_.@-]{1,64}$")
MIN_PASSWORD_LEN = 6
MAX_PASSWORD_LEN = 256

_DUMMY_SALT = "00112233445566778899aabbccddeeff"
_DUMMY_HASH = "00" * 32


def validate_username(raw: str | None) -> str:
    text = str(raw or "").strip()
    if not USERNAME_RE.fullmatch(text):
        raise ValueError("用户名需为 1-64 位字母、数字或 . _ - @")
    return text


def validate_password(raw: str | None) -> str:
    if not isinstance(raw, str):
        raise ValueError("密码格式无效")
    if len(raw) < MIN_PASSWORD_LEN:
        raise ValueError(f"密码至少需要 {MIN_PASSWORD_LEN} 位字符")
    if len(raw) > MAX_PASSWORD_LEN:
        raise ValueError(f"密码长度不能超过 {MAX_PASSWORD_LEN} 位")
    return raw


def _scrypt_hex(password: str, salt_hex: str) -> str:
    salt = bytes.fromhex(salt_hex)
    derived = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=SCRYPT_DKLEN,
    )
    return derived.hex()


def _verify_password(password: str, salt_hex: str, expected_hex: str) -> bool:
    try:
        candidate = _scrypt_hex(password, salt_hex)
    except Exception:
        return False
    return hmac.compare_digest(candidate, expected_hex)


def _hash_token(token_key_hex: str, raw_token: str) -> str:
    key = bytes.fromhex(token_key_hex)
    return hmac.new(key, raw_token.encode("utf-8"), hashlib.sha256).hexdigest()


def _write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    fd, tmp_name = tempfile.mkstemp(prefix=".auth_", suffix=".tmp", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(raw)
        try:
            os.chmod(tmp_path, 0o600)
        except OSError:
            pass
        os.replace(tmp_path, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def parse_cookies(cookie_header: str | None) -> dict[str, str]:
    cookies: dict[str, str] = {}
    if not cookie_header:
        return cookies
    for part in str(cookie_header).split(";"):
        if "=" not in part:
            continue
        name, value = part.split("=", 1)
        key = name.strip()
        if key:
            cookies[key] = value.strip()
    return cookies


def extract_client_ip(remote_addr: str, headers) -> str:
    peer = str(remote_addr or "").strip()
    if peer in {"127.0.0.1", "::1", "::ffff:127.0.0.1"}:
        candidates = [
            headers.get("X-Real-IP"),
            (headers.get("X-Forwarded-For") or "").split(",", 1)[0],
        ]
        for raw in candidates:
            candidate = str(raw or "").strip()
            if not candidate:
                continue
            try:
                return str(ipaddress.ip_address(candidate))
            except ValueError:
                continue
    return peer or "unknown"


def is_https_request(headers) -> bool:
    proto = str(headers.get("X-Forwarded-Proto") or "").split(",", 1)[0].strip().lower()
    return proto == "https"


def build_session_cookie(token: str, ttl: int, https: bool) -> str:
    parts = [
        f"{AUTH_COOKIE_NAME}={token}",
        "Path=/",
        f"Max-Age={int(ttl)}",
        "HttpOnly",
        "SameSite=Lax",
    ]
    if https:
        parts.append("Secure")
    return "; ".join(parts)


def build_clear_cookie(https: bool) -> str:
    parts = [
        f"{AUTH_COOKIE_NAME}=",
        "Path=/",
        "Max-Age=0",
        "Expires=Thu, 01 Jan 1970 00:00:00 GMT",
        "HttpOnly",
        "SameSite=Lax",
    ]
    if https:
        parts.append("Secure")
    return "; ".join(parts)


def _format_wait_cn(seconds: int) -> str:
    secs = max(1, int(seconds))
    mins, rem = divmod(secs, 60)
    if mins and rem:
        return f"{mins} 分 {rem} 秒"
    if mins:
        return f"{mins} 分钟"
    return f"{rem} 秒"


class AuthManager:
    def __init__(self, auth_file: Path, db_file: Path):
        self.auth_file = Path(auth_file)
        self.db_file = Path(db_file)
        self.lock = threading.RLock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        self.db_file.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_file), timeout=10.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self.lock:
            conn = self._connect()
            try:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS auth_sessions (
                        token_hash TEXT PRIMARY KEY,
                        username TEXT NOT NULL,
                        remember INTEGER NOT NULL DEFAULT 0,
                        ttl INTEGER NOT NULL,
                        created_at INTEGER NOT NULL,
                        last_seen_at INTEGER NOT NULL,
                        expires_at INTEGER NOT NULL,
                        ip TEXT NOT NULL DEFAULT '',
                        user_agent TEXT NOT NULL DEFAULT ''
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS auth_attempts (
                        ip TEXT PRIMARY KEY,
                        fail_count INTEGER NOT NULL DEFAULT 0,
                        last_failed_at INTEGER NOT NULL DEFAULT 0,
                        locked_until INTEGER NOT NULL DEFAULT 0
                    )
                    """
                )
                conn.commit()
            finally:
                conn.close()
            try:
                os.chmod(self.db_file, 0o600)
            except OSError:
                pass

    def _read_record_locked(self) -> dict | None:
        if not self.auth_file.is_file():
            return None
        try:
            data = json.loads(self.auth_file.read_text(encoding="utf-8"))
        except Exception:
            return None
        if not isinstance(data, dict):
            return None
        if not (data.get("username") and data.get("salt") and data.get("passwordHash") and data.get("tokenKey")):
            return None
        return data

    def is_configured(self) -> bool:
        with self.lock:
            return self._read_record_locked() is not None

    def username(self) -> str | None:
        with self.lock:
            rec = self._read_record_locked()
            return str(rec["username"]) if rec else None

    def set_credentials(self, username: str, password: str, *, clear_sessions: bool = True) -> dict:
        clean_user = validate_username(username)
        clean_pass = validate_password(password)
        salt_hex = secrets.token_bytes(16).hex()
        pass_hash = _scrypt_hex(clean_pass, salt_hex)
        token_key = secrets.token_bytes(32).hex()
        record = {
            "version": 1,
            "username": clean_user,
            "salt": salt_hex,
            "passwordHash": pass_hash,
            "tokenKey": token_key,
            "updatedAt": int(time.time()),
        }
        with self.lock:
            prev = self._read_record_locked()
            if prev and isinstance(prev.get("shortcutToken"), str) and prev["shortcutToken"].strip():
                record["shortcutToken"] = prev["shortcutToken"].strip()
                if prev.get("shortcutTokenUpdatedAt"):
                    record["shortcutTokenUpdatedAt"] = prev["shortcutTokenUpdatedAt"]
            _write_json_atomic(self.auth_file, record)
            if clear_sessions:
                conn = self._connect()
                try:
                    conn.execute("DELETE FROM auth_sessions")
                    conn.execute("DELETE FROM auth_attempts")
                    conn.commit()
                finally:
                    conn.close()
        return {"ok": True, "username": clean_user, "path": str(self.auth_file)}

    def _create_session_locked(self, record: dict, *, remember: bool, client_ip: str, user_agent: str) -> dict:
        now = int(time.time())
        ttl = SESSION_TTL_REMEMBER if remember else SESSION_TTL_SHORT
        raw_token = secrets.token_urlsafe(32)
        token_hash = _hash_token(record["tokenKey"], raw_token)
        expires_at = now + ttl
        ua = str(user_agent or "")[:240]
        ip = str(client_ip or "")[:64]

        conn = self._connect()
        try:
            conn.execute("DELETE FROM auth_sessions WHERE expires_at <= ?", (now,))
            conn.execute(
                """
                INSERT INTO auth_sessions
                    (token_hash, username, remember, ttl, created_at, last_seen_at, expires_at, ip, user_agent)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (token_hash, record["username"], 1 if remember else 0, ttl, now, now, expires_at, ip, ua),
            )
            conn.commit()
        finally:
            conn.close()

        return {
            "token": raw_token,
            "ttl": ttl,
            "remember": bool(remember),
            "username": record["username"],
            "expiresAt": expires_at,
        }

    def verify_login(
        self,
        username: str | None,
        password: str | None,
        *,
        client_ip: str,
        user_agent: str = "",
        remember: bool = False,
    ) -> dict:
        now = int(time.time())
        ip = str(client_ip or "unknown")[:64]
        raw_user = str(username or "").strip()
        raw_pass = password if isinstance(password, str) else ""

        with self.lock:
            record = self._read_record_locked()
            if record is None:
                return {
                    "ok": False,
                    "status": 503,
                    "error": "管理员账号尚未初始化，请先完成账号设置",
                }

            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT fail_count, last_failed_at, locked_until FROM auth_attempts WHERE ip = ?",
                    (ip,),
                ).fetchone()
                fail_count = int(row["fail_count"]) if row else 0
                last_failed_at = int(row["last_failed_at"]) if row else 0
                locked_until = int(row["locked_until"]) if row else 0

                if locked_until > now:
                    wait_secs = locked_until - now
                    return {
                        "ok": False,
                        "status": 429,
                        "retryAfter": wait_secs,
                        "error": f"连续输错次数过多，请在 {_format_wait_cn(wait_secs)}后再试",
                    }

                if row and (locked_until > 0 or (now - last_failed_at) > LOCKOUT_SECONDS):
                    fail_count = 0

                user_match = hmac.compare_digest(raw_user, str(record["username"]))
                salt_hex = record["salt"] if user_match else _DUMMY_SALT
                hash_hex = record["passwordHash"] if user_match else _DUMMY_HASH
                pass_match = bool(raw_pass) and _verify_password(raw_pass, salt_hex, hash_hex)

                if user_match and pass_match:
                    conn.execute("DELETE FROM auth_attempts WHERE ip = ?", (ip,))
                    conn.commit()
                    session = self._create_session_locked(
                        record,
                        remember=bool(remember),
                        client_ip=ip,
                        user_agent=user_agent,
                    )
                    return {"ok": True, "status": 200, **session}

                fail_count += 1
                new_locked_until = (now + LOCKOUT_SECONDS) if fail_count >= MAX_FAILED_ATTEMPTS else 0
                conn.execute(
                    """
                    INSERT INTO auth_attempts (ip, fail_count, last_failed_at, locked_until)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(ip) DO UPDATE SET
                        fail_count = excluded.fail_count,
                        last_failed_at = excluded.last_failed_at,
                        locked_until = excluded.locked_until
                    """,
                    (ip, fail_count, now, new_locked_until),
                )
                conn.commit()

                if new_locked_until > now:
                    return {
                        "ok": False,
                        "status": 429,
                        "retryAfter": LOCKOUT_SECONDS,
                        "error": f"密码连续错误 {MAX_FAILED_ATTEMPTS} 次，已锁定 15 分钟",
                    }
                remaining = max(0, MAX_FAILED_ATTEMPTS - fail_count)
                return {
                    "ok": False,
                    "status": 401,
                    "remainingAttempts": remaining,
                    "error": f"用户名或密码错误（还可尝试 {remaining} 次）",
                }
            finally:
                conn.close()

    def validate_session(self, raw_token: str | None) -> dict | None:
        if not raw_token:
            return None
        now = int(time.time())
        with self.lock:
            record = self._read_record_locked()
            if record is None:
                return None
            token_hash = _hash_token(record["tokenKey"], raw_token)
            conn = self._connect()
            try:
                row = conn.execute(
                    """
                    SELECT token_hash, username, remember, ttl, created_at, last_seen_at, expires_at
                    FROM auth_sessions
                    WHERE token_hash = ?
                    """,
                    (token_hash,),
                ).fetchone()
                if not row:
                    return None
                if int(row["expires_at"]) <= now or not hmac.compare_digest(str(row["username"]), str(record["username"])):
                    conn.execute("DELETE FROM auth_sessions WHERE token_hash = ?", (token_hash,))
                    conn.commit()
                    return None

                ttl = max(SESSION_TTL_SHORT, int(row["ttl"]))
                expires_at = int(row["expires_at"])
                refreshed = False
                if now - int(row["last_seen_at"]) >= SLIDING_REFRESH_SECONDS:
                    expires_at = now + ttl
                    conn.execute(
                        "UPDATE auth_sessions SET last_seen_at = ?, expires_at = ? WHERE token_hash = ?",
                        (now, expires_at, token_hash),
                    )
                    conn.commit()
                    refreshed = True

                return {
                    "tokenHash": str(row["token_hash"]),
                    "username": str(row["username"]),
                    "remember": bool(row["remember"]),
                    "ttl": ttl,
                    "createdAt": int(row["created_at"]),
                    "expiresAt": expires_at,
                    "refreshed": refreshed,
                }
            finally:
                conn.close()

    def logout_session(self, raw_token: str | None) -> None:
        if not raw_token:
            return
        with self.lock:
            record = self._read_record_locked()
            if record is None:
                return
            token_hash = _hash_token(record["tokenKey"], raw_token)
            conn = self._connect()
            try:
                conn.execute("DELETE FROM auth_sessions WHERE token_hash = ?", (token_hash,))
                conn.commit()
            finally:
                conn.close()

    def logout_all(self) -> int:
        with self.lock:
            conn = self._connect()
            try:
                cur = conn.execute("DELETE FROM auth_sessions")
                conn.commit()
                return int(cur.rowcount or 0)
            finally:
                conn.close()

    def change_credentials(
        self,
        *,
        current_password: str | None,
        new_password: str | None,
        new_username: str | None = None,
        client_ip: str = "",
        user_agent: str = "",
        keep_remember: bool = True,
    ) -> dict:
        with self.lock:
            record = self._read_record_locked()
            if record is None:
                return {"ok": False, "status": 400, "error": "管理员账号尚未初始化"}
            cur_pass = current_password if isinstance(current_password, str) else ""
            if not cur_pass or not _verify_password(cur_pass, record["salt"], record["passwordHash"]):
                return {"ok": False, "status": 403, "error": "当前密码验证失败"}

            target_user = str(new_username or "").strip() or str(record["username"])
            clean_user = validate_username(target_user)
            clean_new_pass = validate_password(new_password)

            salt_hex = secrets.token_bytes(16).hex()
            pass_hash = _scrypt_hex(clean_new_pass, salt_hex)
            token_key = secrets.token_bytes(32).hex()
            updated = {
                "version": 1,
                "username": clean_user,
                "salt": salt_hex,
                "passwordHash": pass_hash,
                "tokenKey": token_key,
                "updatedAt": int(time.time()),
            }
            if isinstance(record.get("shortcutToken"), str) and record["shortcutToken"].strip():
                updated["shortcutToken"] = record["shortcutToken"].strip()
                if record.get("shortcutTokenUpdatedAt"):
                    updated["shortcutTokenUpdatedAt"] = record["shortcutTokenUpdatedAt"]
            _write_json_atomic(self.auth_file, updated)

            conn = self._connect()
            try:
                conn.execute("DELETE FROM auth_sessions")
                conn.execute("DELETE FROM auth_attempts")
                conn.commit()
            finally:
                conn.close()

            session = self._create_session_locked(
                updated,
                remember=bool(keep_remember),
                client_ip=client_ip,
                user_agent=user_agent,
            )
            return {
                "ok": True,
                "status": 200,
                "message": "登录凭据已更新，所有历史会话已失效",
                **session,
            }

    def get_shortcut_token(self, *, create: bool = True) -> str | None:
        with self.lock:
            record = self._read_record_locked()
            if record is None:
                return None
            existing = record.get("shortcutToken")
            if isinstance(existing, str) and existing.strip():
                return existing.strip()
            if not create:
                return None
            token = secrets.token_urlsafe(32)
            record["shortcutToken"] = token
            record["shortcutTokenUpdatedAt"] = int(time.time())
            _write_json_atomic(self.auth_file, record)
            return token

    def rotate_shortcut_token(self) -> str | None:
        with self.lock:
            record = self._read_record_locked()
            if record is None:
                return None
            token = secrets.token_urlsafe(32)
            record["shortcutToken"] = token
            record["shortcutTokenUpdatedAt"] = int(time.time())
            _write_json_atomic(self.auth_file, record)
            return token

    def validate_shortcut_token(self, raw_token: str | None) -> dict | None:
        if not isinstance(raw_token, str):
            return None
        candidate = raw_token.strip()
        if not candidate:
            return None
        with self.lock:
            record = self._read_record_locked()
            if record is None:
                return None
            stored = str(record.get("shortcutToken") or "").strip()
            if stored and hmac.compare_digest(candidate, stored):
                return {"username": str(record["username"]), "via": "shortcut_token"}
            return None
