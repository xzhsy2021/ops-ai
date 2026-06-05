"""Server asset helpers for database SSH proxy configuration.

The database connection form needs to choose bastion and target hosts from the
same server inventory used by the server workspace.  In older builds, servers
may exist either in the JSON config inventory or in the SQLite servers table, so
this module merges both sources and exposes safe browser-facing records.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional


def _server_obj_to_dict(server: Any) -> Dict[str, Any]:
    if server is None:
        return {}
    if isinstance(server, dict):
        return dict(server)
    result: Dict[str, Any] = {}
    for key in (
        "id", "name", "host", "port", "user", "username", "key", "key_file",
        "key_content", "password", "jump_host", "group", "auth_type", "created_at", "updated_at",
        "sftp_allowed_roots", "allowed_roots", "file_roots", "description", "tags", "status", "enabled",
    ):
        if hasattr(server, key):
            value = getattr(server, key)
            if value is not None:
                result[key] = value
    return result


def _coerce_allowed_roots(server: Dict[str, Any]) -> Optional[list]:
    raw = (
        server.get("sftp_allowed_roots")
        if server.get("sftp_allowed_roots") is not None
        else server.get("allowed_roots")
        if server.get("allowed_roots") is not None
        else server.get("file_roots")
    )
    if raw is None:
        return None
    if isinstance(raw, str):
        items = [r.strip() for r in raw.replace("\n", ",").split(",")]
    else:
        try:
            items = [str(r).strip() for r in raw]
        except TypeError:
            return None
    items = [r for r in items if r]
    return items


def _normalize_server(server: Dict[str, Any], source: str) -> Optional[Dict[str, Any]]:
    name = str(server.get("name") or "").strip()
    host = str(server.get("host") or "").strip()
    if not name and not host:
        return None
    port = server.get("port") or 22
    try:
        port = int(port)
    except Exception:
        port = 22
    user = server.get("user") or server.get("username") or "root"
    key = server.get("key") or server.get("key_file") or ""
    password = server.get("password")
    key_content = server.get("key_content")
    auth_type = server.get("auth_type") or ("key_content" if key_content else "key_file" if key else "password" if password else "")
    allowed_roots = _coerce_allowed_roots(server)
    raw_status = str(server.get("status") or "").strip().lower()
    if server.get("enabled") is False or raw_status in {"disabled", "停用", "inactive", "off"}:
        asset_status = "disabled"
    elif raw_status in {"offline", "离线"}:
        asset_status = "offline"
    else:
        asset_status = raw_status or "online"
    result = {
        "id": server.get("id") or name or host,
        "name": name or host,
        "host": host,
        "port": port,
        "user": user,
        "username": user,
        "key": key,
        "key_file": key,
        "jump_host": server.get("jump_host") or "",
        "group": server.get("group") or server.get("environment") or "",
        "auth_type": auth_type,
        "has_password": bool(password),
        "has_key_content": bool(key_content),
        "has_key": bool(key),
        "source": source,
        "status": asset_status,
        "enabled": asset_status != "disabled",
    }
    if allowed_roots is not None:
        result["sftp_allowed_roots"] = allowed_roots
    return result


def _merge_server_lists(*lists: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_name: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for items in lists:
        for raw in items or []:
            item = _normalize_server(raw, raw.get("source") or "inventory")
            if not item:
                continue
            key = item.get("name") or item.get("host")
            if key not in by_name:
                order.append(key)
                by_name[key] = item
            else:
                # Prefer the richer record if the earlier one was sparse.
                existing = by_name[key]
                for field in ("host", "user", "username", "key", "key_file", "jump_host", "group", "auth_type", "status", "enabled"):
                    if not existing.get(field) and item.get(field):
                        existing[field] = item[field]
                if not existing.get("sftp_allowed_roots") and item.get("sftp_allowed_roots"):
                    existing["sftp_allowed_roots"] = item["sftp_allowed_roots"]
                existing["has_password"] = bool(existing.get("has_password") or item.get("has_password"))
                existing["has_key_content"] = bool(existing.get("has_key_content") or item.get("has_key_content"))
                existing["has_key"] = bool(existing.get("has_key") or item.get("has_key"))
    result = [by_name[k] for k in order]
    result.sort(key=lambda x: (str(x.get("group") or ""), str(x.get("name") or x.get("host") or "")))
    return result


def list_server_assets(db=None) -> List[Dict[str, Any]]:
    config_servers: List[Dict[str, Any]] = []
    db_servers: List[Dict[str, Any]] = []
    try:
        from config_manager import get_all_servers
        config_servers = [_normalize_server(s, "config") for s in get_all_servers()]
        config_servers = [s for s in config_servers if s]
    except Exception:
        config_servers = []

    if db is not None:
        # Browser-facing asset discovery must not require decrypting stored
        # passwords or inline private keys.  Some deployments have encrypted
        # server secrets but run the UI without OPS_SECRET_KEY in read-only
        # contexts; ServerRepository.list_all() would attempt to decrypt and
        # then silently drop every DB-backed server from the selector.  Read the
        # non-secret columns directly first, and only expose boolean secret
        # markers to the browser.
        try:
            from app.db.models import Server
            rows = db.query(Server).all()
            db_servers = []
            for row in rows:
                # Phase 3a SSOT migration stores extra non-secret fields
                # (group, description, tags, auth_type, sftp_allowed_roots,
                # enabled, username) in the JSON `metadata_json` column. They
                # must be merged into the API response so the UI sidebar /
                # group dropdown / jump-host column render correctly.
                meta = getattr(row, "metadata_json", None) or {}
                item: Dict[str, Any] = {
                    "id": getattr(row, "id", None),
                    "name": getattr(row, "name", None),
                    "host": getattr(row, "host", None),
                    "port": getattr(row, "port", None),
                    "user": getattr(row, "user", None),
                    "username": getattr(row, "user", None),
                    "key": getattr(row, "key", None),
                    "key_file": getattr(row, "key", None),
                    "jump_host": getattr(row, "jump_host", None),
                    "status": getattr(row, "status", None) or "online",
                    "has_password": bool(getattr(row, "password", None)),
                    "has_key_content": bool(getattr(row, "key_content", None)),
                    "source": "database",
                }
                # Overlay metadata_json fields. Only set when present to avoid
                # clobbering column-derived defaults (e.g. status='online').
                for meta_key in (
                    "group",
                    "description",
                    "tags",
                    "auth_type",
                    "sftp_allowed_roots",
                    "allowed_roots",
                    "file_roots",
                    "enabled",
                ):
                    if meta_key in meta and meta[meta_key] is not None:
                        item[meta_key] = meta[meta_key]
                db_servers.append(item)
            db_servers = [_normalize_server(s, "database") for s in db_servers]
            db_servers = [s for s in db_servers if s]
        except Exception:
            db_servers = []
    return _merge_server_lists(config_servers, db_servers)


def _normalize_server_with_secrets(server: Dict[str, Any], source: str) -> Optional[Dict[str, Any]]:
    item = _normalize_server(server, source)
    if not item:
        return None
    # Execution code needs the real credentials in memory. This helper is only
    # used server-side; browser-facing lists must use list_server_assets().
    item["password"] = server.get("password")
    item["key_content"] = server.get("key_content")
    item["key"] = server.get("key") or server.get("key_file") or item.get("key")
    item["key_file"] = server.get("key_file") or server.get("key") or item.get("key_file")
    item["user"] = server.get("user") or server.get("username") or item.get("user")
    item["username"] = item.get("user")
    return item


def get_server_asset(name: Optional[str], db=None) -> Optional[Dict[str, Any]]:
    """Resolve a server asset by name, preserving secrets for backend use only."""
    if not name:
        return None
    wanted = str(name).strip()

    # Prefer legacy config lookup to preserve existing behavior and jump-host
    # compatibility.
    try:
        from config_manager import get_server_by_name
        server = get_server_by_name(wanted)
        if server:
            return _normalize_server_with_secrets(server, "config")
    except Exception:
        pass

    if db is not None:
        try:
            from app.db.repository import ServerRepository
            server = ServerRepository(db).get_by_name(wanted)
            if server:
                return _normalize_server_with_secrets(_server_obj_to_dict(server), "database")
        except Exception:
            pass
    return None
