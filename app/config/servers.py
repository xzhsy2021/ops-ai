import copy
import logging
from typing import Any, Dict, List, Optional

from app.core.secret_store import decrypt_secret, encrypt_secret, is_encrypted

logger = logging.getLogger(__name__)

_SECRET_FIELDS = ("password", "key_content")
_REDACTED = "********"


def _with_secret_transform(server: Dict[str, Any], transform) -> Dict[str, Any]:
    result = copy.deepcopy(server or {})
    for key in _SECRET_FIELDS:
        if result.get(key):
            result[key] = transform(result.get(key))

    # Inline jump host definitions may also carry credentials.  Named jump hosts
    # are resolved separately and are left untouched.
    jump_host = result.get("jump_host")
    if isinstance(jump_host, dict):
        for key in _SECRET_FIELDS:
            if jump_host.get(key):
                jump_host[key] = transform(jump_host.get(key))

    jump_hosts = result.get("jump_hosts")
    if isinstance(jump_hosts, list):
        for hop in jump_hosts:
            if not isinstance(hop, dict):
                continue
            for key in _SECRET_FIELDS:
                if hop.get(key):
                    hop[key] = transform(hop.get(key))
    return result


def encrypt_server_secrets(server: Dict[str, Any]) -> Dict[str, Any]:
    return _with_secret_transform(server, encrypt_secret)


def decrypt_server_secrets(server: Dict[str, Any]) -> Dict[str, Any]:
    return _with_secret_transform(server, decrypt_secret)


def redact_server_secrets(server: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy that is safe to send to browsers/logs."""
    result = copy.deepcopy(server or {})
    for key in _SECRET_FIELDS:
        value = result.pop(key, None)
        result[f"has_{key}"] = bool(value)
    if result.get("auth_type") == "password" and result.get("has_password"):
        result["password"] = _REDACTED
    if result.get("auth_type") == "key_content" and result.get("has_key_content"):
        result["key_content"] = _REDACTED
    return result


def get_all_servers() -> List[Dict[str, Any]]:
    from app.config.cache import load_config_cached
    return [decrypt_server_secrets(s) for s in load_config_cached().get("servers", [])]


def get_server_by_name(name: str) -> Optional[Dict[str, Any]]:
    for s in get_all_servers():
        if s["name"] == name:
            return s
    return None


def save_server(server: Dict[str, Any]) -> bool:
    from app.config.repository import load_config, save_config
    from app.config.cache import invalidate_config_cache

    config = load_config()
    servers = config.get("servers", [])
    server_to_store = encrypt_server_secrets(server)
    idx = next((i for i, s in enumerate(servers) if s["name"] == server_to_store["name"]), None)
    if idx is not None:
        servers[idx] = server_to_store
        logger.info(f"Updated server: {server_to_store['name']}")
    else:
        servers.append(server_to_store)
        logger.info(f"Added server: {server_to_store['name']}")
    config["servers"] = servers
    saved = save_config(config)
    if saved:
        try:
            invalidate_config_cache()
        except Exception:
            logger.debug("Failed to invalidate config cache after save_server", exc_info=True)
    return saved


def _get_database_connection_references(db, server_name: str) -> list:
    """Check if any database connection references this server."""
    from app.db.models import DatabaseConnection
    refs = []
    for conn in db.query(DatabaseConnection).all():
        if conn.ssh_server_name == server_name:
            refs.append({"type": "ssh_bastion", "connection_name": conn.name, "connection_id": conn.id})
        if getattr(conn, "ssh_target_server_name", None) == server_name:
            refs.append({"type": "ssh_target", "connection_name": conn.name, "connection_id": conn.id})
    return refs


def delete_server(name: str, db=None) -> bool:
    from app.config.repository import load_config, save_config
    from app.config.cache import invalidate_config_cache

    if db is not None:
        refs = _get_database_connection_references(db, name)
        if refs:
            ref_names = ", ".join({r["connection_name"] for r in refs})
            raise ValueError(f"服务器 '{name}' 正被以下数据库连接引用: {ref_names}。请先修改或删除相关数据库连接。")

    config = load_config()
    config["servers"] = [s for s in config.get("servers", []) if s["name"] != name]
    logger.info(f"Deleted server: {name}")
    saved = save_config(config)
    if saved:
        try:
            invalidate_config_cache()
        except Exception:
            logger.debug("Failed to invalidate config cache after delete_server", exc_info=True)
    return saved


def get_jump_host_by_name(name: str) -> Optional[Dict[str, Any]]:
    from app.config.repository import load_config
    jump_hosts = load_config().get("jump_hosts", [])
    for jh in jump_hosts:
        if jh.get("name") == name:
            return decrypt_server_secrets(jh)
    return None


def resolve_jump_host_config(server_config: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    jh_name = server_config.get("jump_host")
    if not jh_name or isinstance(jh_name, dict):
        return jh_name if isinstance(jh_name, dict) else None
    return get_jump_host_by_name(jh_name)


def migrate_server_secrets_at_rest() -> int:
    """Encrypt plaintext server credentials already present in config storage.

    For local deployments without OPS_SECRET_KEY this is a no-op because
    encrypt_secret intentionally falls back to plaintext in development.
    """
    from app.config.repository import load_config, save_config
    from app.config.cache import invalidate_config_cache

    config = load_config()
    changed = 0
    encrypted_servers = []
    for server in config.get("servers", []) or []:
        encrypted = encrypt_server_secrets(server)
        if encrypted != server:
            changed += 1
        encrypted_servers.append(encrypted)
    config["servers"] = encrypted_servers

    encrypted_jump_hosts = []
    for jump_host in config.get("jump_hosts", []) or []:
        encrypted = encrypt_server_secrets(jump_host)
        if encrypted != jump_host:
            changed += 1
        encrypted_jump_hosts.append(encrypted)
    if "jump_hosts" in config:
        config["jump_hosts"] = encrypted_jump_hosts

    if changed:
        save_config(config)
        try:
            invalidate_config_cache()
        except Exception:
            logger.debug("Failed to invalidate config cache after secret migration", exc_info=True)
        logger.info("Encrypted %s server/jump-host secret records at rest", changed)
    return changed


def get_server_references(server_name: str) -> List[Dict[str, Any]]:
    from app.config.repository import load_config
    config = load_config()
    refs = []
    for sys_name, sys_cfg in config.get("systems", {}).items():
        if server_name in sys_cfg.get("servers", []):
            refs.append({"type": "system", "system": sys_name, "field": "servers"})
        for env_name, env_cfg in sys_cfg.get("environments", {}).items():
            if server_name in env_cfg.get("servers", []):
                refs.append({"type": "environment", "system": sys_name, "environment": env_name, "field": "servers"})
        for gcode, gcfg in sys_cfg.get("groups", {}).items():
            if gcfg.get("server") == server_name:
                refs.append({"type": "group", "system": sys_name, "group": gcode, "field": "server"})
        for env_name, env_cfg in sys_cfg.get("environments", {}).items():
            for gcode, gcfg in env_cfg.get("groups", {}).items():
                if gcfg.get("server") == server_name:
                    refs.append({"type": "group", "system": sys_name, "environment": env_name, "group": gcode, "field": "server"})
    return refs
