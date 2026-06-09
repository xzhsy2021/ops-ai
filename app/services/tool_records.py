from __future__ import annotations

from typing import Any, Dict, List

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.db.models import ToolCallLog, ToolPlan, ToolPlanEvent

ACTIVE_PLAN_STATUSES = {
    "draft",
    "ready",
    "prechecked",
    "confirmed",
    "pending",
    "pending_confirmation",
    "pending_approval",
    "running",
    "executing",
}


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _call_to_dict(row: ToolCallLog) -> Dict[str, Any]:
    return {
        "id": row.id,
        "tool_name": row.tool_name,
        "client_name": row.client_name,
        "token_owner": row.token_owner,
        "username": row.username,
        "input_args": row.input_args,
        "normalized_args": row.normalized_args,
        "result_preview": row.result_preview,
        "status": row.status,
        "risk_level": row.risk_level,
        "policy_result": row.policy_result,
        "blocked_reason": row.blocked_reason,
        "related_plan_id": row.related_plan_id,
        "related_deployment_id": row.related_deployment_id,
        "related_job_id": row.related_job_id,
        "ip_address": row.ip_address,
        "user_agent": row.user_agent,
        "duration_ms": row.duration_ms,
        "created_at": _iso(row.created_at),
    }


def plan_to_dict(row: ToolPlan) -> Dict[str, Any]:
    return {
        "id": row.id,
        "plan_type": row.plan_type,
        "status": row.status,
        "created_by": row.created_by,
        "source_tool": row.source_tool,
        "system": row.system,
        "service": row.service,
        "environment": row.environment,
        "servers": row.servers or [],
        "package_name": row.package_name,
        "risk_level": row.risk_level,
        "confirm_text": row.confirm_text,
        "related_deployment_id": row.related_deployment_id,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


def _dedupe(ids: List[str] | None, *, field: str) -> List[str]:
    result: List[str] = []
    seen = set()
    for raw in ids or []:
        value = str(raw or "").strip()
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    if len(result) > 200:
        raise HTTPException(status_code=400, detail=f"Cannot process more than 200 {field} at once")
    return result


def list_tool_call_logs(
    db: Session,
    *,
    limit: int = 100,
    offset: int = 0,
    tool: str = "",
    status: str = "",
    since_id: str = "",
    user: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    limit = max(1, min(int(limit or 100), 500))
    offset = max(0, int(offset or 0))
    user = user or {}
    q = db.query(ToolCallLog)
    if not user.get("is_admin"):
        username = user.get("username")
        q = q.filter((ToolCallLog.username == username) | (ToolCallLog.token_owner == username))
    if tool:
        q = q.filter(ToolCallLog.tool_name == tool)
    if status:
        q = q.filter(ToolCallLog.status == status)
    if since_id:
        q = q.filter(ToolCallLog.id < since_id)
    total = q.count()
    rows = q.order_by(ToolCallLog.created_at.desc()).offset(offset).limit(limit).all()
    return {"items": [_call_to_dict(row) for row in rows], "total": total, "limit": limit, "offset": offset}


def list_tool_plans(
    db: Session,
    *,
    limit: int = 100,
    offset: int = 0,
    plan_type: str = "",
    status: str = "",
    user: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    limit = max(1, min(int(limit or 100), 500))
    offset = max(0, int(offset or 0))
    user = user or {}
    q = db.query(ToolPlan)
    if not user.get("is_admin"):
        q = q.filter(ToolPlan.created_by == user.get("username"))
    if plan_type:
        q = q.filter(ToolPlan.plan_type == plan_type)
    if status:
        q = q.filter(ToolPlan.status == status)
    total = q.count()
    rows = q.order_by(ToolPlan.created_at.desc()).offset(offset).limit(limit).all()
    return {"items": [plan_to_dict(row) for row in rows], "total": total, "limit": limit, "offset": offset}


def delete_tool_records(
    db: Session,
    *,
    call_ids: List[str] | None = None,
    plan_ids: List[str] | None = None,
    actor: str = "",
    force: bool = False,
) -> Dict[str, Any]:
    call_ids = _dedupe(call_ids, field="tool calls")
    plan_ids = _dedupe(plan_ids, field="tool plans")
    if not call_ids and not plan_ids:
        raise HTTPException(status_code=400, detail="call_ids or plan_ids is required")

    deleted = {"tool_call_logs": 0, "tool_plan_events": 0, "tool_plans": 0}
    if plan_ids:
        plans = db.query(ToolPlan).filter(ToolPlan.id.in_(plan_ids)).all()
        by_id = {plan.id: plan for plan in plans}
        missing = [plan_id for plan_id in plan_ids if plan_id not in by_id]
        if missing:
            raise HTTPException(status_code=404, detail=f"Tool plan not found: {', '.join(missing[:5])}")
        active = [
            plan.id
            for plan in plans
            if str(plan.status or "").lower() in ACTIVE_PLAN_STATUSES
        ]
        if active and not force:
            raise HTTPException(status_code=409, detail=f"Tool plans are still active: {', '.join(active[:5])}")
        deleted["tool_plan_events"] = db.query(ToolPlanEvent).filter(ToolPlanEvent.plan_id.in_(plan_ids)).delete(synchronize_session=False)
        deleted["tool_plans"] = db.query(ToolPlan).filter(ToolPlan.id.in_(plan_ids)).delete(synchronize_session=False)

    if call_ids:
        existing = {row.id for row in db.query(ToolCallLog.id).filter(ToolCallLog.id.in_(call_ids)).all()}
        missing = [call_id for call_id in call_ids if call_id not in existing]
        if missing:
            raise HTTPException(status_code=404, detail=f"Tool call log not found: {', '.join(missing[:5])}")
        deleted["tool_call_logs"] = db.query(ToolCallLog).filter(ToolCallLog.id.in_(call_ids)).delete(synchronize_session=False)

    db.commit()
    return {
        "call_ids": call_ids,
        "plan_ids": plan_ids,
        "deleted": deleted,
        "force": bool(force),
        "actor": actor or "",
    }
