"""Scoped, time-boxed self-approval for test-environment changes."""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

from sqlalchemy import and_, case
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import System, SystemEnvironment, TemporaryApprovalGrant
from app.services.message_context import MessageContext, normalize_identity, normalize_message_context


TEMPORARY_SELF_APPROVAL_ACTIONS = frozenset({
    "MATRIX_PULL",
    "FILE_UPLOAD",
    "RELEASE",
    "SERVICE_CONTROL",
    "HEALTH_CHECK",
})
CONFIRMATION_TTL_SECONDS = 15 * 60
MAX_GRANT_SECONDS = 4 * 7 * 24 * 60 * 60
MAX_CONFIRMATION_ATTEMPTS = 5


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _hash_code(code: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", code.encode(), salt.encode(), 100000)
    return f"pbkdf2_sha256${salt}${digest.hex()}"


def _verify_code(code: str, stored_hash: str) -> bool:
    try:
        algorithm, salt, digest_hex = stored_hash.split("$", 2)
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac("sha256", code.encode(), salt.encode(), 100000)
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (AttributeError, ValueError):
        return False


def _context(value: MessageContext | dict) -> MessageContext:
    return normalize_message_context(value)


def _normalize_actor_key(value: str, context: MessageContext) -> str:
    actor_key = str(value or "").strip()
    prefix = f"{context.channel}:{context.channel_account_id}:"
    if not actor_key.startswith(prefix) or not actor_key[len(prefix):].strip():
        raise ValueError("beneficiary_actor_key must use the request channel and account")
    return actor_key


def _duration_seconds(duration_value: int, duration_unit: str) -> int:
    if type(duration_value) is not int or duration_value <= 0:
        raise ValueError("duration_value must be a positive integer")
    unit = str(duration_unit or "day").strip().lower()
    multiplier = {"day": 24 * 60 * 60, "week": 7 * 24 * 60 * 60}.get(unit)
    if multiplier is None:
        raise ValueError("duration_unit must be day or week")
    seconds = duration_value * multiplier
    if seconds > MAX_GRANT_SECONDS:
        raise ValueError("temporary approval cannot exceed 4 weeks")
    return seconds


def _normalize_actions(actions: list[str] | None) -> list[str]:
    if not isinstance(actions, list) or not actions:
        raise ValueError("allowed actions must not be empty")
    normalized = sorted({str(item or "").strip().upper() for item in actions if str(item or "").strip()})
    if not normalized:
        raise ValueError("allowed actions must not be empty")
    forbidden = [item for item in normalized if item not in TEMPORARY_SELF_APPROVAL_ACTIONS]
    if forbidden:
        raise ValueError(f"action is not allowed for temporary self-approval: {', '.join(forbidden)}")
    return normalized


def _identity_keys(values: list[dict]) -> set[str]:
    return {
        f"{item['channel']}:{item['channel_account_id']}:{item['sender_id']}"
        for item in values
    }


def _active_scope_key(grant: TemporaryApprovalGrant) -> str:
    return ":".join((
        grant.beneficiary_actor_key,
        grant.channel,
        grant.channel_account_id,
        grant.conversation_id,
        grant.system_id,
        grant.environment_id,
    ))


@dataclass(frozen=True)
class TemporaryApprovalGrantView:
    """Public grant projection; never carries the confirmation hash."""

    id: str
    beneficiary_actor_key: str
    channel: str
    channel_account_id: str
    conversation_id: str
    system_id: str
    environment_id: str
    allowed_actions: list[str]
    authorized_identities: list[dict]
    reason: str
    starts_at: datetime | None
    expires_at: datetime | None
    status: str
    requested_by_actor_key: str
    approved_by_actor_key: str | None
    request_message_id: str
    confirmation_message_id: str | None
    request_digest: str
    confirmation_expires_at: datetime
    revoked_by_actor_key: str | None
    revoked_at: datetime | None
    revoke_reason: str | None
    requested_duration_seconds: int
    created_at: datetime
    updated_at: datetime

    def to_dict(self) -> dict:
        return {
            key: value for key, value in self.__dict__.items()
            if value is not None
        }


class TemporaryApprovalService:
    def __init__(self, db: Session, approver_resolver: Callable | None = None):
        self.db = db
        self.approver_resolver = approver_resolver

    def _scope(self, system_name: str, environment_name: str) -> tuple[System, SystemEnvironment]:
        system = self.db.query(System).filter(System.name == str(system_name or "").strip()).first()
        if system is None:
            raise ValueError("system does not exist")
        environment = self.db.query(SystemEnvironment).filter(
            SystemEnvironment.system_name == system.name,
            SystemEnvironment.name == str(environment_name or "").strip(),
        ).first()
        if environment is None:
            raise ValueError("environment does not exist")
        if str(environment.category or "").strip().lower() != "test":
            raise ValueError("temporary self-approval is only available for a test environment")
        return system, environment

    def _configured_approvers(self, system: System, context: MessageContext) -> list[dict]:
        if self.approver_resolver is not None:
            values = self.approver_resolver(system.name, context)
        else:
            routing = system.message_routing or {}
            values = routing.get("approvers", []) if isinstance(routing, dict) else []
        identities = [normalize_identity(item) for item in values or []]
        return [
            item for item in identities
            if item["channel"] == context.channel
            and item["channel_account_id"] == context.channel_account_id
        ]

    @staticmethod
    def _view(grant: TemporaryApprovalGrant) -> TemporaryApprovalGrantView:
        return TemporaryApprovalGrantView(
            id=grant.id,
            beneficiary_actor_key=grant.beneficiary_actor_key,
            channel=grant.channel,
            channel_account_id=grant.channel_account_id,
            conversation_id=grant.conversation_id,
            system_id=grant.system_id,
            environment_id=grant.environment_id,
            allowed_actions=list(grant.allowed_actions or []),
            authorized_identities=list(grant.authorized_identities or []),
            reason=grant.reason,
            starts_at=grant.starts_at,
            expires_at=grant.expires_at,
            status=grant.status,
            requested_by_actor_key=grant.requested_by_actor_key,
            approved_by_actor_key=grant.approved_by_actor_key,
            request_message_id=grant.request_message_id,
            confirmation_message_id=grant.confirmation_message_id,
            request_digest=grant.request_digest,
            confirmation_expires_at=grant.confirmation_expires_at,
            revoked_by_actor_key=grant.revoked_by_actor_key,
            revoked_at=grant.revoked_at,
            revoke_reason=grant.revoke_reason,
            requested_duration_seconds=grant.requested_duration_seconds,
            created_at=grant.created_at,
            updated_at=grant.updated_at,
        )

    def _active_row(
        self,
        *,
        actor_key: str,
        system_name: str,
        environment_name: str,
        message_context: MessageContext,
    ) -> TemporaryApprovalGrant | None:
        if actor_key != message_context.actor_key:
            return None
        system = self.db.query(System).filter(System.name == system_name).first()
        environment = self.db.query(SystemEnvironment).filter(
            SystemEnvironment.system_name == system_name,
            SystemEnvironment.name == environment_name,
        ).first()
        if system is None or environment is None or str(environment.category or "").lower() != "test":
            return None
        grant = self.db.query(TemporaryApprovalGrant).filter(
            TemporaryApprovalGrant.beneficiary_actor_key == actor_key,
            TemporaryApprovalGrant.system_id == system.id,
            TemporaryApprovalGrant.environment_id == environment.id,
            TemporaryApprovalGrant.channel == message_context.channel,
            TemporaryApprovalGrant.channel_account_id == message_context.channel_account_id,
            TemporaryApprovalGrant.conversation_id == message_context.conversation_id,
            TemporaryApprovalGrant.status == "ACTIVE",
        ).order_by(TemporaryApprovalGrant.created_at.desc()).first()
        return grant

    def request(
        self,
        *,
        message_context: MessageContext | dict,
        beneficiary_actor_key: str,
        system_name: str,
        environment_name: str,
        allowed_actions: list[str],
        reason: str,
        authorized_identities: list[dict | str] | None = None,
        duration_value: int = 1,
        duration_unit: str = "day",
    ) -> tuple[TemporaryApprovalGrantView, str]:
        context = _context(message_context)
        beneficiary = _normalize_actor_key(beneficiary_actor_key, context)
        system, environment = self._scope(system_name, environment_name)
        actions = _normalize_actions(allowed_actions)
        reason = str(reason or "").strip()
        if not reason:
            raise ValueError("reason must not be empty")
        identities = self._configured_approvers(system, context)
        if authorized_identities is not None:
            supplied = [normalize_identity(item) for item in authorized_identities]
            if _identity_keys(supplied) != _identity_keys(identities):
                raise ValueError("authorized approvers must come from the configured policy")
        if not identities:
            raise ValueError("authorized approvers must not be empty")
        if not _identity_keys(identities):
            raise ValueError("authorized approvers must not be empty")
        duration_seconds = _duration_seconds(duration_value, duration_unit)
        active = self._active_row(
            actor_key=beneficiary,
            system_name=system.name,
            environment_name=environment.name,
            message_context=context,
        )
        if active is not None:
            current = self._expire_if_needed(active)
            if current is not None:
                return self._view(current), ""
        request_payload = {
            "beneficiary_actor_key": beneficiary,
            "channel": context.channel,
            "channel_account_id": context.channel_account_id,
            "conversation_id": context.conversation_id,
            "request_message_id": context.message_id,
            "system_id": system.id,
            "environment_id": environment.id,
            "allowed_actions": actions,
            "authorized_identities": identities,
            "reason": reason,
            "requested_duration_seconds": duration_seconds,
            "message_context": context.to_dict(),
        }
        request_digest = hashlib.sha256(_canonical_json(request_payload).encode("utf-8")).hexdigest()
        pending = self.db.query(TemporaryApprovalGrant).filter(
            TemporaryApprovalGrant.request_digest == request_digest,
            TemporaryApprovalGrant.status == "PENDING",
        ).first()
        if pending is not None:
            return self._view(pending), ""
        code = secrets.token_hex(4).upper()
        now = _utcnow()
        grant = TemporaryApprovalGrant(
            beneficiary_actor_key=beneficiary,
            channel=context.channel,
            channel_account_id=context.channel_account_id,
            conversation_id=context.conversation_id,
            system_id=system.id,
            environment_id=environment.id,
            allowed_actions=actions,
            authorized_identities=identities,
            reason=reason,
            status="PENDING",
            requested_by_actor_key=context.actor_key,
            request_message_id=context.message_id,
            request_digest=request_digest,
            confirmation_code_hash=_hash_code(code),
            confirmation_expires_at=now + timedelta(seconds=CONFIRMATION_TTL_SECONDS),
            requested_duration_seconds=duration_seconds,
            created_at=now,
            updated_at=now,
        )
        self.db.add(grant)
        self.db.commit()
        self.db.refresh(grant)
        return self._view(grant), code

    def _same_conversation(self, grant: TemporaryApprovalGrant, context: MessageContext) -> bool:
        return (
            grant.channel == context.channel
            and grant.channel_account_id == context.channel_account_id
            and grant.conversation_id == context.conversation_id
        )

    def confirm(
        self,
        grant_id: str,
        code: str,
        *,
        actor_key: str,
        message_context: MessageContext | dict,
    ) -> TemporaryApprovalGrantView | None:
        grant = self.db.query(TemporaryApprovalGrant).filter(TemporaryApprovalGrant.id == grant_id).first()
        if grant is None or grant.status != "PENDING" or grant.confirmation_consumed_at is not None:
            return None
        context = _context(message_context)
        if actor_key != context.actor_key or not self._same_conversation(grant, context):
            return None
        if actor_key not in _identity_keys(grant.authorized_identities or []):
            return None
        if not _verify_code(code, grant.confirmation_code_hash or ""):
            next_attempts = TemporaryApprovalGrant.confirmation_attempts + 1
            self.db.query(TemporaryApprovalGrant).filter(
                and_(
                    TemporaryApprovalGrant.id == grant_id,
                    TemporaryApprovalGrant.status == "PENDING",
                    TemporaryApprovalGrant.confirmation_consumed_at.is_(None),
                    TemporaryApprovalGrant.confirmation_attempts < MAX_CONFIRMATION_ATTEMPTS,
                )
            ).update({
                "confirmation_attempts": next_attempts,
                "status": case(
                    (next_attempts >= MAX_CONFIRMATION_ATTEMPTS, "EXPIRED"),
                    else_=TemporaryApprovalGrant.status,
                ),
                "updated_at": _utcnow(),
            }, synchronize_session=False)
            self.db.commit()
            return None
        now = _utcnow()
        if grant.confirmation_expires_at and now > grant.confirmation_expires_at:
            grant.status = "EXPIRED"
            self.db.commit()
            return None
        environment = self.db.query(SystemEnvironment).filter(SystemEnvironment.id == grant.environment_id).first()
        if environment is None or str(environment.category or "").strip().lower() != "test":
            return None
        active = self.db.query(TemporaryApprovalGrant).filter(
            TemporaryApprovalGrant.id != grant_id,
            TemporaryApprovalGrant.beneficiary_actor_key == grant.beneficiary_actor_key,
            TemporaryApprovalGrant.channel == grant.channel,
            TemporaryApprovalGrant.channel_account_id == grant.channel_account_id,
            TemporaryApprovalGrant.conversation_id == grant.conversation_id,
            TemporaryApprovalGrant.system_id == grant.system_id,
            TemporaryApprovalGrant.environment_id == grant.environment_id,
            TemporaryApprovalGrant.status == "ACTIVE",
        ).first()
        if active is not None:
            return None
        result = self.db.query(TemporaryApprovalGrant).filter(
            and_(
                TemporaryApprovalGrant.id == grant_id,
                TemporaryApprovalGrant.status == "PENDING",
                TemporaryApprovalGrant.confirmation_consumed_at.is_(None),
            )
        ).update({
            "status": "ACTIVE",
            "starts_at": now,
            "expires_at": now + timedelta(seconds=grant.requested_duration_seconds),
            "approved_by_actor_key": actor_key,
            "confirmation_message_id": context.message_id,
            "confirmation_consumed_at": now,
            "active_scope_key": _active_scope_key(grant),
            "updated_at": now,
        }, synchronize_session=False)
        try:
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            return None
        if not result:
            return None
        self.db.refresh(grant)
        return self._view(grant)

    def revoke(
        self,
        grant_id: str,
        *,
        actor_key: str,
        message_context: MessageContext | dict,
        reason: str,
    ) -> TemporaryApprovalGrantView | None:
        grant = self.db.query(TemporaryApprovalGrant).filter(TemporaryApprovalGrant.id == grant_id).first()
        if grant is None or grant.status != "ACTIVE":
            return None
        context = _context(message_context)
        if actor_key != context.actor_key or not self._same_conversation(grant, context):
            return None
        if actor_key not in _identity_keys(grant.authorized_identities or []):
            return None
        now = _utcnow()
        grant.status = "REVOKED"
        grant.revoked_by_actor_key = actor_key
        grant.revoked_at = now
        grant.revoke_reason = str(reason or "").strip() or "revoked"
        grant.active_scope_key = None
        grant.updated_at = now
        self.db.commit()
        self.db.refresh(grant)
        return self._view(grant)

    def _expire_if_needed(self, grant: TemporaryApprovalGrant) -> TemporaryApprovalGrant | None:
        now = _utcnow()
        if grant.status in {"PENDING", "ACTIVE"} and grant.expires_at and now > grant.expires_at:
            grant.status = "EXPIRED"
            grant.active_scope_key = None
            grant.updated_at = now
            self.db.commit()
            self.db.refresh(grant)
            return None
        if grant.status == "PENDING" and grant.confirmation_expires_at and now > grant.confirmation_expires_at:
            grant.status = "EXPIRED"
            grant.active_scope_key = None
            grant.updated_at = now
            self.db.commit()
            self.db.refresh(grant)
            return None
        return grant

    def expire_if_needed(self, grant: TemporaryApprovalGrant) -> TemporaryApprovalGrantView | None:
        current = self._expire_if_needed(grant)
        return self._view(current) if current is not None else None

    def get_active_grant(
        self,
        *,
        actor_key: str,
        system_name: str,
        environment_name: str,
        message_context: MessageContext | dict,
    ) -> TemporaryApprovalGrantView | None:
        context = _context(message_context)
        grant = self._active_row(
            actor_key=actor_key,
            system_name=system_name,
            environment_name=environment_name,
            message_context=context,
        )
        if grant is None:
            return None
        current = self._expire_if_needed(grant)
        return self._view(current) if current is not None else None

    def is_self_approval_allowed(
        self,
        *,
        actor_key: str,
        system_name: str,
        environment_name: str,
        action_types: list[str],
        message_context: MessageContext | dict,
    ) -> bool:
        actions = {str(item or "").strip().upper() for item in action_types or []}
        grant = self.get_active_grant(
            actor_key=actor_key,
            system_name=system_name,
            environment_name=environment_name,
            message_context=message_context,
        )
        return bool(grant and actions and actions <= set(grant.allowed_actions or []))

    def can_self_approve_plan(self, **kwargs) -> bool:
        return self.is_self_approval_allowed(**kwargs)

    def list(
        self,
        *,
        status: str | None = None,
        limit: int = 50,
        channel: str | None = None,
        channel_account_id: str | None = None,
        conversation_id: str | None = None,
        beneficiary_actor_key: str | None = None,
        system_id: str | None = None,
        environment_id: str | None = None,
    ) -> list[TemporaryApprovalGrantView]:
        """按条件查询授权列表。

        传入 channel/channel_account_id/conversation_id 时按会话作用域过滤
        （MCP 查询路径用它防止跨会话枚举授权记录）。投影不含确认码哈希。
        """
        query = self.db.query(TemporaryApprovalGrant)
        if status:
            query = query.filter(TemporaryApprovalGrant.status == status)
        if channel:
            query = query.filter(TemporaryApprovalGrant.channel == channel)
        if channel_account_id:
            query = query.filter(TemporaryApprovalGrant.channel_account_id == channel_account_id)
        if conversation_id:
            query = query.filter(TemporaryApprovalGrant.conversation_id == conversation_id)
        if beneficiary_actor_key:
            query = query.filter(TemporaryApprovalGrant.beneficiary_actor_key == beneficiary_actor_key)
        if system_id:
            query = query.filter(TemporaryApprovalGrant.system_id == system_id)
        if environment_id:
            query = query.filter(TemporaryApprovalGrant.environment_id == environment_id)
        return [self._view(row) for row in query.order_by(TemporaryApprovalGrant.created_at.desc()).limit(limit).all()]

    def get(self, grant_id: str) -> TemporaryApprovalGrantView | None:
        """按 ID 查询单条授权（无权限判定，调用方自行强制作用域）。

        过期检查会同步落库：到期记录被更新为 EXPIRED 后按当前状态返回，
        而不是对查询者隐藏其存在。
        """
        grant = self.db.query(TemporaryApprovalGrant).filter(TemporaryApprovalGrant.id == str(grant_id or "").strip()).first()
        if grant is None:
            return None
        self._expire_if_needed(grant)
        return self._view(grant)
