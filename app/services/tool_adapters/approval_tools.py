"""qclaw Element 审批 MCP 工具。

注册 ops.routing.* 和 ops.approval.* MCP 工具，供 qclaw 通过 MCP 调用。
这些工具是 qclaw 集成的唯一 OPS 接口，不暴露 deploy:execute / package:write
等原始危险 scope。
"""
from __future__ import annotations

from typing import Any, Dict

from app.services.tool_registry import registry
from app.services.tool_token import enforce_room_binding, _normalize_approver_ids
from app.services.qclaw_routing import (
    resolve_message_target,
    issue_ticket,
    verify_ticket,
    compute_routing_revision,
    _extract_routing,
    _extract_approvers,
    RoutingOutcome,
)
from app.services.action_approval import ActionApprovalService
from app.services.execution_plan import ExecutionPlanService
from app.services.package_intake import intake_package, list_staging_packages
from app.config.systems import get_all_systems


def _lookup_approvers(ctx, system_name: str, service_name: str | None = None) -> list[str]:
    """查找授权审批人：优先使用调用 token 的 approver_matrix_ids 白名单。

    若 token 级白名单非空，则它是最强约束（审批人白名单是 per-credential、
    admin 可管理的数据，见 ToolToken.approver_matrix_ids），直接返回。
    否则回退到系统/服务级 message_routing.approvers 配置；两者都未配置时
    返回空列表（此时 consume 对授权人不做限制，向后兼容）。
    """
    token_approvers = _normalize_approver_ids(getattr(ctx, "approver_matrix_ids", None))
    if token_approvers:
        return token_approvers
    if not system_name:
        return []
    systems = get_all_systems()
    sys_cfg = systems.get(system_name)
    if not sys_cfg:
        return []
    sys_routing = _extract_routing(sys_cfg)
    if service_name:
        for svc in sys_cfg.get("services", []) or []:
            if svc.get("name") == service_name:
                svc_routing = _extract_routing(svc)
                return list(_extract_approvers(sys_routing, svc_routing))
    return list(_extract_approvers(sys_routing))


# ──────────────────────────────────────────────────────────────
# ops.routing.* — 消息路由
# ──────────────────────────────────────────────────────────────

@registry.register(
    name="ops.routing.resolve_message_target",
    title="解析 Element 消息路由目标",
    description="将 Element 房间消息文本确定性路由到 OPS 系统/服务，并签发路由票据。qclaw 调用此工具确定消息对应的操作目标。",
    scopes=["ops:read"],
    risk="low",
    category="routing",
    input_schema={
        "type": "object",
        "properties": {
            "message_text": {
                "type": "string",
                "description": "Element 房间消息原文",
            },
            "room_id": {
                "type": "string",
                "description": "Matrix 房间 ID",
            },
            "event_id": {
                "type": "string",
                "description": "Matrix 事件 ID",
            },
            "content_sha256": {
                "type": "string",
                "description": "消息内容的 SHA-256，用于绑定票据",
            },
        },
        "required": ["message_text", "room_id", "event_id", "content_sha256"],
        "additionalProperties": False,
    },
)
def routing_resolve_message_target(args, ctx, db):
    message_text = args["message_text"]
    room_id = args["room_id"]
    event_id = args["event_id"]
    content_sha256 = args["content_sha256"]

    # Enforce Element room binding at the MCP layer: if the caller's token
    # was issued with a non-empty bound_room_ids list, only rooms on the
    # list are allowed. Empty / missing binding = no restriction.
    enforce_room_binding(getattr(ctx, "bound_room_ids", None), room_id)

    # 获取所有系统配置
    systems_dict = get_all_systems()
    systems = list(systems_dict.values())

    # 解析路由
    decision = resolve_message_target(message_text, systems)

    if decision.outcome != RoutingOutcome.RESOLVED:
        return {
            "outcome": decision.outcome.value,
            "system_name": None,
            "service_name": None,
            "matched_by": None,
            "candidates": list(decision.candidates),
            "approvers": list(decision.approvers),
            "ticket": None,
            "ticket_digest": None,
            "routing_config_revision": decision.routing_config_revision,
        }

    # 签发路由票据
    ticket = issue_ticket(
        room_id=room_id,
        event_id=event_id,
        content_sha256=content_sha256,
        system_name=decision.system_name,
        service_name=decision.service_name,
        routing_config_revision=decision.routing_config_revision,
    )

    return {
        "outcome": decision.outcome.value,
        "system_name": decision.system_name,
        "service_name": decision.service_name,
        "matched_by": decision.matched_by,
        "candidates": list(decision.candidates),
        "approvers": list(decision.approvers),
        "ticket": ticket.ticket,
        "ticket_digest": ticket.digest,
        "routing_config_revision": decision.routing_config_revision,
    }


