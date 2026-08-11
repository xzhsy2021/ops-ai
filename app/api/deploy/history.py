"""发布管理 - 发布历史、报告、保留策略、通知、锁管理"""
from typing import List, Dict, Any
from fastapi import APIRouter, HTTPException, Request, Depends, Query
from fastapi.responses import PlainTextResponse, Response
from sqlalchemy.orm import Session

from app.api.helpers import api_response, audit
from app.db import get_db, DeploymentRepository, DeployLogRepository
from app.core.auth_v2 import require_auth
from app.deploy.report import deployment_report_payload, deployment_report_markdown as build_deployment_report_markdown
from app.deploy.history import (
    delete_deployment_histories,
    delete_deployment_history,
    deployment_delete_batch_confirm_text,
    deployment_delete_confirm_text,
    deployment_list_payload,
)
from app.deploy.logs import deployment_logs_payload, deployment_tasks_payload
from app.deploy.logs import _compute_deployment_logs_etag
from app.deploy.locks import acquire_deployment_locks, release_deployment_locks, list_deployment_locks_payload
from app.domain.runtime.snapshots import build_deployment_snapshot

from app.api.deploy._shared import (
    _clean_str_list, _notification_defaults, _notification_settings,
    _send_release_notification, _flush_deploy_logs, logger,
)

history_router = APIRouter(tags=["发布管理v2-历史"])


@history_router.get("/deployments")
async def list_deployments_v2(
    request: Request,
    limit: int = 50,
    offset: int = 0,
    system: str = "",
    service: str = "",
    environment: str = "",
    status: str = "",
    created_by: str = "",
    q: str = "",
    db: Session = Depends(get_db),
):
    require_auth(request, db)
    payload = deployment_list_payload(
        db,
        limit=limit,
        offset=offset,
        system=system,
        service=service,
        environment=environment,
        status=status,
        created_by=created_by,
        q=q,
    )
    return api_response(data=payload["items"], pagination=payload["pagination"], filters=payload["filters"])


@history_router.get("/deployments/{deployment_id}/report")
async def deployment_report_json(deployment_id: str, request: Request, db: Session = Depends(get_db)):
    require_auth(request, db)
    return api_response(data=deployment_report_payload(deployment_id, db))


@history_router.post("/deployments/delete")
async def delete_deployment_histories_api(request: Request, db: Session = Depends(get_db)):
    user = require_auth(request, db)
    data = await request.json()
    deployment_ids = data.get("deployment_ids") or []
    confirm_text = str(data.get("confirm_text") or "")
    force = bool(data.get("force", False))
    result = delete_deployment_histories(
        db,
        deployment_ids,
        confirm_text=confirm_text,
        actor=user.get("username") or "",
        force=force,
    )
    audit(
        "deploy.history.delete_many",
        "deployment",
        ",".join(result.get("deployment_ids", [])),
        f"user={user.get('username')} force={force} expected={deployment_delete_batch_confirm_text(deployment_ids)} deleted={result.get('deleted')}",
    )
    return api_response(data=result, message="Deployment histories deleted")


@history_router.delete("/deployments/{deployment_id}")
async def delete_deployment_history_api(deployment_id: str, request: Request, db: Session = Depends(get_db)):
    user = require_auth(request, db)
    data = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    confirm_text = str((data or {}).get("confirm_text") or "")
    force = bool((data or {}).get("force", False))
    result = delete_deployment_history(
        db,
        deployment_id,
        confirm_text=confirm_text,
        actor=user.get("username") or "",
        force=force,
    )
    audit(
        "deploy.history.delete",
        "deployment",
        deployment_id,
        f"user={user.get('username')} force={force} expected={deployment_delete_confirm_text(deployment_id)} deleted={result.get('deleted')}",
    )
    return api_response(data=result, message="Deployment history deleted")


@history_router.get("/deployments/{deployment_id}/report.md")
async def deployment_report_markdown(deployment_id: str, request: Request, db: Session = Depends(get_db)):
    require_auth(request, db)
    from fastapi.responses import PlainTextResponse
    payload = deployment_report_payload(deployment_id, db)
    return PlainTextResponse(build_deployment_report_markdown(payload), media_type="text/markdown; charset=utf-8", headers={"Content-Disposition": f"attachment; filename=deployment-{deployment_id}.md"})


@history_router.get("/deployments/{deployment_id}/report.txt")
async def deployment_report_text(deployment_id: str, request: Request, db: Session = Depends(get_db)):
    require_auth(request, db)
    from fastapi.responses import PlainTextResponse
    payload = deployment_report_payload(deployment_id, db)
    return PlainTextResponse(build_deployment_report_markdown(payload), headers={"Content-Disposition": f"attachment; filename=deployment-{deployment_id}.txt"})


@history_router.get("/retention")
async def get_release_retention(request: Request, db: Session = Depends(get_db)):
    require_auth(request, db)
    from app.services.release_retention import get_retention_policy, preview_release_cleanup
    return api_response(data={
        "policy": get_retention_policy(db),
        "preview": preview_release_cleanup(db),
    })


