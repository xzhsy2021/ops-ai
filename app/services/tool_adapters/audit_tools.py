from __future__ import annotations

from app.db.models import AuditRecord, ToolCallLog
from app.services.tool_registry import registry


@registry.register(
    name="ops.list_audit_logs",
    description="查询 OPS 审计日志。",
    scopes=["ops:read", "audit:read"],
    input_schema={
        "type": "object",
        "properties": {"limit": {"type": "integer"}, "action": {"type": "string"}},
        "additionalProperties": False,
    },
)
def list_audit_logs(args, ctx, db):
    q = db.query(AuditRecord)
    if args.get("action"):
        q = q.filter(AuditRecord.action.like(f"%{args.get('action')}%"))
    rows = q.order_by(AuditRecord.id.desc()).limit(args.get("limit") or 50).all()
    return [{"id": r.id, "action": r.action, "target_type": r.target_type, "target_name": r.target_name, "details": r.details, "created_at": r.created_at} for r in rows]


@registry.register(
    name="ops.list_tool_calls",
    description="查询 Capability Server 工具调用记录。",
    scopes=["ops:read", "audit:read"],
    input_schema={
        "type": "object",
        "properties": {"limit": {"type": "integer"}, "tool": {"type": "string"}, "status": {"type": "string"}},
        "additionalProperties": False,
    },
)
def list_tool_calls(args, ctx, db):
    q = db.query(ToolCallLog)
    if args.get("tool"):
        q = q.filter(ToolCallLog.tool_name == args.get("tool"))
    if args.get("status"):
        q = q.filter(ToolCallLog.status == args.get("status"))
    rows = q.order_by(ToolCallLog.created_at.desc()).limit(args.get("limit") or 50).all()
    return [
        {
            "id": r.id,
            "tool_name": r.tool_name,
            "client_name": r.client_name,
            "token_owner": r.token_owner,
            "username": r.username,
            "status": r.status,
            "risk_level": r.risk_level,
            "blocked_reason": r.blocked_reason,
            "related_plan_id": r.related_plan_id,
            "related_deployment_id": r.related_deployment_id,
            "duration_ms": r.duration_ms,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


@registry.register(
    name="ops.list_operation_chains",
    title="查询操作链路",
    description="查询最近 OPS / MCP / AI 操作链路摘要，用于审计和回放。只读，不执行任何操作。",
    scopes=["ops:read", "audit:read"],
    risk="low",
    category="audit_read",
    write=False,
    input_schema={
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "minimum": 1, "maximum": 200},
            "kind": {"type": "string", "description": "tool_call/job/plan/deployment"},
            "status": {"type": "string"},
            "risk": {"type": "string"},
        },
        "additionalProperties": False,
    },
)
def list_operation_chains_tool(args, ctx, db):
    from app.services.audit_chain import list_operation_chains

    return list_operation_chains(
        db,
        limit=args.get("limit") or 50,
        kind=args.get("kind") or "",
        status=args.get("status") or "",
        risk=args.get("risk") or "",
    )


@registry.register(
    name="ops.get_operation_chain",
    title="查看操作链路回放",
    description="按 tool:/job:/plan:/deployment:/audit: 链路 ID 重构一次 OPS/MCP/AI 操作的时间线、证据和风险边界。只读。",
    scopes=["ops:read", "audit:read"],
    risk="low",
    category="audit_read",
    write=False,
    input_schema={
        "type": "object",
        "properties": {
            "chain_id": {"type": "string", "description": "例如 tool:<id>, job:<id>, plan:<id>, deployment:<id>, audit:<id>"},
            "tool_call_id": {"type": "string"},
            "job_id": {"type": "string"},
            "plan_id": {"type": "string"},
            "deployment_id": {"type": "string"},
            "audit_id": {"type": "string"},
        },
        "additionalProperties": False,
    },
)
def get_operation_chain_tool(args, ctx, db):
    from app.services.audit_chain import build_operation_chain

    return build_operation_chain(
        db,
        chain_id=args.get("chain_id") or "",
        tool_call_id=args.get("tool_call_id") or "",
        job_id=args.get("job_id") or "",
        plan_id=args.get("plan_id") or "",
        deployment_id=args.get("deployment_id") or "",
        audit_id=args.get("audit_id") or "",
    )
