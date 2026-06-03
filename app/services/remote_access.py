"""统一远程访问服务 - 支持直接连接和多跳跳板机链"""
import logging
from typing import Optional, Dict, Any, Tuple, List
from config_manager import get_server_by_name

logger = logging.getLogger(__name__)

_AUTH_MODE_LABELS = {
    "key_file": "private_key_file",
    "key_content": "private_key_content",
    "password": "password",
    "none": "none",
}

_HOP_SENSITIVE_KEYS = {"password", "key_content", "key", "private_key"}


def get_server_config(name: str) -> Optional[Dict[str, Any]]:
    srv = get_server_by_name(name)
    if not srv:
        return None
    config = dict(srv)
    return config


def get_connection_metadata(server_config: Dict[str, Any]) -> Dict[str, Any]:
    auth_mode = _detect_auth_mode(server_config)
    return {
        "host": server_config.get("host", ""),
        "port": server_config.get("port", 22),
        "username": server_config.get("user", server_config.get("username", "root")),
        "auth_mode": auth_mode,
        "auth_mode_label": _AUTH_MODE_LABELS.get(auth_mode, auth_mode),
    }


def _detect_auth_mode(server_config: Dict[str, Any]) -> str:
    if server_config.get("key_content"):
        return "key_content"
    if server_config.get("key") or server_config.get("key_file"):
        return "key_file"
    if server_config.get("password"):
        return "password"
    return "none"


def resolve_hop_chain(server_config: Dict[str, Any]) -> List[Dict[str, Any]]:
    hops = []

    jh = server_config.get("jump_host")
    if jh:
        if isinstance(jh, dict):
            hops.append(_sanitize_hop(jh))
        elif isinstance(jh, str):
            resolved = get_server_by_name(jh)
            if resolved:
                hops.append(_sanitize_hop(resolved))
            else:
                hops.append({"host": jh, "name": jh})

    jump_hosts = server_config.get("jump_hosts")
    if isinstance(jump_hosts, list):
        for h in jump_hosts:
            if isinstance(h, dict):
                hops.append(_sanitize_hop(h))
            elif isinstance(h, str):
                resolved = get_server_by_name(h)
                if resolved:
                    hops.append(_sanitize_hop(resolved))
                else:
                    hops.append({"host": h, "name": h})

    return hops


def _sanitize_hop(hop: Dict[str, Any]) -> Dict[str, Any]:
    result = {
        "host": hop.get("host") or hop.get("name", ""),
        "name": hop.get("name", hop.get("host", "")),
        "port": hop.get("port", 22),
        "username": hop.get("user", hop.get("username", "root")),
        "auth_mode": _detect_auth_mode(hop),
    }
    result["auth_mode_label"] = _AUTH_MODE_LABELS.get(result["auth_mode"], result["auth_mode"])
    return result


def get_hop_summary(server_config: Dict[str, Any]) -> Dict[str, Any]:
    hops = resolve_hop_chain(server_config)
    return {
        "hop_count": len(hops),
        "hops": [{"name": h.get("name"), "host": h.get("host")} for h in hops],
        "is_proxied": len(hops) > 0,
    }


def build_audit_context(server_config: Dict[str, Any], server_name: str = "") -> Dict[str, Any]:
    """构建统一的审计上下文，供 exec/terminal/sftp 一致使用"""
    auth_mode = _detect_auth_mode(server_config)
    hop_summary = get_hop_summary(server_config)
    hop_names = [h["name"] for h in hop_summary.get("hops", [])]
    return {
        "server": server_name or server_config.get("name", server_config.get("host", "")),
        "host": server_config.get("host", ""),
        "port": server_config.get("port", 22),
        "auth_mode": auth_mode,
        "auth_mode_label": _AUTH_MODE_LABELS.get(auth_mode, auth_mode),
        "hop_count": hop_summary["hop_count"],
        "hop_chain": " -> ".join(hop_names) if hop_names else "direct",
        "is_proxied": hop_summary["is_proxied"],
    }


def format_audit_detail(audit_ctx: Dict[str, Any], **extra) -> str:
    """格式化审计详情字符串"""
    parts = [
        f"server={audit_ctx['server']}",
        f"auth={audit_ctx['auth_mode']}",
        f"hops={audit_ctx['hop_chain']}",
    ]
    for k, v in extra.items():
        parts.append(f"{k}={v}")
    return " ".join(parts)


def create_exec_client(server_config: Dict[str, Any]):
    from ssh_client import create_ssh_client

    auth_mode = _detect_auth_mode(server_config)
    if auth_mode == "none":
        host = server_config.get("host", "unknown")
        name = server_config.get("name", host)
        raise RuntimeError(
            f"服务器 '{name}' ({host}) 缺少认证凭据。"
            f"请在服务器管理中设置密码、密钥文件或密钥内容后重试。"
        )

    return create_ssh_client(server_config)


def open_shell_channel(server_config: Dict[str, Any], cols: int = 80, rows: int = 24):
    from ssh_client import SSHClient
    client = create_exec_client(server_config)
    if not client or not client._client:
        raise ConnectionError(f"Cannot connect to {server_config.get('host')}")
    transport = client._client.get_transport()
    if not transport or not transport.is_active():
        raise ConnectionError("SSH transport not active")
    channel = transport.open_session()
    channel.get_pty(term="xterm-256color", width=cols, height=rows)
    channel.invoke_shell()
    return channel, client


def open_sftp_client(server_config: Dict[str, Any]):
    from ssh_client import SSHClient
    client = create_exec_client(server_config)
    if not client or not client._client:
        raise ConnectionError(f"Cannot connect to {server_config.get('host')}")
    sftp = client._client.open_sftp()
    return sftp, client
