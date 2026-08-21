"""服务器管理 API - V2"""
import time
import logging
import shlex
from contextlib import contextmanager
from typing import Dict
from fastapi import APIRouter, HTTPException, Request, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from config_manager import save_server, delete_server
from app.domain.inventory import inventory
from app.config.servers import redact_server_secrets
from app.api.helpers import api_response, audit
from app.db import get_db
from app.db.models import Server, Service
from app.db.repository import ServerGroupRepository
from app.core.auth_v2 import require_auth, require_admin
from app.core.command_security import validate_command, sanitize_command_output
from app.services.remote_access import build_audit_context, format_audit_detail, get_hop_summary
from app.services.command_history import record_execution, query_executions, get_execution, delete_execution, delete_executions, count_executions, log_to_dict
from app.services.server_ops import analyze_server_config, summarize_servers, build_server_health_probe_result

logger = logging.getLogger(__name__)

servers_v2_router = APIRouter(prefix="/api/v2/servers", tags=["服务器工作台"])

_MAX_OUTPUT_LENGTH = 50000

_SENSITIVE_PATTERNS = [
    (r'--password[= ]\S+', '--password=***'),
    (r'-p[= ]\S+', '-p ***'),
    (r'Authorization:\s*\S+', 'Authorization: ***'),
    (r'token[= ]\S+', 'token=***'),
    (r'passwd[= ]\S+', 'passwd=***'),
    (r'PASSWORD[= ]\S+', 'PASSWORD=***'),
]

import re as _re

def _mask_sensitive_command(command: str) -> str:
    masked = command
    for pattern, replacement in _SENSITIVE_PATTERNS:
        masked = _re.sub(pattern, replacement, masked, flags=_re.IGNORECASE)
    return masked
_MAX_EXEC_TIMEOUT = 60


class DeleteCommandExecutionsPayload(BaseModel):
    log_ids: list[str] = Field(default_factory=list, max_length=200)


def _get_ssh(name: str):
    srv = inventory.get_server(name)
    if not srv:
        raise HTTPException(status_code=404, detail=f"Server '{name}' not found")

    from app.services.remote_access import _detect_auth_mode
    auth_mode = _detect_auth_mode(srv)
    if auth_mode == "none":
        host = srv.get("host", "unknown")
        raise HTTPException(
            status_code=400,
            detail=f"服务器 '{name}' ({host}) 缺少认证凭据。请在服务器管理中设置密码、密钥文件或密钥内容后重试。"
        )

    from ssh_client import create_ssh_client
    try:
        ssh = create_ssh_client(srv)
        if not ssh:
            raise HTTPException(status_code=503, detail=f"Cannot connect to {name}")
        return ssh, srv
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Cannot connect to {name}: {e}")


@contextmanager
def _ssh_session(name: str):
    """Borrow a pooled SSH client. Leaves it in the pool on success; closes
    (and evicts) on exception so a broken transport isn't reused."""
    ssh, srv = _get_ssh(name)
    try:
        yield ssh, srv
    except Exception:
        try:
            ssh.close()
        except Exception:
            logger.debug("ssh.close() after error failed", exc_info=True)
        raise


@servers_v2_router.get("")
def list_servers_v2(request: Request, db: Session = Depends(get_db), with_status: bool = True):
    require_auth(request, db)
    # Merge legacy JSON-config servers and SQLite server assets. Some team
    # installations use the DB-backed server table while older code only read
    # config_manager.get_all_servers(), which made the database proxy selector
    # look empty even though the server workspace had assets.
    from app.maintenance.server_assets import list_server_assets
    servers = []
    for item in list_server_assets(db):
        safe = dict(item)
        if with_status:
            try:
                safe["config_status"] = analyze_server_config({
                    "name": safe.get("name"),
                    "host": safe.get("host"),
                    "port": safe.get("port"),
                    "user": safe.get("user") or safe.get("username"),
                    "key": safe.get("key"),
                    "password": "********" if safe.get("has_password") else None,
                    "key_content": "********" if safe.get("has_key_content") else None,
                    "auth_type": safe.get("auth_type"),
                })
            except Exception:
                safe["config_status"] = {"status": "unknown", "warnings": ["无法分析服务器配置"]}
        servers.append(safe)
    return api_response(data=servers)


@servers_v2_router.get("/ops-summary")
def server_ops_summary(request: Request, db: Session = Depends(get_db)):
    require_auth(request, db)
    return api_response(data=summarize_servers())


@servers_v2_router.get("/{name}/ops-health")
def server_ops_health(request: Request, name: str, force: bool = False, db: Session = Depends(get_db)):
    require_auth(request, db)
    result = build_server_health_probe_result(name, force=force)
    audit("server.health_probe", "server", name, f"user={request.state.username} status={result.get('status')} cache_hit={result.get('cache_hit')}")
    return api_response(data=result)


@servers_v2_router.get("/groups/list")
def list_server_groups_v2(request: Request, db: Session = Depends(get_db)):
    """Phase 3h: SSOT migration for the sidebar group list.

    Reads from the `server_groups` table (DB is canonical) instead of deriving
    from legacy `inventory.list_servers()`. Counts per group are computed from
    `servers.metadata_json` (`$.group`).
    """
    require_auth(request, db)
    repo = ServerGroupRepository(db)

    # Per-group server counts from the DB SSOT.
    rows = (
        db.query(Server.metadata_json)
        .filter(Server.metadata_json.isnot(None))
        .all()
    )
    import json
    counts: Dict[str, int] = {}
    for (meta_raw,) in rows:
        meta = meta_raw if isinstance(meta_raw, dict) else (json.loads(meta_raw) if meta_raw else {})
        g = (meta or {}).get("group") or ""
        if g:
            counts[g] = counts.get(g, 0) + 1

    # Build response from the SSOT table (so empty groups are still listed).
    result = []
    for g in repo.list_all():
        result.append({
            "name": g.name,
            "display_name": g.display_name or g.name,
            "description": g.description,
            "is_default": False,
            "server_count": counts.get(g.name, 0),
            "server_names": g.server_names or [],
        })
    result.sort(key=lambda x: (x["is_default"], x["name"]))
    # Ungrouped bucket = servers whose `metadata_json.group` is missing/empty.
    from sqlalchemy import func, or_
    json_extract = func.json_extract
    g_expr = json_extract(Server.metadata_json, "$.group")
    ungrouped_count = (
        db.query(Server)
        .filter(
            or_(
                Server.metadata_json.is_(None),
                g_expr.is_(None),
                g_expr == "",
            )
        )
        .count()
    )
    return api_response(data=result + [{
        "name": "未分组",
        "display_name": "未分组",
        "description": None,
        "is_default": True,
        "server_count": ungrouped_count,
        "server_names": [],
    }])


