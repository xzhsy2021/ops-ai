"""执行计划管理 API 路由（只读 + 拒绝）。

一条消息对应一个 ExecutionPlan。此模块提供计划级可见性：列表、详情、
拒绝、过期清理。不暴露审批短码或其哈希。旧 /api/v2/approvals 单动作
审批端点保持不变，二者互不干扰。
"""
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.auth_v2 import get_current_user
from app.db.base import get_db
from app.db.models import ExecutionPlan
from app.services.execution_plan import ExecutionPlanService, step_approval_details

router = APIRouter(prefix="/api/v2/execution-plans", tags=["qclaw执行计划管理"])


class PlanStepSummary(BaseModel):
    step_key: str
    step_order: int
    action_type: str
    status: str
    attempt_count: int
    started_at: str | None = None
    finished_at: str | None = None
    error_message: str | None = None
    result: dict[str, Any] | None = None
    approval_details: dict[str, Any] = {}


class PlanSummary(BaseModel):
    plan_id: str
    status: str
    plan_digest: str
    risk_level: str | None = None
    room_id: str | None = None
    request_event_id: str | None = None
    system_name: str | None = None
    service_name: str | None = None
    environment: str | None = None
    targets: list[str] = []
    step_count: int = 0
    package_name: str | None = None
    approved_by: str | None = None
    rejected_by: str | None = None
    created_at: str | None = None
    expires_at: str | None = None
    approved_at: str | None = None
    execution_job_id: int | str | None = None
    failure_reason: str | None = None
    message_context: dict[str, str] | None = None


class PlanDetail(PlanSummary):
    content_sha256: str | None = None
    routing_ticket_digest: str | None = None
    routing_config_revision: str | None = None
    ai_reason: str | None = None
    policy: dict[str, Any] = {}
    steps: list[PlanStepSummary] = []
    channel: str | None = None
    channel_account_id: str | None = None
    conversation_id: str | None = None
    request_message_id: str | None = None
    request_sender_id: str | None = None
    approval_message_id: str | None = None


def _to_summary(p: ExecutionPlan) -> PlanSummary:
    message_context = None
    if (
        p.channel
        and p.channel_account_id
        and p.conversation_id
        and p.request_message_id
        and p.request_sender_id
        and p.content_sha256
    ):
        message_context = {
            "channel": p.channel,
            "channel_account_id": p.channel_account_id,
            "conversation_id": p.conversation_id,
            "message_id": p.request_message_id,
            "sender_id": p.request_sender_id,
            "content_sha256": p.content_sha256,
        }
    return PlanSummary(
        plan_id=p.id,
        status=p.status,
        plan_digest=p.plan_digest,
        risk_level=p.risk_level,
        room_id=p.room_id,
        request_event_id=p.request_event_id,
        system_name=p.system_name,
        service_name=p.service_name,
        environment=p.environment,
        targets=p.targets or [],
        step_count=len(p.steps),
        package_name=p.package_name,
        approved_by=p.approved_by,
        rejected_by=p.rejected_by,
        created_at=p.created_at.isoformat() if p.created_at else None,
        expires_at=p.expires_at.isoformat() if p.expires_at else None,
        approved_at=p.approved_at.isoformat() if p.approved_at else None,
        execution_job_id=p.execution_job_id,
        failure_reason=p.failure_reason,
        message_context=message_context,
    )


def _to_detail(p: ExecutionPlan) -> PlanDetail:
    s = _to_summary(p)
    return PlanDetail(
        **s.model_dump(),
        content_sha256=p.content_sha256,
        routing_ticket_digest=p.routing_ticket_digest,
        routing_config_revision=p.routing_config_revision,
        ai_reason=p.ai_reason,
        policy=p.policy or {},
        steps=[
            PlanStepSummary(
                step_key=st.step_key,
                step_order=st.step_order,
                action_type=st.action_type,
                status=st.status,
                attempt_count=st.attempt_count or 0,
                started_at=st.started_at.isoformat() if st.started_at else None,
                finished_at=st.finished_at.isoformat() if st.finished_at else None,
                error_message=st.error_message,
                result=st.result,
                approval_details=step_approval_details(st.action_type, st.parameters, p.targets),
            )
            for st in p.steps
        ],
        channel=p.channel,
        channel_account_id=p.channel_account_id,
        conversation_id=p.conversation_id,
        request_message_id=p.request_message_id,
        request_sender_id=p.request_sender_id,
        approval_message_id=p.approval_message_id,
    )


@router.get("", summary="查询执行计划列表")
def list_plans(
    status: Optional[str] = Query(None, description="状态过滤"),
    action_type: Optional[str] = Query(None, description="按步骤类型过滤"),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    user: Dict[str, Any] = Depends(get_current_user),
):
    service = ExecutionPlanService(db)
    items = service.list(status=status, action_type=action_type, limit=limit)
    return {"total": len(items), "items": [_to_summary(p).model_dump() for p in items]}


@router.get("/{plan_id}", summary="查询执行计划详情")
def get_plan(
    plan_id: str,
    db: Session = Depends(get_db),
    user: Dict[str, Any] = Depends(get_current_user),
):
    service = ExecutionPlanService(db)
    plan = service.get(plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="执行计划不存在")
    return _to_detail(plan).model_dump()


@router.post("/{plan_id}/reject", summary="拒绝执行计划")
def reject_plan(
    plan_id: str,
    db: Session = Depends(get_db),
    user: Dict[str, Any] = Depends(get_current_user),
):
    service = ExecutionPlanService(db)
    plan = service.reject(
        plan_id=plan_id,
        rejecter_matrix_id=user.get("username") or str(user.get("id")),
    )
    if not plan:
        raise HTTPException(status_code=400, detail="执行计划不存在或已不在待审批状态")
    return _to_summary(plan).model_dump()


@router.post("/expire-stale", summary="清理过期执行计划")
def expire_stale_plans(
    db: Session = Depends(get_db),
    user: Dict[str, Any] = Depends(get_current_user),
):
    service = ExecutionPlanService(db)
    count = service.expire_stale()
    return {"expired_count": count}
