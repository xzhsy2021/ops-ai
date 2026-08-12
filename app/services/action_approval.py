"""Channel-neutral approval lifecycle with Matrix compatibility at the boundary."""
import hashlib
import hmac
import json
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_
from sqlalchemy.orm import Session

from app.core.config import QCLAW_APPROVAL_TTL_SECONDS
from app.db.models import AiActionApproval
from app.services.message_context import MessageContext, normalize_identity, normalize_message_context


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _canonical_json(data: dict) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _context(value, *, room_id=None, request_event_id=None, content_sha256=None, sender_id="legacy-requester") -> MessageContext:
    if value is not None:
        return normalize_message_context(value)
    if not room_id or not request_event_id or not content_sha256:
        raise ValueError("message_context is required")
    legacy_hash = content_sha256
    if not isinstance(legacy_hash, str) or len(legacy_hash) != 64 or any(
        char not in "0123456789abcdefABCDEF" for char in legacy_hash
    ):
        legacy_hash = hashlib.sha256(str(legacy_hash).encode("utf-8")).hexdigest()
    return MessageContext(
        channel="matrix",
        channel_account_id="default",
        conversation_id=room_id,
        message_id=request_event_id,
        sender_id=sender_id,
        content_sha256=legacy_hash,
    )


def compute_action_digest(
    action_type: str,
    room_id: str | None = None,
    request_event_id: str | None = None,
    content_sha256: str | None = None,
    system_name: str = "",
    service_name: str | None = None,
    environment: str = "",
    targets: list[str] | None = None,
    action_parameters: dict | None = None,
    routing_config_revision: str = "",
    message_context: MessageContext | dict | None = None,
) -> str:
    context = _context(
        message_context,
        room_id=room_id,
        request_event_id=request_event_id,
        content_sha256=content_sha256,
    )
    manifest = {
        "action_type": action_type,
        "message_context": context.to_dict(),
        "system_name": system_name,
        "service_name": service_name,
        "environment": environment,
        "targets": sorted(targets or []),
        "action_parameters": action_parameters or {},
        "routing_config_revision": routing_config_revision,
    }
    return hashlib.sha256(_canonical_json(manifest).encode("utf-8")).hexdigest()