# ──────────────────────────────────────────────────────────────
# ops.approval.prepare_* — 准备审批工单
# ──────────────────────────────────────────────────────────────

def _common_prepare_schema() -> dict:
    """prepare_* 工具的公共输入 schema。"""
    return {
        "type": "object",
        "properties": {
            "room_id": {"type": "string", "description": "Matrix 房间 ID"},
            "request_event_id": {"type": "string", "description": "请求消息的 Matrix 事件 ID"},
            "content_sha256": {"type": "string", "description": "消息内容 SHA-256"},
            "system_name": {"type": "string", "description": "目标系统名"},
            "service_name": {"type": "string", "description": "目标服务名（可选）"},
            "environment": {"type": "string", "description": "环境（test/staging/prod）"},
            "routing_config_revision": {"type": "string", "description": "路由配置 revision"},
            "routing_ticket_digest": {"type": "string", "description": "路由票据摘要"},
            "ai_reason": {"type": "string", "description": "AI 建议此操作的理由"},
        },
        "required": [
            "room_id",
            "request_event_id",
            "content_sha256",
            "system_name",
            "environment",
            "routing_config_revision",
            "routing_ticket_digest",
        ],
        "additionalProperties": False,
    }


# ──────────────────────────────────────────────────────────────
# ops.approval.prepare_service_control — 服务控制审批
# ──────────────────────────────────────────────────────────────

@registry.register(
    name="ops.approval.prepare_service_control",
    title="准备服务控制审批",
    description="为服务控制操作（重启/停止/启动/更新）创建不可变审批工单，返回一次性审批短码。qclaw 将短码展示在 Element 房间，授权用户回复「批准 <短码>」来审批。",
    scopes=["ops:read"],
    risk="low",
    category="approval_prepare",
    input_schema={
        "type": "object",
        "properties": {
            **_common_prepare_schema()["properties"],
            "control_action": {
                "type": "string",
                "enum": ["restart", "stop", "start", "update"],
                "description": "控制操作类型：restart / stop / start / update（update=拉取镜像并重新部署）",
            },
            "targets": {
                "type": "array",
                "items": {"type": "string"},
                "description": "目标服务器列表",
            },
            "action_parameters": {
                "type": "object",
                "description": "额外参数（如 compose_service 指定 Docker Compose 服务名、命令覆盖等）",
            },
        },
        "required": _common_prepare_schema()["required"] + ["control_action", "targets"],
        "additionalProperties": False,
    },
)
def approval_prepare_service_control(args, ctx, db):
    enforce_room_binding(getattr(ctx, "bound_room_ids", None), args.get("room_id"))
    service = ActionApprovalService(db)
    control_action = args["control_action"]
    action_label = {"restart": "重启", "stop": "停止", "start": "启动", "update": "更新"}.get(control_action, control_action)
    approvers = _lookup_approvers(ctx, args["system_name"], args.get("service_name"))
    approval, short_code = service.prepare(
        action_type="SERVICE_CONTROL",
        tool_name="ops.approval.prepare_service_control",
        room_id=args["room_id"],
        request_event_id=args["request_event_id"],
        content_sha256=args["content_sha256"],
        system_name=args["system_name"],
        service_name=args.get("service_name"),
        environment=args["environment"],
        targets=args["targets"],
        action_parameters={
            "control_action": control_action,
            **args.get("action_parameters", {}),
        },
        routing_config_revision=args["routing_config_revision"],
        routing_ticket_digest=args["routing_ticket_digest"],
        risk_level="high",
        ai_reason=args.get("ai_reason", ""),
        authorized_matrix_users=approvers,
    )
    return {
        "approval_id": approval.id,
        "short_code": short_code,
        "action_digest": approval.action_digest,
        "expires_at": approval.expires_at.isoformat() if approval.expires_at else None,
        "status": approval.status,
        "control_action": control_action,
        "action_label": action_label,
        "targets": args["targets"],
        "authorized_approvers": approvers,
    }


