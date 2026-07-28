"""qclaw Element 审批 MCP 工具。

注册 ops.routing.* 和 ops.approval.* MCP 工具，供 qclaw 通过 MCP 调用。
这些工具是 qclaw 集成的唯一 OPS 接口，不暴露 deploy:execute / package:write
等原始危险 scope。
"""
from __future__ import annotations

from typing import Any, Dict

from app.services.tool_registry import registry
from app.services.tool_token import enforce_room_binding
from app.services.qclaw_routing import (
    resolve_message_target,
    issue_ticket,
    verify_ticket,
    compute_routing_revision,
    RoutingOutcome,
)
from app.services.action_approval import ActionApprovalService
from app.services.package_intake import intake_package, list_staging_packages
from app.config.systems import get_all_systems


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


@registry.register(
    name="ops.approval.prepare_release",
    title="准备发布审批",
    description="为发布操作创建不可变审批工单，返回一次性审批短码。qclaw 将短码展示在 Element 房间，授权用户回复「批准 <短码>」来审批。",
    scopes=["ops:read"],
    risk="low",
    category="approval_prepare",
    input_schema={
        "type": "object",
        "properties": {
            **_common_prepare_schema()["properties"],
            "targets": {
                "type": "array",
                "items": {"type": "string"},
                "description": "目标服务器列表",
            },
            "package_name": {"type": "string", "description": "部署包文件名"},
            "package_sha256": {"type": "string", "description": "部署包 SHA-256"},
            "package_size_bytes": {"type": "integer", "description": "部署包大小（字节）"},
            "action_parameters": {
                "type": "object",
                "description": "发布参数（pipeline、variables 等）",
            },
        },
        "required": _common_prepare_schema()["required"] + ["targets"],
        "additionalProperties": False,
    },
)
def approval_prepare_release(args, ctx, db):
    # Enforce Element room binding (mirror routing tool's check).
    enforce_room_binding(getattr(ctx, "bound_room_ids", None), args.get("room_id"))
    service = ActionApprovalService(db)
    approval, short_code = service.prepare(
        action_type="RELEASE",
        tool_name="ops.approval.prepare_release",
        room_id=args["room_id"],
        request_event_id=args["request_event_id"],
        content_sha256=args["content_sha256"],
        system_name=args["system_name"],
        service_name=args.get("service_name"),
        environment=args["environment"],
        targets=args["targets"],
        action_parameters=args.get("action_parameters", {}),
        routing_config_revision=args["routing_config_revision"],
        routing_ticket_digest=args["routing_ticket_digest"],
        risk_level="high",
        ai_reason=args.get("ai_reason", ""),
        package_name=args.get("package_name"),
        package_sha256=args.get("package_sha256"),
        package_size_bytes=args.get("package_size_bytes"),
    )
    return {
        "approval_id": approval.id,
        "short_code": short_code,
        "action_digest": approval.action_digest,
        "expires_at": approval.expires_at.isoformat() if approval.expires_at else None,
        "status": approval.status,
    }


@registry.register(
    name="ops.approval.prepare_rollback",
    title="准备回滚审批",
    description="为回滚操作创建不可变审批工单，返回一次性审批短码。",
    scopes=["ops:read"],
    risk="low",
    category="approval_prepare",
    input_schema={
        "type": "object",
        "properties": {
            **_common_prepare_schema()["properties"],
            "deployment_id": {"type": "string", "description": "要回滚的部署 ID"},
            "targets": {
                "type": "array",
                "items": {"type": "string"},
                "description": "目标服务器列表",
            },
            "action_parameters": {"type": "object"},
        },
        "required": _common_prepare_schema()["required"] + ["deployment_id", "targets"],
        "additionalProperties": False,
    },
)
def approval_prepare_rollback(args, ctx, db):
    enforce_room_binding(getattr(ctx, "bound_room_ids", None), args.get("room_id"))
    service = ActionApprovalService(db)
    approval, short_code = service.prepare(
        action_type="ROLLBACK",
        tool_name="ops.approval.prepare_rollback",
        room_id=args["room_id"],
        request_event_id=args["request_event_id"],
        content_sha256=args["content_sha256"],
        system_name=args["system_name"],
        service_name=args.get("service_name"),
        environment=args["environment"],
        targets=args["targets"],
        action_parameters={
            "deployment_id": args["deployment_id"],
            **args.get("action_parameters", {}),
        },
        routing_config_revision=args["routing_config_revision"],
        routing_ticket_digest=args["routing_ticket_digest"],
        risk_level="high",
        ai_reason=args.get("ai_reason", ""),
    )
    return {
        "approval_id": approval.id,
        "short_code": short_code,
        "action_digest": approval.action_digest,
        "expires_at": approval.expires_at.isoformat() if approval.expires_at else None,
        "status": approval.status,
    }


