"""配置管理 API - V2"""
import os
import json
import logging
import tempfile
import shutil
import time
from pathlib import Path
from fastapi import APIRouter, HTTPException, Request, UploadFile, File, Form, Depends
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from sqlalchemy import text
from config_manager import load_config, save_config, get_all_servers, get_server_by_name
from app.api.helpers import api_response, audit
from app.core.config import get_runtime_path
from app.db import get_db
from app.core.auth_v2 import require_auth, require_admin
from app.services.backup_service import (
    BackupServiceError,
    create_database_backup,
    delete_database_backup,
    expected_delete_confirm_text,
    expected_restore_confirm_text,
    get_backup_dir,
    get_sqlite_db_path,
    list_database_backups,
    resolve_backup_file,
    restore_database_backup,
    verify_backup_file,
)

admin_ops_router = APIRouter(prefix="/api/v2/admin", tags=["后台维护"])
logger = logging.getLogger(__name__)


def _as_http_error(exc: BackupServiceError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.message)


def _get_sqlite_db_path() -> Path:
    try:
        return get_sqlite_db_path()
    except BackupServiceError as exc:
        raise _as_http_error(exc)


def _backup_dir() -> Path:
    return get_backup_dir()


def _resolve_backup_file(file_name: str) -> Path:
    try:
        return resolve_backup_file(file_name)
    except BackupServiceError as exc:
        raise _as_http_error(exc)


def _create_config_import_backup() -> str:
    """Create a DB backup before config import so changes can be restored."""
    try:
        data = create_database_backup(actor="system", reason="before_config_import", prefix="ops_backup_before_config_import")
        return data.get("file", "")
    except Exception:
        logger.exception("Failed to create config import backup")
        return ""


def _config_import_diff(data: dict, db: Session) -> dict:
    current_config = load_config()
    imported_config = data.get("config", {}) or {}
    diff = {"config_keys": {}, "pipelines": {}, "server_groups": {}, "servers": {}}
    for key in ("systems", "settings", "global_variables", "deploy_defaults"):
        if key in imported_config:
            before = current_config.get(key, {}) or {}
            after = imported_config.get(key, {}) or {}
            diff["config_keys"][key] = {
                "before_count": len(before) if isinstance(before, dict) else 1,
                "after_count": len(after) if isinstance(after, dict) else 1,
                "changed": before != after,
            }
    existing_servers = {s.get("name") for s in get_all_servers()}
    imported_servers = imported_config.get("servers", []) or []
    diff["servers"] = {
        "create_or_update": len([s for s in imported_servers if s.get("name")]),
        "new": len([s for s in imported_servers if s.get("name") not in existing_servers]),
    }
    from app.db import PipelineRepository, ServerGroupRepository
    pipeline_repo = PipelineRepository(db)
    group_repo = ServerGroupRepository(db)
    imported_pipelines = data.get("pipelines", []) or []
    imported_groups = data.get("server_groups", []) or []
    diff["pipelines"] = {"incoming": len(imported_pipelines), "new": len([p for p in imported_pipelines if not pipeline_repo.get_by_id(p.get("id", "")) and not pipeline_repo.get_by_name(p.get("name", ""), p.get("system_name"))])}
    diff["server_groups"] = {"incoming": len(imported_groups), "new": len([g for g in imported_groups if not group_repo.get_by_id(g.get("id", "")) and not group_repo.get_by_name(g.get("name", ""))])}
    return diff



@admin_ops_router.get("/schema-version")
def schema_version(request: Request, db: Session = Depends(get_db)):
    require_admin(request, db)
    from sqlalchemy import text
    rows = []
    try:
        rows = db.execute(text("SELECT version, name, applied_at, checksum FROM schema_migrations ORDER BY applied_at DESC")).fetchall()
    except Exception:
        rows = []
    return api_response(data={
        "current": rows[0][0] if rows else "base",
        "migrations": [
            {"version": r[0], "name": r[1], "applied_at": r[2], "checksum": r[3]}
            for r in rows
        ],
    })