@history_router.put("/retention")
async def update_release_retention(request: Request, db: Session = Depends(get_db)):
    user = require_auth(request, db)
    data = await request.json()
    from app.services.release_retention import save_retention_policy, preview_release_cleanup
    policy = save_retention_policy(db, data or {})
    audit("release.retention.update", "system", "release_retention", f"user={user.get('username')}")
    return api_response(data={"policy": policy, "preview": preview_release_cleanup(db)}, message="Retention policy updated")


@history_router.post("/retention/preview")
async def preview_release_retention(request: Request, db: Session = Depends(get_db)):
    require_auth(request, db)
    from app.services.release_retention import preview_release_cleanup
    return api_response(data=preview_release_cleanup(db), message="Retention cleanup preview")


@history_router.post("/retention/cleanup")
async def cleanup_release_retention(request: Request, db: Session = Depends(get_db)):
    user = require_auth(request, db)
    data = await request.json()
    dry_run = bool(data.get("dry_run", True))
    from app.services.release_retention import cleanup_release_history
    result = cleanup_release_history(db, dry_run=dry_run)
    audit("release.retention.cleanup", "system", "release_retention", f"user={user.get('username')} dry_run={dry_run} deployments={result.get('deployment_count')} audits={result.get('audit_count')} tool_calls={result.get('candidate_counts', {}).get('tool_call_logs')}")
    return api_response(data=result, message="Retention cleanup preview" if dry_run else "Retention cleanup finished")


@history_router.get("/notifications")
async def get_notification_settings(request: Request, db: Session = Depends(get_db)):
    require_auth(request, db)
    return api_response(data=_notification_settings(db))


@history_router.put("/notifications")
async def update_notification_settings(request: Request, db: Session = Depends(get_db)):
    user = require_auth(request, db)
    data = await request.json()
    from app.db.repository import NotificationSettingsRepository
    settings = {
        "enabled": bool(data.get("enabled", False)),
        "webhook_urls": _clean_str_list(data.get("webhook_urls", [])),
        "events": _clean_str_list(data.get("events", ["deploy.success", "deploy.failed", "deploy.canceled", "rollback.success", "rollback.failed"])),
        "timeout": int(data.get("timeout", 5) or 5),
    }
    NotificationSettingsRepository(db).set(settings)
    db.commit()
    audit("notification.settings.update", "system", "release_notification", f"user={user.get('username')}")
    return api_response(data=settings, message="Notification settings updated")


@history_router.post("/notifications/test")
async def test_notification(request: Request, db: Session = Depends(get_db)):
    user = require_auth(request, db)
    fake = type("FakeDeployment", (), {
        "id": "test", "system": "ops", "service": "notification", "environment": "test",
        "status": "test", "version": "test", "servers": "-", "message": "notification test",
    })()
    _send_release_notification("notification.test", fake, {"requested_by": user.get("username")}, db)
    audit("notification.test", "system", "release_notification", f"user={user.get('username')}")
    return api_response(message="Notification test triggered")


@history_router.get("/locks")
async def list_deploy_locks(request: Request, db: Session = Depends(get_db)):
    require_auth(request, db)
    return api_response(data=list_deployment_locks_payload(db))


@history_router.post("/locks/release")
async def release_deploy_lock(request: Request, db: Session = Depends(get_db)):
    user = require_auth(request, db)
    data = await request.json()
    lock_key = data.get("lock_key", "")
    if not lock_key:
        raise HTTPException(status_code=400, detail="lock_key is required")
    release_deployment_locks([lock_key], db)
    audit("deploy.lock.release", "lock", lock_key, f"user={user.get('username')}")
    return api_response(message=f"Lock '{lock_key}' released")


@history_router.get("/deployments/{deployment_id}/logs")
async def get_deployment_logs_v2(
    deployment_id: str,
    request: Request,
    response: Response,
    cursor: str = Query(default=None),
    limit: int = Query(default=500, ge=1, le=5000),
    db: Session = Depends(get_db),
):
    require_auth(request, db)
    etag = _compute_deployment_logs_etag(db, deployment_id)
    response.headers["ETag"] = etag
    response.headers["Cache-Control"] = "private, max-age=5"
    if request.headers.get("If-None-Match") == etag:
        return Response(status_code=304)

    _flush_deploy_logs()
    repo = DeployLogRepository(db)
    if cursor:
        logs, next_cursor = repo.list_by_deployment_cursor(deployment_id, cursor=cursor, limit=limit)
    else:
        logs = repo.list_by_deployment(deployment_id, limit=limit)
        next_cursor = None
    items = []
    for log in logs:
        item = {
            "level": log.level,
            "message": log.message,
            "step_name": log.step_name,
            "created_at": log.created_at.isoformat() if log.created_at else None,
        }
        items.append(item)
    data = {"deployment_id": deployment_id, "logs": items, "limit": limit, "count": len(items)}
    if next_cursor:
        data["next_cursor"] = next_cursor
    return api_response(data=data)


@history_router.get("/deployments/{deployment_id}/snapshot")
async def deployment_snapshot(deployment_id: str, request: Request, db: Session = Depends(get_db)):
    require_auth(request, db)
    result = build_deployment_snapshot(db, deployment_id, log_tail=10, include_report=True)
    if not result.get("found"):
        raise HTTPException(status_code=404, detail="Deployment not found")
    return api_response(data=result)
