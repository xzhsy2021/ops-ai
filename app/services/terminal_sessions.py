"""终端会话管理服务 - 内存级生命周期管理"""
import os
import secrets
import time
import logging
import threading
from typing import Dict, Optional, List

logger = logging.getLogger(__name__)

_sessions: Dict[str, Dict] = {}
_lock = threading.Lock()

def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except Exception:
        return default


MAX_IDLE_SECONDS = _env_int("TERMINAL_IDLE_TIMEOUT_SECONDS", 600)
MAX_LIFETIME_SECONDS = _env_int("TERMINAL_MAX_LIFETIME_SECONDS", 3600)
MAX_SESSIONS_PER_USER_SERVER = _env_int("MAX_TERMINAL_SESSIONS_PER_USER_SERVER", 2)
MAX_TERMINAL_SESSIONS = _env_int("MAX_TERMINAL_SESSIONS", 3)


def create_session(
    user: str,
    server_name: str,
    server_config: dict,
    hop_count: int,
    cols: int = 120,
    rows: int = 30,
    max_retries: int = 3,
    per_attempt_timeout: int = None,
) -> dict:
    from app.services.remote_access import create_exec_client
    ssh = create_exec_client(server_config,
                             max_retries=max_retries,
                             per_attempt_timeout=per_attempt_timeout)
    if not ssh:
        raise RuntimeError(f"Cannot connect to {server_name}")

    transport = ssh._client.get_transport()
    if not transport or not transport.is_active():
        ssh.close()
        raise RuntimeError(f"SSH transport not available for {server_name}")

    try:
        channel = transport.open_session()
        channel.get_pty(term="xterm-256color", width=cols, height=rows)
        channel.invoke_shell()
    except Exception as e:
        ssh.close()
        raise RuntimeError(f"Shell open failed for {server_name}: {e}")

    session_id = secrets.token_urlsafe(32)
    now = _now()

    session = {
        "session_id": session_id,
        "user": user,
        "server_name": server_name,
        "channel": channel,
        "client": ssh,
        "created_at": now,
        "last_active_at": now,
        "expires_at": now + MAX_LIFETIME_SECONDS * 1000,
        "cols": cols,
        "rows": rows,
        "hop_count": hop_count,
        "ws_connected": False,
    }

    with _lock:
        _cleanup_expired_locked()
        total_existing = sorted(_sessions.values(), key=lambda s: s["last_active_at"])
        while len(total_existing) >= MAX_TERMINAL_SESSIONS:
            oldest = total_existing.pop(0)
            _cleanup_locked(oldest["session_id"])
            logger.info(f"Auto-purged excess terminal session {oldest['session_id']} for global limit")

        existing = sorted(
            (s for s in _sessions.values() if s["user"] == user and s["server_name"] == server_name),
            key=lambda s: s["last_active_at"],
        )
        while len(existing) >= MAX_SESSIONS_PER_USER_SERVER:
            oldest = existing.pop(0)
            _cleanup_locked(oldest["session_id"])
            logger.info(f"Auto-purged excess session {oldest['session_id']} for {user}@{server_name}")
        _sessions[session_id] = session

    logger.info(f"Terminal session {session_id} created: user={user} server={server_name}")
    return session


def get_session(session_id: str) -> Optional[dict]:
    with _lock:
        session = _sessions.get(session_id)
        if not session:
            return None
        now = _now()
        if now > session["expires_at"]:
            _cleanup_locked(session_id)
            return None
        if now - session["last_active_at"] > MAX_IDLE_SECONDS * 1000:
            _cleanup_locked(session_id)
            return None
        return session


def touch_session(session_id: str):
    with _lock:
        session = _sessions.get(session_id)
        if session:
            session["last_active_at"] = _now()


def set_ws_connected(session_id: str, connected: bool):
    with _lock:
        session = _sessions.get(session_id)
        if session:
            session["ws_connected"] = connected


def resize_session(session_id: str, cols: int, rows: int):
    if cols <= 0 or rows <= 0:
        return
    with _lock:
        session = _sessions.get(session_id)
    if not session:
        return
    try:
        session["channel"].resize_pty(width=cols, height=rows)
        session["cols"] = cols
        session["rows"] = rows
        with _lock:
            session["last_active_at"] = _now()
    except Exception:
        pass


def close_session(session_id: str, username: str = None):
    with _lock:
        session = _sessions.pop(session_id, None)
    if not session:
        return
    if username and session.get("user") and session["user"] != username:
        logger.warning(f"User {username} attempted to close session {session_id} owned by {session['user']}")
        with _lock:
            _sessions[session_id] = session
        raise PermissionError(f"Session {session_id} is owned by {session['user']}")
    try:
        chan = session.get("channel")
        if chan and not chan.closed:
            chan.close()
    except Exception:
        pass
    try:
        session.get("client", None) and session["client"].close()
    except Exception:
        pass
    logger.info(f"Terminal session {session_id} closed by {username or 'system'}")


def list_sessions(server_name: str = None, user: str = None) -> List[dict]:
    result = []
    now = _now()
    with _lock:
        _cleanup_expired_locked()
        for sid, s in _sessions.items():
            if server_name and s["server_name"] != server_name:
                continue
            if user and s["user"] != user:
                continue
            if now > s["expires_at"]:
                continue
            if now - s["last_active_at"] > MAX_IDLE_SECONDS * 1000:
                continue
            result.append({
                "session_id": sid,
                "user": s["user"],
                "server_name": s["server_name"],
                "created_at": s["created_at"],
                "last_active_at": s["last_active_at"],
                "cols": s["cols"],
                "rows": s["rows"],
                "hop_count": s["hop_count"],
                "ws_connected": s["ws_connected"],
            })
    return result


def session_count_for_user_server(user: str, server_name: str) -> int:
    with _lock:
        _cleanup_expired_locked()
        return sum(
            1 for s in _sessions.values()
            if s["user"] == user and s["server_name"] == server_name
        )


def purge_inactive():
    now = _now()
    with _lock:
        expired = [
            sid for sid, s in _sessions.items()
            if now > s["expires_at"]
            or now - s["last_active_at"] > MAX_IDLE_SECONDS * 1000
        ]
        for sid in expired:
            _cleanup_locked(sid)


def _now() -> int:
    return int(time.time() * 1000)


def _cleanup_locked(session_id: str):
    session = _sessions.pop(session_id, None)
    if session:
        try:
            if session.get("channel") and not session["channel"].closed:
                session["channel"].close()
        except Exception:
            pass
        try:
            if session.get("client"):
                session["client"].close()
        except Exception:
            pass


def _cleanup_expired_locked():
    now = _now()
    expired = [
        sid for sid, s in list(_sessions.items())
        if now > s["expires_at"]
        or now - s["last_active_at"] > MAX_IDLE_SECONDS * 1000
    ]
    for sid in expired:
        session = _sessions.pop(sid, None)
        if session:
            try:
                if session.get("channel") and not session["channel"].closed:
                    session["channel"].close()
            except Exception:
                pass
            try:
                if session.get("client"):
                    session["client"].close()
            except Exception:
                pass


def get_terminal_limits() -> dict:
    return {
        "max_terminal_sessions": MAX_TERMINAL_SESSIONS,
        "max_sessions_per_user_server": MAX_SESSIONS_PER_USER_SERVER,
        "terminal_idle_timeout_seconds": MAX_IDLE_SECONDS,
        "terminal_max_lifetime_seconds": MAX_LIFETIME_SECONDS,
    }
