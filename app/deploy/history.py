from __future__ import annotations

from typing import Any, Dict, Iterable, List

from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.db.models import (
    Deployment,
    DeployLog,
    DeployTask,
    DeploymentPackageDistribution,
    DeploymentServerTask,
    DeploymentStepTask,
)
from app.deploy.state import is_active_status


def _dt(value: Any) -> str | None:
    return value.isoformat() if value else None


def deployment_item(d: Deployment) -> Dict[str, Any]:
    return {
        "id": d.id,
        "system": d.system,
        "service": d.service,
        "environment": d.environment,
        "strategy": d.strategy,
        "status": d.status,
        "servers": d.servers,
        "version": d.version,
        "message": d.message,
        "started_at": _dt(d.started_at),
        "finished_at": _dt(d.finished_at),
        "created_by": d.created_by,
        "can_rollback": d.status == "success",
    }


def deployment_list_payload(
    db: Session,
    *,
    limit: int = 50,
    offset: int = 0,
    system: str = "",
    service: str = "",
    environment: str = "",
    status: str = "",
    created_by: str = "",
    q: str = "",
) -> Dict[str, Any]:
    """Return paged deployment history.

    Kept as a small service so REST routes, MCP tools, and future pages share
    the same pagination/filter semantics instead of each endpoint rebuilding a
    slightly different query.
    """
    limit = max(1, min(int(limit or 50), 200))
    offset = max(0, int(offset or 0))
    query = db.query(Deployment)
    if system:
        query = query.filter(Deployment.system == system)
    if service:
        query = query.filter(Deployment.service == service)
    if environment:
        query = query.filter(Deployment.environment == environment)
    if status:
        query = query.filter(Deployment.status == status)
    if created_by:
        query = query.filter(Deployment.created_by == created_by)
    if q:
        like = f"%{q}%"
        query = query.filter(
            (Deployment.id.like(like))
            | (Deployment.system.like(like))
            | (Deployment.service.like(like))
            | (Deployment.environment.like(like))
            | (Deployment.version.like(like))
            | (Deployment.servers.like(like))
            | (Deployment.message.like(like))
        )
    total = query.count()
    rows = query.order_by(Deployment.started_at.desc()).offset(offset).limit(limit).all()
    return {
        "items": [deployment_item(d) for d in rows],
        "pagination": {
            "limit": limit,
            "offset": offset,
            "total": total,
            "has_more": offset + len(rows) < total,
        },
        "filters": {
            "system": system,
            "service": service,
            "environment": environment,
            "status": status,
            "created_by": created_by,
            "q": q,
        },
    }


def deployment_delete_confirm_text(deployment_id: str) -> str:
    return f"DELETE DEPLOYMENT {str(deployment_id or '').strip()}"


def _is_delete_blocked_status(status: str | None) -> bool:
    return is_active_status(status) or str(status or "").strip().lower() == "queued"


def _clean_deployment_ids(deployment_ids: Iterable[str]) -> List[str]:
    cleaned: List[str] = []
    seen = set()
    for raw in deployment_ids or []:
        deployment_id = str(raw or "").strip()
        if not deployment_id or deployment_id in seen:
            continue
        cleaned.append(deployment_id)
        seen.add(deployment_id)
    return cleaned


def deployment_delete_batch_confirm_text(deployment_ids: Iterable[str]) -> str:
    return f"DELETE DEPLOYMENTS {len(_clean_deployment_ids(deployment_ids))}"


