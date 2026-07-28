"""qclaw 审批管理 API 路由。"""
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.auth_v2 import get_current_user
from app.db.base import get_db
from app.db.models import AiActionApproval
from app.services.action_approval import ActionApprovalService
from app.services.approval_executor import ApprovalExecutor
from app.config.systems import get_all_systems, get_system_by_name, save_system

router = APIRouter(prefix="/api/v2/approvals", tags=["qclaw审批管理"])


# ── 响应模型 ──

class ApprovalSummary(BaseModel):
    approval_id: str
    action_type: str
    status: str
    system_name: str | None = None
    service_name: str | None = None
    environment: str | None = None
    risk_level: str | None = None
    ai_reason: str | None = None
    requested_by: str | None = None
    approved_by: str | None = None
    rejected_by: str | None = None
    created_at: str | None = None
    expires_at: str | None = None
    approved_at: str | None = None
    executed_at: str | None = None
    execution_job_id: int | None = None
    package_name: str | None = None
    failure_reason: str | None = None


class ApprovalDetail(ApprovalSummary):
    action_digest: str | None = None
    room_id: str | None = None
    request_event_id: str | None = None
    approval_event_id: str | None = None
    content_sha256: str | None = None
    routing_ticket_digest: str | None = None
    routing_config_revision: str | None = None
    request_payload: dict[str, Any] = {}
    execution_result: dict[str, Any] | None = None


def _to_summary(a: AiActionApproval) -> ApprovalSummary:
    payload = a.request_payload or {}
    return ApprovalSummary(
        approval_id=a.id,
        action_type=a.action_type,
        status=a.status,
        system_name=payload.get("system_name"),
        service_name=payload.get("service_name"),
        environment=payload.get("environment"),
        risk_level=a.risk_level,
        ai_reason=a.ai_reason,
        requested_by=a.requested_by,
        approved_by=a.approved_by,
        rejected_by=a.rejected_by,
        created_at=a.created_at.isoformat() if a.created_at else None,
        expires_at=a.expires_at.isoformat() if a.expires_at else None,
        approved_at=a.approved_at.isoformat() if a.approved_at else None,
        executed_at=a.executed_at.isoformat() if a.executed_at else None,
        execution_job_id=a.execution_job_id,
        package_name=a.package_name,
        failure_reason=a.failure_reason,
    )


def _to_detail(a: AiActionApproval) -> ApprovalDetail:
    s = _to_summary(a)
    return ApprovalDetail(
        **s.model_dump(),
        action_digest=a.action_digest,
        room_id=a.room_id,
        request_event_id=a.request_event_id,
        approval_event_id=a.approval_event_id,
        content_sha256=a.content_sha256,
        routing_ticket_digest=a.routing_ticket_digest,
        routing_config_revision=a.routing_config_revision,
        request_payload=a.request_payload or {},
        execution_result=a.execution_result,
    )


# ── 路由 ──

@router.get("", summary="查询审批列表")
def list_approvals(
    status: Optional[str] = Query(None, description="状态过滤"),
    action_type: Optional[str] = Query(None, description="操作类型过滤"),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    user: Dict[str, Any] = Depends(get_current_user),
):
    query = db.query(AiActionApproval)
    if status:
        query = query.filter(AiActionApproval.status == status)
    if action_type:
        query = query.filter(AiActionApproval.action_type == action_type)
    items = query.order_by(AiActionApproval.created_at.desc()).limit(limit).all()
    return {"total": len(items), "items": [_to_summary(a).model_dump() for a in items]}


@router.get("/{approval_id}", summary="查询审批详情")
def get_approval(
    approval_id: str,
    db: Session = Depends(get_db),
    user: Dict[str, Any] = Depends(get_current_user),
):
    service = ActionApprovalService(db)
    approval = service.get(approval_id)
    if not approval:
        raise HTTPException(status_code=404, detail="审批工单不存在")
    return _to_detail(approval).model_dump()


