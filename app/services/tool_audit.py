from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict

from app.db.models import ToolCallLog, ToolPlanEvent
from app.api.helpers import audit


def _preview(value: Any, limit: int = 4000) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        text = str(value)
    if len(text) > limit:
        return text[:limit] + "..."
    return text


def record_tool_call(
    db,
    *,
    audit_id: str | None = None,
    tool_name: str,
    ctx,
    input_args: Dict[str, Any],
    normalized_args: Dict[str, Any] | None = None,
    result: Any = None,
    status: str = "success",
    risk_level: str = "low",
    policy_result: Dict[str, Any] | None = None,
    blocked_reason: str = "",
    related_plan_id: str = "",
    related_deployment_id: str = "",
    related_job_id: str = "",
    duration_ms: int | None = None,
):
    item = ToolCallLog(
        id=audit_id or uuid.uuid4().hex,
        tool_name=tool_name,
        client_name=getattr(ctx, "client_name", "") or getattr(ctx, "token_name", ""),
        token_id=getattr(ctx, "token_id", ""),
        token_owner=getattr(ctx, "token_owner", "") or getattr(ctx, "username", ""),
        username=getattr(ctx, "username", ""),
        input_args=_preview(input_args),
        normalized_args=_preview(normalized_args or {}),
        result_preview=_preview(result),
        status=status,
        risk_level=risk_level,
        policy_result=_preview(policy_result or {}),
        blocked_reason=blocked_reason,
        related_plan_id=related_plan_id or "",
        related_deployment_id=related_deployment_id or "",
        related_job_id=related_job_id or "",
        ip_address=getattr(ctx, "ip_address", ""),
        user_agent=getattr(ctx, "user_agent", ""),
        duration_ms=duration_ms,
    )
    db.add(item)
    db.commit()
    try:
        audit(
            f"tool.{status}",
            "tool",
            tool_name,
            f"user={item.username or item.token_owner} risk={risk_level} plan={related_plan_id or '-'} deployment={related_deployment_id or '-'} job={related_job_id or '-'} blocked={blocked_reason or '-'}",
        )
    except Exception:
        pass
    return item


def record_plan_event(db, plan_id: str, event_type: str, actor: str = "", message: str = "", payload: Any = None):
    item = ToolPlanEvent(
        id=uuid.uuid4().hex,
        plan_id=plan_id,
        event_type=event_type,
        actor=actor,
        message=message,
        payload=payload or {},
        created_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    db.add(item)
    db.commit()
    return item
