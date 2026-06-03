from __future__ import annotations

import copy
import re
from datetime import datetime, timezone
from fastapi import HTTPException

from config_manager import load_config_cached, save_config, invalidate_config_cache, get_server_by_name
from app.db.models import ToolPlan
from app.services.tool_registry import registry
from app.services.audit_writer import record_plan_event_async


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _validate_env_name(name: str) -> str:
    name = (name or "").strip()
    if not name or not re.match(r"^[A-Za-z0-9_.-]{1,64}$", name):
        raise HTTPException(status_code=400, detail="环境标识只能包含英文、数字、_、-、.，长度 1-64")
    return name


def _find_system(cfg, system: str):
    systems = cfg.get("systems") or {}
    sys_cfg = systems.get(system)
    if not sys_cfg:
        raise HTTPException(status_code=404, detail=f"System not found: {system}")
    return systems, sys_cfg


def _find_service(system: str, service: str):
    cfg = load_config_cached()
    systems, sys_cfg = _find_system(cfg, system)
    target = (service or "").lower()
    services = sys_cfg.get("services") or []
    for idx, svc in enumerate(services):
        if not isinstance(svc, dict):
            continue
        names = {str(svc.get("name") or "").lower(), str(svc.get("display_name") or "").lower(), str((svc.get("template_variables") or {}).get("service_name") or "").lower()}
        if target in names or any(n.endswith(f"-{target}") for n in names if n):
            return cfg, systems, sys_cfg, services, idx, svc
    raise HTTPException(status_code=404, detail=f"Service not found: {service}")


def _new_plan(ctx, db, *, plan_type: str, source_tool: str, system: str, service: str = "", environment: str = "", servers=None, payload=None, diff=None, confirm_text: str = ""):
    plan = ToolPlan(
        plan_type=plan_type,
        status="ready",
        created_by=ctx.username or ctx.token_owner,
        source_tool=source_tool,
        system=system,
        service=service,
        environment=environment,
        servers=servers or [],
        payload=payload or {},
        diff=diff or {},
        risk_level="high",
        confirm_text=confirm_text,
    )
    db.add(plan)
    db.commit()
    db.refresh(plan)
    record_plan_event_async(plan.id, "created", ctx.username or ctx.token_owner, "配置变更计划已生成", diff or {})
    return {"plan_id": plan.id, "status": plan.status, "diff": diff or {}, "confirm_text": confirm_text, "risk_level": plan.risk_level}


@registry.register(
    name="ops.create_config_change_plan",
    description="创建配置变更计划，返回 diff，不直接应用。支持环境新增、服务环境服务器、脚本、健康检查配置变更。",
    scopes=["config:write"],
    risk="high",
    category="config_write",
    write=True,
    requires_confirmation=True,
    input_schema={
        "type": "object",
        "properties": {
            "change_type": {"type": "string"},
            "system": {"type": "string"},
            "service": {"type": "string"},
            "environment": {"type": "string"},
            "display_name": {"type": "string"},
            "category": {"type": "string"},
            "servers": {"type": "array", "items": {"type": "string"}},
            "update_script": {"type": "string"},
            "health_check_command": {"type": "string"},
            "log_path": {"type": "string"},
        },
        "required": ["change_type", "system"],
        "additionalProperties": False,
    },
)
def create_config_change_plan(args, ctx, db):
    change_type = args.get("change_type")
    system = args.get("system")
    cfg = load_config_cached()
    systems, sys_cfg = _find_system(cfg, system)

    if change_type == "create_environment":
        env = _validate_env_name(args.get("environment"))
        existing = sys_cfg.get("environments") or []
        for item in existing:
            name = item.get("name") if isinstance(item, dict) else item
            if name == env:
                raise HTTPException(status_code=400, detail=f"Environment already exists: {env}")
        after = {"name": env, "display_name": args.get("display_name") or env, "category": args.get("category") or "custom"}
        diff = {"field": f"systems.{system}.environments", "before": copy.deepcopy(existing), "after": copy.deepcopy(existing) + [after]}
        return _new_plan(ctx, db, plan_type="config_change", source_tool="ops.create_config_change_plan", system=system, environment=env, payload={"change_type": change_type, "system": system, "environment": env, "environment_item": after}, diff=diff, confirm_text=f"确认新增环境 {system}/{env}")

    if change_type == "update_service_env_servers":
        service = args.get("service")
        env = _validate_env_name(args.get("environment"))
        for name in args.get("servers") or []:
            if not get_server_by_name(name):
                raise HTTPException(status_code=400, detail=f"Server does not exist: {name}")
        _, _, _, _, _, svc = _find_service(system, service)
        before = copy.deepcopy(((svc.get("template_variables") or {}).get("servers_by_env") or {}).get(env, []))
        after = args.get("servers") or []
        diff = {"field": f"systems.{system}.services.{svc.get('name')}.template_variables.servers_by_env.{env}", "before": before, "after": after}
        return _new_plan(ctx, db, plan_type="config_change", source_tool="ops.create_config_change_plan", system=system, service=svc.get("name"), environment=env, servers=after, payload={"change_type": change_type, "system": system, "service": svc.get("name"), "environment": env, "servers": after}, diff=diff, confirm_text=f"确认修改 {system}/{svc.get('name')}/{env} 服务器映射")

    if change_type in {"update_service_script", "update_service_health_check"}:
        service = args.get("service")
        _, _, _, _, _, svc = _find_service(system, service)
        tv = dict(svc.get("template_variables") or {})
        fields = {}
        if change_type == "update_service_script":
            fields["update_script"] = args.get("update_script") or ""
        else:
            if args.get("health_check_command"):
                fields["health_check_command"] = args.get("health_check_command")
            if args.get("log_path"):
                fields["log_path"] = args.get("log_path")
        if not fields:
            raise HTTPException(status_code=400, detail="No field to update")
        diff = {"field": f"systems.{system}.services.{svc.get('name')}.template_variables", "before": {k: tv.get(k) for k in fields}, "after": fields}
        return _new_plan(ctx, db, plan_type="config_change", source_tool="ops.create_config_change_plan", system=system, service=svc.get("name"), payload={"change_type": change_type, "system": system, "service": svc.get("name"), "fields": fields}, diff=diff, confirm_text=f"确认修改 {system}/{svc.get('name')} 服务配置")

    raise HTTPException(status_code=400, detail="Unsupported change_type")


