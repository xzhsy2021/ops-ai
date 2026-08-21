"""Daily operations dashboard aggregation for local/small-team installs.

The dashboard endpoint intentionally keeps the data compact and read-only.  It
combines the most useful status from deployments, servers, packages, runtime
health, storage and maintenance into one request so the React landing page does
not have to fan out to many endpoints on every load.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.models import (
    CleanupJob,
    DeployPackage,
    DeployTask,
    Deployment,
    Service,
    ToolPlan,
)
from app.deploy.state import normalize_status
from app.services.runtime_resources import get_runtime_usage, get_storage_usage
from app.services.system_health import build_system_health


def _now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _iso(value: Any) -> str | None:
    if not value:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _safe_count(db: Session, model, *filters) -> int:
    try:
        query = db.query(func.count(model.id))
        if filters:
            query = query.filter(*filters)
        return int(query.scalar() or 0)
    except Exception:
        return 0


def _safe_servers_summary() -> Dict[str, Any]:
    try:
        from app.services.server_ops import summarize_servers

        raw = summarize_servers() or {}
        counts = raw.get("summary") if isinstance(raw.get("summary"), dict) else raw
        warnings = int(counts.get("warnings") or counts.get("warning") or counts.get("warn") or 0)
        errors = int(counts.get("errors") or counts.get("error") or counts.get("incomplete") or 0)
        total = int(counts.get("total") or len(raw.get("items") or []) or 0)
        return {
            "total": total,
            "healthy": int(counts.get("healthy") or counts.get("ok") or counts.get("complete") or max(0, total - errors) or 0),
            "warnings": warnings,
            "errors": errors,
            "groups": raw.get("groups") or [],
            "items": raw.get("items") or [],
        }
    except Exception as exc:
        return {"total": 0, "healthy": 0, "warnings": 0, "errors": 0, "error": str(exc)}


def _deployment_row(row: Deployment) -> Dict[str, Any]:
    return {
        "id": row.id,
        "system": row.system or "",
        "service": row.service or "",
        "environment": row.environment or "",
        "status": normalize_status(row.status),
        "servers": [x.strip() for x in (row.servers or "").split(",") if x.strip()],
        "version": row.version or "",
        "message": row.message or "",
        "started_at": _iso(row.started_at),
        "finished_at": _iso(row.finished_at),
        "created_by": row.created_by or "",
    }


def _recent_deployments(db: Session, limit: int = 8) -> List[Dict[str, Any]]:
    try:
        rows = db.query(Deployment).order_by(Deployment.started_at.desc()).limit(limit).all()
        return [_deployment_row(row) for row in rows]
    except Exception:
        return []


def _running_tasks(db: Session, limit: int = 5) -> List[Dict[str, Any]]:
    """运行中任务：发布任务 + 后台工具任务（MCP / operation_jobs）。"""
    tasks: List[Dict[str, Any]] = []
    try:
        rows = (
            db.query(DeployTask)
            .filter(DeployTask.status.in_(["pending", "running"]))
            .order_by(DeployTask.created_at.desc())
            .limit(limit)
            .all()
        )
        tasks.extend([
            {
                "id": row.id,
                "deployment_id": row.deployment_id,
                "status": normalize_status(row.status),
                "worker": row.worker or "",
                "created_at": _iso(row.created_at),
                "started_at": _iso(row.started_at),
                "cancel_requested": bool(row.cancel_requested),
            }
            for row in rows
        ])
    except Exception:
        pass
    try:
        from app.db.models import OperationJob
        jobs = (
            db.query(OperationJob)
            .filter(OperationJob.status.in_(["queued", "pending", "running"]))
            .order_by(OperationJob.created_at.desc())
            .limit(limit)
            .all()
        )
        for job in jobs:
            started = job.started_at or job.created_at
            tasks.append({
                "id": job.id,
                "kind": "tool",
                "label": job.title or job.source_tool or "工具任务",
                "environment": job.source_tool or "",
                "system": "",
                "status": job.status or "running",
                "worker": job.worker_id or "",
                "created_at": _iso(job.created_at),
                "started_at": _iso(started) if started else None,
                "cancel_requested": False,
            })
    except Exception:
        pass
    return tasks


def _package_summary(db: Session) -> Dict[str, Any]:
    try:
        total = _safe_count(db, DeployPackage, DeployPackage.deleted == False)  # noqa: E712
        protected = _safe_count(db, DeployPackage, DeployPackage.deleted == False, DeployPackage.protected == True)  # noqa: E712
        latest = (
            db.query(DeployPackage)
            .filter(DeployPackage.deleted == False)  # noqa: E712
            .order_by(DeployPackage.uploaded_at.desc())
            .first()
        )
        return {
            "total": total,
            "protected": protected,
            "latest": {
                "package_name": latest.package_name,
                "system": latest.system or "",
                "service": latest.service or latest.service_hint or "",
                "size_bytes": int(latest.size_bytes or 0),
                "uploaded_at": _iso(latest.uploaded_at),
                "uploaded_by": latest.uploaded_by or "",
            } if latest else None,
        }
    except Exception as exc:
        return {"total": 0, "protected": 0, "latest": None, "error": str(exc)}


def _pending_approvals(db: Session) -> Dict[str, Any]:
    cleanup = _safe_count(db, CleanupJob, CleanupJob.status.in_(["pending_approval", "approved"]))
    tool_plans = _safe_count(db, ToolPlan, ToolPlan.status.in_(["draft", "pending_confirmation", "confirmed"]))
    return {"cleanup_jobs": cleanup, "tool_plans": tool_plans, "total": cleanup + tool_plans}


def _status_count(counts: Dict[str, int], *names: str) -> int:
    normalized = {normalize_status(k): int(v or 0) for k, v in (counts or {}).items()}
    return sum(normalized.get(normalize_status(name), 0) for name in names)


def _build_risks(
    *,
    backend_online: bool,
    health: Dict[str, Any],
    deployments_by_status: Dict[str, int],
    server_summary: Dict[str, Any],
    approvals: Dict[str, int],
    storage: Dict[str, Any],
) -> List[Dict[str, str]]:
    risks: List[Dict[str, str]] = []
    health_status = health.get("status")
    if not backend_online or health_status == "unhealthy":
        risks.append({"level": "high", "title": "后端健康检查异常", "message": "请优先查看系统状态、数据库和运行目录。", "to": "/system"})
    elif health_status == "degraded":
        risks.append({"level": "medium", "title": "系统处于降级状态", "message": "存在告警项，建议今天内处理。", "to": "/system"})

    failed = _status_count(deployments_by_status, "failed")
    if failed:
        risks.append({"level": "high", "title": "存在失败发布", "message": f"当前记录中有 {failed} 条失败发布，建议查看报告或回滚计划。", "to": "/deploy"})

    running = _status_count(deployments_by_status, "running", "pending")
    if running:
        risks.append({"level": "medium", "title": "有发布仍在执行", "message": f"{running} 个发布/任务尚未结束，注意避免重复操作同一目标。", "to": "/deploy"})

    if int(server_summary.get("errors") or 0) > 0 or int(server_summary.get("warnings") or 0) > 0:
        risks.append({"level": "medium", "title": "服务器配置需要检查", "message": "部分服务器认证、分组或连通性状态异常。", "to": "/servers"})

    if int(approvals.get("total") or 0) > 0:
        risks.append({"level": "medium", "title": "存在待确认操作", "message": f"{approvals.get('total')} 个维护/工具计划需要人工确认。", "to": "/tasks"})

    try:
        buckets = storage.get("buckets") or {}
        db_size = int((buckets.get("database") or {}).get("size_bytes") or 0)
        upload_size = int((buckets.get("uploads") or {}).get("size_bytes") or 0)
        if db_size > 100 * 1024 * 1024 or upload_size > 1024 * 1024 * 1024:
            risks.append({"level": "medium", "title": "本地数据体积偏大", "message": "建议预览运行时清理或发布包保留策略。", "to": "/maintenance"})
    except Exception:
        pass

    if not risks:
        risks.append({"level": "low", "title": "暂无明显风险", "message": "继续保持发布预检、备份和审计习惯。", "to": "/system"})
    return risks[:8]


def build_dashboard_summary(db: Session, *, backend_online: bool = True) -> Dict[str, Any]:
    """Build a one-call dashboard payload optimized for the landing page."""
    generated_at = _now_naive()
    health = build_system_health(db)
    runtime = get_runtime_usage(db)
    storage = get_storage_usage(db)
    server_summary = _safe_servers_summary()
    deployments_by_status = runtime.get("deployments_by_status") or {}
    deploy_tasks_by_status = runtime.get("deploy_tasks_by_status") or {}
    approvals = _pending_approvals(db)
    packages = _package_summary(db)
    recent = _recent_deployments(db, limit=8)
    running_tasks = _running_tasks(db, limit=5)

    servers_total = int(server_summary.get("total") or 0)
    try:
        from app.config.systems import get_all_systems
        systems_total = len(get_all_systems() or {})
    except Exception:
        systems_total = 0
    services_total = _safe_count(db, Service)
    failed = _status_count(deployments_by_status, "failed")
    running = _status_count(deployments_by_status, "running", "pending") + _status_count(deploy_tasks_by_status, "running", "pending")
    try:
        from app.db.models import OperationJob
        running += int(db.query(OperationJob).filter(
            OperationJob.status.in_(["queued", "pending", "running"])).count() or 0)
    except Exception:
        pass
    warnings = int((health.get("summary") or {}).get("warnings") or 0) + int(server_summary.get("warnings") or 0)
    errors = int((health.get("summary") or {}).get("errors") or 0) + int(server_summary.get("errors") or 0)
    if failed:
        errors += failed
    if approvals.get("total"):
        warnings += int(approvals["total"])

    raw_score = 100 - errors * 12 - warnings * 4 - running * 2
    score = max(40, min(100, raw_score if backend_online else min(raw_score, 65)))
    status = "healthy" if errors == 0 and warnings == 0 else "attention" if errors == 0 else "critical"

    risks = _build_risks(
        backend_online=backend_online,
        health=health,
        deployments_by_status=deployments_by_status,
        server_summary=server_summary,
        approvals=approvals,
        storage=storage,
    )

    return {
        "generated_at": generated_at.isoformat(),
        "status": status,
        "score": score,
        "metrics": {
            "systems": systems_total,
            "services": services_total,
            "servers": servers_total,
            "running_work": running,
            "failed_deployments": failed,
            "pending_approvals": int(approvals.get("total") or 0),
            "packages": int(packages.get("total") or 0),
        },
        "health": {
            "backend_online": backend_online,
            "system": health.get("status"),
            "summary": health.get("summary") or {},
            "database": (health.get("checks") or {}).get("database") or {},
            "disk": (health.get("checks") or {}).get("disk") or {},
            "backups": (health.get("checks") or {}).get("backups") or {},
            "deploy_worker": (health.get("checks") or {}).get("deploy_worker") or {},
        },
        "servers": server_summary,
        "deployments": {
            "by_status": deployments_by_status,
            "tasks_by_status": deploy_tasks_by_status,
            "recent": recent,
            "running_tasks": running_tasks,
        },
        "approvals": approvals,
        "packages": packages,
        "storage": {
            "total_managed_size_human": storage.get("total_managed_size_human"),
            "buckets": storage.get("buckets") or {},
            "table_counts": storage.get("table_counts") or {},
        },
        "runtime": {
            "process": runtime.get("process") or {},
            "runtime_limits": runtime.get("runtime_limits") or {},
            "terminal": runtime.get("terminal") or {},
            "ssh_pool": runtime.get("ssh_pool") or {},
        },
        "risks": risks,
        "quick_actions": [
            {"title": "发起发布", "description": "预检、确认、执行和日志", "to": "/deploy", "level": "high"},
            {"title": "查看服务器", "description": "只读巡检、终端和文件", "to": "/servers", "level": "medium"},
            {"title": "维护工具", "description": "备份、清理与恢复", "to": "/maintenance", "level": "medium"},
            {"title": "系统诊断", "description": "健康检查和诊断包", "to": "/system/diagnostics", "level": "low"},
        ],
    }
