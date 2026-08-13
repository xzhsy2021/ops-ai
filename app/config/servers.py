import copy
import logging
from typing import Any, Dict, List, Optional

from app.core.secret_store import decrypt_secret, encrypt_secret, is_encrypted

logger = logging.getLogger(__name__)

# Server and jump-host inventory is stored in dedicated database tables.

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
    """Convert a Server row to the API-compatible decrypted dictionary."""
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


def get_server_by_id(server_id: str, db=None) -> Optional[Dict[str, Any]]:
    """Return a single server by its primary-key UUID (decrypted), or None."""
    from app.db.repository import ServerRepository

    db, owns = _open_session(db)
    try:
        repo = ServerRepository(db)
        row = repo.get_by_id(server_id)
        return _server_row_to_dict(row)
    finally:
        if owns:
            db.close()


_HEX_SHORT_RE = __import__("re").compile(r"^[0-9a-fA-F]{1,32}$")


def resolve_server(key: Optional[str], db=None) -> Optional[Dict[str, Any]]:
    """Resolve a server by ANY identifier (name / host / full UUID / short UUID prefix).

    Why this exists
    ---------------
    Path A (`ops.inspection.*`) is the primary inspection entry point; it accepts
    server name / UUID / host interchangeably through `inspection_center`. But
    Path B (single-shot probes like `ops.check_disk`, `ops.run_health_check`,
    `ops.check_process`) used to fall back to `get_server_by_name`, which
    failed whenever callers passed the UUID they got from
    `/api/v2/servers` (whose `id` field is the DB primary key, not the name).

    This helper closes the gap so Path B remains a usable fallback. It tries
    multiple lookup strategies in order:

    1. Exact ``name`` match.
    2. Exact ``id`` (UUID) match.
    3. Short-prefix UUID match — if the caller passed a hex string of 4-32
       characters that uniquely matches the start of a row's ``id``.
    4. Exact ``host`` match.

    Returns the server dict (with the canonical name preserved) or ``None``.
    """
    if not key:
        return None
    wanted = str(key).strip()
    if not wanted:
        return None

    # 1) name match (highest priority — preserves existing Path B behavior)
    srv = get_server_by_name(wanted, db=db)
    if srv:
        return srv

    # 2) exact UUID match
    if _HEX_SHORT_RE.match(wanted) and len(wanted) >= 8:
        srv = get_server_by_id(wanted, db=db)
        if srv:
            return srv

    # 3) short-prefix UUID match — only meaningful for short hex strings
    if _HEX_SHORT_RE.match(wanted) and 4 <= len(wanted) < 32:
        try:
            db_session, owns = _open_session(db)
            try:
                from app.db.models import Server
                rows = (
                    db_session.query(Server)
                    .filter(Server.id.like(f"{wanted}%"))
                    .all()
                )
                if len(rows) == 1:
                    return _server_row_to_dict(rows[0])
                if len(rows) > 1:
                    # ambiguous — let the caller disambiguate by full UUID or name
                    return None
            finally:
                if owns:
                    db_session.close()
        except Exception:
            pass

    # 4) host match
    try:
        all_servers = get_all_servers(db=db)
    except Exception:
        all_servers = []
    for srv in all_servers or []:
        if str(srv.get("host") or "").strip() == wanted:
            return srv
    return None



# ─── Public write API (Phase 3a SSOT: DB) ────────────────────────────────────


