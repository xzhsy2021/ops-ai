from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.db.models import Deployment, DeployTask, DeployLog, SqlQueryHistory, CleanupJob, OperationJob


def _normalize_status(kind: str, status: str) -> str:
    raw = (status or "unknown").lower()
    if raw in ("success", "succeeded", "done", "completed"):
        return "success"
    if raw in ("failed", "error", "rejected"):
        return "failed"
    if raw in ("running", "executing", "in_progress"):
        return "running"
    if raw in ("pending", "draft", "submitted", "pending_approval"):
        return "pending"
    if raw in ("cancelled", "canceled", "paused"):
        return raw
    return raw


def _iso(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _duration_ms(started_at=None, finished_at=None, fallback=None):
    if fallback is not None:
        return fallback
    if started_at and finished_at:
        try:
            return int((finished_at - started_at).total_seconds() * 1000)
        except Exception:
            return None
    return None


def runtime_job_to_dict(kind: str, item: Any) -> Dict[str, Any]:
    if kind == "deploy" and isinstance(item, Deployment):
        return {
            "kind": "deploy",
            "id": item.id,
            "title": f"发布 {item.system}/{item.service or 'default'}",
            "status": _normalize_status("deploy", item.status),
            "raw_status": item.status or "unknown",
            "operator": item.created_by or "-",
            "target": item.servers or item.system,
            "started_at": _iso(item.started_at),
            "finished_at": _iso(item.finished_at),
            "duration_ms": _duration_ms(item.started_at, item.finished_at),
            "detail": {
                "system": item.system,
                "service": item.service,
                "environment": item.environment,
                "version": item.version,
                "message": item.message,
            },
        }
    if kind == "sql" and isinstance(item, SqlQueryHistory):
        return {
            "kind": "sql",
            "id": item.id,
            "title": f"SQL 查询 {item.connection_name or ''}".strip(),
            "status": _normalize_status("sql", item.status),
            "raw_status": item.status or "unknown",
            "operator": item.executed_by or "-",
            "target": item.connection_name or item.database_name or "-",
            "started_at": _iso(item.created_at),
            "finished_at": _iso(item.created_at),
            "duration_ms": _duration_ms(None, None, item.duration_ms),
            "detail": {
                "database_name": item.database_name,
                "row_count": item.row_count,
                "query_type": item.query_type,
                "error_message": item.error_message,
                "sql_preview": (item.sql_text or "")[:200],
            },
        }
    if kind == "cleanup" and isinstance(item, CleanupJob):
        return {
            "kind": "cleanup",
            "id": item.id,
            "title": f"清理 {item.database_name}.{item.table_name}",
            "status": _normalize_status("cleanup", item.status),
            "raw_status": item.status or "unknown",
            "operator": item.created_by or "-",
            "target": item.connection_name or item.environment,
            "started_at": _iso(item.started_at or item.created_at),
            "finished_at": _iso(item.finished_at),
            "duration_ms": _duration_ms(item.started_at or item.created_at, item.finished_at),
            "detail": {
                "environment": item.environment,
                "risk_level": item.risk_level,
                "matched_rows": item.matched_rows,
                "deleted_rows": item.deleted_rows,
                "approval_required": item.approval_required,
            },
        }
    if kind in ("tool", "mcp_tool", "job") and isinstance(item, OperationJob):
        return {
            "kind": "tool",
            "id": item.id,
            "job_type": item.job_type or "mcp_tool",
            "source": item.source or "",
            "source_tool": item.source_tool or "",
            "title": item.title or f"工具任务 {item.source_tool or item.id}",
            "status": _normalize_status("tool", item.status),
            "raw_status": item.status or "unknown",
            "operator": item.operator or "-",
            "target": item.target or item.source_tool or "-",
            "risk_level": item.risk_level,
            "progress": item.progress or 0,
            "request": item.request_json or {},
            "result": item.result_json or {},
            "error_message": item.error_message or "",
            "audit_id": item.audit_id or "",
            "worker_id": item.worker_id or "",
            "created_at": _iso(item.created_at),
            "updated_at": _iso(item.updated_at),
            "started_at": _iso(item.started_at or item.created_at),
            "finished_at": _iso(item.finished_at),
            "duration_ms": _duration_ms(item.started_at or item.created_at, item.finished_at),
            "detail": {
                "source": item.source,
                "source_tool": item.source_tool,
                "risk_level": item.risk_level,
                "progress": item.progress,
                "audit_id": item.audit_id,
                "error_message": item.error_message,
            },
        }
    return {
        "kind": kind,
        "id": getattr(item, "id", ""),
        "title": str(item),
        "status": "unknown",
        "raw_status": "unknown",
        "operator": "-",
        "target": "-",
        "started_at": None,
        "finished_at": None,
        "duration_ms": None,
        "detail": {},
    }


def list_runtime_jobs(
    db: Session,
    *,
    status: str = "",
    kind: str = "",
    limit: int = 100,
) -> List[Dict[str, Any]]:
    limit = max(1, min(limit, 500))
    jobs: List[Dict[str, Any]] = []

    if not kind or kind == "deploy":
        q = db.query(Deployment)
        if status:
            q = q.filter(Deployment.status == status)
        for item in q.order_by(Deployment.started_at.desc()).limit(limit).all():
            jobs.append(runtime_job_to_dict("deploy", item))

    if not kind or kind == "sql":
        q = db.query(SqlQueryHistory)
        if status:
            q = q.filter(SqlQueryHistory.status == status)
        for item in q.order_by(SqlQueryHistory.created_at.desc()).limit(limit).all():
            jobs.append(runtime_job_to_dict("sql", item))

    if not kind or kind == "cleanup":
        q = db.query(CleanupJob)
        if status:
            q = q.filter(CleanupJob.status == status)
        for item in q.order_by(CleanupJob.created_at.desc()).limit(limit).all():
            jobs.append(runtime_job_to_dict("cleanup", item))

    if not kind or kind in ("tool", "mcp_tool", "job"):
        q = db.query(OperationJob)
        if status:
            q = q.filter(OperationJob.status == status)
        for item in q.order_by(OperationJob.created_at.desc()).limit(limit).all():
            jobs.append(runtime_job_to_dict("tool", item))

    jobs.sort(key=lambda x: x.get("started_at") or "", reverse=True)
    return jobs[:limit]


def get_runtime_job(
    db: Session,
    kind: str,
    item_id: str,
) -> Optional[Dict[str, Any]]:
    if kind == "deploy":
        item = db.query(Deployment).filter(Deployment.id == item_id).first()
        if item:
            return runtime_job_to_dict("deploy", item)
    if kind == "sql":
        item = db.query(SqlQueryHistory).filter(SqlQueryHistory.id == item_id).first()
        if item:
            return runtime_job_to_dict("sql", item)
    if kind == "cleanup":
        item = db.query(CleanupJob).filter(CleanupJob.id == item_id).first()
        if item:
            return runtime_job_to_dict("cleanup", item)
    if kind in ("tool", "mcp_tool", "job"):
        item = db.query(OperationJob).filter(OperationJob.id == item_id).first()
        if item:
            return runtime_job_to_dict("tool", item)
    return None


def get_runtime_job_detail(
    db: Session,
    kind: str,
    item_id: str,
) -> Optional[Dict[str, Any]]:
    result = get_runtime_job(db, kind, item_id)
    if not result:
        return None
    if kind == "deploy":
        task_rows = db.query(DeployTask).filter(DeployTask.deployment_id == item_id).order_by(DeployTask.created_at.desc()).all()
        logs = db.query(DeployLog).filter(DeployLog.deployment_id == item_id).order_by(DeployLog.created_at.asc()).limit(500).all()
        result["sub_tasks"] = [
            {"id": t.id, "status": t.status, "result": t.result, "started_at": _iso(t.started_at), "finished_at": _iso(t.finished_at)}
            for t in task_rows
        ]
        result["logs"] = [
            {"level": l.level, "step_name": l.step_name, "message": l.message, "created_at": _iso(l.created_at)}
            for l in logs
        ]
    if kind in ("tool", "mcp_tool", "job"):
        item = db.query(OperationJob).filter(OperationJob.id == item_id).first()
        if item:
            result["request"] = item.request_json or {}
            result["result"] = item.result_json or {}
            result["error_message"] = item.error_message or ""
    return result


def list_runtime_job_summary(db: Session) -> Dict[str, Any]:
    summary = {"total": 0, "by_kind": {}, "by_status": {}}

    for kind, model in [
        ("deploy", Deployment),
        ("sql", SqlQueryHistory),
        ("cleanup", CleanupJob),
        ("tool", OperationJob),
    ]:
        items = db.query(model).all()
        kind_total = len(items)
        summary["total"] += kind_total
        kind_summary = {"total": kind_total}
        for item in items:
            st = _normalize_status(kind, getattr(item, "status", "unknown"))
            kind_summary[st] = kind_summary.get(st, 0) + 1
            summary["by_status"][st] = summary["by_status"].get(st, 0) + 1
        summary["by_kind"][kind] = kind_summary

    return summary