@servers_v2_router.post("/groups/create")
async def create_server_group(request: Request, db: Session = Depends(get_db)):
    """Phase 3i: also write to the `server_groups` DB table (SSOT).

    Legacy behaviour cleared: this endpoint no longer operates on KV only.
    """
    require_admin(request, db)
    data = await request.json()
    group_name = (data.get("name") or "").strip()
    display_name = (data.get("display_name") or group_name).strip() or group_name
    if not group_name:
        raise HTTPException(status_code=400, detail="name is required")

    repo = ServerGroupRepository(db)
    existed = repo.get_by_name(group_name) is not None
    if not existed:
        repo.create(
            name=group_name,
            display_name=display_name,
            description=data.get("description") or "",
            server_names=[],
        )

    # Legacy KV mirror: ensure the bucket is consistent for older readers.
    servers = inventory.list_servers()
    kv_existed = any(s.get("group") == group_name for s in servers)
    audit("server.create_group", "server", group_name,
          f"user={request.state.username} existed={existed} kv_existed={kv_existed}")
    return api_response(data={"name": group_name, "existed": existed})


@servers_v2_router.post("/groups/assign")
async def assign_server_group(request: Request, db: Session = Depends(get_db)):
    """Phase 3i: also write to `servers.metadata_json.group` and the
    `server_groups.server_names` JSON column (SSOT)."""
    require_admin(request, db)
    data = await request.json()
    server_names = data.get("server_names", [])
    group = (data.get("group") or "").strip()
    if not server_names and not group:
        raise HTTPException(status_code=400, detail="server_names or group is required")
    updated = []
    failed = []
    from app.db.models import Server
    for name in server_names:
        srv = inventory.get_server(name)
        if not srv:
            failed.append({"name": name, "error": "not found"})
            continue
        srv["group"] = group
        if save_server(srv):
            updated.append(name)
        else:
            failed.append({"name": name, "error": "save failed"})

    # Sync server_groups.server_names for the target group (DB SSOT).
    if group:
        g = ServerGroupRepository(db).get_by_name(group)
        if g is not None:
            current = set(g.server_names or [])
            current.update(server_names)
            g.server_names = sorted(current)
            ServerGroupRepository(db).update(g)
        # And remove from any other group these servers used to belong to.
        affected = set(server_names)
        for other in ServerGroupRepository(db).list_all():
            if other.name == group:
                continue
            if not other.server_names:
                continue
            if any(n in affected for n in other.server_names):
                other.server_names = [n for n in (other.server_names or []) if n not in affected]
                ServerGroupRepository(db).update(other)

    audit("server.assign_group", "server", ",".join(server_names),
          f"user={request.state.username} group={group or '(未分组)'}")
    return api_response(data={"updated": updated, "failed": failed})


@servers_v2_router.post("/groups/rename")
async def rename_server_group(request: Request, db: Session = Depends(get_db)):
    """Phase 3i: also rename the `server_groups` DB row + rewrite all
    `servers.metadata_json.group` references (SSOT)."""
    require_admin(request, db)
    data = await request.json()
    old_name = (data.get("old_name") or "").strip()
    new_name = (data.get("new_name") or "").strip()
    if not old_name or not new_name:
        raise HTTPException(status_code=400, detail="old_name and new_name are required")

    # 1) Legacy KV mirror (for old readers)
    servers = inventory.list_servers()
    existing_conflict = any(srv.get("group") == new_name and srv.get("group") != old_name for srv in servers)
    if existing_conflict:
        raise HTTPException(status_code=409, detail=f"Group '{new_name}' already exists on some servers")
    updated = 0
    for srv in servers:
        if srv.get("group") == old_name:
            srv["group"] = new_name
            if save_server(srv):
                updated += 1

    # 2) DB SSOT: rename the `server_groups` row
    repo = ServerGroupRepository(db)
    g = repo.get_by_name(old_name)
    if g is not None:
        g.name = new_name
        repo.update(g)

    # 3) DB SSOT: rewrite metadata_json.group on every server that pointed
    # at the old name (covers servers not touched by save_server above).
    from app.db.models import Server
    from sqlalchemy import or_, func
    g_expr = func.json_extract(Server.metadata_json, "$.group")
    rows = db.query(Server).filter(g_expr == old_name).all()
    for s in rows:
        meta = s.metadata_json if isinstance(s.metadata_json, dict) else {}
        meta = dict(meta or {})
        meta["group"] = new_name
        s.metadata_json = meta
    if rows:
        db.commit()

    audit("server.rename_group", "server", old_name,
          f"user={request.state.username} new_name={new_name} affected={updated} meta_rewrite={len(rows)}")
    return api_response(data={"updated": updated, "meta_rewrite": len(rows)})