@admin_ops_router.get("/export-config")
def export_config(request: Request, db: Session = Depends(get_db)):
    require_admin(request, db)
    config = load_config()
    safe_config = {}

    for key in ("systems", "settings", "global_variables", "deploy_defaults"):
        if key in config:
            safe_config[key] = config[key]

    servers_list = get_all_servers()
    safe_servers = []
    for s in servers_list:
        safe_servers.append({
            "name": s.get("name"), "host": s.get("host"), "port": s.get("port", 22),
            "username": s.get("username", "root"), "auth_type": s.get("auth_type", "password"),
            "tags": s.get("tags", []), "description": s.get("description", ""),
            "sftp_allowed_roots": s.get("sftp_allowed_roots") or s.get("allowed_roots") or ["/"],
        })
    safe_config["servers"] = safe_servers

    from app.db import PipelineRepository, ServerGroupRepository, PipelineStepRepository

    pipeline_repo = PipelineRepository(db)
    pipelines_data = []
    for p in pipeline_repo.list_all():
        step_repo = PipelineStepRepository(db)
        steps = step_repo.list_by_pipeline(p.id)
        pipelines_data.append({
            "id": p.id,
            "name": p.name,
            "system_name": p.system_name,
            "description": p.description,
            "strategy": p.strategy,
            "steps": [
                {"name": s.name, "step_type": s.step_type, "config": s.config, "sort_order": s.sort_order}
                for s in steps
            ],
            "created_at": p.created_at.isoformat() if p.created_at else None,
        })

    group_repo = ServerGroupRepository(db)
    groups_data = []
    for g in group_repo.list_all():
        groups_data.append({
            "id": g.id, "name": g.name, "display_name": g.display_name,
            "description": g.description, "server_names": g.server_names,
            "tags": g.tags,
        })

    return api_response(data={
        "app": "ops",
        "version": "2.0",
        "exported_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "config": safe_config,
        "pipelines": pipelines_data,
        "server_groups": groups_data,
    })



@admin_ops_router.post("/import-config/preview")
async def import_config_preview(request: Request, db: Session = Depends(get_db)):
    require_admin(request, db)
    data = await request.json()
    if not data.get("config"):
        raise HTTPException(status_code=400, detail="No config data in import payload")
    return api_response(data=_config_import_diff(data, db))


@admin_ops_router.post("/import-config")
async def import_config(request: Request, db: Session = Depends(get_db)):
    require_admin(request, db)
    data = await request.json()
    imported_config = data.get("config", {})

    if not imported_config:
        raise HTTPException(status_code=400, detail="No config data in import payload")

    import_backup = _create_config_import_backup()
    diff = _config_import_diff(data, db)
    current_config = load_config()

    for key in ("systems", "global_variables", "deploy_defaults"):
        if key in imported_config:
            current_config[key] = imported_config[key]

    save_config(current_config)

    imported_servers = imported_config.get("servers", [])
    if imported_servers:
        for srv in imported_servers:
            from config_manager import save_server
            save_server(srv)

    imported_pipelines = data.get("pipelines", [])
    if imported_pipelines:
        from app.db import PipelineRepository, PipelineStepRepository
        pipeline_repo = PipelineRepository(db)
        step_repo = PipelineStepRepository(db)
        for p_data in imported_pipelines:
            existing = pipeline_repo.get_by_id(p_data.get("id", ""))
            if not existing:
                pipeline = pipeline_repo.create(
                    name=p_data.get("name", ""),
                    system_name=p_data.get("system_name", ""),
                    description=p_data.get("description", ""),
                    strategy=p_data.get("strategy", "DIRECT"),
                )
                for s in p_data.get("steps", []):
                    step_repo.create(
                        pipeline_id=pipeline.id,
                        name=s.get("name", ""),
                        step_type=s.get("step_type", "command"),
                        config=s.get("config", {}),
                        sort_order=s.get("sort_order", 0),
                    )

    imported_groups = data.get("server_groups", [])
    if imported_groups:
        from app.db import ServerGroupRepository
        group_repo = ServerGroupRepository(db)
        for g_data in imported_groups:
            existing = group_repo.get_by_id(g_data.get("id", ""))
            if not existing and not group_repo.get_by_name(g_data.get("name", "")):
                group_repo.create(
                    name=g_data.get("name", ""),
                    display_name=g_data.get("display_name", g_data.get("name", "")),
                    description=g_data.get("description", ""),
                    server_names=g_data.get("server_names", []),
                    tags=g_data.get("tags", []),
                )

    audit("config.import", "system", "", f"user={request.state.username} backup={import_backup}")
    return api_response(data={"backup": import_backup, "diff": diff}, message="Configuration imported successfully")