@registry.register(
    name="ops.approval.prepare_dml",
    title="准备 DML 审批",
    description="为数据库 DML 操作创建不可变审批工单，返回一次性审批短码。",
    scopes=["ops:read"],
    risk="low",
    category="approval_prepare",
    input_schema={
        "type": "object",
        "properties": {
            **_common_prepare_schema()["properties"],
            "database_connection_id": {"type": "string", "description": "数据库连接 ID"},
            "sql_text": {"type": "string", "description": "要执行的 SQL 语句"},
            "max_affected_rows": {"type": "integer", "description": "最大影响行数"},
            "action_parameters": {"type": "object"},
        },
        "required": _common_prepare_schema()["required"]
        + ["database_connection_id", "sql_text"],
        "additionalProperties": False,
    },
)
def approval_prepare_dml(args, ctx, db):
    enforce_room_binding(getattr(ctx, "bound_room_ids", None), args.get("room_id"))
    service = ActionApprovalService(db)
    approval, short_code = service.prepare(
        action_type="DML",
        tool_name="ops.approval.prepare_dml",
        room_id=args["room_id"],
        request_event_id=args["request_event_id"],
        content_sha256=args["content_sha256"],
        system_name=args["system_name"],
        service_name=args.get("service_name"),
        environment=args["environment"],
        targets=[args["database_connection_id"]],
        action_parameters={
            "database_connection_id": args["database_connection_id"],
            "sql_text": args["sql_text"],
            "max_affected_rows": args.get("max_affected_rows", 100),
            **args.get("action_parameters", {}),
        },
        routing_config_revision=args["routing_config_revision"],
        routing_ticket_digest=args["routing_ticket_digest"],
        risk_level="critical",
        ai_reason=args.get("ai_reason", ""),
    )
    return {
        "approval_id": approval.id,
        "short_code": short_code,
        "action_digest": approval.action_digest,
        "expires_at": approval.expires_at.isoformat() if approval.expires_at else None,
        "status": approval.status,
    }