# ──────────────────────────────────────────────────────────────
# ops.approval.execute / reject / get — 审批操作
# ──────────────────────────────────────────────────────────────

@registry.register(
    name="ops.approval.execute",
    title="执行已审批的操作",
    description="消费审批短码，验证通过后将审批工单标记为 EXECUTING 并触发实际操作（部署/回滚/DML/包清理）。安全门禁是审批短码本身（一次性、15 分钟过期、绑定房间+事件），而非调用方 token scope，因此只需 ops:read。",
    scopes=["ops:read"],
    risk="low",
    category="approval_execute",
    write=False,
    input_schema={
        "type": "object",
        "properties": {
            "approval_id": {"type": "string", "description": "审批工单 ID"},
            "short_code": {"type": "string", "description": "一次性审批短码"},
            "approver_matrix_id": {"type": "string", "description": "审批人的 Matrix user ID"},
            "room_id": {"type": "string", "description": "Matrix 房间 ID"},
            "approval_event_id": {"type": "string", "description": "审批消息的 Matrix 事件 ID"},
        },
        "required": ["approval_id", "short_code", "approver_matrix_id", "room_id", "approval_event_id"],
        "additionalProperties": False,
    },
)
def approval_execute(args, ctx, db):
    # execute is called from the same room that prepared the approval, so the
    # room_id is the natural anchor for the binding check.
    enforce_room_binding(getattr(ctx, "bound_room_ids", None), args.get("room_id"))
    service = ActionApprovalService(db)
    approval = service.consume(
        approval_id=args["approval_id"],
        short_code=args["short_code"],
        approver_matrix_id=args["approver_matrix_id"],
        room_id=args["room_id"],
        approval_event_id=args["approval_event_id"],
    )
    if not approval:
        return {
            "ok": False,
            "error": "审批码无效、已过期、已被消费或房间不匹配",
            "approval_id": args["approval_id"],
        }

    # 同步执行已审批操作。单机部署不需要异步 job 队列，consume 成功后
    # 立即在当前事务中执行，执行结果直接反映在 approval.status 上。
    from app.services.approval_executor import ApprovalExecutor
    executor = ApprovalExecutor(db)
    approval = executor.execute(approval.id)

    if not approval:
        return {
            "ok": False,
            "error": "执行器未找到审批工单",
            "approval_id": args["approval_id"],
        }

    return {
        "ok": approval.status == "SUCCEEDED",
        "approval_id": approval.id,
        "action_type": approval.action_type,
        "status": approval.status,
        "approved_by": approval.approved_by,
        "approved_at": approval.approved_at.isoformat() if approval.approved_at else None,
        "executed_at": approval.executed_at.isoformat() if approval.executed_at else None,
        "execution_result": approval.execution_result,
        "failure_reason": approval.failure_reason,
        "message": (
            f"审批已通过，操作 {approval.action_type} 执行{'成功' if approval.status == 'SUCCEEDED' else '失败'}"
            if approval.status in ("SUCCEEDED", "FAILED")
            else f"审批已通过，操作 {approval.action_type} 状态: {approval.status}"
        ),
    }


# ──────────────────────────────────────────────────────────────
# ops.approval.list — 审批列表
# ──────────────────────────────────────────────────────────────