@admin_ops_router.post("/backup-db")
def backup_database(request: Request, db: Session = Depends(get_db)):
    user = require_admin(request, db)
    try:
        data = create_database_backup(actor=user.get("username") or getattr(request.state, "username", ""), reason="manual")
    except BackupServiceError as exc:
        raise _as_http_error(exc)
    return api_response(data=data, message="Database backup created and verified")


@admin_ops_router.get("/backups")
def list_backups(request: Request, verify_latest: bool = False, db: Session = Depends(get_db)):
    require_admin(request, db)
    try:
        return api_response(data=list_database_backups(verify_latest=verify_latest))
    except BackupServiceError as exc:
        raise _as_http_error(exc)


@admin_ops_router.post("/backups/{file_name:path}/verify")
def verify_backup(file_name: str, request: Request, include_checksum: bool = True, db: Session = Depends(get_db)):
    user = require_admin(request, db)
    try:
        data = verify_backup_file(file_name, include_checksum=include_checksum)
    except BackupServiceError as exc:
        raise _as_http_error(exc)
    audit("db.backup.verify", "system", file_name, f"user={user.get('username')} status={data.get('status')} sha256={data.get('sha256', '-')}")
    return api_response(data=data, message=data.get("message") or "Backup verified")


@admin_ops_router.delete("/backups/{file_name:path}")
async def delete_backup(file_name: str, request: Request, db: Session = Depends(get_db)):
    user = require_admin(request, db)
    data = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    confirm_text = str(data.get("confirm_text") or "")
    reason = str(data.get("reason") or "").strip()
    try:
        result = delete_database_backup(file_name, confirm_text=confirm_text, actor=user.get("username") or getattr(request.state, "username", ""))
    except BackupServiceError as exc:
        raise _as_http_error(exc)
    audit("db.backup.delete.confirmed", "system", file_name, f"user={user.get('username')} reason={reason or '-'}")
    return api_response(data=result, message="Backup deleted")


@admin_ops_router.get("/backups/{file_name:path}/download")
def download_backup(file_name: str, request: Request, db: Session = Depends(get_db)):
    require_admin(request, db)
    backup_path = _resolve_backup_file(file_name)
    audit("db.backup.download", "system", file_name, request.state.username)
    return FileResponse(
        str(backup_path),
        media_type="application/octet-stream",
        filename=file_name,
    )


@admin_ops_router.post("/restore-db")
async def restore_database(request: Request, db: Session = Depends(get_db)):
    user = require_admin(request, db)
    data = await request.json()
    backup_file = data.get("file", "")
    if not backup_file:
        raise HTTPException(status_code=400, detail="backup file name is required")
    confirm_text = str(data.get("confirm_text") or "")
    reason = str(data.get("reason") or "").strip()
    create_safety_backup = bool(data.get("create_safety_backup", True))
    try:
        # Close this request's session before replacing the SQLite database file.
        # The app should still be restarted after restore so all pooled connections reopen.
        try:
            db.close()
        except Exception:
            pass
        result = restore_database_backup(
            backup_file,
            confirm_text=confirm_text,
            actor=user.get("username") or getattr(request.state, "username", ""),
            create_safety_backup=create_safety_backup,
        )
    except BackupServiceError as exc:
        raise _as_http_error(exc)
    audit("db.restore.confirmed", "system", backup_file, f"user={user.get('username')} reason={reason or '-'} safety_backup={(result.get('safety_backup') or {}).get('file') or '-'}")
    return api_response(data=result, message=result.get("message") or f"Database restored from {backup_file}. Restart application to apply.")


@admin_ops_router.post("/upload-key")
async def upload_ssh_key(request: Request, file: UploadFile = File(...), db: Session = Depends(get_db)):
    require_admin(request, db)
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file selected")
    content = await file.read()
    if len(content) > 100 * 1024:
        raise HTTPException(status_code=400, detail="Key file too large (max 100KB)")
    if len(content) < 10:
        raise HTTPException(status_code=400, detail="File too small to be a valid key")
    safe_name = "".join(c for c in file.filename if c.isalnum() or c in "._-") or "uploaded_key"
    saved_name = f"{safe_name}_{int(time.time())}"
    try:
        key_text = content.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="Key file must be UTF-8 text")
    from config_manager import save_key_file, get_key_file_path
    if not save_key_file(saved_name, key_text.rstrip() + "\n"):
        raise HTTPException(status_code=400, detail="Failed to save key")
    key_path = get_key_file_path(saved_name)
    audit("server.key.upload", "admin", saved_name, f"user={request.state.username} file={safe_name}")
    return api_response(data={
        "name": saved_name,
        "path": key_path,
        "filename": safe_name,
        "size": len(content),
    }, message="Key uploaded successfully")


