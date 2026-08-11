"""Release history, deploy runtime data, tool audit and audit-log retention rules."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Iterable, List, Tuple

from sqlalchemy.orm import Session

from app.db.models import (
    Deployment,
    DeployTask,
    DeployLog,
    DeploymentRecord,
    AuditRecord,
    DeploymentServerTask,
    DeploymentStepTask,
    DeploymentPackageDistribution,
    ToolCallLog,
    ToolPlan,
    ToolPlanEvent,
)
from app.db.repository import RetentionPolicyRepository


DEFAULT_RETENTION: Dict[str, Any] = {
    # Backward-compatible keys used by older pages / tools.
    "deploy_keep_days": 90,
    "deploy_keep_max": 1000,
    "audit_keep_days": 180,
    "audit_keep_max": 5000,
    "keep_failed_days": 180,
    "dry_run": True,
    # More explicit release-history rules.
    "deploy_success_keep_days": 90,
    "deploy_prod_success_keep_days": 180,
    "deploy_failed_keep_days": 180,
    "deploy_canceled_keep_days": 90,
    "deploy_rollback_keep_days": 365,
    "deploy_running_keep_hours": 72,
    # Audit and capability-server records.
    "audit_high_risk_keep_days": 365,
    "tool_call_keep_days": 180,
    "tool_call_keep_max": 10000,
    "tool_plan_keep_days": 180,
    "tool_plan_keep_max": 5000,
    # Safety switches.
    "keep_prod_failed_days": 365,
    "min_keep_days": 7,
}

HIGH_RISK_AUDIT_KEYWORDS = (
    "deploy",
    "rollback",
    "server.exec",
    "server.terminal",
    "server.file.delete",
    "server.file.edit",
    "sql.query.execute",
    "key",
    "token",
    "tool.",
    "config.import",
    "db.restore",
    "retention",
)

ROLLBACK_STATUSES = {"rollback", "rollbacked", "rolledback", "reverted"}
RUNNING_STATUSES = {"pending", "running", "queued"}
FAILED_STATUSES = {"failed", "error"}
CANCELED_STATUSES = {"canceled", "cancelled"}
SUCCESS_STATUSES = {"success", "succeeded"}


def _now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in {"1", "true", "yes", "on"}
    if value is None:
        return default
    return bool(value)


def _normalize_policy(policy: Dict[str, Any]) -> Dict[str, Any]:
    merged = {**DEFAULT_RETENTION, **(policy or {})}
    # Older config only had deploy_keep_days / keep_failed_days; keep new fields aligned unless explicitly set.
    if "deploy_success_keep_days" not in (policy or {}):
        merged["deploy_success_keep_days"] = merged.get("deploy_keep_days", DEFAULT_RETENTION["deploy_keep_days"])
    if "deploy_failed_keep_days" not in (policy or {}):
        merged["deploy_failed_keep_days"] = merged.get("keep_failed_days", DEFAULT_RETENTION["keep_failed_days"])

    int_keys = [
        "deploy_keep_days", "deploy_keep_max", "audit_keep_days", "audit_keep_max", "keep_failed_days",
        "deploy_success_keep_days", "deploy_prod_success_keep_days", "deploy_failed_keep_days",
        "deploy_canceled_keep_days", "deploy_rollback_keep_days", "deploy_running_keep_hours",
        "audit_high_risk_keep_days", "tool_call_keep_days", "tool_call_keep_max",
        "tool_plan_keep_days", "tool_plan_keep_max", "keep_prod_failed_days", "min_keep_days",
    ]
    for key in int_keys:
        merged[key] = _as_int(merged.get(key), DEFAULT_RETENTION.get(key, 0))
    merged["dry_run"] = _as_bool(merged.get("dry_run"), True)
    return merged


def get_retention_policy(db: Session) -> Dict[str, Any]:
    cfg = RetentionPolicyRepository(db).get("release") or {}
    if not isinstance(cfg, dict):
        cfg = {}
    return _normalize_policy(cfg)


def save_retention_policy(db: Session, policy: Dict[str, Any]) -> Dict[str, Any]:
    merged = _normalize_policy({**get_retention_policy(db), **(policy or {})})
    RetentionPolicyRepository(db).set("release", merged)
    db.commit()
    return merged


def _is_prod_env(value: str | None) -> bool:
    env = (value or "").strip().lower()
    return env in {"prod", "production", "online", "live", "release", "线上", "生产"}


def _is_rollback_deployment(row: Deployment) -> bool:
    status = (row.status or "").lower()
    if status in ROLLBACK_STATUSES:
        return True
    text = f"{row.message or ''} {row.strategy or ''}".lower()
    return "rollback" in text or "回滚" in text


def _deployment_keep_days(row: Deployment, policy: Dict[str, Any]) -> int | None:
    status = (row.status or "").lower()
    if status in RUNNING_STATUSES:
        return None
    if _is_rollback_deployment(row):
        return int(policy["deploy_rollback_keep_days"])
    if status in FAILED_STATUSES:
        return int(policy["keep_prod_failed_days"] if _is_prod_env(row.environment) else policy["deploy_failed_keep_days"])
    if status in CANCELED_STATUSES:
        return int(policy["deploy_canceled_keep_days"])
    if status in SUCCESS_STATUSES:
        return int(policy["deploy_prod_success_keep_days"] if _is_prod_env(row.environment) else policy["deploy_success_keep_days"])
    return int(policy["deploy_keep_days"])


def _sample(items: List[Dict[str, Any]], limit: int = 20) -> List[Dict[str, Any]]:
    return items[:limit]


def _deployment_candidates(db: Session, policy: Dict[str, Any]) -> Tuple[List[str], Dict[str, Any]]:
    now = _now_naive()
    min_keep_cutoff = now - timedelta(days=int(policy.get("min_keep_days", 7)))
    candidates: Dict[str, Dict[str, Any]] = {}
    protected = 0

    rows = db.query(Deployment).order_by(Deployment.started_at.desc()).all()
    for row in rows:
        if not row.started_at or row.started_at > min_keep_cutoff:
            protected += 1
            continue
        keep_days = _deployment_keep_days(row, policy)
        if keep_days is None:
            protected += 1
            continue
        if row.started_at < now - timedelta(days=keep_days):
            candidates[row.id] = {
                "id": row.id,
                "status": row.status,
                "system": row.system,
                "service": row.service,
                "environment": row.environment,
                "started_at": row.started_at.isoformat() if row.started_at else None,
                "reason": f"age>{keep_days}d",
            }

    keep_max = int(policy.get("deploy_keep_max", 1000) or 0)
    if keep_max > 0 and len(rows) > keep_max:
        for row in rows[keep_max:]:
            if not row.started_at or row.started_at > min_keep_cutoff or (row.status or "").lower() in RUNNING_STATUSES:
                continue
            candidates.setdefault(row.id, {
                "id": row.id,
                "status": row.status,
                "system": row.system,
                "service": row.service,
                "environment": row.environment,
                "started_at": row.started_at.isoformat() if row.started_at else None,
                "reason": f"exceed_max>{keep_max}",
            })

    ids = list(candidates.keys())
    by_status = Counter(item.get("status") or "unknown" for item in candidates.values())
    by_reason = Counter(item.get("reason") or "unknown" for item in candidates.values())
    return ids, {
        "count": len(ids),
        "total": len(rows),
        "protected_recent_or_running": protected,
        "by_status": dict(by_status),
        "by_reason": dict(by_reason),
        "sample": _sample(list(candidates.values())),
    }


def _deployment_record_candidates(db: Session, policy: Dict[str, Any]) -> Tuple[List[str], Dict[str, Any]]:
    now = _now_naive()
    cutoff = now - timedelta(days=int(policy.get("deploy_keep_days", 90)))
    rows = db.query(DeploymentRecord).order_by(DeploymentRecord.started_at.desc()).all()
    candidates: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        value = row.started_at or ""
        if value and value < cutoff.isoformat():
            candidates[row.id] = {"id": row.id, "system": row.system, "status": row.status, "started_at": value, "reason": f"age>{policy.get('deploy_keep_days')}d"}
    keep_max = int(policy.get("deploy_keep_max", 1000) or 0)
    if keep_max > 0 and len(rows) > keep_max:
        for row in rows[keep_max:]:
            candidates.setdefault(row.id, {"id": row.id, "system": row.system, "status": row.status, "started_at": row.started_at, "reason": f"exceed_max>{keep_max}"})
    return list(candidates.keys()), {"count": len(candidates), "total": len(rows), "sample": _sample(list(candidates.values()))}


def _is_high_risk_action(action: str | None) -> bool:
    text = (action or "").lower()
    return any(k.lower() in text for k in HIGH_RISK_AUDIT_KEYWORDS)


def _audit_cutoff_for_action(action: str | None, policy: Dict[str, Any]) -> datetime:
    days = int(policy["audit_high_risk_keep_days"] if _is_high_risk_action(action) else policy["audit_keep_days"])
    return _now_naive() - timedelta(days=days)


def _audit_record_candidates(db: Session, policy: Dict[str, Any]) -> Tuple[List[int], Dict[str, Any]]:
    rows = db.query(AuditRecord).order_by(AuditRecord.created_at.desc()).all()
    candidates: Dict[int, Dict[str, Any]] = {}
    for row in rows:
        created_text = row.created_at or ""
        cutoff = _audit_cutoff_for_action(row.action, policy).isoformat()
        if created_text and created_text < cutoff:
            candidates[row.id] = {"id": row.id, "action": row.action, "created_at": created_text, "reason": "age_high_risk" if _is_high_risk_action(row.action) else "age"}
    keep_max = int(policy.get("audit_keep_max", 5000) or 0)
    if keep_max > 0 and len(rows) > keep_max:
        for row in rows[keep_max:]:
            candidates.setdefault(row.id, {"id": row.id, "action": row.action, "created_at": row.created_at, "reason": f"exceed_max>{keep_max}"})
    by_reason = Counter(item.get("reason") for item in candidates.values())
    return list(candidates.keys()), {"count": len(candidates), "total": len(rows), "by_reason": dict(by_reason), "sample": _sample(list(candidates.values()))}


def _audit_log_candidates(policy: Dict[str, Any]) -> Tuple[List[int], Dict[str, Any]]:
    """audit_logs 已迁移到 ORM audit_records 表，委托给 _audit_record_candidates 保持前端兼容。"""
    from app.db.base import SessionLocal
    try:
        with SessionLocal() as db:
            return _audit_record_candidates(db, policy)
    except Exception:
        return [], {"count": 0, "total": 0, "error": "audit_records unavailable"}


def _tool_call_candidates(db: Session, policy: Dict[str, Any]) -> Tuple[List[str], Dict[str, Any]]:
    rows = db.query(ToolCallLog).order_by(ToolCallLog.created_at.desc()).all()
    cutoff_normal = _now_naive() - timedelta(days=int(policy.get("tool_call_keep_days", 180)))
    cutoff_high = _now_naive() - timedelta(days=int(policy.get("audit_high_risk_keep_days", 365)))
    candidates: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        cutoff = cutoff_high if (row.risk_level or "").lower() in {"high", "critical"} else cutoff_normal
        if row.created_at and row.created_at < cutoff:
            candidates[row.id] = {"id": row.id, "tool_name": row.tool_name, "risk_level": row.risk_level, "created_at": row.created_at.isoformat(), "reason": "age_high_risk" if cutoff == cutoff_high else "age"}
    keep_max = int(policy.get("tool_call_keep_max", 10000) or 0)
    if keep_max > 0 and len(rows) > keep_max:
        for row in rows[keep_max:]:
            candidates.setdefault(row.id, {"id": row.id, "tool_name": row.tool_name, "risk_level": row.risk_level, "created_at": row.created_at.isoformat() if row.created_at else None, "reason": f"exceed_max>{keep_max}"})
    return list(candidates.keys()), {"count": len(candidates), "total": len(rows), "sample": _sample(list(candidates.values()))}


def _tool_plan_candidates(db: Session, policy: Dict[str, Any]) -> Tuple[List[str], Dict[str, Any]]:
    rows = db.query(ToolPlan).order_by(ToolPlan.created_at.desc()).all()
    cutoff_normal = _now_naive() - timedelta(days=int(policy.get("tool_plan_keep_days", 180)))
    cutoff_high = _now_naive() - timedelta(days=int(policy.get("audit_high_risk_keep_days", 365)))
    candidates: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        cutoff = cutoff_high if (row.risk_level or "").lower() in {"high", "critical"} else cutoff_normal
        if row.created_at and row.created_at < cutoff:
            candidates[row.id] = {"id": row.id, "plan_type": row.plan_type, "status": row.status, "risk_level": row.risk_level, "created_at": row.created_at.isoformat(), "reason": "age_high_risk" if cutoff == cutoff_high else "age"}
    keep_max = int(policy.get("tool_plan_keep_max", 5000) or 0)
    if keep_max > 0 and len(rows) > keep_max:
        for row in rows[keep_max:]:
            candidates.setdefault(row.id, {"id": row.id, "plan_type": row.plan_type, "status": row.status, "risk_level": row.risk_level, "created_at": row.created_at.isoformat() if row.created_at else None, "reason": f"exceed_max>{keep_max}"})
    return list(candidates.keys()), {"count": len(candidates), "total": len(rows), "sample": _sample(list(candidates.values()))}


def preview_release_cleanup(db: Session) -> Dict[str, Any]:
    policy = get_retention_policy(db)
    deploy_ids, deploy_summary = _deployment_candidates(db, policy)
    record_ids, record_summary = _deployment_record_candidates(db, policy)
    audit_record_ids, audit_record_summary = _audit_record_candidates(db, policy)
    audit_log_ids, audit_log_summary = _audit_log_candidates(policy)
    tool_call_ids, tool_call_summary = _tool_call_candidates(db, policy)
    tool_plan_ids, tool_plan_summary = _tool_plan_candidates(db, policy)

    return {
        "policy": policy,
        "summary": {
            "deployments": deploy_summary,
            "deployment_records": record_summary,
            "audit_logs": audit_log_summary,
            "audit_records": audit_record_summary,
            "tool_call_logs": tool_call_summary,
            "tool_plans": tool_plan_summary,
        },
        "candidate_counts": {
            "deployments": len(deploy_ids),
            "deployment_records": len(record_ids),
            "audit_logs": len(audit_log_ids),
            "audit_records": len(audit_record_ids),
            "tool_call_logs": len(tool_call_ids),
            "tool_plans": len(tool_plan_ids),
        },
    }


def _delete_config_audit_logs(ids: Iterable[int]) -> int:
    """audit_logs 已迁移到 ORM audit_records 表，用 ORM 删除。"""
    ids = list(ids)
    if not ids:
        return 0
    try:
        from app.db.base import SessionLocal
        from app.db.models import AuditRecord
        with SessionLocal() as db:
            count = db.query(AuditRecord).filter(AuditRecord.id.in_(ids)).delete(synchronize_session=False)
            db.commit()
            return int(count or 0)
    except Exception:
        return 0


def cleanup_release_history(db: Session, dry_run: bool = True) -> Dict[str, Any]:
    policy = get_retention_policy(db)
    deploy_ids, deploy_summary = _deployment_candidates(db, policy)
    record_ids, record_summary = _deployment_record_candidates(db, policy)
    audit_record_ids, audit_record_summary = _audit_record_candidates(db, policy)
    audit_log_ids, audit_log_summary = _audit_log_candidates(policy)
    tool_call_ids, tool_call_summary = _tool_call_candidates(db, policy)
    tool_plan_ids, tool_plan_summary = _tool_plan_candidates(db, policy)

    result: Dict[str, Any] = {
        "dry_run": dry_run,
        "policy": policy,
        "deployment_count": len(deploy_ids),
        "audit_count": len(audit_log_ids) + len(audit_record_ids),
        "candidate_counts": {
            "deployments": len(deploy_ids),
            "deployment_records": len(record_ids),
            "audit_logs": len(audit_log_ids),
            "audit_records": len(audit_record_ids),
            "tool_call_logs": len(tool_call_ids),
            "tool_plans": len(tool_plan_ids),
        },
        "summary": {
            "deployments": deploy_summary,
            "deployment_records": record_summary,
            "audit_logs": audit_log_summary,
            "audit_records": audit_record_summary,
            "tool_call_logs": tool_call_summary,
            "tool_plans": tool_plan_summary,
        },
        "deleted": {},
    }
    if dry_run:
        return result

    if deploy_ids:
        result["deleted"]["deployment_package_distributions"] = db.query(DeploymentPackageDistribution).filter(DeploymentPackageDistribution.deployment_id.in_(deploy_ids)).delete(synchronize_session=False)
        result["deleted"]["deployment_step_tasks"] = db.query(DeploymentStepTask).filter(DeploymentStepTask.deployment_id.in_(deploy_ids)).delete(synchronize_session=False)
        result["deleted"]["deployment_server_tasks"] = db.query(DeploymentServerTask).filter(DeploymentServerTask.deployment_id.in_(deploy_ids)).delete(synchronize_session=False)
        result["deleted"]["deploy_logs"] = db.query(DeployLog).filter(DeployLog.deployment_id.in_(deploy_ids)).delete(synchronize_session=False)
        result["deleted"]["deploy_tasks"] = db.query(DeployTask).filter(DeployTask.deployment_id.in_(deploy_ids)).delete(synchronize_session=False)
        result["deleted"]["deployments"] = db.query(Deployment).filter(Deployment.id.in_(deploy_ids)).delete(synchronize_session=False)
    if record_ids:
        result["deleted"]["deployment_records"] = db.query(DeploymentRecord).filter(DeploymentRecord.id.in_(record_ids)).delete(synchronize_session=False)
    if tool_plan_ids:
        result["deleted"]["tool_plan_events"] = db.query(ToolPlanEvent).filter(ToolPlanEvent.plan_id.in_(tool_plan_ids)).delete(synchronize_session=False)
        result["deleted"]["tool_plans"] = db.query(ToolPlan).filter(ToolPlan.id.in_(tool_plan_ids)).delete(synchronize_session=False)
    if tool_call_ids:
        result["deleted"]["tool_call_logs"] = db.query(ToolCallLog).filter(ToolCallLog.id.in_(tool_call_ids)).delete(synchronize_session=False)
    if audit_record_ids:
        result["deleted"]["audit_records"] = db.query(AuditRecord).filter(AuditRecord.id.in_(audit_record_ids)).delete(synchronize_session=False)
    # audit_logs 已迁移到 audit_records 表，不再单独删除（保持前端 key 兼容）
    result["deleted"]["audit_logs"] = 0
    db.commit()
    return result