@servers_v2_router.post("/groups/delete")
async def delete_server_group(request: Request, db: Session = Depends(get_db)):
    """Phase 3i fix: also delete the `server_groups` DB row (SSOT).

    Bug before fix: this endpoint only cleared `servers[*].group` in the
    Server table but never removed the corresponding `server_groups` row.
    Since the sidebar reads from the `server_groups` table, the group
    remained visible to the user despite the "success" toast.
    """
    require_admin(request, db)
    data = await request.json()
    group_name = (data.get("name") or "").strip()
    if not group_name:
        raise HTTPException(status_code=400, detail="name is required")

    # 1) Clear group metadata on every server that has it.
    servers = inventory.list_servers()
    updated = 0
    for srv in servers:
        if srv.get("group") == group_name:
            srv["group"] = ""
            if save_server(srv):
                updated += 1

    # 2) Ensure metadata_json.group is cleared for the same servers.
    from app.db.models import Server
    from sqlalchemy import or_, func
    g_expr = func.json_extract(Server.metadata_json, "$.group")
    rows = db.query(Server).filter(g_expr == group_name).all()
    for s in rows:
        meta = s.metadata_json if isinstance(s.metadata_json, dict) else {}
        meta = dict(meta or {})
        meta["group"] = ""
        s.metadata_json = meta
    if rows:
        db.commit()

    # 3) Delete the server_groups row itself (the actual bug fix)
    repo = ServerGroupRepository(db)
    g = repo.get_by_name(group_name)
    deleted = False
    if g is not None:
        deleted = repo.delete(g.id)

    audit("server.delete_group", "server", group_name,
          f"user={request.state.username} cleared={updated} "
          f"meta_rewrite={len(rows)} row_deleted={deleted}")
    return api_response(data={
        "updated": updated,
        "meta_rewrite": len(rows),
        "row_deleted": deleted,
    })


@servers_v2_router.put("/batch")
async def batch_update_servers(request: Request, db: Session = Depends(get_db)):
    require_admin(request, db)
    data = await request.json()
    names = data.get("names", [])
    updates = data.get("updates", {})

    if not names or not isinstance(names, list):
        raise HTTPException(status_code=400, detail="names list is required")
    if len(names) == 0:
        raise HTTPException(status_code=400, detail="at least one server name required")
    if len(names) > 50:
        raise HTTPException(status_code=400, detail="batch update limited to 50 servers")

    updated = []
    failed = []
    for name in names:
        existing = inventory.get_server(name)
        if not existing:
            failed.append({"name": name, "error": "not found"})
            logger.warning(f"Batch update: server '{name}' not found, skipped")
            continue

        updated_server = dict(existing)
        changed = False

        if "description" in updates:
            updated_server["description"] = updates["description"]
            changed = True
        if "tags" in updates:
            updated_server["tags"] = updates["tags"]
            changed = True
        if "jump_host" in updates:
            if updates["jump_host"] == "__clear__":
                updated_server["jump_host"] = None
            else:
                updated_server["jump_host"] = updates["jump_host"]
            changed = True
        if "username" in updates and updates["username"]:
            updated_server["username"] = updates["username"]
            changed = True
        if "auth_type" in updates and updates["auth_type"]:
            updated_server["auth_type"] = updates["auth_type"]
            changed = True
        if "port" in updates and updates["port"]:
            updated_server["port"] = int(updates["port"])
            changed = True
        if "group" in updates:
            updated_server["group"] = updates["group"]
            changed = True

        if not changed:
            updated.append(name)
            continue

        if not save_server(updated_server):
            logger.error(f"Batch update: failed to save server '{name}', user={request.state.username}")
            failed.append({"name": name, "error": "save failed"})
            continue

        logger.info(f"Batch update: server '{name}' updated by user={request.state.username}")
        updated.append(name)

    logger.info(f"Batch update completed: {len(updated)} updated, {len(failed)} failed, user={request.state.username}")
    audit("server.batch_update", "server", ",".join(names),
          f"user={request.state.username} updated={len(updated)} failed={len(failed)}")

    return api_response(
        data={"updated": updated, "failed": failed, "total": len(names)},
        message=f"Batch update: {len(updated)} succeeded, {len(failed)} failed"
    )


@servers_v2_router.get("/exec/history")
def all_exec_history(
    request: Request,
    limit: int = 50, offset: int = 0,
    server_name: str = None, username: str = None,
    risk_level: str = None,
    db: Session = Depends(get_db),
):
    require_admin(request, db)
    entries = query_executions(db, server_name=server_name, username=username,
                               risk_level=risk_level, limit=limit, offset=offset)
    total = count_executions(db, server_name=server_name, username=username,
                             risk_level=risk_level)
    return api_response(data={
        "entries": [log_to_dict(e) for e in entries],
        "total": total,
        "limit": limit,
        "offset": offset,
    })


@servers_v2_router.post("/exec/history/delete")
def delete_exec_histories(payload: DeleteCommandExecutionsPayload, request: Request, db: Session = Depends(get_db)):
    user = require_admin(request, db)
    result = delete_executions(db, payload.log_ids)
    audit("server.exec.history.delete_many", "command_execution", ",".join(result.get("log_ids") or []), f"user={user.get('username')} deleted={result.get('deleted')}")
    return api_response(data=result, message="Command execution histories deleted")


@servers_v2_router.delete("/exec/history/{log_id}")
def delete_exec_history(request: Request, log_id: str, db: Session = Depends(get_db)):
    user = require_admin(request, db)
    result = delete_execution(db, log_id)
    audit("server.exec.history.delete", "command_execution", log_id, f"user={user.get('username')}")
    return api_response(data=result, message="Command execution history deleted")


@servers_v2_router.get("/exec/history/{log_id}")
def exec_history_detail(request: Request, log_id: str, db: Session = Depends(get_db)):
    require_admin(request, db)
    entry = get_execution(db, log_id)
    if not entry:
        raise HTTPException(status_code=404, detail=f"Execution log '{log_id}' not found")
    return api_response(data=log_to_dict(entry))


