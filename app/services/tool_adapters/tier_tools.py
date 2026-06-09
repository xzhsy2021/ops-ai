"""MCP/HTTP tools for 3-tier inspection schedule management (DAILY/WEEKLY/MONTHLY).

配置存储在 DB（inspection_tier_schedules / inspection_notification_routes /
inspection_cascade_policies），不再依赖 yaml 文件。提供 10 个工具：
- ops.tier.list / upsert / delete
- ops.notif_route.list / upsert
- ops.cascade.list / upsert
- ops.tier.run_now / approve / history
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List

from sqlalchemy import select

from app.db.models import (
    InspectionCascadePolicy,
    InspectionNotificationRoute,
    InspectionRun,
    InspectionTierSchedule,
    _utcnow,
)
from app.services.tool_registry import registry


# ──────────────────────────────────────────────────────────────────────────
# 工具实现
# ──────────────────────────────────────────────────────────────────────────

def _to_dict(obj) -> Dict[str, Any]:
    """把 ORM 对象转成可序列化 dict。"""
    if obj is None:
        return {}
    out: Dict[str, Any] = {}
    for col in obj.__table__.columns:
        v = getattr(obj, col.name, None)
        if isinstance(v, datetime):
            v = v.isoformat()
        out[col.name] = v
    return out


# ── tier.list ──────────────────────────────────────────────────────────────

@registry.register(
    name="ops.tier.list",
    title="查询三级巡检调度",
    description="列日/周/月三级巡检调度配置（DB 单源）。返回每级的 cron / categories / thresholds / retention / report / require_approval / last_run 状态。",
    scopes=["ops:read"],
    risk="low",
    category="inspection",
    write=False,
    ai_callable=True,
    ai_auto_callable=True,
    data_sensitivity="internal",
    input_schema={
        "type": "object",
        "properties": {
            "tier": {"type": "string", "enum": ["DAILY", "WEEKLY", "MONTHLY"]},
            "enabled": {"type": "boolean"},
        },
        "additionalProperties": False,
    },
)
def tier_list(args: Dict[str, Any], ctx, db):
    q = select(InspectionTierSchedule)
    if args.get("tier"):
        q = q.where(InspectionTierSchedule.tier == args["tier"])
    if args.get("enabled") is not None:
        q = q.where(InspectionTierSchedule.enabled == bool(args["enabled"]))
    rows = db.execute(q.order_by(InspectionTierSchedule.tier)).scalars().all()
    return {"items": [_to_dict(r) for r in rows], "count": len(rows)}


# ── tier.upsert ────────────────────────────────────────────────────────────

@registry.register(
    name="ops.tier.upsert",
    title="新建/更新三级巡检调度",
    description="按 name 唯一键 upsert 一个三级巡检调度（DAILY/WEEKLY/MONTHLY）。AI/MCP token 需要 admin scope + 人工审批。配置落 inspection_tier_schedules 表。",
    scopes=["ops:write", "ops:admin"],
    risk="high",
    category="inspection_execute",
    write=True,
    requires_confirmation=True,
    requires_human_approval=True,
    ai_callable=True,
    ai_auto_callable=False,
    data_sensitivity="internal",
    input_schema={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "调度名称，唯一"},
            "tier": {"type": "string", "enum": ["DAILY", "WEEKLY", "MONTHLY"]},
            "cron_expression": {"type": "string", "description": "5 字段标准 cron"},
            "timezone": {"type": "string"},
            "enabled": {"type": "boolean"},
            "timeout_minutes": {"type": "integer", "minimum": 1, "maximum": 1440},
            "concurrency": {"type": "integer", "minimum": 1, "maximum": 32},
            "categories": {"type": "array", "items": {"type": "object"}, "description": "巡检项 code 列表"},
            "thresholds": {"type": "object"},
            "notification": {"type": "object"},
            "retention": {"type": "object"},
            "report": {"type": "object"},
            "require_approval": {"type": "boolean"},
        },
        "required": ["name", "tier", "cron_expression"],
        "additionalProperties": False,
    },
)
def tier_upsert(args: Dict[str, Any], ctx, db):
    name = args["name"]
    existing = db.execute(
        select(InspectionTierSchedule).where(InspectionTierSchedule.name == name)
    ).scalar_one_or_none()
    fields = {
        "tier": args["tier"],
        "cron_expression": args["cron_expression"],
        "timezone": args.get("timezone") or "Asia/Shanghai",
        "enabled": bool(args.get("enabled", True)),
        "timeout_minutes": int(args.get("timeout_minutes") or 60),
        "concurrency": int(args.get("concurrency") or 3),
        "categories": args.get("categories") or [],
        "thresholds": args.get("thresholds") or {},
        "notification": args.get("notification") or {},
        "retention": args.get("retention") or {},
        "report": args.get("report") or {},
        "require_approval": bool(args.get("require_approval", False)),
    }
    if existing:
        for k, v in fields.items():
            setattr(existing, k, v)
        existing.updated_at = _utcnow()
        action = "updated"
        obj = existing
    else:
        obj = InspectionTierSchedule(name=name, **fields)
        db.add(obj)
        action = "inserted"
    db.commit()
    db.refresh(obj)
    return {"action": action, "tier": _to_dict(obj)}


# ── tier.delete（软删）──────────────────────────────────────────────────────

@registry.register(
    name="ops.tier.delete",
    title="禁用三级巡检调度",
    description="按 name 软删一个三级巡检调度（设置 enabled=False，不会真删行）。需要 admin。",
    scopes=["ops:write", "ops:admin"],
    risk="high",
    category="inspection_execute",
    write=True,
    requires_confirmation=True,
    requires_human_approval=True,
    ai_callable=True,
    ai_auto_callable=False,
    data_sensitivity="internal",
    input_schema={
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "required": ["name"],
        "additionalProperties": False,
    },
)
def tier_delete(args: Dict[str, Any], ctx, db):
    name = args["name"]
    obj = db.execute(
        select(InspectionTierSchedule).where(InspectionTierSchedule.name == name)
    ).scalar_one_or_none()
    if not obj:
        return {"ok": False, "error": f"tier not found: {name}"}
    obj.enabled = False
    obj.updated_at = _utcnow()
    db.commit()
    return {"ok": True, "name": name, "enabled": False}


# ── notif_route.list ───────────────────────────────────────────────────────

@registry.register(
    name="ops.notif_route.list",
    title="查询巡检通知路由",
    description="列巡检结果通知路由（severity → channels/recipients/SLA）。",
    scopes=["ops:read"],
    risk="low",
    category="inspection",
    write=False,
    ai_callable=True,
    ai_auto_callable=True,
    data_sensitivity="internal",
    input_schema={
        "type": "object",
        "properties": {
            "severity": {"type": "string", "enum": ["high", "medium", "low", "pass"]},
            "tier": {"type": "string", "enum": ["DAILY", "WEEKLY", "MONTHLY"]},
        },
        "additionalProperties": False,
    },
)
def notif_route_list(args: Dict[str, Any], ctx, db):
    q = select(InspectionNotificationRoute)
    if args.get("severity"):
        q = q.where(InspectionNotificationRoute.severity == args["severity"])
    if args.get("tier"):
        q = q.where(InspectionNotificationRoute.tier == args["tier"])
    rows = db.execute(q.order_by(InspectionNotificationRoute.severity)).scalars().all()
    return {"items": [_to_dict(r) for r in rows], "count": len(rows)}


# ── notif_route.upsert ─────────────────────────────────────────────────────

@registry.register(
    name="ops.notif_route.upsert",
    title="新建/更新通知路由",
    description="按 (severity, tier) upsert 一条通知路由。需要 admin。",
    scopes=["ops:write", "ops:admin"],
    risk="high",
    category="inspection_execute",
    write=True,
    requires_confirmation=True,
    requires_human_approval=True,
    ai_callable=True,
    ai_auto_callable=False,
    data_sensitivity="internal",
    input_schema={
        "type": "object",
        "properties": {
            "severity": {"type": "string", "enum": ["high", "medium", "low", "pass"]},
            "tier": {"type": "string", "enum": ["DAILY", "WEEKLY", "MONTHLY"]},
            "channels": {"type": "array", "items": {"type": "string"}},
            "recipients": {"type": "array", "items": {"type": "string"}},
            "sla_minutes": {"type": "integer"},
            "enabled": {"type": "boolean"},
        },
        "required": ["severity"],
        "additionalProperties": False,
    },
)
def notif_route_upsert(args: Dict[str, Any], ctx, db):
    severity = args["severity"]
    tier = args.get("tier")
    q = select(InspectionNotificationRoute).where(
        InspectionNotificationRoute.severity == severity,
        InspectionNotificationRoute.tier.is_(None) if tier is None
        else InspectionNotificationRoute.tier == tier,
    )
    existing = db.execute(q).scalar_one_or_none()
    fields = {
        "channels": args.get("channels") or [],
        "recipients": args.get("recipients") or [],
        "sla_minutes": args.get("sla_minutes"),
        "enabled": bool(args.get("enabled", True)),
    }
    if existing:
        for k, v in fields.items():
            setattr(existing, k, v)
        existing.updated_at = _utcnow()
        action = "updated"
        obj = existing
    else:
        obj = InspectionNotificationRoute(severity=severity, tier=tier, **fields)
        db.add(obj)
        action = "inserted"
    db.commit()
    db.refresh(obj)
    return {"action": action, "route": _to_dict(obj)}


# ── cascade.list ───────────────────────────────────────────────────────────

@registry.register(
    name="ops.cascade.list",
    title="查询跨级联策略",
    description="列跨级联策略（高危/分数骤降 → 触发升级或告警）。",
    scopes=["ops:read"],
    risk="low",
    category="inspection",
    write=False,
    ai_callable=True,
    ai_auto_callable=True,
    data_sensitivity="internal",
    input_schema={"type": "object", "properties": {}, "additionalProperties": False},
)
def cascade_list(args: Dict[str, Any], ctx, db):
    rows = db.execute(
        select(InspectionCascadePolicy).order_by(InspectionCascadePolicy.name)
    ).scalars().all()
    return {"items": [_to_dict(r) for r in rows], "count": len(rows)}


# ── cascade.upsert ─────────────────────────────────────────────────────────

@registry.register(
    name="ops.cascade.upsert",
    title="新建/更新跨级联策略",
    description="按 name 唯一键 upsert 一条跨级联策略。需要 admin。",
    scopes=["ops:write", "ops:admin"],
    risk="high",
    category="inspection_execute",
    write=True,
    requires_confirmation=True,
    requires_human_approval=True,
    ai_callable=True,
    ai_auto_callable=False,
    data_sensitivity="internal",
    input_schema={
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "trigger_type": {"type": "string", "enum": ["high_count", "score_drop", "manual"]},
            "trigger_config": {"type": "object"},
            "action": {"type": "string", "enum": ["trigger_monthly", "trigger_weekly", "alert_director", "open_jira"]},
            "action_config": {"type": "object"},
            "enabled": {"type": "boolean"},
        },
        "required": ["name", "trigger_type", "action"],
        "additionalProperties": False,
    },
)
def cascade_upsert(args: Dict[str, Any], ctx, db):
    name = args["name"]
    existing = db.execute(
        select(InspectionCascadePolicy).where(InspectionCascadePolicy.name == name)
    ).scalar_one_or_none()
    fields = {
        "trigger_type": args["trigger_type"],
        "trigger_config": args.get("trigger_config") or {},
        "action": args["action"],
        "action_config": args.get("action_config") or {},
        "enabled": bool(args.get("enabled", True)),
    }
    if existing:
        for k, v in fields.items():
            setattr(existing, k, v)
        existing.updated_at = _utcnow()
        action = "updated"
        obj = existing
    else:
        obj = InspectionCascadePolicy(name=name, **fields)
        db.add(obj)
        action = "inserted"
    db.commit()
    db.refresh(obj)
    return {"action": action, "policy": _to_dict(obj)}


# ── tier.run_now（立即触发）─────────────────────────────────────────────────

@registry.register(
    name="ops.tier.run_now",
    title="立即触发一次三级巡检",
    description="绕过 cron 立即执行一次指定 tier 的巡检。需要 admin。返回 path A 的 run_id。",
    scopes=["ops:write"],
    risk="high",
    category="inspection_execute",
    write=True,
    requires_confirmation=True,
    requires_human_approval=True,
    ai_callable=True,
    ai_auto_callable=False,
    data_sensitivity="sensitive",
    input_schema={
        "type": "object",
        "properties": {
            "tier": {"type": "string", "enum": ["DAILY", "WEEKLY", "MONTHLY"]},
            "force": {"type": "boolean", "description": "强制忽略 require_approval 锁"},
        },
        "required": ["tier"],
        "additionalProperties": False,
    },
)
def tier_run_now(args: Dict[str, Any], ctx, db):
    tier = args["tier"]
    force = bool(args.get("force"))
    sch = db.execute(
        select(InspectionTierSchedule).where(InspectionTierSchedule.tier == tier)
    ).scalar_one_or_none()
    if not sch:
        return {"ok": False, "error": f"tier not found: {tier}"}
    if not sch.enabled:
        return {"ok": False, "error": f"tier {tier} is disabled"}
    if sch.require_approval and not sch.last_run_at and not force:
        return {"ok": False, "error": f"tier {tier} requires approval first (call ops.tier.approve)"}
    # 触发执行（独立 session 跑，避免 ctx 事务问题）
    from app.services.inspection_tier_dispatcher import execute_tier
    result = execute_tier(sch)
    # 落 last_run_at
    sch.last_run_at = _utcnow()
    sch.last_status = result.get("status")
    sch.last_error = result.get("error")
    sch.last_run_id = result.get("run_id")
    db.commit()
    return {"ok": True, "tier": tier, "result": result}


# ── tier.approve（审批解锁）─────────────────────────────────────────────────

@registry.register(
    name="ops.tier.approve",
    title="审批解锁月巡检",
    description="MONTHLY 默认 require_approval=True，首次跑前需走本工具解锁。解锁后 dispatcher 会按 cron 正常调度。",
    scopes=["ops:write", "ops:admin"],
    risk="high",
    category="inspection_execute",
    write=True,
    requires_confirmation=True,
    requires_human_approval=True,
    ai_callable=True,
    ai_auto_callable=False,
    data_sensitivity="internal",
    input_schema={
        "type": "object",
        "properties": {
            "tier": {"type": "string", "enum": ["MONTHLY"]},
            "approver": {"type": "string", "description": "审批人标识"},
            "comment": {"type": "string"},
        },
        "required": ["tier"],
        "additionalProperties": False,
    },
)
def tier_approve(args: Dict[str, Any], ctx, db):
    tier = args["tier"]
    sch = db.execute(
        select(InspectionTierSchedule).where(InspectionTierSchedule.tier == tier)
    ).scalar_one_or_none()
    if not sch:
        return {"ok": False, "error": f"tier not found: {tier}"}
    if not sch.require_approval:
        return {"ok": True, "tier": tier, "note": "tier does not require approval (no-op)"}
    # 解锁：把 last_run_at 设成 now-1day 的占位值（保留 require_approval=True 字段，但 dispatcher 视为已批准）
    # 更稳妥的做法：增加 approved_at 字段；当前最小改动是把 require_approval 临时关掉
    sch.require_approval = False
    sch.last_run_at = _utcnow()                  # 标记"已审批过"
    sch.updated_at = _utcnow()
    db.commit()
    return {
        "ok": True,
        "tier": tier,
        "approver": args.get("approver") or "",
        "comment": args.get("comment") or "",
        "approved_at": sch.last_run_at.isoformat() if sch.last_run_at else None,
    }


# ── tier.history（历史回溯）─────────────────────────────────────────────────

@registry.register(
    name="ops.tier.history",
    title="查三级巡检历史",
    description="查某 tier 的历史 run（基于 trigger_type=DAILY/WEEKLY/MONTHLY 过滤 inspection_runs）。",
    scopes=["ops:read"],
    risk="low",
    category="inspection",
    write=False,
    ai_callable=True,
    ai_auto_callable=True,
    data_sensitivity="internal",
    input_schema={
        "type": "object",
        "properties": {
            "tier": {"type": "string", "enum": ["DAILY", "WEEKLY", "MONTHLY"]},
            "days": {"type": "integer", "minimum": 1, "maximum": 365, "default": 30},
            "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 50},
        },
        "additionalProperties": False,
    },
)
def tier_history(args: Dict[str, Any], ctx, db):
    tier = args["tier"]
    days = int(args.get("days") or 30)
    limit = int(args.get("limit") or 50)
    cutoff = _utcnow().fromtimestamp(_utcnow().timestamp() - days * 86400)
    rows = db.execute(
        select(InspectionRun)
        .where(InspectionRun.trigger_type == tier.upper())
        .where(InspectionRun.created_at >= cutoff)
        .order_by(InspectionRun.created_at.desc())
        .limit(limit)
    ).scalars().all()
    items: List[Dict[str, Any]] = []
    for r in rows:
        d = _to_dict(r)
        d["issue_count"] = (r.high_count or 0) + (r.medium_count or 0) + (r.low_count or 0)
        items.append(d)
    return {"items": items, "count": len(items), "tier": tier, "days": days}
