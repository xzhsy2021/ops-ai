"""临时自审批授权管理 API。

提供 Web 管理界面的授权列表/详情查询，以及管理员会话回退的
确认（confirm）/撤销（revoke）端点。管理员回退路径把 Web 操作者
持久化为 `web:local:<username>`，并作为高权限回退动作审计；
确认必须提供包含授权短标识的短语；任何响应都不返回确认码哈希。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.helpers import api_response, audit
from app.core.auth_v2 import require_admin, require_auth
from app.db.base import get_db
from app.db.models import TemporaryApprovalGrant
from app.services.temporary_approval import TemporaryApprovalService


router = APIRouter(prefix="/api/v2/temporary-approvals", tags=["临时自审批授权"])


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _short_id(grant_id: str) -> str:
    """授权短标识：id 前 8 位大写，用于管理员确认短语。"""
    return str(grant_id or "")[:8].upper()


def _active_scope_key(grant: TemporaryApprovalGrant) -> str:
    return ":".join((
        grant.beneficiary_actor_key,
        grant.channel,
        grant.channel_account_id,
        grant.conversation_id,
        grant.system_id,
        grant.environment_id,
    ))


def _find_grant(db: Session, grant_id: str) -> TemporaryApprovalGrant:
    grant = db.query(TemporaryApprovalGrant).filter(TemporaryApprovalGrant.id == grant_id).first()
    if grant is None:
        raise HTTPException(status_code=404, detail="临时授权不存在")
    return grant


def _view_dict(grant: TemporaryApprovalGrant) -> Dict[str, Any]:
    return TemporaryApprovalService._view(grant).to_dict()


class ConfirmPayload(BaseModel):
    confirmation_phrase: str = ""


class RevokePayload(BaseModel):
    reason: str = ""


@router.get("", summary="查询临时自审批授权列表")
def list_grants(
    request: Request,
    status: str = Query("", description="状态过滤：PENDING/ACTIVE/REVOKED/EXPIRED"),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    require_auth(request, db)
    service = TemporaryApprovalService(db)
    items = service.list(status=status or None, limit=limit)
    return api_response(data={"total": len(items), "items": [item.to_dict() for item in items]})


@router.get("/{grant_id}", summary="查询临时自审批授权详情")
def grant_detail(grant_id: str, request: Request, db: Session = Depends(get_db)):
    require_auth(request, db)
    grant = _find_grant(db, grant_id)
    return api_response(data=_view_dict(grant))


@router.post("/{grant_id}/confirm", summary="管理员确认临时自审批授权")
def confirm_grant(
    grant_id: str,
    payload: ConfirmPayload,
    request: Request,
    db: Session = Depends(get_db),
):
    """管理员回退确认：把 Web 操作者记录为 web:local:<username>。

    要求确认短语包含授权短标识（id 前 8 位大写）；不经过通道短码，
    因为 Web 会话没有通道消息上下文。该路径只允许管理员。
    """
    user = require_admin(request, db)
    grant = _find_grant(db, grant_id)
    if grant.status != "PENDING" or grant.confirmation_consumed_at is not None:
        raise HTTPException(status_code=409, detail="授权不在待确认状态")
    phrase = str(payload.confirmation_phrase or "").strip()
    if _short_id(grant.id) not in phrase.upper():
        raise HTTPException(status_code=400, detail="确认短语必须包含授权的短标识")

    # 与通道确认一致：同一作用域只能有一个 ACTIVE 授权，避免唯一索引冲突。
    existing = db.query(TemporaryApprovalGrant).filter(
        TemporaryApprovalGrant.id != grant.id,
        TemporaryApprovalGrant.beneficiary_actor_key == grant.beneficiary_actor_key,
        TemporaryApprovalGrant.channel == grant.channel,
        TemporaryApprovalGrant.channel_account_id == grant.channel_account_id,
        TemporaryApprovalGrant.conversation_id == grant.conversation_id,
        TemporaryApprovalGrant.system_id == grant.system_id,
        TemporaryApprovalGrant.environment_id == grant.environment_id,
        TemporaryApprovalGrant.status == "ACTIVE",
    ).first()
    if existing is not None:
        raise HTTPException(status_code=409, detail="同一作用域已有生效的临时授权")

    now = _now()
    username = str(user.get("username") or user.get("id") or "admin")
    grant.status = "ACTIVE"
    grant.starts_at = now
    grant.expires_at = now + timedelta(seconds=grant.requested_duration_seconds)
    grant.approved_by_actor_key = f"web:local:{username}"
    grant.confirmation_consumed_at = now
    grant.active_scope_key = _active_scope_key(grant)
    grant.updated_at = now
    db.commit()
    db.refresh(grant)
    audit(
        "temporary_approval.confirm",
        "temporary_approval_grant",
        grant_id,
        f"admin={username} fallback=1 phrase={phrase}",
    )
    return api_response(data=_view_dict(grant), message="临时授权已确认")


@router.post("/{grant_id}/revoke", summary="管理员撤销临时自审批授权")
def revoke_grant(
    grant_id: str,
    payload: RevokePayload,
    request: Request,
    db: Session = Depends(get_db),
):
    """管理员回退撤销：把 Web 操作者记录为 web:local:<username>。"""
    user = require_admin(request, db)
    grant = _find_grant(db, grant_id)
    if grant.status != "ACTIVE":
        raise HTTPException(status_code=409, detail="授权不在生效状态")
    now = _now()
    username = str(user.get("username") or user.get("id") or "admin")
    reason = str(payload.reason or "").strip() or "revoked"
    grant.status = "REVOKED"
    grant.revoked_by_actor_key = f"web:local:{username}"
    grant.revoked_at = now
    grant.revoke_reason = reason
    grant.active_scope_key = None
    grant.updated_at = now
    db.commit()
    db.refresh(grant)
    audit(
        "temporary_approval.revoke",
        "temporary_approval_grant",
        grant_id,
        f"admin={username} fallback=1 reason={reason}",
    )
    return api_response(data=_view_dict(grant), message="临时授权已撤销")
