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
from app.services.approval_phrase import (
    PROD_CONFIRM_CLAUSE,
    build_approval_phrase,
    fingerprint_of,
    split_prod_confirmation,
)
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


def _verify_approval_code_tolerant(short_code: str, plan: ExecutionPlan) -> bool:
    """批准短语校验（对中文编码损坏容错）。

    首选整句全文 PBKDF2 校验（与 prepare 时 Hash 的含中文原文对称）。
    若因 agent/协议层把非 ASCII 字符替换成 ``?`` 导致全文失配，则退化为
    校验短码末尾的 8 位指纹段是否与 ``plan_digest`` 派生值一致。

    指纹由 plan_digest 经 sha256 派生（8 位十六进制，约 32bit 熵），且调用方
    必须同时持有对应 ``plan_id``。encode 损坏只影响描述性前缀，数字指纹段
    （十六进制大写）不会失真，因此退化校验仍能阻断跨计划复用与伪造。
    """
    if _verify_approval_code(short_code, plan.approval_code_hash or ""):
        return True
    fingerprint = fingerprint_of(plan.plan_digest or "")
    tokens = (short_code or "").strip().split()
    if not tokens:
        return False
    # compare_digest 对含非 ASCII 的 str 会抛 TypeError（审批消息带中文说明、
    # 或末段是生产确认从句时都会命中）。统一按 UTF-8 字节比较：保留常量时间
    # 语义，同时不再把"审批人写了中文"变成异常。
    return hmac.compare_digest(
        tokens[-1].upper().encode("utf-8"), fingerprint.encode("utf-8")
    )


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
    action_type = str(action_type or "").strip()
    params = dict(parameters or {})
    if action_type == "FILE_UPLOAD":
        action_parameters = dict(params.get("action_parameters") or {})
        return {
            "package_name": action_parameters.get("package_name") or "",
            "remote_path": action_parameters.get("remote_path") or "",
            "overwrite": bool(action_parameters.get("overwrite", False)),
            "expected_sha256": action_parameters.get("expected_sha256") or "",
            "expected_size_bytes": action_parameters.get("expected_size_bytes"),
            "targets": list(params.get("targets") or default_targets or []),
        }
    if action_type == "MATRIX_PULL":
        minutes = params.get("minutes")
        return {
            "room_id": str(params.get("room_id") or ""),
            "sender": str(params.get("sender") or ""),
            "filename_hint": str(params.get("filename") or ""),
            "minutes": int(minutes) if minutes else None,
            "note": "将从 Matrix 房间拉取该发送者最新媒体附件入库；package_name 由执行时回填到后续 RELEASE 步骤",
        }
    return {}


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


class EnvironmentTargetViolation(ValueError):
    """targets 越界（含非本环境服务器）——环境隔离闸的专用异常。"""


def _environment_server_ids(db, system_name: str, environment_name: str) -> set[str]:
    """环境权威服务器集合（SystemEnvironment.servers 实时读取）。"""
    from app.db.models import SystemEnvironment

    env = db.query(SystemEnvironment).filter(
        SystemEnvironment.system_name == (system_name or "").strip(),
        SystemEnvironment.name == (environment_name or "").strip(),
    ).first()
    if env is None:
        return set()
    ids: set[str] = set()
    for s in env.servers or []:
        if isinstance(s, dict):
            sid = str(s.get("id") or s.get("name") or "").strip()
        elif isinstance(s, str):
            sid = s.strip()
        else:
            sid = ""
        if sid:
            ids.add(sid)
    return ids