def delete_deployment_rows(db: Session, deployment_ids: Iterable[str]) -> Dict[str, int]:
    ids = _clean_deployment_ids(deployment_ids)
    deleted: Dict[str, int] = {
        "deployment_package_distributions": 0,
        "deployment_step_tasks": 0,
        "deployment_server_tasks": 0,
        "deploy_logs": 0,
        "deploy_tasks": 0,
        "deployments": 0,
    }
    if not ids:
        return deleted

    task_ids = [
        item[0]
        for item in db.query(DeployTask.id).filter(DeployTask.deployment_id.in_(ids)).all()
        if item and item[0]
    ]
    deleted["deployment_package_distributions"] = db.query(DeploymentPackageDistribution).filter(DeploymentPackageDistribution.deployment_id.in_(ids)).delete(synchronize_session=False)
    deleted["deployment_step_tasks"] = db.query(DeploymentStepTask).filter(DeploymentStepTask.deployment_id.in_(ids)).delete(synchronize_session=False)
    deleted["deployment_server_tasks"] = db.query(DeploymentServerTask).filter(DeploymentServerTask.deployment_id.in_(ids)).delete(synchronize_session=False)
    log_filter = DeployLog.deployment_id.in_(ids)
    if task_ids:
        log_filter = or_(log_filter, DeployLog.task_id.in_(task_ids))
    deleted["deploy_logs"] = db.query(DeployLog).filter(log_filter).delete(synchronize_session=False)
    deleted["deploy_tasks"] = db.query(DeployTask).filter(DeployTask.deployment_id.in_(ids)).delete(synchronize_session=False)
    deleted["deployments"] = db.query(Deployment).filter(Deployment.id.in_(ids)).delete(synchronize_session=False)
    return deleted


def delete_deployment_history(db: Session, deployment_id: str, *, confirm_text: str, actor: str = "", force: bool = False) -> Dict[str, Any]:
    deployment_id = str(deployment_id or "").strip()
    if not deployment_id:
        raise HTTPException(status_code=400, detail="deployment_id is required")

    row = db.query(Deployment).filter(Deployment.id == deployment_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Deployment not found")

    expected = deployment_delete_confirm_text(deployment_id)
    if str(confirm_text or "").strip() != expected:
        raise HTTPException(
            status_code=428,
            detail={
                "code": "CONFIRMATION_REQUIRED",
                "message": "Deployment history deletion requires confirmation text",
                "expected_confirm_text": expected,
            },
        )

    if _is_delete_blocked_status(row.status) and not force:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "DEPLOYMENT_ACTIVE",
                "message": "Running or pending deployments cannot be deleted",
                "status": row.status,
            },
        )

    deleted = delete_deployment_rows(db, [deployment_id])
    db.commit()

    return {
        "deployment_id": deployment_id,
        "confirm_text": expected,
        "force": bool(force),
        "actor": actor,
        "deleted": deleted,
    }


def delete_deployment_histories(db: Session, deployment_ids: Iterable[str], *, confirm_text: str, actor: str = "", force: bool = False) -> Dict[str, Any]:
    ids = _clean_deployment_ids(deployment_ids)
    if not ids:
        raise HTTPException(status_code=400, detail="deployment_ids is required")
    if len(ids) > 200:
        raise HTTPException(status_code=400, detail="Cannot delete more than 200 deployment histories at once")

    rows = db.query(Deployment).filter(Deployment.id.in_(ids)).all()
    found_ids = {row.id for row in rows}
    missing_ids = [deployment_id for deployment_id in ids if deployment_id not in found_ids]
    if missing_ids:
        raise HTTPException(status_code=404, detail={"code": "DEPLOYMENT_NOT_FOUND", "missing_ids": missing_ids})

    expected = deployment_delete_batch_confirm_text(ids)
    if str(confirm_text or "").strip() != expected:
        raise HTTPException(
            status_code=428,
            detail={
                "code": "CONFIRMATION_REQUIRED",
                "message": "Batch deployment history deletion requires confirmation text",
                "expected_confirm_text": expected,
            },
        )

    active_rows = [row for row in rows if _is_delete_blocked_status(row.status)]
    if active_rows and not force:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "DEPLOYMENT_ACTIVE",
                "message": "Running or pending deployments cannot be deleted",
                "active_ids": [row.id for row in active_rows],
            },
        )

    deleted = delete_deployment_rows(db, ids)
    db.commit()
    return {
        "deployment_ids": ids,
        "confirm_text": expected,
        "force": bool(force),
        "actor": actor,
        "deleted": deleted,
    }
