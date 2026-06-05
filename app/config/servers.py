import copy
import logging
from typing import Any, Dict, List, Optional

from app.core.secret_store import decrypt_secret, encrypt_secret, is_encrypted

logger = logging.getLogger(__name__)

# Phase 3a SSOT migration:
# - server 写路径:save_server / delete_server → ServerRepository (DB 表)
# - server 读路径:get_all_servers / get_server_by_name → ServerRepository
# - config_kv["servers"] 不再被这些函数写入;保留其历史数据作为只读回退已不再需要。
#
# 注:get_jump_host_by_name 仍读 config_kv["jump_hosts"],因为尚无 JumpHost 表。
# 后续 Phase(3.x)将引入 JumpHost 模型,把跳板机也迁到 DB。

_SECRET_FIELDS = ("password", "key_content")
_REDACTED = "********"


# ─── Secret transform helpers (operate on dict; ORM encryption goes via repo) ──


def _with_secret_transform(server: Dict[str, Any], transform) -> Dict[str, Any]:
    result = copy.deepcopy(server or {})
    for key in _SECRET_FIELDS:
        if result.get(key):
            result[key] = transform(result.get(key))

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


# ─── Field mapping helpers (dict ⇄ Server ORM) ────────────────────────────────


_CORE_FIELDS = ("name", "host", "port", "user", "key", "key_content", "password", "jump_host", "status")
_EXTRA_FIELDS = ("description", "tags", "group", "sftp_allowed_roots",
                 "auth_type", "enabled", "username")


def _server_dict_to_core(server: Dict[str, Any]) -> Dict[str, Any]:
    """Extract dict fields that map directly to Server table columns."""
    jump_host_value = server.get("jump_host")
    if isinstance(jump_host_value, str):
        jump_host_name = jump_host_value or None
    elif isinstance(jump_host_value, dict):
        # Inline jump host: keep its identifier in jump_host column for trace
        # purposes; full inline creds go into metadata_json (encrypted).
        jump_host_name = jump_host_value.get("name") or jump_host_value.get("host")
    else:
        jump_host_name = None

    return {
        "name": server.get("name"),
        "host": server.get("host"),
        "port": int(server.get("port") or 22),
        "user": server.get("user") or server.get("username") or "root",
        "key": server.get("key") or server.get("key_file") or "~/.ssh/id_rsa",
        "key_content": server.get("key_content"),
        "password": server.get("password"),
        "jump_host": jump_host_name,
        "status": server.get("status") or "online",
    }


def _server_dict_to_metadata(server: Dict[str, Any]) -> Dict[str, Any]:
    """Extract dict fields that don't map to Server columns → metadata_json."""
    extra: Dict[str, Any] = {}
    for field in _EXTRA_FIELDS:
        if field in server:
            extra[field] = server[field]
    if isinstance(server.get("jump_host"), dict):
        # Inline jump host dict (with potentially encrypted creds) preserved
        # verbatim in metadata_json. We do not re-encrypt here: the caller is
        # expected to pass secrets already via encrypt_server_secrets().
        extra["inline_jump_host"] = server["jump_host"]
    return extra


def _server_row_to_dict(row) -> Optional[Dict[str, Any]]:
    """ORM Server row → legacy config_kv-style dict (decrypted, metadata merged)."""
    if row is None:
        return None
    meta = row.metadata_json or {}
    result: Dict[str, Any] = {
        "id": row.id,
        "name": row.name,
        "host": row.host,
        "port": row.port,
        "user": row.user,
        "username": row.user,
        "key": row.key,
        "key_file": row.key,
        "password": row.password,
        "key_content": row.key_content,
        "jump_host": row.jump_host,
        "status": row.status or "online",
        "has_password": bool(row.password),
        "has_key_content": bool(row.key_content),
        "has_key": bool(row.key),
    }
    for k, v in meta.items():
        if k == "inline_jump_host" and isinstance(v, dict):
            # Preserve the original inline jump host shape.
            result["jump_host"] = v
        else:
            result[k] = v
    return result


# ─── Session helper (DB ownership tracking) ──────────────────────────────────


def _open_session(db):
    """If caller didn't pass a Session, open and own a new one."""
    from app.db.base import SessionLocal
    owns = db is None
    if owns:
        db = SessionLocal()
    return db, owns


# ─── Public read API (Phase 3a SSOT: DB) ─────────────────────────────────────


def get_all_servers(db=None) -> List[Dict[str, Any]]:
    """Return all servers from the Server table (decrypted)."""
    from app.db.repository import ServerRepository

    db, owns = _open_session(db)
    try:
        repo = ServerRepository(db)
        rows = repo.list_all()
        return [_server_row_to_dict(r) for r in rows if r is not None]
    finally:
        if owns:
            db.close()


def get_server_by_name(name: str, db=None) -> Optional[Dict[str, Any]]:
    """Return a single server by name (decrypted), or None if missing."""
    from app.db.repository import ServerRepository

    db, owns = _open_session(db)
    try:
        repo = ServerRepository(db)
        row = repo.get_by_name(name)
        return _server_row_to_dict(row)
    finally:
        if owns:
            db.close()


# ─── Public write API (Phase 3a SSOT: DB) ────────────────────────────────────


