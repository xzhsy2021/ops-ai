"""Approval lifecycle: prepare, consume, reject, expire for qclaw Element integration.

qclaw 从 Element 房间读取消息并路由到对应系统后，会调用 prepare() 创建一个
不可变的审批工单。授权用户在 Element 中回复"批准 <short-code>"后，qclaw
路由回来调用 consume() 消费短码，工单状态变为 EXECUTING。

设计要点：
- 短码（short_code）只在 prepare 时返回一次明文，数据库只存加盐哈希。
- action_digest 是不可变 action manifest 的 SHA-256，用于幂等去重。
- consume() 使用 UPDATE ... WHERE status='PENDING_APPROVAL' AND consumed_at IS NULL
  保证原子性，多个并发调用只有一个成功。
- reject/expire 都是终态操作，不能再消费。
"""
import hashlib
import hmac
import json
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_
from sqlalchemy.orm import Session

from app.core.config import QCLAW_APPROVAL_TTL_SECONDS
from app.db.models import AiActionApproval


def _utcnow() -> datetime:
    """统一返回无时区信息的 UTC 时间，与现有模型字段保持一致。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _canonical_json(data: dict) -> str:
    """规范化 JSON：排序键 + 紧凑分隔符，保证 digest 计算的稳定性。"""
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compute_action_digest(
    action_type: str,
    room_id: str,
    request_event_id: str,
    content_sha256: str,
    system_name: str,
    service_name: str | None,
    environment: str,
    targets: list[str],
    action_parameters: dict,
    routing_config_revision: str,
) -> str:
    """计算不可变 action manifest 的 SHA-256 摘要。

    digest 用于幂等去重：相同 manifest 不应重复创建审批工单。
    """
    manifest = {
        "action_type": action_type,
        "room_id": room_id,
        "request_event_id": request_event_id,
        "content_sha256": content_sha256,
        "system_name": system_name,
        "service_name": service_name,
        "environment": environment,
        "targets": sorted(targets),
        "action_parameters": action_parameters,
        "routing_config_revision": routing_config_revision,
    }
    canonical = _canonical_json(manifest)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _hash_approval_code(code: str) -> str:
    """加盐哈希审批码，只存哈希值。

    使用 pbkdf2_sha256 + 随机 salt，与 Django 风格的哈希格式兼容。
    """
    salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", code.encode(), salt.encode(), 100000)
    return f"pbkdf2_sha256${salt}${h.hex()}"


def _verify_approval_code(code: str, stored_hash: str) -> bool:
    """验证审批码。使用常量时间比较防止时序攻击。"""
    try:
        algo, salt, hash_hex = stored_hash.split("$", 2)
        h = hashlib.pbkdf2_hmac("sha256", code.encode(), salt.encode(), 100000)
        return hmac.compare_digest(h.hex(), hash_hex)
    except Exception:
        return False


class ActionApprovalService:
    """审批工单生命周期管理。"""

    def __init__(self, db: Session):
        self.db = db

    def prepare(
        self,
        action_type: str,
        tool_name: str,
        room_id: str,
        request_event_id: str,
        content_sha256: str,
        system_name: str,
        service_name: str | None,
        environment: str,
        targets: list[str],
        action_parameters: dict,
        routing_config_revision: str,
        routing_ticket_digest: str,
        risk_level: str = "high",
        ai_reason: str = "",
        package_name: str | None = None,
        package_sha256: str | None = None,
        package_size_bytes: int | None = None,
        authorized_matrix_users: list[str] | None = None,
    ) -> tuple[AiActionApproval, str]:
        """创建审批工单，返回 (approval, plaintext_short_code)。

        短码只返回一次明文，之后只存哈希。
        如果相同 action_digest 的 PENDING_APPROVAL 工单已存在，返回已有工单（幂等），
        此时不再生成新的短码，第二返回值为空字符串。
        """
        digest = compute_action_digest(
            action_type, room_id, request_event_id, content_sha256,
            system_name, service_name, environment, targets,
            action_parameters, routing_config_revision,
        )

        # 幂等：相同 digest 的 PENDING_APPROVAL 工单已存在则直接返回
        existing = self.db.query(AiActionApproval).filter(
            and_(
                AiActionApproval.action_digest == digest,
                AiActionApproval.status == "PENDING_APPROVAL",
            )
        ).first()
        if existing:
            return existing, ""  # 已存在，不返回新短码

        short_code = secrets.token_hex(4).upper()  # 8 字符十六进制
        code_hash = _hash_approval_code(short_code)
        expires_at = _utcnow() + timedelta(seconds=QCLAW_APPROVAL_TTL_SECONDS)

        approval = AiActionApproval(
            action_type=action_type,
            tool_name=tool_name,
            status="PENDING_APPROVAL",
            action_digest=digest,
            approval_code_hash=code_hash,
            room_id=room_id,
            request_event_id=request_event_id,
            content_sha256=content_sha256,
            routing_ticket_digest=routing_ticket_digest,
            routing_config_revision=routing_config_revision,
            expires_at=expires_at,
            risk_level=risk_level,
            ai_reason=ai_reason,
            package_name=package_name,
            package_sha256=package_sha256,
            package_size_bytes=package_size_bytes,
            request_payload={
                "system_name": system_name,
                "service_name": service_name,
                "environment": environment,
                "targets": sorted(targets),
                "action_parameters": action_parameters,
                "authorized_matrix_users": authorized_matrix_users or [],
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
        approver_matrix_id: str,
        room_id: str,
        approval_event_id: str,
    ) -> AiActionApproval | None:
        """原子消费审批码。成功返回 approval（状态变为 EXECUTING），失败返回 None。

        使用 WHERE status='PENDING_APPROVAL' AND consumed_at IS NULL 确保原子性：
        多个并发调用者执行 UPDATE 时，数据库只会让其中一个的 rowcount=1。
        """
        approval = self.db.query(AiActionApproval).filter(
            AiActionApproval.id == approval_id
        ).first()
        if not approval:
            return None
        if approval.status != "PENDING_APPROVAL":
            return None
        if approval.consumed_at is not None:
            return None
        # 验证审批码
        if not _verify_approval_code(short_code, approval.approval_code_hash or ""):
            return None
        # 验证房间一致，防止跨房间重放
        if approval.room_id != room_id:
            return None
        # 验证是否过期
        if approval.expires_at and _utcnow() > approval.expires_at:
            approval.status = "EXPIRED"
            self.db.commit()
            return None

        # 原子消费：WHERE 条件确保只有一个调用者成功
        result = self.db.query(AiActionApproval).filter(
            and_(
                AiActionApproval.id == approval_id,
                AiActionApproval.status == "PENDING_APPROVAL",
                AiActionApproval.consumed_at.is_(None),
            )
        ).update(
            {
                "status": "EXECUTING",
                "approved_by": approver_matrix_id,
                "approval_event_id": approval_event_id,
                "approved_at": _utcnow(),
                "consumed_at": _utcnow(),
            },
            synchronize_session=False,
        )
        self.db.commit()
        if result == 0:
            return None
        self.db.refresh(approval)
        return approval

    def reject(
        self,
        approval_id: str,
        rejecter_matrix_id: str,
    ) -> AiActionApproval | None:
        """拒绝审批，终态操作。拒绝后不能再消费。"""
        approval = self.db.query(AiActionApproval).filter(
            AiActionApproval.id == approval_id
        ).first()
        if not approval:
            return None
        if approval.status != "PENDING_APPROVAL":
            return None
        approval.status = "REJECTED"
        approval.rejected_by = rejecter_matrix_id
        approval.rejected_at = _utcnow()
        self.db.commit()
        self.db.refresh(approval)
        return approval

    def expire_stale(self) -> int:
        """将过期的 PENDING_APPROVAL 标记为 EXPIRED，返回处理数量。"""
        now = _utcnow()
        count = self.db.query(AiActionApproval).filter(
            and_(
                AiActionApproval.status == "PENDING_APPROVAL",
                AiActionApproval.expires_at < now,
            )
        ).update({"status": "EXPIRED"}, synchronize_session=False)
        self.db.commit()
        return count

    def get(self, approval_id: str) -> AiActionApproval | None:
        """根据 ID 获取审批工单。"""
        return self.db.query(AiActionApproval).filter(
            AiActionApproval.id == approval_id
        ).first()
