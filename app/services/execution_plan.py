"""Message-level execution plan lifecycle for qclaw Element integration.

一条 Element/qclaw 消息对应一个 ExecutionPlan：授权人批准一次后，计划内
声明的步骤按顺序自动执行，不再逐步骤审批。

设计要点（与 AiActionApproval 单动作审批对齐）：
- plan_digest 是不可变 plan manifest 的 SHA-256，用于幂等去重。
- 短码（short_code）只在 prepare 时返回一次明文，数据库只存加盐哈希。
- consume() 使用 UPDATE ... WHERE status='PENDING_APPROVAL' AND consumed_at IS NULL
  保证原子性，多个并发调用只有一个成功。
- consume 还必须校验存储 manifest 的 digest 未变（防止审批后计划被篡改）。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_
from sqlalchemy.orm import Session

from app.core.config import QCLAW_APPROVAL_TTL_SECONDS
from app.db.models import ExecutionPlan, ExecutionPlanStep
from app.services.message_context import MessageContext, normalize_identity, normalize_message_context


class PlanValidationError(ValueError):
    """执行计划 manifest 校验失败。"""


def _utcnow() -> datetime:
    """统一返回无时区信息的 UTC 时间，与现有模型字段保持一致。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _canonical_json(data: dict) -> str:
    """规范化 JSON：排序键 + 紧凑分隔符，保证 digest 计算的稳定性。"""
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _context(value, *, room_id=None, request_event_id=None, content_sha256=None, sender_id="legacy-requester") -> MessageContext:
    if value is not None:
        return normalize_message_context(value)
    if not room_id or not request_event_id or not content_sha256:
        raise PlanValidationError("message_context is required")
    legacy_hash = content_sha256
    if not isinstance(legacy_hash, str) or len(legacy_hash) != 64 or any(
        char not in "0123456789abcdefABCDEF" for char in legacy_hash
    ):
        legacy_hash = hashlib.sha256(str(legacy_hash).encode("utf-8")).hexdigest()
    return MessageContext("matrix", "default", room_id, request_event_id, sender_id, legacy_hash)


def _request_actor_key(plan: ExecutionPlan) -> str | None:
    sender_id = str(plan.request_sender_id or "").strip()
    if not sender_id:
        return None
    prefix = f"{plan.channel}:{plan.channel_account_id}:"
    if sender_id.startswith(prefix):
        return sender_id
    return f"{prefix}{sender_id}"


def _legacy_actor_key(value: str) -> str:
    value = str(value or "").strip()
    if value.startswith("matrix:default:"):
        return value
    return f"matrix:default:{value}"


def _hash_approval_code(code: str) -> str:
    """加盐哈希审批码，只存哈希值。与 action_approval 同格式。"""
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


def _normalize_steps(steps: list[dict]) -> list[dict]:
    """规范化步骤列表：step_key 去重、依赖引用校验、按输入顺序保留。"""
    if not isinstance(steps, list) or not steps:
        raise PlanValidationError("执行计划必须包含至少一个步骤")

    seen: set[str] = set()
    normalized: list[dict] = []
    for idx, raw in enumerate(steps):
        if not isinstance(raw, dict):
            raise PlanValidationError(f"步骤 #{idx} 必须是对象")
        step_key = str(raw.get("step_key") or "").strip()
        if not step_key:
            raise PlanValidationError(f"步骤 #{idx} 缺少 step_key")
        if step_key in seen:
            raise PlanValidationError(f"重复的 step_key: {step_key}")
        seen.add(step_key)
        action_type = str(raw.get("action_type") or "").strip()
        if not action_type:
            raise PlanValidationError(f"步骤 {step_key} 缺少 action_type")
        normalized.append({
            "step_key": step_key,
            "action_type": action_type,
            "parameters": dict(raw.get("parameters") or {}),
            "dependencies": list(raw.get("dependencies") or []),
        })

    for step in normalized:
        for dep in step["dependencies"]:
            if dep not in seen:
                raise PlanValidationError(
                    f"步骤 {step['step_key']} 依赖不存在的步骤: {dep}"
                )
    return normalized


def step_approval_details(action_type: str, parameters: dict | None, default_targets: list[str] | None = None) -> dict:
    """Return the non-secret fields an approver must see for a plan step."""
    if str(action_type or "").strip() != "FILE_UPLOAD":
        return {}
    params = dict(parameters or {})
    action_parameters = dict(params.get("action_parameters") or {})
    return {
        "package_name": action_parameters.get("package_name") or "",
        "remote_path": action_parameters.get("remote_path") or "",
        "overwrite": bool(action_parameters.get("overwrite", False)),
        "expected_sha256": action_parameters.get("expected_sha256") or "",
        "expected_size_bytes": action_parameters.get("expected_size_bytes"),
        "targets": list(params.get("targets") or default_targets or []),
    }