@registry.register(
    name="ops.approval.prepare_package_cleanup",
    title="准备包清理审批",
    description="为包清理操作创建不可变审批工单，返回一次性审批短码。",
    scopes=["ops:read"],
    risk="low",
    category="approval_prepare",
    input_schema={
        "type": "object",
        "properties": {
            **_common_prepare_schema()["properties"],
            "package_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "要清理的包 ID 列表",
            },
            "action_parameters": {"type": "object"},
        },
        "required": _common_prepare_schema()["required"] + ["package_ids"],
        "additionalProperties": False,
    },
)
def approval_prepare_package_cleanup(args, ctx, db):
    enforce_room_binding(getattr(ctx, "bound_room_ids", None), args.get("room_id"))
    service = ActionApprovalService(db)
    approval, short_code = service.prepare(
        action_type="PACKAGE_CLEANUP",
        tool_name="ops.approval.prepare_package_cleanup",
        room_id=args["room_id"],
        request_event_id=args["request_event_id"],
        content_sha256=args["content_sha256"],
        system_name=args["system_name"],
        service_name=args.get("service_name"),
        environment=args["environment"],
        targets=args["package_ids"],
        action_parameters={
            "package_ids": args["package_ids"],
            **args.get("action_parameters", {}),
        },
        routing_config_revision=args["routing_config_revision"],
        routing_ticket_digest=args["routing_ticket_digest"],
        risk_level="high",
        ai_reason=args.get("ai_reason", ""),
    )
    return {
        "approval_id": approval.id,
        "short_code": short_code,
        "action_digest": approval.action_digest,
        "expires_at": approval.expires_at.isoformat() if approval.expires_at else None,
        "status": approval.status,
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


@registry.register(
    name="ops.approval.reject",
    title="拒绝审批",
    description="拒绝审批工单，终态操作。拒绝后不可再消费。拒绝是状态变更而非生产写操作，只需 ops:read。",
    scopes=["ops:read"],
    risk="low",
    category="approval_reject",
    write=False,
    input_schema={
        "type": "object",
        "properties": {
            "approval_id": {"type": "string", "description": "审批工单 ID"},
            "rejecter_matrix_id": {"type": "string", "description": "拒绝人的 Matrix user ID"},
            # Optional: if the caller's token has room binding configured,
            # passing room_id lets the MCP layer enforce that the reject
            # comes from an allowed room. When the token is unbound, this
            # field is ignored.
            "room_id": {"type": "string", "description": "调用方所在 Element 房间 ID（用于 Token 房间绑定校验，可选）"},
        },
        "required": ["approval_id", "rejecter_matrix_id"],
        "additionalProperties": False,
    },
)
def approval_reject(args, ctx, db):
    # Enforce Element room binding when room_id is provided. Tokens without
    # binding are not restricted (backward compat). Tokens with binding but
    # missing room_id are rejected by enforce_room_binding itself.
    enforce_room_binding(getattr(ctx, "bound_room_ids", None), args.get("room_id"))
    service = ActionApprovalService(db)
    approval = service.reject(
        approval_id=args["approval_id"],
        rejecter_matrix_id=args["rejecter_matrix_id"],
    )
    if not approval:
        return {
            "ok": False,
            "error": "审批工单不存在或已不在 PENDING_APPROVAL 状态",
            "approval_id": args["approval_id"],
        }
    return {
        "ok": True,
        "approval_id": approval.id,
        "status": approval.status,
        "rejected_by": approval.rejected_by,
        "rejected_at": approval.rejected_at.isoformat() if approval.rejected_at else None,
    }


@registry.register(
    name="ops.approval.get",
    title="查询审批状态",
    description="查询审批工单的当前状态、执行结果等信息。",
    scopes=["ops:read"],
    risk="low",
    category="approval_read",
    input_schema={
        "type": "object",
        "properties": {
            "approval_id": {"type": "string", "description": "审批工单 ID"},
        },
        "required": ["approval_id"],
        "additionalProperties": False,
    },
)
def approval_get(args, ctx, db):
    service = ActionApprovalService(db)
    approval = service.get(args["approval_id"])
    if not approval:
        return {"ok": False, "error": "审批工单不存在"}
    return {
        "ok": True,
        "approval_id": approval.id,
        "action_type": approval.action_type,
        "status": approval.status,
        "system_name": (approval.request_payload or {}).get("system_name"),
        "service_name": (approval.request_payload or {}).get("service_name"),
        "environment": (approval.request_payload or {}).get("environment"),
        "targets": (approval.request_payload or {}).get("targets", []),
        "risk_level": approval.risk_level,
        "ai_reason": approval.ai_reason,
        "requested_by": approval.requested_by,
        "approved_by": approval.approved_by,
        "rejected_by": approval.rejected_by,
        "created_at": approval.created_at.isoformat() if approval.created_at else None,
        "approved_at": approval.approved_at.isoformat() if approval.approved_at else None,
        "consumed_at": approval.consumed_at.isoformat() if approval.consumed_at else None,
        "expires_at": approval.expires_at.isoformat() if approval.expires_at else None,
        "execution_job_id": approval.execution_job_id,
        "execution_result": approval.execution_result,
        "failure_reason": approval.failure_reason,
        "package_name": approval.package_name,
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

@registry.register(
    name="ops.approval.expire_stale",
    title="清理过期审批",
    description="将过期的 PENDING_APPROVAL 工单批量标记为 EXPIRED。状态变更而非生产写操作，只需 ops:read。",
    scopes=["ops:read"],
    risk="low",
    category="approval_maintenance",
    write=False,
    input_schema={
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    },
)
def approval_expire_stale(args, ctx, db):
    service = ActionApprovalService(db)
    count = service.expire_stale()
    return {"ok": True, "expired_count": count}