def _hash_approval_code(code: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", code.encode(), salt.encode(), 100000)
    return f"pbkdf2_sha256${salt}${digest.hex()}"


def _verify_approval_code(code: str, stored_hash: str) -> bool:
    try:
        _, salt, hash_hex = stored_hash.split("$", 2)
        digest = hashlib.pbkdf2_hmac("sha256", code.encode(), salt.encode(), 100000)
        return hmac.compare_digest(digest.hex(), hash_hex)
    except Exception:
        return False


def _identity_keys(values: list[dict] | None) -> set[str]:
    return {
        f"{item['channel']}:{item['channel_account_id']}:{item['sender_id']}"
        for item in values or []
        if isinstance(item, dict)
    }


def _request_actor_key(approval: AiActionApproval) -> str | None:
    sender_id = str(approval.request_sender_id or "").strip()
    if not sender_id:
        return None
    prefix = f"{approval.channel}:{approval.channel_account_id}:"
    if sender_id.startswith(prefix):
        return sender_id
    return f"{prefix}{sender_id}"


def _legacy_actor_key(value: str) -> str:
    value = str(value or "").strip()
    if value.startswith("matrix:default:"):
        return value
    return f"matrix:default:{value}"


def _stored_context(approval: AiActionApproval) -> MessageContext | None:
    if not all(
        getattr(approval, field, None)
        for field in (
            "channel",
            "channel_account_id",
            "conversation_id",
            "request_message_id",
            "request_sender_id",
            "content_sha256",
        )
    ):
        return None
    return MessageContext(
        channel=approval.channel,
        channel_account_id=approval.channel_account_id,
        conversation_id=approval.conversation_id,
        message_id=approval.request_message_id,
        sender_id=approval.request_sender_id,
        content_sha256=approval.content_sha256,
    )


class ActionApprovalService:
    def __init__(self, db: Session):
        self.db = db

    def prepare(
        self,
        action_type: str,
        tool_name: str,
        room_id: str | None = None,
        request_event_id: str | None = None,
        content_sha256: str | None = None,
        system_name: str = "",
        service_name: str | None = None,
        environment: str = "",
        targets: list[str] | None = None,
        action_parameters: dict | None = None,
        routing_config_revision: str = "",
        routing_ticket_digest: str = "",
        risk_level: str = "high",
        ai_reason: str = "",
        package_name: str | None = None,
        package_sha256: str | None = None,
        package_size_bytes: int | None = None,
        authorized_matrix_users: list[str] | None = None,
        *,
        message_context: MessageContext | dict | None = None,
        authorized_identities: list[dict | str] | None = None,
        temporary_grant_id: str | None = None,
    ) -> tuple[AiActionApproval, str]:
        context = _context(
            message_context,
            room_id=room_id,
            request_event_id=request_event_id,
            content_sha256=content_sha256,
        )
        if authorized_identities is None and authorized_matrix_users is not None:
            authorized_identities = authorized_matrix_users
        legacy_matrix_compat = (
            message_context is None
            and authorized_identities is None
            and authorized_matrix_users is None
        )
        identities = [normalize_identity(item) for item in (authorized_identities or [])]
        if message_context is not None and authorized_identities is None and authorized_matrix_users is None:
            raise ValueError("authorized approvers must not be empty")
        if (authorized_identities is not None or authorized_matrix_users is not None) and not identities:
            raise ValueError("authorized approvers must not be empty")
        digest = compute_action_digest(
            action_type=action_type,
            system_name=system_name,
            service_name=service_name,
            environment=environment,
            targets=targets,
            action_parameters=action_parameters,
            routing_config_revision=routing_config_revision,
            message_context=context,
        )
        existing = self.db.query(AiActionApproval).filter(
            and_(AiActionApproval.action_digest == digest, AiActionApproval.status == "PENDING_APPROVAL")
        ).first()
        if existing:
            return existing, ""
        short_code = secrets.token_hex(4).upper()
        approval = AiActionApproval(
            action_type=action_type,
            tool_name=tool_name,
            status="PENDING_APPROVAL",
            action_digest=digest,
            approval_code_hash=_hash_approval_code(short_code),
            room_id=context.conversation_id,
            request_event_id=context.message_id,
            content_sha256=context.content_sha256,
            channel=context.channel,
            channel_account_id=context.channel_account_id,
            conversation_id=context.conversation_id,
            request_message_id=context.message_id,
            request_sender_id=context.sender_id,
            authorized_identities=identities,
            temporary_grant_id=temporary_grant_id,
            requested_by=context.actor_key,
            routing_ticket_digest=routing_ticket_digest,
            routing_config_revision=routing_config_revision,
            expires_at=_utcnow() + timedelta(seconds=QCLAW_APPROVAL_TTL_SECONDS),
            risk_level=risk_level,
            ai_reason=ai_reason,
            package_name=package_name,
            package_sha256=package_sha256,
            package_size_bytes=package_size_bytes,
            request_payload={
                "system_name": system_name,
                "service_name": service_name,
                "environment": environment,
                "targets": sorted(targets or []),
                "action_parameters": action_parameters or {},
                "authorized_matrix_users": [item["sender_id"] for item in identities],
                "legacy_matrix_compat": legacy_matrix_compat,
            },
        )
        self.db.add(approval)
        self.db.commit()
        self.db.refresh(approval)
        return approval, short_code

    def consume(
        self,
        approval_id: str,
        short_code: str,
        approver_matrix_id: str | None = None,
        room_id: str | None = None,
        approval_event_id: str | None = None,
        *,
        approval_context: MessageContext | dict | None = None,
        digest: str | None = None,
    ) -> AiActionApproval | None:
        approval = self.db.query(AiActionApproval).filter(AiActionApproval.id == approval_id).first()
        if not approval or approval.status != "PENDING_APPROVAL" or approval.consumed_at is not None:
            return None
        if not _verify_approval_code(short_code, approval.approval_code_hash or ""):
            return None
        context = _context(
            approval_context,
            room_id=room_id,
            request_event_id=approval_event_id,
            content_sha256=approval.content_sha256 or "0" * 64,
            sender_id=approver_matrix_id or "",
        )
        if (approval.channel, approval.channel_account_id, approval.conversation_id) != (
            context.channel, context.channel_account_id, context.conversation_id
        ):
            return None
        request_actor_key = _request_actor_key(approval)
        if request_actor_key == context.actor_key:
            return None
        if not approval.authorized_identities:
            if not (approval.request_payload or {}).get("legacy_matrix_compat"):
                return None
        elif context.actor_key not in _identity_keys(approval.authorized_identities):
            return None
        stored_context = _stored_context(approval)
        if stored_context is None:
            return None
        payload = approval.request_payload or {}
        expected_digest = compute_action_digest(
            action_type=approval.action_type,
            system_name=payload.get("system_name") or "",
            service_name=payload.get("service_name"),
            environment=payload.get("environment") or "",
            targets=payload.get("targets") or [],
            action_parameters=payload.get("action_parameters") or {},
            routing_config_revision=approval.routing_config_revision or "",
            message_context=stored_context,
        )
        if expected_digest != approval.action_digest:
            return None
        if digest is not None and digest != approval.action_digest:
            return None
        if approval.expires_at and _utcnow() > approval.expires_at:
            approval.status = "EXPIRED"
            self.db.commit()
            return None
        now = _utcnow()
        result = self.db.query(AiActionApproval).filter(
            and_(AiActionApproval.id == approval_id, AiActionApproval.status == "PENDING_APPROVAL", AiActionApproval.consumed_at.is_(None))
        ).update({
            "status": "EXECUTING",
            "approved_by": context.actor_key,
            "approval_event_id": context.message_id,
            "approval_message_id": context.message_id,
            "approved_at": now,
            "consumed_at": now,
        }, synchronize_session=False)
        self.db.commit()
        if not result:
            return None
        self.db.refresh(approval)
        return approval

    def reject(
        self,
        approval_id: str,
        rejecter_matrix_id: str,
        *,
        rejection_context: MessageContext | dict | None = None,
    ) -> AiActionApproval | None:
        approval = self.db.query(AiActionApproval).filter(AiActionApproval.id == approval_id).first()
        if not approval or approval.status != "PENDING_APPROVAL":
            return None
        approval.status = "REJECTED"
        if rejection_context is not None:
            context = normalize_message_context(rejection_context)
            approval.rejected_by = context.actor_key
            approval.approval_message_id = context.message_id
        else:
            approval.rejected_by = _legacy_actor_key(rejecter_matrix_id)
        approval.rejected_at = _utcnow()
        self.db.commit()
        self.db.refresh(approval)
        return approval

    def expire_stale(self) -> int:
        count = self.db.query(AiActionApproval).filter(
            and_(AiActionApproval.status == "PENDING_APPROVAL", AiActionApproval.expires_at < _utcnow())
        ).update({"status": "EXPIRED"}, synchronize_session=False)
        self.db.commit()
        return count

    def get(self, approval_id: str) -> AiActionApproval | None:
        return self.db.query(AiActionApproval).filter(AiActionApproval.id == approval_id).first()