@registry.register(
    name="ops.apply_config_change_plan",
    description="应用已确认的配置变更计划。",
    scopes=["config:write"],
    risk="high",
    category="config_write",
    write=True,
    requires_confirmation=True,
    input_schema={
        "type": "object",
        "properties": {"plan_id": {"type": "string"}, "confirm_text": {"type": "string"}},
        "required": ["plan_id", "confirm_text"],
        "additionalProperties": False,
    },
)
def apply_config_change_plan(args, ctx, db):
    plan = db.query(ToolPlan).filter(ToolPlan.id == args.get("plan_id")).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    if plan.plan_type != "config_change":
        raise HTTPException(status_code=400, detail="Plan is not a config change plan")
    if plan.status not in {"ready", "confirmed"}:
        raise HTTPException(status_code=400, detail=f"Plan status cannot be applied: {plan.status}")
    if args.get("confirm_text") != plan.confirm_text:
        raise HTTPException(status_code=400, detail=f"确认短语不匹配，应输入: {plan.confirm_text}")
    payload = plan.payload or {}
    cfg = load_config_cached()
    systems, sys_cfg = _find_system(cfg, payload.get("system"))
    change_type = payload.get("change_type")

    if change_type == "create_environment":
        env_item = payload.get("environment_item") or {}
        environments = list(sys_cfg.get("environments") or [])
        if not any((x.get("name") if isinstance(x, dict) else x) == env_item.get("name") for x in environments):
            environments.append(env_item)
        sys_cfg["environments"] = environments
    elif change_type == "update_service_env_servers":
        _, _, sys_cfg, services, idx, svc = _find_service(payload.get("system"), payload.get("service"))
        tv = dict(svc.get("template_variables") or {})
        servers_by_env = dict(tv.get("servers_by_env") or {})
        servers_by_env[payload.get("environment")] = payload.get("servers") or []
        tv["servers_by_env"] = servers_by_env
        services[idx] = {**svc, "template_variables": tv}
        sys_cfg["services"] = services
    elif change_type in {"update_service_script", "update_service_health_check"}:
        _, _, sys_cfg, services, idx, svc = _find_service(payload.get("system"), payload.get("service"))
        tv = dict(svc.get("template_variables") or {})
        tv.update(payload.get("fields") or {})
        services[idx] = {**svc, "template_variables": tv}
        sys_cfg["services"] = services
    else:
        raise HTTPException(status_code=400, detail="Unsupported change_type")

    systems[payload.get("system")] = sys_cfg
    cfg["systems"] = systems
    save_config(cfg)
    invalidate_config_cache()
    plan.status = "applied"
    plan.confirmed_by = ctx.username or ctx.token_owner
    plan.confirmed_at = _utcnow()
    plan.updated_at = _utcnow()
    db.commit()
    record_plan_event_async(plan.id, "applied", ctx.username or ctx.token_owner, "配置变更计划已应用", plan.diff)
    return {"plan_id": plan.id, "applied": True, "diff": plan.diff}