@servers_v2_router.post("")
async def create_server_v2(request: Request, db: Session = Depends(get_db)):
    require_admin(request, db)
    data = await request.json()
    name = (data.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    if inventory.get_server(name):
        raise HTTPException(status_code=409, detail=f"Server '{name}' already exists")
    host = (data.get("host") or "").strip()
    if not host:
        raise HTTPException(status_code=400, detail="host is required")
    port = int(data.get("port", 22))
    username = data.get("username", data.get("user", "root"))
    auth_type = data.get("auth_type", "password")
    password = data.get("password")
    key_file = data.get("key") or data.get("key_file")
    key_content = data.get("key_content")
    if data.get("key_content") and not isinstance(data.get("key_content"), str):
        raise HTTPException(status_code=400, detail="key_content must be a string")

    if auth_type == "password" and not data.get("password"):
        raise HTTPException(status_code=400, detail="密码认证模式下必须提供密码")
    if auth_type == "key_file" and not data.get("key"):
        raise HTTPException(status_code=400, detail="密钥文件认证模式下必须指定密钥文件路径")
    if auth_type == "key_content" and not data.get("key_content"):
        raise HTTPException(status_code=400, detail="密钥内容认证模式下必须提供密钥内容")
    jump_host = data.get("jump_host")
    description = data.get("description", "")
    tags = data.get("tags", [])
    sftp_allowed_roots = data.get("sftp_allowed_roots") or data.get("allowed_roots") or ["/"]

    status = str(data.get("status") or "online").strip().lower()
    if status not in {"online", "disabled", "offline"}:
        status = "online"

    server = {
        "name": name,
        "host": host,
        "port": port,
        "username": username,
        "auth_type": auth_type,
        "password": password,
        "key": key_file,
        "key_content": key_content,
        "jump_host": jump_host,
        "description": description,
        "tags": tags,
        "group": data.get("group", ""),
        "sftp_allowed_roots": sftp_allowed_roots,
        "status": status,
        "enabled": status != "disabled",
    }
    if not save_server(server):
        logger.error(f"Failed to save server '{name}': config save returned False, user={request.state.username}")
        raise HTTPException(status_code=500, detail=f"Failed to persist server '{name}' to storage")
    logger.info(f"Server '{name}' created successfully by user={request.state.username}, host={host}:{port}, auth={auth_type}")
    ac = build_audit_context(server)
    audit("server.create", "server", name,
          f"user={request.state.username} host={ac['host']}:{ac['port']} auth={ac['auth_mode']}")
    hop_context = get_hop_summary(server)
    server["hop_context"] = hop_context
    return api_response(data=redact_server_secrets(server), message=f"Server '{name}' created")


@servers_v2_router.get("/{id_or_name}")
def get_server_v2(request: Request, id_or_name: str, db: Session = Depends(get_db)):
    """Fetch one server.

    The path parameter accepts **any** server identifier — display name, host,
    full UUID, or short UUID prefix — so callers can paste the ``id`` returned
    by ``GET /api/v2/servers`` directly without looking up the name first.
    """
    require_auth(request, db)
    srv = inventory.get_server(id_or_name)
    if not srv:
        raise HTTPException(status_code=404, detail=f"Server '{id_or_name}' not found")
    result = dict(srv)
    hop_context = get_hop_summary(srv)
    result["hop_context"] = hop_context
    return api_response(data=redact_server_secrets(result))


@servers_v2_router.put("/{name}")
async def update_server_v2(request: Request, name: str, db: Session = Depends(get_db)):
    require_admin(request, db)
    existing = inventory.get_server(name)
    if not existing:
        raise HTTPException(status_code=404, detail=f"Server '{name}' not found")
    data = await request.json()
    host = (data.get("host") or "").strip() or existing.get("host", "")
    port = int(data.get("port", existing.get("port", 22)))
    username = data.get("username") or data.get("user") or existing.get("username", "root")
    auth_type = data.get("auth_type") or existing.get("auth_type", "password")
    incoming_password = data.get("password")
    incoming_key_content = data.get("key_content")
    password = incoming_password if incoming_password not in (None, "", "********") else existing.get("password")
    key_file = data.get("key") or data.get("key_file") or existing.get("key")
    key_content = incoming_key_content if incoming_key_content not in (None, "", "********") else existing.get("key_content")
    jump_host = existing.get("jump_host")
    if "jump_host" in data:
        jh = data["jump_host"]
        if jh is None or jh == "" or jh == "__clear__":
            jump_host = None
        else:
            jump_host = jh
    description = existing.get("description", "")
    if "description" in data:
        description = data["description"] or ""
    tags = existing.get("tags", [])
    if "tags" in data:
        tags = data["tags"] or []
    sftp_allowed_roots = existing.get("sftp_allowed_roots") or existing.get("allowed_roots") or ["/"]
    if "sftp_allowed_roots" in data:
        sftp_allowed_roots = data["sftp_allowed_roots"] or ["/"]
    elif "allowed_roots" in data:
        sftp_allowed_roots = data["allowed_roots"] or ["/"]

    status = existing.get("status", "online")
    if "status" in data:
        status = data["status"]
    status = str(status or "online").strip().lower()
    if status not in {"online", "disabled", "offline"}:
        status = "online"

    server = {
        "name": name,
        "host": host,
        "port": port,
        "username": username,
        "auth_type": auth_type,
        "password": password,
        "key": key_file,
        "key_content": key_content,
        "jump_host": jump_host,
        "description": description,
        "tags": tags,
        "group": data.get("group") if "group" in data else existing.get("group", ""),
        "sftp_allowed_roots": sftp_allowed_roots,
        "status": status,
        "enabled": status != "disabled",
    }
    if not save_server(server):
        logger.error(f"Failed to update server '{name}': config save returned False, user={request.state.username}")
        raise HTTPException(status_code=500, detail=f"Failed to persist server '{name}' to storage")

    # 同步 server_groups.server_names：把该服务器从其他组的 server_names 中剔除；
    # 若指定了新分组（非空），把它加入该组的 server_names。避免出现「组里残留已不存在的服务器」导致删除时 409。
    if "group" in data:
        try:
            new_group = (data.get("group") or "").strip()
            for g in db.query(ServerGroup).all():
                names = list(g.server_names or [])
                changed = False
                if name in names and g.name != new_group:
                    names = [x for x in names if x != name]
                    changed = True
                if new_group and g.name == new_group and name not in names:
                    names.append(name)
                    changed = True
                if changed:
                    g.server_names = names
            db.commit()
        except Exception as e:
            db.rollback()
            logger.warning(f"sync server_groups.server_names for '{name}' failed: {e}")

    logger.info(f"Server '{name}' updated successfully by user={request.state.username}, host={host}:{port}, auth={auth_type}")
    ac = build_audit_context(server)
    audit("server.update", "server", name,
          f"user={request.state.username} host={ac['host']}:{ac['port']} auth={ac['auth_mode']}")
    hop_context = get_hop_summary(server)
    server["hop_context"] = hop_context
    return api_response(data=redact_server_secrets(server), message=f"Server '{name}' updated")


@servers_v2_router.delete("/{name}")
def delete_server_v2(request: Request, name: str, db: Session = Depends(get_db)):
    require_admin(request, db)
    existing = inventory.get_server(name)
    if not existing:
        raise HTTPException(status_code=404, detail=f"Server '{name}' not found")
    try:
        if not delete_server(name, db=db):
            logger.error(f"Failed to delete server '{name}': config delete returned False, user={request.state.username}")
            raise HTTPException(status_code=500, detail=f"Failed to remove server '{name}' from storage")
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    logger.info(f"Server '{name}' deleted successfully by user={request.state.username}, host={existing.get('host')}")
    audit("server.delete", "server", name, f"user={request.state.username}")
    return api_response(data={"deleted": name}, message=f"Server '{name}' deleted")


@servers_v2_router.get("/{name}/info")
def server_info(request: Request, name: str, db: Session = Depends(get_db)):
    require_admin(request, db)
    started = time.time()
    with _ssh_session(name) as (ssh, srv):
        exit_code, out, _ = ssh.exec(
            "echo '---DISK---'; df -h / 2>/dev/null; echo '---MEM---'; free -m 2>/dev/null || free; echo '---LOAD---'; uptime; echo '---OS---'; cat /etc/os-release 2>/dev/null | head -3",
            timeout=15
        )
        info = {"host": srv["host"], "name": name}
        in_section = None
        for line in (out or "").splitlines():
            line = line.strip()
            if line == "---DISK---": in_section = "disk"
            elif line == "---MEM---": in_section = "mem"
            elif line == "---LOAD---": in_section = "load"
            elif line == "---OS---": in_section = "os"
            elif in_section and line:
                info.setdefault(in_section, []).append(line)
        hop_context = get_hop_summary(srv)
        info["hop_context"] = hop_context
        info["duration_ms"] = int((time.time() - started) * 1000)
        audit("server.info", "server", name, request.state.username)
        return api_response(data=info)


@servers_v2_router.get("/{name}/exec/history")
def server_exec_history(
    request: Request, name: str,
    limit: int = 50, offset: int = 0,
    risk_level: str = None,
    db: Session = Depends(get_db),
):
    require_admin(request, db)
    entries = query_executions(db, server_name=name, risk_level=risk_level, limit=limit, offset=offset)
    total = count_executions(db, server_name=name, risk_level=risk_level)
    return api_response(data={
        "server": name,
        "entries": [log_to_dict(e) for e in entries],
        "total": total,
        "limit": limit,
        "offset": offset,
    })


def _detect_server_deployment(db: Session, server_name: str) -> dict:
    """根据服务器名查找关联的 Service 配置，推断部署方式。

    返回:
        {
            "mode": "docker_compose" | "pm2" | "process_keyword" | "unknown",
            "services": [ {name, system, template, template_variables, ...} ],
        }
    服务器可能同时承载多个服务，全部返回，由前端决定如何展示。
    """
    services = []
    try:
        rows = db.query(Service).all()
    except Exception:
        rows = []
    for svc in rows:
        try:
            servers = svc.servers or []
            if server_name not in servers:
                continue
            tv = svc.template_variables or {}
            template = svc.template or ""
            # 只有显式配置了 compose_dir 或模板本身就是 compose 时才判定为
            # Docker Compose 部署。deploy_path / service_dir 是普通部署路径
            # （如静态前端目录），不能作为 compose 判定依据，否则会选中
            # 没有 docker-compose.yml 的目录执行 compose ps，导致误报 0 服务。
            compose_dir = tv.get("compose_dir") or ""
            explicit_compose = template in ("docker_compose", "crypto_docker_compose") or bool(compose_dir)
            pm2_name = tv.get("pm2_name") or ""
            keyword = tv.get("process_keyword") or tv.get("service_name") or ""
            if explicit_compose:
                mode = "docker_compose"
            elif pm2_name:
                mode = "pm2"
            elif keyword:
                mode = "process_keyword"
            else:
                mode = "unknown"
            services.append({
                "name": svc.name,
                "display_name": svc.display_name or svc.name,
                "system": svc.system_name,
                "template": template,
                "mode": mode,
                "compose_dir": compose_dir,
                "compose_file": tv.get("compose_file", "docker-compose.yml"),
                "pm2_name": pm2_name,
                "process_keyword": keyword,
                "template_variables": tv,
            })
        except Exception:
            continue
    # 优先级：docker_compose > pm2 > process_keyword > unknown
    priority = {"docker_compose": 3, "pm2": 2, "process_keyword": 1, "unknown": 0}
    if services:
        top = max(services, key=lambda s: priority.get(s["mode"], 0))
        return {"mode": top["mode"], "services": services}
    return {"mode": "unknown", "services": []}


@servers_v2_router.get("/{name}/processes")
def server_processes(request: Request, name: str, db: Session = Depends(get_db)):
    require_admin(request, db)
    started = time.time()
    deployment = _detect_server_deployment(db, name)
    mode = deployment["mode"]
    services = deployment["services"]
    import json as _json

    with _ssh_session(name) as (ssh, srv):
        if mode == "docker_compose":
            # 收集所有 docker_compose 服务，按 (compose_dir, compose_file) 去重，
            # 逐个目录探测并合并结果 —— 服务器可能在不同目录部署多套 compose 项目。
            compose_targets = []
            seen = set()
            for s in services:
                if s["mode"] != "docker_compose":
                    continue
                cdir = (s.get("compose_dir") or "").strip() or "."
                cfile = (s.get("compose_file") or "docker-compose.yml").strip() or "docker-compose.yml"
                key = (cdir, cfile)
                if key in seen:
                    continue
                seen.add(key)
                compose_targets.append((cdir, cfile))
            if not compose_targets:
                compose_targets = [("", "docker-compose.yml")]
            result = []
            for cdir, cfile in compose_targets:
                base_cmd = f"cd {shlex.quote(cdir)} && " if cdir not in ("", ".") else ""
                # docker compose ps --format json 输出每行一个 JSON 对象
                exit_code, out, err = ssh.exec(
                    f"{base_cmd} docker compose -f {shlex.quote(cfile)} ps --format json 2>/dev/null || "
                    f"{base_cmd} docker-compose -f {shlex.quote(cfile)} ps 2>/dev/null",
                    timeout=20,
                )
                # 尝试解析 JSON 行格式
                for line in (out or "").splitlines():
                    line = line.strip()
                    if not line or not line.startswith("{"):
                        continue
                    try:
                        p = _json.loads(line)
                    except Exception:
                        continue
                    state = (p.get("State") or p.get("status") or "unknown").lower()
                    result.append({
                        "name": p.get("Service") or p.get("Name") or p.get("name") or "",
                        "pid": p.get("PID") or p.get("PIDs") or 0,
                        "status": state,
                        "image": p.get("Image") or p.get("image") or "",
                        "ports": p.get("Ports") or p.get("Publishers") or "",
                        "uptime": p.get("RunningFor") or p.get("Status") or "",
                        "restarts": 0,
                        "compose_dir": cdir,
                    })
                # 如果 JSON 解析为空，尝试文本解析 docker-compose ps 的表格输出
                if not result or not any(r.get("name") for r in result):
                    if out and not out.lstrip().startswith("{"):
                        lines = [l for l in (out or "").splitlines() if l.strip()]
                        if len(lines) > 1:
                            for line in lines[1:]:
                                parts = line.split()
                                if len(parts) >= 3:
                                    result.append({
                                        "name": parts[0],
                                        "pid": 0,
                                        "status": parts[2].lower() if len(parts) > 2 else "unknown",
                                        "image": parts[1] if len(parts) > 1 else "",
                                        "ports": "",
                                        "uptime": " ".join(parts[3:]) if len(parts) > 3 else "",
                                        "restarts": 0,
                                        "compose_dir": cdir,
                                    })
            audit("server.processes", "server", name, request.state.username)
            return api_response(data={
                "processes": result,
                "deployment_mode": "docker_compose",
                "services": services,
                "duration_ms": int((time.time() - started) * 1000),
            })

        if mode == "pm2":
            exit_code, out, _ = ssh.exec("pm2 jlist 2>/dev/null || echo '[]'", timeout=15)
            try:
                processes = _json.loads(out) if out else []
            except Exception:
                processes = []
            result = []
            for p in processes:
                result.append({
                    "name": p.get("name", ""),
                    "pid": p.get("pid", 0),
                    "status": p.get("pm2_env", {}).get("status", "unknown"),
                    "cpu": p.get("monit", {}).get("cpu", 0),
                    "memory": p.get("monit", {}).get("memory", 0),
                    "uptime": p.get("pm2_env", {}).get("pm_uptime", 0),
                    "restarts": p.get("pm2_env", {}).get("restart_time", 0),
                })
            audit("server.processes", "server", name, request.state.username)
            return api_response(data={
                "processes": result,
                "deployment_mode": "pm2",
                "services": services,
                "duration_ms": int((time.time() - started) * 1000),
            })

        if mode == "process_keyword":
            # 用 ps + 关键字查进程
            svc = next((s for s in services if s["mode"] == "process_keyword"), None)
            keyword = (svc or {}).get("process_keyword") or ""
            result = []
            if keyword:
                exit_code, out, _ = ssh.exec(
                    f"ps -eo pid,pcpu,pmem,etime,comm,args --no-headers 2>/dev/null | grep -E {shlex.quote(keyword)} | grep -v grep",
                    timeout=15,
                )
                for line in (out or "").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    parts = line.split(None, 4)
                    if len(parts) >= 5:
                        result.append({
                            "name": parts[4].split()[0] if parts[4] else keyword,
                            "pid": int(parts[0]) if parts[0].isdigit() else 0,
                            "status": "online",
                            "cpu": float(parts[1]) if _is_float(parts[1]) else 0,
                            "memory": float(parts[2]) if _is_float(parts[2]) else 0,
                            "uptime": parts[3],
                            "restarts": 0,
                        })
            audit("server.processes", "server", name, request.state.username)
            return api_response(data={
                "processes": result,
                "deployment_mode": "process_keyword",
                "services": services,
                "duration_ms": int((time.time() - started) * 1000),
            })

        # 未知模式，回退到 PM2 兼容旧逻辑
        exit_code, out, _ = ssh.exec("pm2 jlist 2>/dev/null || echo '[]'", timeout=15)
        try:
            processes = _json.loads(out) if out else []
        except Exception:
            processes = []
        result = []
        for p in processes:
            result.append({
                "name": p.get("name", ""),
                "pid": p.get("pid", 0),
                "status": p.get("pm2_env", {}).get("status", "unknown"),
                "cpu": p.get("monit", {}).get("cpu", 0),
                "memory": p.get("monit", {}).get("memory", 0),
                "uptime": p.get("pm2_env", {}).get("pm_uptime", 0),
                "restarts": p.get("pm2_env", {}).get("restart_time", 0),
            })
        audit("server.processes", "server", name, request.state.username)
        return api_response(data={
            "processes": result,
            "deployment_mode": "unknown",
            "services": services,
            "duration_ms": int((time.time() - started) * 1000),
        })


def _is_float(s: str) -> bool:
    try:
        float(s)
        return True
    except (ValueError, TypeError):
        return False


_PROCESS_ACTIONS = {"start", "stop", "restart"}


def _resolve_action_command(action: str, process: str, mode: str, services: list) -> str:
    """根据部署方式和动作解析实际执行命令。

    Docker Compose:
      - restart: docker compose -f {file} restart [service]
      - stop:    docker compose -f {file} stop [service]
      - start:   docker compose -f {file} up -d [service]
    PM2:
      - 复用旧逻辑 pm2 {action} {process}
    process_keyword:
      - 不支持单进程控制，回退提示
    """
    if mode == "docker_compose":
        svc = next((s for s in services if s["mode"] == "docker_compose"), None)
        compose_dir = (svc or {}).get("compose_dir") or ""
        compose_file = (svc or {}).get("compose_file") or "docker-compose.yml"
        base = f"cd {shlex.quote(compose_dir or '.')} && " if compose_dir else ""
        # process 可以是 docker compose 服务名（如 strategy / web），也可能为空（控制整个 compose 项目）
        target = shlex.quote(process) if process else ""
        if action == "restart":
            return f"{base} docker compose -f {shlex.quote(compose_file)} restart {target}".strip()
        if action == "stop":
            return f"{base} docker compose -f {shlex.quote(compose_file)} stop {target}".strip()
        if action == "start":
            return f"{base} docker compose -f {shlex.quote(compose_file)} up -d {target}".strip()

    if mode == "pm2":
        return f"pm2 {action} {shlex.quote(process)}"

    # 默认回退到 PM2
    return f"pm2 {action} {shlex.quote(process)}"


@servers_v2_router.post("/{name}/processes/action")
async def server_process_action(request: Request, name: str, db: Session = Depends(get_db)):
    require_admin(request, db)
    data = await request.json()
    action = str(data.get("action", "")).strip().lower()
    process = str(data.get("process", "")).strip()
    # mode 可由前端显式传入（来自 /processes 返回的 deployment_mode），
    # 不传则自动检测
    mode = str(data.get("mode", "")).strip().lower()
    if action not in _PROCESS_ACTIONS:
        raise HTTPException(status_code=400, detail=f"action must be one of {sorted(_PROCESS_ACTIONS)}")
    if not process and mode != "docker_compose":
        raise HTTPException(status_code=400, detail="process is required")
    if process and (len(process) > 200 or not _re.match(r"^[A-Za-z0-9._\-:/@]+$", process)):
        raise HTTPException(status_code=400, detail="process contains invalid characters")

    if not mode:
        deployment = _detect_server_deployment(db, name)
        mode = deployment["mode"]
        services = deployment["services"]
    else:
        services = _detect_server_deployment(db, name)["services"]

    if mode == "process_keyword":
        raise HTTPException(
            status_code=400,
            detail="process_keyword 部署方式不支持单进程操作，请使用服务控制工具或 SSH 执行",
        )

    started = time.time()
    with _ssh_session(name) as (ssh, srv):
        command = _resolve_action_command(action, process, mode, services)
        exit_code, out, err = ssh.exec(command, timeout=60)
        out = sanitize_command_output(out, _MAX_OUTPUT_LENGTH)
        err = sanitize_command_output(err, _MAX_OUTPUT_LENGTH)
        duration_ms = int((time.time() - started) * 1000)
        ac = build_audit_context(srv, name)
        audit(
            f"server.process.{action}",
            "server",
            name,
            format_audit_detail(
                ac,
                user=request.state.username,
                process=process or "(all)",
                mode=mode,
                exit=exit_code,
                dur=f"{duration_ms}ms",
            ),
        )
        if exit_code != 0:
            detail = err or out or f"{action} failed"
            raise HTTPException(status_code=502, detail=detail.strip()[:500])
        return api_response(data={
            "process": process,
            "action": action,
            "mode": mode,
            "exit_code": exit_code,
            "stdout": out,
            "stderr": err,
            "duration_ms": duration_ms,
        })


@servers_v2_router.post("/{name}/exec")
async def server_exec(request: Request, name: str, db: Session = Depends(get_db)):
    require_admin(request, db)
    data = await request.json()
    command = data.get("command", "")
    timeout = min(int(data.get("timeout", 30)), _MAX_EXEC_TIMEOUT)

    if not command:
        raise HTTPException(status_code=400, detail="command is required")
    if len(command) > 10000:
        raise HTTPException(status_code=400, detail="command exceeds max length of 10000 characters")

    allowed, reason, level = validate_command(command)
    if not allowed:
        raise HTTPException(status_code=400, detail=f"Command rejected: {reason}")

    started = time.time()
    with _ssh_session(name) as (ssh, srv):
        exit_code, out, err = ssh.exec(command, timeout=timeout)

        out = sanitize_command_output(out, _MAX_OUTPUT_LENGTH)
        err = sanitize_command_output(err, _MAX_OUTPUT_LENGTH)

        hop_context = get_hop_summary(srv)
        duration_ms = int((time.time() - started) * 1000)
        ac = build_audit_context(srv, name)
        masked_command = _mask_sensitive_command(command)

        audit("server.exec", "server", name,
              format_audit_detail(ac,
                  user=request.state.username,
                  exit=exit_code,
                  dur=f"{duration_ms}ms",
                  cmd=masked_command[:80],
                  risk=level))

        record_execution(
            db,
            server_name=name,
            username=request.state.username,
            command=masked_command,
            exit_code=exit_code,
            stdout=out,
            stderr=err,
            duration_ms=duration_ms,
            risk_level=level,
            hop_context=hop_context,
            auth_mode=ac.get("auth_mode"),
        )

        return api_response(data={
            "exit_code": exit_code,
            "stdout": out,
            "stderr": err,
            "duration_ms": duration_ms,
            "hop_context": hop_context,
            "risk_level": level,
        })


class SecurityMonitorSetupPayload(BaseModel):
    confirm_text: str = Field(default="", description="确认短语: CONFIRM ops.security_module.setup")
    options: Dict[str, str] = Field(default_factory=dict,
                                    description="自定义安装选项变量（键值对，注入安装脚本环境变量），高度可定制；缺省使用默认选项")


class SecurityMonitorStatusPayload(BaseModel):
    enabled: bool = Field(default=True, description="手动标记安全监控启用(true)/停用(false)")


@servers_v2_router.get("/{name}/security-monitor/probe")
def server_security_monitor_probe(name: str, request: Request, db: Session = Depends(get_db)):
    """SSH 探测服务器安全监控实际状态（只读）：监控脚本是否安装、fail2ban/auditd 是否启用。

    用于手动安装过安全脚本的服务器核对真实安装情况。
    探测结论（installed/checked_at 等）持久化到 metadata_json.security_monitor，
    刷新后列表页仍可直接展示上次探测结果，无需重复探测。
    """
    user = require_auth(request, db)
    from config_manager import resolve_server
    server = resolve_server(name)
    if not server:
        raise HTTPException(status_code=404, detail=f"Server not found: {name}")
    from app.services.tool_adapters.security_report_tools import _probe_runtime
    result = _probe_runtime(server)
    server_name = server.get("name") or server.get("id") or name
    row = db.query(Server).filter(Server.name == server_name).first() or \
        db.query(Server).filter(Server.id == server.get("id")).first()
    if row is not None:
        meta = dict(row.metadata_json or {})
        mon = dict(meta.get("security_monitor") or {})
        mon["installed"] = bool(result.get("security_monitor_installed"))
        mon["fail2ban_active"] = bool(result.get("fail2ban_active"))
        mon["auditd_active"] = bool(result.get("auditd_active"))
        mon["installer_present"] = bool(result.get("installer_present"))
        from datetime import datetime, timezone
        mon["checked_at"] = datetime.now(timezone.utc).isoformat()
        meta["security_monitor"] = mon
        row.metadata_json = meta
        db.commit()
    audit("server.security_monitor.probe", "server", name,
          f"user={user.get('username')} installed={result.get('security_monitor_installed')}")
    return api_response(data=result)


@servers_v2_router.post("/{name}/security-monitor/status")
def server_security_monitor_status(name: str, payload: SecurityMonitorStatusPayload,
                                   request: Request, db: Session = Depends(get_db)):
    """手动修改服务器安全监控状态（启用/停用），用于手动安装过安全脚本的服务器人工登记。"""
    user = require_auth(request, db)
    from config_manager import resolve_server
    server = resolve_server(name)
    if not server:
        raise HTTPException(status_code=404, detail=f"Server not found: {name}")
    server_name = server.get("name") or server.get("id") or name
    row = db.query(Server).filter(Server.name == server_name).first() or \
        db.query(Server).filter(Server.id == server.get("id")).first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"未找到服务器记录: {server_name}")
    meta = dict(row.metadata_json or {})
    mon = dict(meta.get("security_monitor") or {})
    mon["enabled"] = bool(payload.enabled)
    mon["source"] = "manual"
    from datetime import datetime, timezone
    mon["updated_at"] = datetime.now(timezone.utc).isoformat()
    meta["security_monitor"] = mon
    row.metadata_json = meta
    db.commit()
    audit("server.security_monitor.status", "server", name,
          f"user={user.get('username')} enabled={payload.enabled}")
    return api_response(data={"name": server_name, "enabled": bool(payload.enabled)},
                        message=f"已手动标记 {server_name} 安全监控{'启用' if payload.enabled else '停用'}")