def compute_plan_digest(
    room_id: str | None = None,
    request_event_id: str | None = None,
    content_sha256: str | None = None,
    system_name: str = "",
    service_name: str | None = None,
    environment: str = "",
    targets: list[str] | None = None,
    steps: list[dict] | None = None,
    policy: dict | None = None,
    routing_config_revision: str = "",
    routing_ticket_digest: str = "",
    message_context: MessageContext | dict | None = None,
) -> str:
    """计算不可变 plan manifest 的 SHA-256 摘要。

    digest 用于幂等去重和审批后完整性校验：相同 manifest 不应重复创建
    待审批计划；审批后 manifest 的任何实质变化都必须导致 digest 不同，
    从而禁止执行被篡改的计划。
    """
    context = _context(message_context, room_id=room_id, request_event_id=request_event_id, content_sha256=content_sha256)
    normalized_steps = _normalize_steps(steps or [])
    manifest = {
        "message_context": context.to_dict(),
        "system_name": system_name,
        "service_name": service_name,
        "environment": environment,
        "targets": sorted(targets or []),
        "steps": normalized_steps,
        "policy": policy or {},
        "routing_config_revision": routing_config_revision,
        "routing_ticket_digest": routing_ticket_digest,
    }
    canonical = _canonical_json(manifest)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_plan_manifest(
    room_id: str,
    request_event_id: str,
    content_sha256: str,
    system_name: str,
    service_name: str | None,
    environment: str,
    targets: list[str],
    steps: list[dict],
    policy: dict,
    routing_config_revision: str,
    routing_ticket_digest: str,
    message_context: MessageContext | dict | None = None,
) -> dict:
    """构建并规范化完整 plan manifest（冻结所有步骤和参数）。"""
    context = _context(message_context, room_id=room_id, request_event_id=request_event_id, content_sha256=content_sha256)
    return {
        "message_context": context.to_dict(),
        "system_name": system_name,
        "service_name": service_name,
        "environment": environment,
        "targets": sorted(targets),
        "steps": _normalize_steps(steps),
        "policy": dict(policy or {}),
        "routing_config_revision": routing_config_revision,
        "routing_ticket_digest": routing_ticket_digest,
    }