@registry.register(
    name="ops.approval.list",
    title="查询审批列表",
    description="查询审批工单列表，可按状态、操作类型过滤。",
    scopes=["ops:read"],
    risk="low",
    category="approval_read",
    input_schema={
        "type": "object",
        "properties": {
            "status": {"type": "string", "description": "状态过滤"},
            "action_type": {"type": "string", "description": "操作类型过滤"},
            "limit": {"type": "integer", "description": "返回数量，默认 50"},
        },
        "additionalProperties": False,
    },
)
def approval_list(args, ctx, db):
    from sqlalchemy import and_
    from app.db.models import AiActionApproval

    query = db.query(AiActionApproval)
    if args.get("status"):
        query = query.filter(AiActionApproval.status == args["status"])
    if args.get("action_type"):
        query = query.filter(AiActionApproval.action_type == args["action_type"])
    query = query.order_by(AiActionApproval.created_at.desc())
    limit = int(args.get("limit", 50))
    items = query.limit(limit).all()

    return {
        "ok": True,
        "total": len(items),
        "items": [
            {
                "approval_id": a.id,
                "action_type": a.action_type,
                "status": a.status,
                "system_name": (a.request_payload or {}).get("system_name"),
                "environment": (a.request_payload or {}).get("environment"),
                "risk_level": a.risk_level,
                "created_at": a.created_at.isoformat() if a.created_at else None,
                "expires_at": a.expires_at.isoformat() if a.expires_at else None,
                "approved_by": a.approved_by,
            }
            for a in items
        ],
    }


# ──────────────────────────────────────────────────────────────
# ops.approval.expire_stale — 过期清理
# ──────────────────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────
# ops.approval.prepare_plan / execute_plan — 消息级执行计划
# ──────────────────────────────────────────────────────────────
#
# 一条 Element 消息对应一个 ExecutionPlan：授权人批准一次后，计划内
# 声明的多个步骤按顺序自动执行，不再逐步骤审批。
# 注意：这些工具绝不调用旧 prepare_* 审批工具（避免审批递归）。


def _plan_steps_schema() -> dict:
    """执行计划步骤的 JSON schema（用于 prepare_plan 输入）。"""
    return {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "step_key": {"type": "string", "description": "计划内唯一步骤键"},
                "action_type": {"type": "string", "description": "步骤类型（如 SERVICE_CONTROL / HEALTH_CHECK）"},
                "parameters": {"type": "object", "description": "冻结的步骤参数"},
                "dependencies": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "依赖的前置步骤 step_key 列表",
                },
            },
            "required": ["step_key", "action_type"],
            "additionalProperties": False,
        },
    }


@registry.register(
    name="ops.approval.prepare_plan",
    title="准备消息级执行计划审批",
    description="为一条 Element 消息的完整执行流程创建不可变执行计划，返回一次性审批短码。授权人批准一次后，计划内所有步骤按顺序自动执行。相同计划内容（plan_digest）的待审批计划幂等复用，不生成新短码。",
    scopes=["ops:read"],
    risk="low",
    category="approval_prepare",
    input_schema={
        "type": "object",
        "properties": {
            "room_id": {"type": "string", "description": "Matrix 房间 ID"},
            "request_event_id": {"type": "string", "description": "请求消息的 Matrix 事件 ID"},
            "content_sha256": {"type": "string", "description": "消息内容 SHA-256"},
            "system_name": {"type": "string", "description": "目标系统名"},
            "service_name": {"type": "string", "description": "目标服务名（可选）"},
            "environment": {"type": "string", "description": "环境（test/staging/prod）"},
            "targets": {
                "type": "array",
                "items": {"type": "string"},
                "description": "目标服务器列表",
            },
            "steps": _plan_steps_schema(),
            "policy": {
                "type": "object",
                "description": "执行策略（如 continue_on_error / max_retries）",
            },
            "routing_config_revision": {"type": "string", "description": "路由配置 revision"},
            "routing_ticket_digest": {"type": "string", "description": "路由票据摘要"},
            "ai_reason": {"type": "string", "description": "AI 建议此计划执行的理由"},
            "risk_level": {"type": "string", "description": "风险等级（low/medium/high）"},
        },
        "required": [
            "room_id",
            "request_event_id",
            "content_sha256",
            "system_name",
            "environment",
            "steps",
            "routing_config_revision",
            "routing_ticket_digest",
        ],
        "additionalProperties": False,
    },
)
def approval_prepare_plan(args, ctx, db):
    """创建消息级执行计划并返回一次性审批短码。"""
    enforce_room_binding(getattr(ctx, "bound_room_ids", None), args.get("room_id"))
    service = ExecutionPlanService(db)
    approvers = _lookup_approvers(ctx, args["system_name"], args.get("service_name"))
    steps = args["steps"]

    plan, short_code = service.prepare(
        room_id=args["room_id"],
        request_event_id=args["request_event_id"],
        content_sha256=args["content_sha256"],
        system_name=args["system_name"],
        service_name=args.get("service_name"),
        environment=args["environment"],
        targets=args.get("targets", []),
        steps=steps,
        policy=args.get("policy", {}),
        routing_config_revision=args["routing_config_revision"],
        routing_ticket_digest=args["routing_ticket_digest"],
        risk_level=args.get("risk_level", "high"),
        ai_reason=args.get("ai_reason", ""),
        authorized_matrix_users=approvers,
    )

    return {
        "plan_id": plan.id,
        "short_code": short_code,
        "plan_digest": plan.plan_digest,
        "status": plan.status,
        "expires_at": plan.expires_at.isoformat() if plan.expires_at else None,
        "system_name": plan.system_name,
        "service_name": plan.service_name,
        "environment": plan.environment,
        "targets": plan.targets,
        "step_count": len(plan.steps),
        "steps": [
            {
                "step_key": s.step_key,
                "step_order": s.step_order,
                "action_type": s.action_type,
                "status": s.status,
            }
            for s in plan.steps
        ],
        "authorized_approvers": approvers,
    }