def validate_targets_in_environment(
    db, system_name: str, environment: str, targets: list[str], *, phase: str = "prepare",
) -> None:
    """环境隔离硬闸（2026-09-04 设计确认）。

    targets 必须 ⊆ SystemEnvironment.servers（环境权威清单）。
    - 环境未配置或清单为空 → fail-closed（任何 targets 都拒绝——
      防止"环境标签存在但没绑服务器"的配置真空期放行生产机）
    - 越界即抛 EnvironmentTargetViolation，错误信息明确指出越界机器

    phase 仅供错误信息区分（prepare 拒绝 / execute 复核）。
    """
    env_name = (environment or "").strip()
    if not env_name:
        # 无环境标签的计划（历史兼容）：targets 校验跳过？
        # 不跳过——无环境=无授权面，fail-closed。
        raise EnvironmentTargetViolation(
            "计划缺少 environment 标签：环境隔离要求显式声明环境"
            f"（{'创建' if phase == 'prepare' else '执行'}被拒绝）"
        )
    allowed = _environment_server_ids(db, system_name, env_name)
    if not allowed:
        raise EnvironmentTargetViolation(
            f"环境 {system_name}@{env_name} 未绑定服务器清单（fail-closed）："
            "请先在系统环境配置中录入该环境的服务器，再"
            f"{'创建' if phase == 'prepare' else '执行'}计划"
        )
    tgt = [str(t).strip() for t in (targets or []) if str(t).strip()]
    stray = [t for t in tgt if t not in allowed]
    if stray:
        raise EnvironmentTargetViolation(
            f"targets 越界：{sorted(stray)} 不属于环境 {system_name}@{env_name} "
            f"（合法服务器 {sorted(allowed)}）——测试计划不得引用其他环境机器"
            f"（{'创建' if phase == 'prepare' else '执行'}被拒绝）"
        )