def save_server(server: Dict[str, Any], db=None) -> bool:
    """Persist a server row to the Server table (DB is SSOT).

    The input dict accepts name/host/port/user/password/key/key_content/
    jump_host/status, plus extras like description/tags/group/sftp_allowed_roots/
    auth_type/enabled/username).
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
        refs = get_server_references(name, db=db)
        # 兜底：如果只剩 server_group 类型引用，先把组里 server_names 中的残留清理掉再继续删
        # （避免前端两步操作；历史脏数据常被该问题困扰）
        _group_refs = [r for r in refs if r.get("type") == "server_group"]
        _other_refs = [r for r in refs if r.get("type") != "server_group"]
        if _group_refs and not _other_refs:
            from app.db.models import ServerGroup
            try:
                for ref in _group_refs:
                    g = db.query(ServerGroup).filter_by(name=ref.get("group")).first()
                    if g:
                        names = [x for x in (g.server_names or []) if x != name]
                        if names != list(g.server_names or []):
                            g.server_names = names
                db.commit()
                refs = get_server_references(name, db=db)
            except Exception as e:
                db.rollback()
                logger.warning(f"delete_server: clean server_group refs for '{name}' failed: {e}")

        if refs:
            ref_labels = sorted({
                "/".join(str(part) for part in (
                    ref.get("type"),
                    ref.get("system"),
                    ref.get("environment") or ref.get("service") or ref.get("group"),
                    ref.get("connection_name") or ref.get("deployment_id"),
                ) if part)
                for ref in refs
            })
            raise ValueError(
                f"Server '{name}' is referenced by: {', '.join(ref_labels)}. "
                "Remove those references before deleting the server."
            )

        repo = ServerRepository(db)
        row = repo._get_server_row_by_name(name)
        if row is None:
            logger.info("delete_server: %s not found", name)
            return False
        db.delete(row)
        db.commit()
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


# ─── Jump hosts ─────────────────────────────────────────────────────────────


def get_jump_host_by_name(name: str) -> Optional[Dict[str, Any]]:
    """Phase 3.g: read jump host metadata from the `jump_hosts` table.

    Returns a plain dict (decrypted) so SSH clients can consume the
    connection parameters without depending on the ORM layer. Returns
    None when no jump host with this name exists.
    """
    from app.db import SessionLocal
    from app.db.repository import JumpHostRepository
    if not name:
        return None
    with SessionLocal() as db:
        row = JumpHostRepository(db).get_by_name(name)
        if row is None:
            return None
        return {
            "name": row.name,
            "host": row.host,
            "port": row.port,
            "user": row.user,
            "username": row.user,
            "key": row.key,
            "key_content": row.key_content,
            "password": row.password,
            "status": row.status,
        }


def get_jump_host_by_host(host: str, port: int = 22) -> Optional[Dict[str, Any]]:
    """Phase 3.g: 按 host:port 查 jump_hosts DB 表。用于 dict 形式 jump_host 缺凭据时的补全。"""
    from app.db import SessionLocal
    from app.db.models import JumpHost
    if not host:
        return None
    with SessionLocal() as db:
        row = db.query(JumpHost).filter(JumpHost.host == host, JumpHost.port == port).first()
        if row is None:
            return None
        return {
            "name": row.name,
            "host": row.host,
            "port": row.port,
            "user": row.user,
            "username": row.user,
            "key": row.key,
            "key_content": row.key_content,
            "password": row.password,
            "status": row.status,
        }


def get_server_as_jump_by_name(name: str) -> Optional[Dict[str, Any]]:
    """按 name 查 servers DB 表并转为 jump config dict（供 jump_host 名称 fallback）。"""
    from app.db import SessionLocal, ServerRepository
    if not name:
        return None
    with SessionLocal() as db:
        srv = ServerRepository(db).get_by_name(name)
        if srv is None:
            return None
        return _server_to_jump_dict(srv)


def get_server_as_jump_by_host(host: str, port: int = 22) -> Optional[Dict[str, Any]]:
    """按 host:port 查 servers DB 表并转为 jump config dict。"""
    from app.db import SessionLocal
    from app.db.models import Server
    if not host:
        return None
    with SessionLocal() as db:
        row = db.query(Server).filter(Server.host == host, Server.port == port).first()
        if row is None:
            return None
        return _server_to_jump_dict(row)


def _server_to_jump_dict(srv) -> Dict[str, Any]:
    """把 Server ORM 行转为 jump config dict 形态。"""
    return {
        "name": srv.name,
        "host": srv.host,
        "port": srv.port,
        "user": srv.user,
        "username": srv.user,
        "key": srv.key,
        "key_content": srv.key_content,
        "password": srv.password,
    }


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
            logger.info("Encrypted %s server rows' plaintext secrets (at rest)", changed)
        return changed
    finally:
        if owns:
            db.close()


# ─── Reference lookup (Phase 3a: query DB tables) ────────────────────────────


def get_server_references(server_name: str, db=None) -> List[Dict[str, Any]]:
    """Find DB rows that reference a server by name.

    Covers every domain table that stores a server name.
    """
    from app.db.models import (
        Deployment,
        ServerGroup,
        Service,
        System,
        SystemEnvironment,
    )

    db, owns = _open_session(db)
    try:
        refs: List[Dict[str, Any]] = []

        for system in db.query(System).all():
            if server_name in (system.servers or []):
                refs.append({
                    "type": "system",
                    "system": system.name,
                })

        for environment in db.query(SystemEnvironment).all():
            if server_name in (environment.servers or []):
                refs.append({
                    "type": "system_environment",
                    "system": environment.system_name,
                    "environment": environment.name,
                })

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

        refs.extend(_get_database_connection_references(db, server_name))

        return refs
    finally:
        if owns:
            db.close()
