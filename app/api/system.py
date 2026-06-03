"""System status and lightweight runtime resource APIs for the local Ops console."""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.helpers import api_response, audit
from app.core.auth_v2 import require_admin, require_auth
from app.db import get_db
from app.services.runtime_resources import (
    CLEANUP_CONFIRM_TEXT,
    cleanup_runtime_artifacts,
    get_runtime_usage,
    get_storage_usage,
    get_runtime_retention_policy,
    preview_runtime_cleanup,
    save_runtime_retention_policy,
    build_runtime_snapshot,
)
from app.services.system_health import build_system_health
from app.services.dashboard import build_dashboard_summary
from app.services.diagnostics import build_diagnostics, build_startup_checks, export_diagnostics_bundle, build_diagnostics_report, export_diagnostics_report_json
from app.services.build_info import get_build_info, get_frontend_build_check
from app.services.error_log import get_recent_errors
from app.services.ai_diagnostics import build_ai_diagnostic_analysis
from app.domain.snapshot import capture_asset_snapshot, save_asset_snapshot, list_asset_snapshots, diff_asset_snapshots

system_router = APIRouter(prefix="/api/v2/system", tags=["系统状态"])


class RuntimeRetentionPayload(BaseModel):
    policy: Dict[str, Any] = {}


class RuntimeCleanupPayload(BaseModel):
    dry_run: bool = True
    confirm_text: str = ""
    policy: Dict[str, Any] = {}


@system_router.get("/health")
def system_health(request: Request, db: Session = Depends(get_db)):
    require_auth(request, db)
    return api_response(data=build_system_health(db))


@system_router.get("/frontend-status")
def frontend_status(request: Request, db: Session = Depends(get_db)):
    require_auth(request, db)
    check = get_frontend_build_check()
    return api_response(data={
        "dist_stale": check["dist_stale"],
        "status": check.get("status", "warn" if check["dist_stale"] else "ok"),
        "hint": check["message"] if check["dist_stale"] else None,
    })


@system_router.get("/dashboard")
def system_dashboard(request: Request, db: Session = Depends(get_db)):
    """One-call daily operations dashboard for local and small-team usage."""
    require_auth(request, db)
    return api_response(data=build_dashboard_summary(db, backend_online=True))



@system_router.get("/build-info")
def system_build_info(request: Request, db: Session = Depends(get_db)):
    require_auth(request, db)
    return api_response(data=get_build_info())


@system_router.get("/diagnostics")
def system_diagnostics(request: Request, db: Session = Depends(get_db)):
    require_auth(request, db)
    return api_response(data=build_diagnostics(db))


@system_router.get("/diagnostics/export")
def export_diagnostics(request: Request, db: Session = Depends(get_db)):
    require_auth(request, db)
    path = export_diagnostics_bundle(db)
    return FileResponse(path, media_type="application/zip", filename=path.split("/")[-1].split("\\")[-1])




@system_router.get("/diagnostics/report")
def diagnostics_report(request: Request, db: Session = Depends(get_db)):
    require_auth(request, db)
    return api_response(data=build_diagnostics_report(db))


@system_router.get("/diagnostics/report/export")
def export_diagnostics_report(request: Request, db: Session = Depends(get_db)):
    require_auth(request, db)
    path = export_diagnostics_report_json(db)
    return FileResponse(path, media_type="application/json", filename=path.split("/")[-1].split("\\")[-1])


@system_router.get("/recent-errors")
def system_recent_errors(request: Request, limit: int = 20, include_warnings: bool = True, db: Session = Depends(get_db)):
    require_auth(request, db)
    return api_response(data=get_recent_errors(limit=limit, include_warnings=include_warnings))


@system_router.get("/ai-diagnostics")
def system_ai_diagnostics(
    request: Request,
    mode: str = "summary",
    focus: str = "",
    include_report: bool = False,
    db: Session = Depends(get_db),
):
    require_auth(request, db)
    return api_response(data=build_ai_diagnostic_analysis(db, mode=mode, focus=focus, include_report=include_report))


@system_router.get("/startup-check")
def startup_check(request: Request, db: Session = Depends(get_db)):
    require_auth(request, db)
    return api_response(data=build_startup_checks(db))