@registry.register(
    name="ops.approval.execute_plan",
    title="执行已审批的执行计划",
    description="消费一次性审批短码，验证通过后将执行计划置为可执行并按冻结步骤顺序执行。安全门禁是审批短码本身（一次性、15 分钟过期、绑定房间+事件），而非调用方 token scope，因此只需 ops:read。",
    scopes=["ops:read"],
    risk="low",
    category="approval_execute",
    write=False,
    input_schema={
        "type": "object",
        "properties": {
            "plan_id": {"type": "string", "description": "执行计划 ID"},
            "short_code": {"type": "string", "description": "一次性审批短码"},
            "approver_matrix_id": {"type": "string", "description": "审批人的 Matrix user ID"},
            "room_id": {"type": "string", "description": "Matrix 房间 ID"},
            "approval_event_id": {"type": "string", "description": "审批消息的 Matrix 事件 ID"},
        },
        "required": ["plan_id", "short_code", "approver_matrix_id", "room_id", "approval_event_id"],
        "additionalProperties": False,
    },
)
def approval_execute_plan(args, ctx, db):
    """消费短码并执行已审批的计划。"""
    enforce_room_binding(getattr(ctx, "bound_room_ids", None), args.get("room_id"))
    service = ExecutionPlanService(db)
    plan = service.consume(
        plan_id=args["plan_id"],
        short_code=args["short_code"],
        approver_matrix_id=args["approver_matrix_id"],
        room_id=args["room_id"],
        approval_event_id=args["approval_event_id"],
    )
    if not plan:
        return {
            "ok": False,
            "error": "审批码无效、已过期、已被消费、房间不匹配或计划内容已变化",
            "plan_id": args["plan_id"],
        }

    # 同步执行计划内全部步骤
    from app.services.plan_executor import PlanExecutor
    executor = PlanExecutor(db)
    plan = executor.execute(plan.id)

    if not plan:
        return {
            "ok": False,
            "error": "执行器未找到执行计划",
            "plan_id": args["plan_id"],
        }

    return {
        "ok": plan.status == "SUCCEEDED",
        "plan_id": plan.id,
        "plan_digest": plan.plan_digest,
        "status": plan.status,
        "approved_by": plan.approved_by,
        "approved_at": plan.approved_at.isoformat() if plan.approved_at else None,
        "failure_reason": plan.failure_reason,
        "steps": [
            {
                "step_key": s.step_key,
                "action_type": s.action_type,
                "status": s.status,
                "result": s.result,
                "error_message": s.error_message,
                "attempt_count": s.attempt_count,
            }
            for s in plan.steps
        ],
        "message": (
            f"执行计划已完成: {plan.status}"
            if plan.status in ("SUCCEEDED", "FAILED", "PARTIAL_FAILED")
            else f"执行计划状态: {plan.status}"
        ),
    }