class ExecutionPlanService:
    """执行计划生命周期管理。"""

    def __init__(self, db: Session):
        self.db = db

    def prepare(
        self,
        room_id: str | None = None,
        request_event_id: str | None = None,
        content_sha256: str | None = None,
        system_name: str = "",
        service_name: str | None = None,
        environment: str = "",
        targets: list[str] | None = None,
        steps: list[dict] | None = None,
        policy: dict | None = None,
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
    ) -> tuple[ExecutionPlan, str]:
        """创建待审批执行计划，返回 (plan, plaintext_short_code)。

        相同 plan_digest 的 PENDING_APPROVAL 计划已存在时，返回已有计划（幂等），
        第二返回值为空字符串。
        """
        context = _context(message_context, room_id=room_id, request_event_id=request_event_id, content_sha256=content_sha256)
        if authorized_identities is None and authorized_matrix_users is not None:
            authorized_identities = authorized_matrix_users
        legacy_matrix_compat = (
            message_context is None
            and authorized_identities is None
            and authorized_matrix_users is None
        )
        identities = [normalize_identity(item) for item in (authorized_identities or [])]
        if message_context is not None and authorized_identities is None and authorized_matrix_users is None:
            raise PlanValidationError("authorized approvers must not be empty")
        if (authorized_identities is not None or authorized_matrix_users is not None) and not identities:
            raise PlanValidationError("authorized approvers must not be empty")

        manifest = build_plan_manifest(
            room_id, request_event_id, content_sha256,
            system_name, service_name, environment, targets,
            steps, policy, routing_config_revision, routing_ticket_digest, message_context=context,
        )
        digest = compute_plan_digest(
            room_id, request_event_id, content_sha256,
            system_name, service_name, environment, targets,
            steps, policy, routing_config_revision, routing_ticket_digest, message_context=context,
        )

        # 幂等：相同 digest 的 PENDING_APPROVAL 计划已存在则直接返回
        if legacy_matrix_compat:
            manifest["legacy_matrix_compat"] = True

        existing = self.db.query(ExecutionPlan).filter(
            and_(
                ExecutionPlan.plan_digest == digest,
                ExecutionPlan.status == "PENDING_APPROVAL",
            )
        ).first()
        if existing:
            return existing, ""

        short_code = secrets.token_hex(4).upper()  # 8 字符十六进制
        code_hash = _hash_approval_code(short_code)
        expires_at = _utcnow() + timedelta(seconds=QCLAW_APPROVAL_TTL_SECONDS)

        plan = ExecutionPlan(
            status="PENDING_APPROVAL",
            plan_digest=digest,
            risk_level=risk_level,
            room_id=room_id or context.conversation_id,
            request_event_id=request_event_id or context.message_id,
            content_sha256=content_sha256 or context.content_sha256,
            channel=context.channel,
            channel_account_id=context.channel_account_id,
            conversation_id=context.conversation_id,
            request_message_id=context.message_id,
            request_sender_id=context.sender_id,
            authorized_identities=identities,
            temporary_grant_id=temporary_grant_id,
            requested_by=context.actor_key,
            system_name=system_name,
            service_name=service_name,
            environment=environment,
            targets=sorted(targets or []),
            routing_ticket_digest=routing_ticket_digest,
            routing_config_revision=routing_config_revision,
            package_name=package_name,
            package_sha256=package_sha256,
            package_size_bytes=package_size_bytes,
            manifest=manifest,
            policy=dict(policy or {}),
            ai_reason=ai_reason,
            authorized_matrix_users=[item["sender_id"] for item in identities],
            approval_code_hash=code_hash,
            expires_at=expires_at,
        )
        self.db.add(plan)
        self.db.flush()

        for idx, step in enumerate(manifest["steps"]):
            self.db.add(ExecutionPlanStep(
                plan_id=plan.id,
                step_key=step["step_key"],
                step_order=idx,
                action_type=step["action_type"],
                parameters=step["parameters"],
                dependencies=step["dependencies"],
                status="PENDING",
                attempt_count=0,
            ))
        self.db.commit()
        self.db.refresh(plan)
        return plan, short_code

    def get(self, plan_id: str) -> ExecutionPlan | None:
        """根据 ID 获取执行计划。"""
        return self.db.query(ExecutionPlan).filter(
            ExecutionPlan.id == plan_id
        ).first()

    def get_by_digest(self, digest: str, status: str | None = None) -> ExecutionPlan | None:
        """根据 plan_digest 查找计划，可按状态过滤。"""
        query = self.db.query(ExecutionPlan).filter(
            ExecutionPlan.plan_digest == digest
        )
        if status:
            query = query.filter(ExecutionPlan.status == status)
        return query.order_by(ExecutionPlan.created_at.desc()).first()

    def _verify_stored_manifest(self, plan: ExecutionPlan) -> bool:
        """校验存储 manifest 未发生实质变化：重新计算 digest 与计划一致。"""
        m = plan.manifest or {}
        stored_context = m.get("message_context")
        if not isinstance(stored_context, dict):
            return False
        current_digest = compute_plan_digest(
            content_sha256=stored_context["content_sha256"],
            system_name=m.get("system_name") or plan.system_name or "",
            service_name=m.get("service_name") or plan.service_name,
            environment=m.get("environment") or plan.environment or "",
            targets=m.get("targets") or plan.targets or [],
            steps=m.get("steps") or [],
            policy=m.get("policy") or plan.policy or {},
            routing_config_revision=m.get("routing_config_revision") or plan.routing_config_revision or "",
            routing_ticket_digest=m.get("routing_ticket_digest") or plan.routing_ticket_digest or "",
            message_context=stored_context,
        )
        return current_digest == plan.plan_digest

    def consume(
        self,
        plan_id: str,
        short_code: str,
        approver_matrix_id: str | None = None,
        room_id: str | None = None,
        approval_event_id: str | None = None,
        *,
        approval_context: MessageContext | dict | None = None,
        digest: str | None = None,
    ) -> ExecutionPlan | None:
        """原子消费审批码。成功返回 plan（状态 APPROVED），失败返回 None。

        使用 WHERE status='PENDING_APPROVAL' AND consumed_at IS NULL 确保原子性。
        消费前校验：审批码、房间、内容摘要、授权审批人、过期时间、存储 manifest 完整性。
        """
        plan = self.db.query(ExecutionPlan).filter(
            ExecutionPlan.id == plan_id
        ).first()
        if not plan:
            return None
        if plan.status != "PENDING_APPROVAL":
            return None
        if plan.consumed_at is not None:
            return None
        # 验证审批码
        if not _verify_approval_code(short_code, plan.approval_code_hash or ""):
            return None
        # 验证房间一致，防止跨房间重放
        context = _context(approval_context, room_id=room_id, request_event_id=approval_event_id, content_sha256=plan.content_sha256 or "0" * 64, sender_id=approver_matrix_id or "")
        if (plan.channel, plan.channel_account_id, plan.conversation_id) != (context.channel, context.channel_account_id, context.conversation_id):
            return None
        # 验证授权审批人
        authorized_keys = {
            f"{item.get('channel')}:{item.get('channel_account_id')}:{item.get('sender_id')}"
            for item in plan.authorized_identities or [] if isinstance(item, dict)
        }
        # 自审批路径：请求方==消费方时。若计划绑定了临时授权（temporary_grant_id
        # 非空），必须校验授权仍活跃——即使消费方在 authorized_keys 中（受益人也会
        # 被加入 authorized_keys），否则过期授权下受益人会被误放行。未绑定临时授权
        # 时，仅当消费方是配置的原始审批人（在 authorized_keys 中）才放行，
        # 否则拒绝（防止非审批人申请人自批）。
        if _request_actor_key(plan) == context.actor_key:
            if plan.temporary_grant_id:
                from app.services.temporary_approval import TemporaryApprovalService
                grant_service = TemporaryApprovalService(self.db)
                if not grant_service.is_self_approval_allowed(
                    actor_key=context.actor_key,
                    system_name=plan.system_name or "",
                    environment_name=plan.environment or "",
                    action_types=[step.action_type for step in plan.steps],
                    message_context=context,
                ):
                    return None
                if grant_service.get_active_grant(
                    actor_key=context.actor_key,
                    system_name=plan.system_name or "",
                    environment_name=plan.environment or "",
                    message_context=context,
                ) is None:
                    return None
            elif context.actor_key in authorized_keys:
                pass
            else:
                return None
        elif not plan.authorized_identities:
            if not (plan.manifest or {}).get("legacy_matrix_compat"):
                return None
        elif context.actor_key not in authorized_keys:
            return None
        if digest is not None and digest != plan.plan_digest:
            return None
        # 验证是否过期
        if plan.expires_at and _utcnow() > plan.expires_at:
            plan.status = "EXPIRED"
            self.db.commit()
            return None
        # 验证存储 manifest 未发生实质变化
        if not self._verify_stored_manifest(plan):
            plan.failure_reason = "plan manifest digest mismatch; re-approval required"
            self.db.commit()
            return None

        # 原子消费：WHERE 条件确保只有一个调用者成功
        result = self.db.query(ExecutionPlan).filter(
            and_(
                ExecutionPlan.id == plan_id,
                ExecutionPlan.status == "PENDING_APPROVAL",
                ExecutionPlan.consumed_at.is_(None),
            )
        ).update(
            {
                "status": "APPROVED",
                "approved_by": context.actor_key,
                "approval_event_id": context.message_id,
                "approval_message_id": context.message_id,
                "approved_at": _utcnow(),
                "consumed_at": _utcnow(),
            },
            synchronize_session=False,
        )
        self.db.commit()
        if result == 0:
            return None
        self.db.refresh(plan)
        return plan

    def reject(
        self,
        plan_id: str,
        rejecter_matrix_id: str,
        *,
        rejection_context: MessageContext | dict | None = None,
    ) -> ExecutionPlan | None:
        """拒绝审批，终态操作。拒绝后不能再消费。"""
        plan = self.db.query(ExecutionPlan).filter(
            ExecutionPlan.id == plan_id
        ).first()
        if not plan:
            return None
        if plan.status != "PENDING_APPROVAL":
            return None
        plan.status = "REJECTED"
        if rejection_context is not None:
            context = _context(rejection_context)
            plan.rejected_by = context.actor_key
            plan.approval_message_id = context.message_id
        else:
            plan.rejected_by = _legacy_actor_key(rejecter_matrix_id)
        plan.rejected_at = _utcnow()
        self.db.commit()
        self.db.refresh(plan)
        return plan

    def expire_stale(self) -> int:
        """将过期的 PENDING_APPROVAL 计划标记为 EXPIRED，返回处理数量。"""
        now = _utcnow()
        count = self.db.query(ExecutionPlan).filter(
            and_(
                ExecutionPlan.status == "PENDING_APPROVAL",
                ExecutionPlan.expires_at < now,
            )
        ).update({"status": "EXPIRED"}, synchronize_session=False)
        self.db.commit()
        return count

    def list(
        self,
        status: str | None = None,
        action_type: str | None = None,
        limit: int = 50,
    ) -> list[ExecutionPlan]:
        """列出执行计划，可按状态和步骤 action_type 过滤。"""
        query = self.db.query(ExecutionPlan)
        if status:
            query = query.filter(ExecutionPlan.status == status)
        if action_type:
            query = query.join(ExecutionPlan.steps).filter(
                ExecutionPlanStep.action_type == action_type
            ).distinct()
        query = query.order_by(ExecutionPlan.created_at.desc())
        return query.limit(limit).all()