@system_router.get("/mcp/self-check")
def mcp_self_check(request: Request, db: Session = Depends(get_db)):
    require_admin(request, db)
    data = build_diagnostics(db).get("sections", {}).get("mcp", {})
    return api_response(data=data)


@system_router.get("/runtime/usage")
def runtime_usage(request: Request, db: Session = Depends(get_db)):
    require_auth(request, db)
    return api_response(data=get_runtime_usage(db))


@system_router.get("/snapshot")
def runtime_snapshot(request: Request, force: bool = False, db: Session = Depends(get_db)):
    require_auth(request, db)
    return api_response(data=build_runtime_snapshot(db, force=force))




@system_router.get("/storage/summary")
def storage_summary(request: Request, db: Session = Depends(get_db)):
    require_auth(request, db)
    return api_response(data=get_storage_usage(db))


@system_router.post("/storage/scan")
def storage_scan(request: Request, db: Session = Depends(get_db)):
    require_auth(request, db)
    from app.services.runtime_resources import clear_runtime_resource_cache
    clear_runtime_resource_cache()
    return api_response(data=get_storage_usage(db))


@system_router.get("/storage/usage")
def storage_usage(request: Request, db: Session = Depends(get_db)):
    require_auth(request, db)
    return api_response(data=get_storage_usage(db))


@system_router.get("/runtime/retention")
def runtime_retention(request: Request, db: Session = Depends(get_db)):
    require_admin(request, db)
    return api_response(data=get_runtime_retention_policy(db))


@system_router.put("/runtime/retention")
def update_runtime_retention(payload: RuntimeRetentionPayload, request: Request, db: Session = Depends(get_db)):
    user = require_admin(request, db)
    policy = save_runtime_retention_policy(db, payload.policy or {})
    audit("runtime.retention.update", "runtime", "local", f"user={user.get('username')} policy_keys={','.join(sorted((payload.policy or {}).keys()))}")
    return api_response(data=policy)


@system_router.post("/runtime/cleanup/preview")
def runtime_cleanup_preview(payload: RuntimeCleanupPayload, request: Request, db: Session = Depends(get_db)):
    require_admin(request, db)
    return api_response(data=preview_runtime_cleanup(db, payload.policy or {}))


@system_router.post("/runtime/cleanup")
def runtime_cleanup(payload: RuntimeCleanupPayload, request: Request, db: Session = Depends(get_db)):
    user = require_admin(request, db)
    try:
        result = cleanup_runtime_artifacts(
            db,
            dry_run=bool(payload.dry_run),
            confirm_text=payload.confirm_text or "",
            actor=user.get("username") or "",
            policy=payload.policy or {},
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    audit(
        "runtime.cleanup.preview" if payload.dry_run else "runtime.cleanup.execute",
        "runtime",
        "local",
        f"user={user.get('username')} dry_run={payload.dry_run} confirm_required={CLEANUP_CONFIRM_TEXT}",
    )
    return api_response(data=result)


@system_router.get("/snapshots/assets")
def get_asset_snapshots(limit: int = 20, request: Request = None, db: Session = Depends(get_db)):
    snapshots = list_asset_snapshots(limit=limit)
    return api_response(data={"snapshots": snapshots, "total": len(snapshots)})


@system_router.post("/snapshots/assets")
def create_asset_snapshot(request: Request, db: Session = Depends(get_db)):
    user = require_admin(request, db)
    result = save_asset_snapshot(db=db)
    audit("snapshot.asset.capture", "snapshot", result["file"], f"user={user.get('username')} servers={result['total_servers']}")
    return api_response(data=result)


@system_router.get("/snapshots/assets/current")
def get_current_asset_snapshot(db: Session = Depends(get_db)):
    captured = capture_asset_snapshot(db=db)
    return api_response(data=captured)


@system_router.get("/snapshots/assets/diff")
def get_asset_snapshot_diff(id1: str, id2: str, request: Request = None, db: Session = Depends(get_db)):
    result = diff_asset_snapshots(id1, id2)
    return api_response(data=result)
