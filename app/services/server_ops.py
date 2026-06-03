from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from config_manager import get_all_servers, get_server_by_name

_CACHE_TTL_SECONDS = 30
_HEALTH_CACHE: Dict[str, Dict[str, Any]] = {}


def _auth_mode(server: Dict[str, Any]) -> str:
    explicit = (server.get("auth_type") or "").strip()
    if explicit:
        return explicit
    if server.get("key_content"):
        return "key_content"
    if server.get("key") or server.get("key_file"):
        return "key_file"
    if server.get("password"):
        return "password"
    return "none"


def analyze_server_config(server: Dict[str, Any]) -> Dict[str, Any]:
    """Return a non-secret, UI-friendly configuration health summary."""
    missing: List[str] = []
    warnings: List[str] = []
    suggestions: List[str] = []

    name = (server.get("name") or "").strip()
    host = (server.get("host") or server.get("ip") or "").strip()
    username = (server.get("username") or server.get("user") or "").strip()
    port = server.get("port") or 22
    group = (server.get("group") or server.get("environment") or "").strip()
    auth = _auth_mode(server)

    if not name:
        missing.append("name")
        suggestions.append("补充服务器名称，便于发布和审计定位")
    if not host:
        missing.append("host")
        suggestions.append("补充 host/IP，否则无法建立 SSH/SFTP 连接")
    if not username:
        missing.append("username")
        suggestions.append("补充 SSH 用户名")
    if auth == "none":
        missing.append("credential")
        suggestions.append("配置密码、密钥文件或密钥内容中的一种认证方式")
    if not group:
        warnings.append("missing_group")
        suggestions.append("建议设置分组/环境，发布选择服务器时更容易识别")
    if not server.get("sftp_allowed_roots") and not server.get("allowed_roots"):
        warnings.append("default_sftp_roots")
        suggestions.append("建议为文件中心配置明确允许目录，减少误操作范围")

    complete = not missing
    return {
        "complete": complete,
        "status": "passed" if complete and not warnings else "warning" if complete else "blocked",
        "missing": missing,
        "warnings": warnings,
        "suggestions": suggestions,
        "auth_mode": auth,
        "host": host,
        "port": port,
        "username": username,
        "group": group,
    }


def summarize_servers() -> Dict[str, Any]:
    servers = get_all_servers()
    items = []
    counts = {"total": len(servers), "complete": 0, "incomplete": 0, "warning": 0}
    for srv in servers:
        summary = analyze_server_config(srv)
        if summary["complete"]:
            counts["complete"] += 1
        else:
            counts["incomplete"] += 1
        if summary.get("warnings"):
            counts["warning"] += 1
        items.append({
            "name": srv.get("name"),
            "host": summary.get("host"),
            "port": summary.get("port"),
            "group": summary.get("group"),
            "config_status": summary,
        })
    return {"summary": counts, "items": items}


def get_cached_health(server_name: str, *, force: bool = False) -> Optional[Dict[str, Any]]:
    now = time.time()
    cached = _HEALTH_CACHE.get(server_name)
    if cached and not force and now - float(cached.get("cached_at_epoch") or 0) < _CACHE_TTL_SECONDS:
        return {**cached, "cache_hit": True}
    return None


def set_cached_health(server_name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    data = dict(payload)
    data["cached_at_epoch"] = time.time()
    data["cached_ttl_seconds"] = _CACHE_TTL_SECONDS
    _HEALTH_CACHE[server_name] = data
    return {**data, "cache_hit": False}


def build_server_health_probe_result(server_name: str, *, force: bool = False) -> Dict[str, Any]:
    cached = get_cached_health(server_name, force=force)
    if cached:
        return cached

    server = get_server_by_name(server_name)
    if not server:
        return set_cached_health(server_name, {
            "server": server_name,
            "status": "blocked",
            "online": False,
            "message": "服务器不存在",
            "checks": [{"key": "exists", "status": "blocked", "message": "服务器不存在"}],
            "suggestions": ["检查服务器名称是否正确"],
        })

    config = analyze_server_config(server)
    if not config["complete"]:
        return set_cached_health(server_name, {
            "server": server_name,
            "status": "blocked",
            "online": False,
            "message": "服务器配置不完整，未执行 SSH 探测",
            "config_status": config,
            "checks": [{"key": "config", "status": "blocked", "message": "配置缺失: " + ", ".join(config["missing"])}],
            "suggestions": config.get("suggestions") or [],
        })

    started = time.time()
    try:
        from ssh_client import create_ssh_client
        ssh = create_ssh_client(server)
        try:
            code, out, err = ssh.exec(
                "echo __OPS_OK__; df -Pk / 2>/dev/null | tail -1",
                timeout=12,
            )
        finally:
            try:
                ssh.close()
            except Exception:
                pass
        online = code == 0 and "__OPS_OK__" in (out or "")
        disk = None
        for line in (out or "").splitlines():
            parts = line.split()
            if len(parts) >= 6 and parts[0] != "__OPS_OK__":
                try:
                    disk = {
                        "filesystem": parts[0],
                        "size_kb": int(parts[1]),
                        "used_kb": int(parts[2]),
                        "available_kb": int(parts[3]),
                        "used_percent": parts[4],
                        "mount": parts[5],
                    }
                except Exception:
                    disk = {"raw": line}
        status = "passed" if online else "blocked"
        suggestions = [] if online else ["检查网络、端口、账号、密钥或跳板机配置"]
        return set_cached_health(server_name, {
            "server": server_name,
            "status": status,
            "online": online,
            "message": "SSH 探测通过" if online else "SSH 探测失败",
            "config_status": config,
            "disk": disk,
            "duration_ms": int((time.time() - started) * 1000),
            "checks": [
                {"key": "config", "status": config["status"], "message": "配置检查完成"},
                {"key": "ssh", "status": status, "message": "SSH 可连接" if online else (err or "SSH 不可连接")},
            ],
            "suggestions": suggestions + (config.get("suggestions") or []),
        })
    except Exception as exc:
        return set_cached_health(server_name, {
            "server": server_name,
            "status": "blocked",
            "online": False,
            "message": f"SSH 探测异常: {exc}",
            "config_status": config,
            "duration_ms": int((time.time() - started) * 1000),
            "checks": [
                {"key": "config", "status": config["status"], "message": "配置检查完成"},
                {"key": "ssh", "status": "blocked", "message": str(exc)},
            ],
            "suggestions": ["检查网络、端口、账号、密钥或跳板机配置"] + (config.get("suggestions") or []),
        })