class ExecutionPlanService:
    """执行计划生命周期管理。"""

    # 最近一次 consume() 失败的具体原因（供调用方生成可行动回复；
    # 成功时为空）。单实例单线程语义：每次 consume 前重置。
    last_consume_error: str = ""

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

        # 环境隔离硬闸（闸2）：targets 必须 ⊆ 环境权威清单。
        # 幂等复用前也校验——环境绑定在计划创建后可能收紧，老 PENDING
        # 计划若已越界，复用时同样拒绝（不静默放行历史计划）。
        validate_targets_in_environment(
            self.db, system_name, environment, targets or [], phase="prepare",
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

        # 描述性确认短语：批准<动作摘要> <system>@<env> <指纹8>，
        # 指纹绑定 plan_digest 防跨计划复用（校验机制不变：加盐哈希+一次性+15min）
        short_code = build_approval_phrase(
            action_types=[s.get("action_type") for s in manifest["steps"]],
            system_name=system_name,
            environment=environment,
            digest=digest,
        )
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
        prod_confirm_text: str | None = None,
    ) -> ExecutionPlan | None:
        """原子消费审批码。成功返回 plan（状态 APPROVED），失败返回 None。

        失败时把具体原因写入 self.last_consume_error（供调用方生成可行动的
        回复，而不是笼统的'无效'）：区分 短语不匹配/计划已终态/已消费/房间
        不匹配/非授权审批人/过期/manifest 变化/环境隔离拒绝/并发竞争。
        使用 WHERE status='PENDING_APPROVAL' AND consumed_at IS NULL 确保原子性。
        消费前校验：审批码、房间、内容摘要、授权审批人、过期时间、存储 manifest 完整性。
        """
        self.last_consume_error = ""
        plan = self.db.query(ExecutionPlan).filter(
            ExecutionPlan.id == plan_id
        ).first()
        if not plan:
            self.last_consume_error = f"计划 {plan_id[:8]}... 不存在"
            return None
        if plan.status != "PENDING_APPROVAL":
            self.last_consume_error = (
                f"短语对应的计划 {plan.id[:8]}... 已处于终态 {plan.status}（不再接受执行）"
            )
            return None
        if plan.consumed_at is not None:
            self.last_consume_error = f"计划 {plan.id[:8]}... 的确认短语已被消费过"
            return None
        # 生产额外确认从句的**剥离**先做（纯函数）：审批人可能把
        # 「批准 <短语> 我确认生产操作」整行照抄，剥离后短语才能对上。
        # 是否**要求**从句放到有效期校验之后判定（见下方第 4 层）。
        stripped_code, prod_clause_ok = split_prod_confirmation(short_code, prod_confirm_text)
        # 验证审批码（含中文编码损坏容错：全文失配时退化为指纹段校验）
        if not _verify_approval_code_tolerant(stripped_code, plan):
            self.last_consume_error = "确认短语与计划不匹配"
            return None
        # 验证房间一致，防止跨房间重放
        context = _context(approval_context, room_id=room_id, request_event_id=approval_event_id, content_sha256=plan.content_sha256 or "0" * 64, sender_id=approver_matrix_id or "")
        if (plan.channel, plan.channel_account_id, plan.conversation_id) != (context.channel, context.channel_account_id, context.conversation_id):
            self.last_consume_error = "批准消息所在房间与计划绑定的房间不一致（防跨房间重放）"
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
                    self.last_consume_error = "临时自审批授权已失效或不含此动作（需原始审批人重新授权）"
                    return None
                if grant_service.get_active_grant(
                    actor_key=context.actor_key,
                    system_name=plan.system_name or "",
                    environment_name=plan.environment or "",
                    message_context=context,
                ) is None:
                    self.last_consume_error = "临时自审批授权已过期或被撤销"
                    return None
            elif context.actor_key in authorized_keys:
                pass
            else:
                self.last_consume_error = "批准人不在该计划的授权审批人列表（申请人不能自批，除非有临时授权）"
                return None
        elif not plan.authorized_identities:
            if not (plan.manifest or {}).get("legacy_matrix_compat"):
                self.last_consume_error = "计划未配置授权审批人且无 legacy 兼容标记"
                return None
        elif context.actor_key not in authorized_keys:
            self.last_consume_error = "批准人不在该计划的授权审批人列表"
            return None
        if digest is not None and digest != plan.plan_digest:
            self.last_consume_error = "计划内容指纹变化（manifest 漂移），需重新审批"
            return None
        # 验证是否过期
        if plan.expires_at and _utcnow() > plan.expires_at:
            plan.status = "EXPIRED"
            self.db.commit()
            self.last_consume_error = f"确认短语已过期（有效期至 {plan.expires_at:%Y-%m-%d %H:%M}）"
            return None
        # 第 4 层（strict_prod_confirmation）：生产环境除一次性短语外还必须有
        # 额外确认从句。位置刻意放在有效期校验之后——先如实报告"已过期/已消费"，
        # 再把"缺从句"作为执行前的最后一道拦阻。
        from app.services.tool_policy import strict_prod_confirmation_required

        if not prod_clause_ok and strict_prod_confirmation_required(self.db, plan.environment or ""):
            self.last_consume_error = (
                f"生产环境（{plan.environment}）需要额外确认从句「{PROD_CONFIRM_CLAUSE}」："
                "批准消息必须同时包含一次性短语与该从句，缺一不可"
            )
            return None
        # 验证存储 manifest 未发生实质变化
        if not self._verify_stored_manifest(plan):
            plan.failure_reason = "plan manifest digest mismatch; re-approval required"
            self.db.commit()
            self.last_consume_error = "计划内容与审批时不一致（完整性校验失败），需重新发起"
            return None

        # 环境隔离硬闸（闸3）：执行前二次复核 targets ⊆ 环境权威清单。
        # 审批窗口期内环境绑定可能收紧（新增服务器/调整归属），此时
        # 已批准的计划若引用了越界机器，执行时拒绝——审批人批准的是
        # 当时合法的目标面，配置漂移后不应静默放行。
        try:
            validate_targets_in_environment(
                self.db, plan.system_name or "", plan.environment or "",
                plan.targets or [], phase="execute",
            )
        except EnvironmentTargetViolation as exc:
            plan.status = "REJECTED"
            plan.failure_reason = f"environment isolation: {exc}"
            self.db.commit()
            self.last_consume_error = f"环境隔离拒绝：{exc}"
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
            self.last_consume_error = "确认短语已被并发消费（另一会话先一步执行）"
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