@router.post("/{approval_id}/reject", summary="拒绝审批")
def reject_approval(
    approval_id: str,
    db: Session = Depends(get_db),
    user: Dict[str, Any] = Depends(get_current_user),
):
    service = ActionApprovalService(db)
    approval = service.reject(
        approval_id=approval_id,
        rejecter_matrix_id=user.get("username") or str(user.get("id")),
    )
    if not approval:
        raise HTTPException(status_code=400, detail="审批工单不存在或已不在待审批状态")
    return _to_summary(approval).model_dump()


@router.post("/expire-stale", summary="清理过期审批")
def expire_stale_approvals(
    db: Session = Depends(get_db),
    user: Dict[str, Any] = Depends(get_current_user),
):
    service = ActionApprovalService(db)
    count = service.expire_stale()
    return {"expired_count": count}


@router.post("/{approval_id}/execute", summary="手动触发执行（调试用）")
def manual_execute(
    approval_id: str,
    db: Session = Depends(get_db),
    user: Dict[str, Any] = Depends(get_current_user),
):
    """手动触发已审批操作的执行。正常流程由 qclaw 审批后自动触发。"""
    executor = ApprovalExecutor(db)
    approval = executor.execute(approval_id)
    if not approval:
        raise HTTPException(status_code=404, detail="审批工单不存在")
    return _to_detail(approval).model_dump()


# ── 消息路由配置管理 ──

class MessageRoutingConfig(BaseModel):
    """消息路由配置"""
    enabled: bool = False
    aliases: list[str] = []
    keywords: list[str] = []
    priority: int = 0


@router.get("/routing/systems", summary="查询所有系统的路由配置")
def list_routing_configs(
    user: Dict[str, Any] = Depends(get_current_user),
):
    systems = get_all_systems()
    result = []
    for name, cfg in systems.items():
        routing = cfg.get("message_routing", {})
        result.append({
            "system_name": name,
            "enabled": routing.get("enabled", False),
            "aliases": routing.get("aliases", []),
            "keywords": routing.get("keywords", []),
            "priority": routing.get("priority", 0),
            "services": [
                {
                    "service_name": svc.get("name", ""),
                    "enabled": (svc.get("template_variables", {}).get("message_routing", {}) or {}).get("enabled", False),
                    "aliases": (svc.get("template_variables", {}).get("message_routing", {}) or {}).get("aliases", []),
                    "keywords": (svc.get("template_variables", {}).get("message_routing", {}) or {}).get("keywords", []),
                    "priority": (svc.get("template_variables", {}).get("message_routing", {}) or {}).get("priority", 0),
                }
                for svc in cfg.get("services", [])
            ],
        })
    return {"total": len(result), "items": result}


@router.put("/routing/systems/{system_name}", summary="更新系统消息路由配置")
def update_system_routing(
    system_name: str,
    config: MessageRoutingConfig,
    user: Dict[str, Any] = Depends(get_current_user),
):
    sys_cfg = get_system_by_name(system_name)
    if not sys_cfg:
        raise HTTPException(status_code=404, detail=f"系统不存在: {system_name}")
    sys_cfg["message_routing"] = config.model_dump()
    save_system(system_name, sys_cfg)
    return {"ok": True, "system_name": system_name, "message_routing": config.model_dump()}


@router.put("/routing/systems/{system_name}/services/{service_name}", summary="更新服务消息路由配置")
def update_service_routing(
    system_name: str,
    service_name: str,
    config: MessageRoutingConfig,
    user: Dict[str, Any] = Depends(get_current_user),
):
    sys_cfg = get_system_by_name(system_name)
    if not sys_cfg:
        raise HTTPException(status_code=404, detail=f"系统不存在: {system_name}")
    # 找到对应服务
    found = False
    for svc in sys_cfg.get("services", []):
        if svc.get("name") == service_name:
            tv = svc.setdefault("template_variables", {})
            tv["message_routing"] = config.model_dump()
            found = True
            break
    if not found:
        raise HTTPException(status_code=404, detail=f"服务不存在: {service_name}")
    save_system(system_name, sys_cfg)
    return {"ok": True, "system_name": system_name, "service_name": service_name, "message_routing": config.model_dump()}
