from __future__ import annotations

from typing import Any, Dict

from sqlalchemy.orm import Session

from app.db.models import Deployment


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