@admin_ops_router.get("/ssh-keys")
def list_ssh_keys(request: Request, db: Session = Depends(get_db)):
    require_admin(request, db)
    from config_manager import list_key_files
    keys = list_key_files()
    keys.sort(key=lambda item: item.get("modified", 0), reverse=True)
    return api_response(data=keys)


@admin_ops_router.post("/ssh-keys")
async def create_ssh_key(request: Request, db: Session = Depends(get_db)):
    require_admin(request, db)
    data = await request.json()
    name = (data.get("name") or "").strip()
    content = data.get("content") or ""
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    if not isinstance(content, str) or len(content.strip()) < 10:
        raise HTTPException(status_code=400, detail="content is too short to be a valid key")
    if len(content.encode("utf-8")) > 100 * 1024:
        raise HTTPException(status_code=400, detail="Key content too large (max 100KB)")
    from config_manager import save_key_file
    if not save_key_file(name, content.rstrip() + "\n"):
        raise HTTPException(status_code=400, detail="invalid key name or failed to save key")
    audit("server.key.create", "admin", name, f"user={request.state.username}")
    return api_response(data={"name": name}, message="SSH key saved")


@admin_ops_router.get("/ssh-keys/{name}")
def get_ssh_key(name: str, request: Request, db: Session = Depends(get_db)):
    require_admin(request, db)
    from config_manager import read_key_file
    try:
        content = read_key_file(name)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Key '{name}' not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return api_response(data={"name": name, "content": content})


@admin_ops_router.put("/ssh-keys/{name}")
async def update_ssh_key(name: str, request: Request, db: Session = Depends(get_db)):
    require_admin(request, db)
    data = await request.json()
    new_name = (data.get("new_name") or name).strip()
    content = data.get("content") or ""
    if not isinstance(content, str) or len(content.strip()) < 10:
        raise HTTPException(status_code=400, detail="content is too short to be a valid key")
    if len(content.encode("utf-8")) > 100 * 1024:
        raise HTTPException(status_code=400, detail="Key content too large (max 100KB)")
    from config_manager import save_key_file, delete_key_file, key_file_exists
    if not key_file_exists(name):
        raise HTTPException(status_code=404, detail=f"Key '{name}' not found")
    if not save_key_file(new_name, content.rstrip() + "\n"):
        raise HTTPException(status_code=400, detail="invalid key name or failed to save key")
    if new_name != name:
        delete_key_file(name)
    audit("server.key.update", "admin", name, f"user={request.state.username} new_name={new_name}")
    return api_response(data={"name": new_name}, message="SSH key updated")


@admin_ops_router.delete("/ssh-keys/{name}")
def delete_ssh_key(name: str, request: Request, db: Session = Depends(get_db)):
    require_admin(request, db)
    from config_manager import delete_key_file
    if not delete_key_file(name):
        raise HTTPException(status_code=404, detail=f"Key '{name}' not found or cannot be deleted")
    audit("server.key.delete", "admin", name, f"user={request.state.username}")
    return api_response(data={"deleted": name}, message="SSH key deleted")


@admin_ops_router.get("/health")
def system_health(request: Request, db: Session = Depends(get_db)):
    require_auth(request, db)
    from app.services.system_health import build_system_health
    return api_response(data=build_system_health(db))


@admin_ops_router.post("/import-xshell-sessions")
def import_xshell_sessions(request: Request, db: Session = Depends(get_db)):
    require_admin(request, db)
    xshell_dir = r"C:\Users\admin\Documents\NetSarang Computer\8\Xshell\Sessions"
    try:
        from app.services.xshell_import import import_xshell_sessions_to_config
        result = import_xshell_sessions_to_config(xshell_dir)
        audit("xshell.import", "system", "xshell",
              f"imported={result['imported']} skipped={result['skipped']}")
        return api_response(data=result)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