def save_server(server: Dict[str, Any], db=None) -> bool:
    """Persist a server row to the Server table (DB is SSOT).

    The input dict shape matches what the v2 API and the legacy config_kv
    layer used to accept (name/host/port/user/password/key/key_content/
    jump_host/status, plus extras like description/tags/group/sftp_allowed_roots/
    auth_type/enabled/username). This function does not touch config_kv.
    """
    from app.db.models import Server
    from app.db.repository import ServerRepository, _encrypt_server_fields

    name = (server or {}).get("name")
    if not name:
        raise ValueError("server['name'] is required")

    db, owns = _open_session(db)
    try:
        repo = ServerRepository(db)
        existing = repo._get_server_row_by_name(name)
        core = _server_dict_to_core(server)
        meta = _server_dict_to_metadata(server)

        if existing is None:
            new_server = Server(**core, metadata_json=meta or None)
            db.add(new_server)
            db.commit()
            db.refresh(new_server)
            logger.info("Created server (DB SSOT): %s", name)
        else:
            for k, v in core.items():
                setattr(existing, k, v)
            existing.metadata_json = meta or None
            _encrypt_server_fields(existing)
            db.commit()
            db.refresh(existing)
            logger.info("Updated server (DB SSOT): %s", name)

        try:
            from app.config.cache import invalidate_config_cache
            invalidate_config_cache()
        except Exception:
            logger.debug("Failed to invalidate config cache after save_server", exc_info=True)
        return True
    except Exception:
        logger.exception("save_server failed for %s", name)
        try:
            db.rollback()
        except Exception:
            logger.debug("Failed to rollback after save_server error", exc_info=True)
        return False
    finally:
        if owns:
            db.close()


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
    """Delete a server from the Server table (DB is SSOT).

    Refuses to delete if any DatabaseConnection still references it as an SSH
    bastion or target.
    """
    from app.db.repository import ServerRepository

    db, owns = _open_session(db)
    try:
        refs = _get_database_connection_references(db, name)
        if refs:
            ref_names = ", ".join({r["connection_name"] for r in refs})
            raise ValueError(f"服务器 '{name}' 正被以下数据库连接引用: {ref_names}。请先修改或删除相关数据库连接。")

        repo = ServerRepository(db)
        row = repo._get_server_row_by_name(name)
        if row is None:
            logger.info("delete_server: %s not found", name)
            return False
        db.delete(row)
        db.commit()
        try:
            from app.config.cache import invalidate_config_cache
            invalidate_config_cache()
        except Exception:
            logger.debug("Failed to invalidate config cache after delete_server", exc_info=True)
        logger.info("Deleted server (DB SSOT): %s", name)
        return True
    except ValueError:
        raise
    except Exception:
        logger.exception("delete_server failed for %s", name)
        try:
            db.rollback()
        except Exception:
            logger.debug("Failed to rollback after delete_server error", exc_info=True)
        return False
    finally:
        if owns:
            db.close()


# ─── Jump hosts (still read from config_kv; pending dedicated JumpHost model) ──


def get_jump_host_by_name(name: str) -> Optional[Dict[str, Any]]:
    # TODO(Phase 3.x): 引入 JumpHost 模型后,迁移到 DB 读取。
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


# ─── Maintenance helpers (one-time secret encryption, etc.) ──────────────────


def migrate_server_secrets_at_rest() -> int:
    """Phase 3a: 重新加密 DB 中已存在的明文 secret(password / key_content)。"""
    from app.db.models import Server
    from app.core.secret_store import encrypt_secret

    db, owns = _open_session(None)
    try:
        changed = 0
        for row in db.query(Server).all():
            touched = False
            if row.password and not is_encrypted(row.password):
                row.password = encrypt_secret(row.password)
                touched = True
            if row.key_content and not is_encrypted(row.key_content):
                row.key_content = encrypt_secret(row.key_content)
                touched = True
            if touched:
                changed += 1
        if changed:
            db.commit()
            try:
                from app.config.cache import invalidate_config_cache
                invalidate_config_cache()
            except Exception:
                logger.debug("Failed to invalidate cache after server secret migration", exc_info=True)
            logger.info("Encrypted %s server rows' plaintext secrets (at rest)", changed)
        return changed
    finally:
        if owns:
            db.close()


# ─── Reference lookup (Phase 3a: query DB tables) ────────────────────────────


def get_server_references(server_name: str) -> List[Dict[str, Any]]:
    """Find DB rows that reference a server by name.

    Replaces the old config_kv-based lookup, which read systems[].servers /
    systems[].environments[].servers / systems[].groups[].server. Now we query
    ServerGroup.server_names, Service.servers, and Deployment.servers.
    """
    from app.db.models import Deployment, ServerGroup, Service

    db, owns = _open_session(None)
    try:
        refs: List[Dict[str, Any]] = []

        for g in db.query(ServerGroup).all():
            if server_name in (g.server_names or []):
                refs.append({
                    "type": "server_group",
                    "group": g.name,
                    "display_name": g.display_name or g.name,
                })

        for svc in db.query(Service).all():
            if server_name in (svc.servers or []):
                refs.append({
                    "type": "service",
                    "system": svc.system_name,
                    "service": svc.name,
                })

        # Deployment.servers is a comma/space separated string. Use simple
        # substring check to avoid missing entries with different separators.
        for dep in db.query(Deployment).all():
            servers_blob = (dep.servers or "").replace(",", " ").split()
            if server_name in servers_blob:
                refs.append({
                    "type": "deployment",
                    "deployment_id": dep.id,
                    "system": dep.system,
                    "status": dep.status,
                })

        return refs
    finally:
        if owns:
            db.close()
