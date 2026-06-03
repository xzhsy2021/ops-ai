"""Xshell 会话导入工具 — 解析 .xsh 文件并转为 OPS 服务器配置"""
import os
import logging
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)


def parse_xshell_session(file_path: str, group_name: str = "") -> Optional[Dict]:
    try:
        with open(file_path, "rb") as fh:
            raw = fh.read()
        text = raw.decode("utf-16-le", errors="replace")
    except Exception as e:
        logger.warning(f"Failed to read {file_path}: {e}")
        return None

    sections = {}
    current_section = None

    for line in text.splitlines():
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            current_section = line.strip("[]").strip()
            sections[current_section] = {}
        elif "=" in line and current_section:
            k, _, v = line.partition("=")
            sections[current_section][k.strip()] = v.strip()

    conn = sections.get("CONNECTION") or sections.get("CONNECTION:PROXY", {})
    auth = sections.get("CONNECTION:AUTHENTICATION", {})

    host = conn.get("Host", "").strip()
    if not host:
        return None

    port = int(conn.get("Port", "22"))
    protocol = conn.get("Protocol", "SSH").upper()
    if protocol != "SSH":
        return None

    username = auth.get("UserName", "root").strip()
    description = conn.get("Description", "").strip()

    key_file = auth.get("KeyFilePath", "").strip()
    use_key = auth.get("UseKeyFile", "0") == "1"

    config = {
        "name": os.path.splitext(os.path.basename(file_path))[0],
        "host": host,
        "port": port,
        "user": username,
        "username": username,
        "group": group_name if group_name else "",
        "description": description,
    }

    if use_key and key_file:
        config["key_file"] = key_file
    elif auth.get("Password"):
        config["password"] = auth["Password"]

    return config


def scan_xshell_sessions(scan_dir: str) -> List[Dict]:
    servers = []
    if not os.path.isdir(scan_dir):
        raise FileNotFoundError(f"Xshell sessions directory not found: {scan_dir}")

    for root, dirs, files in os.walk(scan_dir):
        for f in files:
            if not f.endswith(".xsh"):
                continue
            full_path = os.path.join(root, f)
            group = os.path.relpath(root, scan_dir)
            if group == ".":
                group = ""
            config = parse_xshell_session(full_path, group)
            if config:
                servers.append(config)

    logger.info(f"Scanned {len(servers)} SSH sessions from {scan_dir}")
    return servers


def import_xshell_sessions_to_config(scan_dir: str, merge: bool = False) -> Dict:
    scanned = scan_xshell_sessions(scan_dir)
    if not scanned:
        return {"imported": 0, "servers": [], "message": "No SSH sessions found"}
    existing = {}
    try:
        from config_manager import get_all_servers
        for srv in get_all_servers():
            name = srv.get("name", "")
            host = srv.get("host", "")
            if name:
                existing[name] = srv
            if host:
                existing[host] = srv
    except Exception:
        pass
    imported = []
    skipped = []
    group_stats: Dict[str, int] = {}
    for cfg in scanned:
        name = cfg["name"]
        host = cfg["host"]
        if name in existing or host in existing:
            skipped.append({"name": name, "host": host, "reason": "already exists"})
            continue
        group_name = cfg.get("group", "")
        server_entry = {
            "name": name,
            "host": cfg["host"],
            "port": cfg.get("port", 22),
            "user": cfg.get("user", "root"),
            "username": cfg.get("username", "root"),
            "group": group_name,
            "description": cfg.get("description", ""),
        }
        if cfg.get("key_file"):
            server_entry["key_file"] = cfg["key_file"]
        if cfg.get("password"):
            server_entry["password"] = cfg["password"]
        imported.append(server_entry)
        if group_name:
            group_stats[group_name] = group_stats.get(group_name, 0) + 1
    for srv in imported:
        try:
            from config_manager import save_server
            save_server(srv)
        except Exception as e:
            logger.error(f"Failed to add server {srv.get('name')}: {e}")
    return {
        "imported": len(imported),
        "skipped": len(skipped),
        "servers": imported,
        "skipped_servers": skipped,
        "groups": [{"name": g, "count": c} for g, c in sorted(group_stats.items())],
        "message": f"Imported {len(imported)} servers, skipped {len(skipped)} duplicates",
    }