@servers_v2_router.post("/{name}/security-monitor/setup")
def server_security_monitor_setup(name: str, payload: SecurityMonitorSetupPayload,
                                  request: Request, db: Session = Depends(get_db)):
    """一键安装服务器安全监控模块（fail2ban/auditd/每日日报/资源监控 + Telegram 告警）。

    远程执行官方安装脚本为耗时写操作，提交统一任务中心后台执行，立即返回任务 ID；
    进度与结果可在任务中心查看（ops.get_job_status 轮询）。
    """
    user = require_auth(request, db)
    from app.services.job_service import enqueue_tool_job
    from app.services.tool_context import ToolContext
    from app.services.tool_registry import register_builtin_tools, registry

    register_builtin_tools()
    tool = registry.get("ops.security_module.setup")
    ctx = ToolContext(
        username=user.get("username") or "",
        user_id=user.get("id") or "",
        role="operator" if user.get("is_admin") else "user",
        is_admin=bool(user.get("is_admin")),
        can_deploy=bool(user.get("can_deploy")),
        auth_type="session",
        scopes=["*"],
        allow_write=True,
        allow_prod=bool(user.get("is_admin")),
        client_name="ops-web-session",
        ip_address=request.client.host if request.client else "",
        user_agent=request.headers.get("user-agent", "") if request else "",
    )
    args = {
        "server": name,
        "options": payload.options or None,
        "confirm_text": payload.confirm_text,
    }
    job = enqueue_tool_job(db, tool_def=tool, arguments=args, ctx=ctx, policy_result={})
    audit("server.security_monitor.setup", "server", name,
          f"user={user.get('username')} job={job.get('id')}")
    return api_response(
        data={"job_id": job.get("id"), "job": job, "status": job.get("status"),
              "task_center_url": f"/tasks?kind=tool&job={job.get('id')}"},
        message="安全监控安装已提交后台任务，可在任务中心查看进度",
    )
