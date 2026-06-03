from __future__ import annotations

import hashlib
from typing import Any, Dict

from sqlalchemy.orm import Session

from app.db import DeployLogRepository, DeployTaskRepository, DeploymentRuntimeRepository


def _dt(value: Any) -> str | None:
    return value.isoformat() if value else None


def _compute_deployment_logs_etag(db: Session, deployment_id: str) -> str:
    repo = DeployLogRepository(db)
    total = repo.count_by_deployment(deployment_id)
    max_ts = repo.max_created_at_for_deployment(deployment_id)
    fingerprint = f"{deployment_id}:{total}:{max_ts}"
    return hashlib.md5(fingerprint.encode()).hexdigest()


def _compute_task_logs_etag(db: Session, task_id: str) -> str:
    repo = DeployLogRepository(db)
    total = repo.count_by_task(task_id)
    max_ts = repo.max_created_at_for_task(task_id)
    fingerprint = f"{task_id}:{total}:{max_ts}"
    return hashlib.md5(fingerprint.encode()).hexdigest()


def deployment_logs_payload(
    db: Session,
    deployment_id: str,
    *,
    include_task_id: bool = True,
    limit: int | None = None,
) -> Dict[str, Any]:
    logs = DeployLogRepository(db).list_by_deployment(deployment_id, limit=limit) if limit else DeployLogRepository(db).list_by_deployment(deployment_id)
    items = []
    for log in logs:
        item = {
            "level": log.level,
            "message": log.message,
            "step_name": log.step_name,
            "created_at": _dt(log.created_at),
        }
        if include_task_id:
            item["task_id"] = log.task_id
        items.append(item)
    return {"deployment_id": deployment_id, "logs": items, "limit": limit, "count": len(items)}


def deployment_tasks_payload(db: Session, deployment_id: str) -> Dict[str, Any]:
    tasks = DeployTaskRepository(db).list_by_deployment(deployment_id)
    runtime = DeploymentRuntimeRepository(db).list_for_deployment(deployment_id)
    return {
        "deployment_id": deployment_id,
        "tasks": [
            {
                "task_id": t.id,
                "status": t.status,
                "worker": t.worker,
                "cancel_requested": bool(getattr(t, "cancel_requested", False)),
                "started_at": _dt(t.started_at),
                "finished_at": _dt(t.finished_at),
                "result": t.result,
            }
            for t in tasks
        ],
        "server_tasks": [
            {
                "id": t.id,
                "task_id": t.task_id,
                "server_name": t.server_name,
                "status": t.status,
                "message": t.message,
                "started_at": _dt(t.started_at),
                "finished_at": _dt(t.finished_at),
            }
            for t in runtime["server_tasks"]
        ],
        "step_tasks": [
            {
                "id": t.id,
                "task_id": t.task_id,
                "server_task_id": t.server_task_id,
                "server_name": t.server_name,
                "step_name": t.step_name,
                "step_type": t.step_type,
                "status": t.status,
                "message": t.message,
                "captured_config": getattr(t, "captured_config", None),
                "started_at": _dt(t.started_at),
                "finished_at": _dt(t.finished_at),
            }
            for t in runtime["step_tasks"]
        ],
        "distributions": [
            {
                "id": d.id,
                "task_id": d.task_id,
                "server_name": d.server_name,
                "package_name": d.package_name,
                "local_sha256": d.local_sha256,
                "remote_sha256": d.remote_sha256,
                "remote_path": d.remote_path,
                "size_bytes": d.size_bytes,
                "status": d.status,
                "reused": bool(d.reused),
                "message": d.message,
                "duration_ms": d.duration_ms,
                "created_at": _dt(d.created_at),
                "updated_at": _dt(d.updated_at),
            }
            for d in runtime["distributions"]
        ],
    }
