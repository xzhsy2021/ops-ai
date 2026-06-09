from __future__ import annotations

"""Release planning helpers shared by web API and MCP tools.

This module intentionally stays read-only.  It converts existing ToolPlan,
precheck and deployment records into an operator runbook so web users, MCP
clients and future AI assistants all reason from the same release contract.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.db.models import ToolPlan, ToolPlanEvent, Deployment


PROD_ENVS = {"prod", "production", "online", "release", "live", "线上", "生产"}
TERMINAL_DEPLOY_STATUSES = {"success", "failed", "partial_failed", "canceled", "cancelled"}


def _dt(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _env_alias(value: Any) -> str:
    text = str(value or "").strip().lower()
    return "prod" if text in PROD_ENVS else text


def _status_from_bool(ok: bool, *, warning: bool = False) -> str:
    if ok and not warning:
        return "passed"
    if ok and warning:
        return "warning"
    return "blocked"


def _step_preview(step: Dict[str, Any]) -> Dict[str, Any]:
    cfg = step.get("config") if isinstance(step.get("config"), dict) else {}
    command = cfg.get("command") or cfg.get("script") or cfg.get("cmd") or cfg.get("remote_path") or ""
    return {
        "name": step.get("name") or step.get("id") or step.get("type") or "step",
        "type": step.get("type") or "unknown",
        "command_preview": str(command)[:180],
        "has_command": bool(command),
    }


def tool_plan_to_dict(plan: ToolPlan, *, include_payload: bool = False) -> Dict[str, Any]:
    data: Dict[str, Any] = {
        "id": plan.id,
        "plan_id": plan.id,
        "plan_type": plan.plan_type,
        "status": plan.status,
        "created_by": plan.created_by,
        "source_tool": plan.source_tool,
        "system": plan.system,
        "service": plan.service,
        "environment": plan.environment,
        "servers": plan.servers or [],
        "package_name": plan.package_name,
        "pipeline_id": plan.pipeline_id,
        "risk_level": plan.risk_level,
        "confirm_text": plan.confirm_text,
        "confirmed_by": plan.confirmed_by,
        "confirmed_at": _dt(plan.confirmed_at),
        "executed_at": _dt(plan.executed_at),
        "related_deployment_id": plan.related_deployment_id,
        "created_at": _dt(plan.created_at),
        "updated_at": _dt(plan.updated_at),
    }
    if include_payload:
        data.update({
            "payload": plan.payload or {},
            "confirmation": plan.confirmation or {},
            "precheck": plan.precheck or {},
            "diff": plan.diff or {},
        })
    return data


def list_release_plans(
    db: Session,
    *,
    plan_type: str = "",
    status: str = "",
    system: str = "",
    service: str = "",
    environment: str = "",
    limit: int = 50,
) -> Dict[str, Any]:
    q = db.query(ToolPlan)
    if plan_type:
        q = q.filter(ToolPlan.plan_type == plan_type)
    else:
        q = q.filter(ToolPlan.plan_type.in_(["deploy", "rollback"]))
    if status:
        q = q.filter(ToolPlan.status == status)
    if system:
        q = q.filter(ToolPlan.system == system)
    if service:
        q = q.filter(ToolPlan.service == service)
    if environment:
        q = q.filter(ToolPlan.environment == environment)
    rows = q.order_by(ToolPlan.created_at.desc()).limit(max(1, min(int(limit or 50), 200))).all()
    return {"items": [tool_plan_to_dict(row) for row in rows], "total": len(rows)}


def get_release_plan(db: Session, plan_id: str) -> ToolPlan:
    plan = db.query(ToolPlan).filter(ToolPlan.id == plan_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Release plan not found")
    if plan.plan_type not in {"deploy", "rollback"}:
        raise HTTPException(status_code=400, detail="Tool plan is not a release/rollback plan")
    return plan


def release_quality_gates(plan: ToolPlan) -> List[Dict[str, Any]]:
    confirmation = plan.confirmation or {}
    precheck = plan.precheck or {}
    payload = plan.payload or {}
    steps = payload.get("steps") if isinstance(payload.get("steps"), list) else []
    rollback = confirmation.get("rollback_plan") or payload.get("rollback_plan") or {}
    blockers = list(confirmation.get("blockers") or [])
    warnings = list(confirmation.get("warnings") or [])
    precheck_blockers = list(precheck.get("blocking_checks") or [])
    precheck_warnings = list(precheck.get("warning_checks") or [])
    environment = _env_alias(plan.environment)

    gates = [
        {
            "key": "package_selected",
            "name": "发布包已选择",
            "status": _status_from_bool(bool(plan.package_name)),
            "detail": plan.package_name or "未选择发布包",
        },
        {
            "key": "target_servers",
            "name": "目标服务器已确定",
            "status": _status_from_bool(bool(plan.servers)),
            "detail": f"{len(plan.servers or [])} 台：{', '.join(plan.servers or []) or '-'}",
        },
        {
            "key": "pipeline_steps",
            "name": "发布步骤已生成",
            "status": _status_from_bool(bool(steps), warning=not bool(steps)),
            "detail": f"{len(steps)} 个步骤" if steps else "未找到步骤，执行前需重新预检",
        },
        {
            "key": "confirmation_phrase",
            "name": "确认短语已生成",
            "status": _status_from_bool(bool(plan.confirm_text)),
            "detail": plan.confirm_text or "缺少确认短语",
        },
        {
            "key": "rollback_plan",
            "name": "回滚方案",
            "status": _status_from_bool(bool(rollback.get("safe")), warning=not bool(rollback.get("safe"))),
            "detail": rollback.get("description") or rollback.get("mode") or "未检测到安全回滚方案",
        },
        {
            "key": "precheck",
            "name": "发布预检",
            "status": "blocked" if precheck_blockers else "warning" if precheck_warnings or not precheck else "passed",
            "detail": "未运行预检" if not precheck else f"阻断 {len(precheck_blockers)} / 警告 {len(precheck_warnings)}",
        },
        {
            "key": "plan_blockers",
            "name": "计划阻断项",
            "status": _status_from_bool(not blockers, warning=bool(warnings)),
            "detail": "; ".join(blockers or warnings or ["无阻断项"]),
        },
    ]
    if environment == "prod":
        has_reason = bool((payload.get("reason") or "").strip())
        gates.append({
            "key": "prod_change_reason",
            "name": "生产变更原因",
            "status": _status_from_bool(has_reason, warning=not has_reason),
            "detail": payload.get("reason") or "生产环境建议在执行时补充 reason/change_reason",
        })
    return gates


def _gate_summary(gates: List[Dict[str, Any]]) -> Dict[str, Any]:
    blocked = [x for x in gates if x.get("status") == "blocked"]
    warnings = [x for x in gates if x.get("status") == "warning"]
    passed = [x for x in gates if x.get("status") == "passed"]
    return {
        "ready": not blocked,
        "status": "blocked" if blocked else "warning" if warnings else "passed",
        "passed": len(passed),
        "warnings": len(warnings),
        "blocked": len(blocked),
        "blocking_gates": blocked,
        "warning_gates": warnings,
    }


def release_runbook(plan: ToolPlan, *, include_events: bool = False, db: Session | None = None) -> Dict[str, Any]:
    payload = plan.payload or {}
    confirmation = plan.confirmation or {}
    precheck = plan.precheck or {}
    steps = payload.get("steps") if isinstance(payload.get("steps"), list) else []
    rollback = confirmation.get("rollback_plan") or payload.get("rollback_plan") or {}
    gates = release_quality_gates(plan)
    gate_summary = _gate_summary(gates)
    env = _env_alias(plan.environment)
    risk = plan.risk_level or ("critical" if env == "prod" else "high")

    sections = [
        {
            "key": "pre_deploy",
            "title": "发布前检查",
            "items": [
                "确认目标系统、服务、环境和服务器列表。",
                "确认发布包 SHA256 / 文件大小与预期一致。",
                "运行 ops.run_precheck 并修复所有阻断项。",
                "生产环境需要补充变更原因、窗口和回滚负责人。",
            ],
        },
        {
            "key": "execute",
            "title": "执行发布",
            "items": [
                f"输入确认短语：{plan.confirm_text or '-'}",
                "调用 ops.execute_deploy_plan。high/critical 写工具会进入统一任务中心。",
                "在 /tasks?kind=tool 和部署详情页持续观察状态、日志和服务器任务。",
            ],
        },
        {
            "key": "post_deploy",
            "title": "发布后验证",
            "items": [
                "检查部署报告、失败归因和处理建议。",
                "确认服务健康检查、进程状态、关键业务路径。",
                "失败时先查看 failed_servers / failed_steps，再决定重试或回滚。",
            ],
        },
        {
            "key": "rollback",
            "title": "回滚预案",
            "items": [
                rollback.get("description") or "未检测到自动回滚描述。",
                "需要回滚时先生成回滚计划，再输入回滚确认短语。",
                "回滚执行同样进入任务中心并写入审计。",
            ],
        },
    ]

    data: Dict[str, Any] = {
        "plan": tool_plan_to_dict(plan, include_payload=True),
        "summary": {
            "title": f"{plan.plan_type} {plan.system or '-'}/{plan.service or '-'} @ {plan.environment or '-'}",
            "ready": bool(gate_summary.get("ready")),
            "status": gate_summary.get("status"),
            "risk_level": risk,
            "server_count": len(plan.servers or []),
            "package_name": plan.package_name or "",
            "confirm_text": plan.confirm_text or "",
            "execution_is_taskized": risk in {"high", "critical"},
        },
        "quality_gates": gates,
        "gate_summary": gate_summary,
        "pipeline_steps": [_step_preview(step) for step in steps],
        "precheck": precheck,
        "rollback": rollback,
        "mcp_flow": [
            {"step": 1, "tool": "ops.create_deploy_plan", "mode": "plan", "risk": "medium", "writes_runtime": False},
            {"step": 2, "tool": "ops.run_precheck", "mode": "precheck", "risk": "medium", "writes_runtime": False},
            {"step": 3, "tool": "ops.generate_release_runbook", "mode": "read", "risk": "low", "writes_runtime": False},
            {"step": 4, "tool": "ops.execute_deploy_plan", "mode": "execute", "risk": "high", "requires_confirmation": True, "taskized": True},
            {"step": 5, "tool": "ops.get_job_status", "mode": "observe", "risk": "low", "writes_runtime": False},
        ],
        "runbook_sections": sections,
        "next_actions": _next_actions_for_plan(plan, gate_summary),
    }
    if include_events and db is not None:
        events = db.query(ToolPlanEvent).filter(ToolPlanEvent.plan_id == plan.id).order_by(ToolPlanEvent.created_at.asc()).all()
        data["events"] = [
            {
                "id": item.id,
                "event_type": item.event_type,
                "actor": item.actor,
                "message": item.message,
                "payload": item.payload or {},
                "created_at": _dt(item.created_at),
            }
            for item in events
        ]
    return data


def _next_actions_for_plan(plan: ToolPlan, gate_summary: Dict[str, Any]) -> List[Dict[str, Any]]:
    actions: List[Dict[str, Any]] = []
    if plan.status in {"executed", "applied"}:
        actions.append({"type": "observe", "tool": "ops.get_deployment_status", "deployment_id": plan.related_deployment_id, "description": "计划已执行，继续观察部署状态和报告。"})
        return actions
    if gate_summary.get("blocked"):
        actions.append({"type": "fix_blockers", "tool": "ops.run_precheck", "plan_id": plan.id, "description": "修复阻断项后重新运行预检。"})
        return actions
    if not plan.precheck:
        actions.append({"type": "run_precheck", "tool": "ops.run_precheck", "plan_id": plan.id, "description": "执行前先运行发布预检。"})
    actions.append({"type": "confirm_execute", "tool": "ops.execute_deploy_plan", "plan_id": plan.id, "confirm_text": plan.confirm_text or "", "description": "用户确认后提交发布，执行将进入统一任务中心。"})
    return actions


def rollback_readiness(db: Session, deployment_id: str) -> Dict[str, Any]:
    dep = db.query(Deployment).filter(Deployment.id == deployment_id).first()
    if not dep:
        raise HTTPException(status_code=404, detail="Deployment not found")
    servers = [x.strip() for x in str(dep.servers or "").split(",") if x.strip()]
    ready = dep.status == "success" and bool(servers)
    blockers: List[str] = []
    warnings: List[str] = []
    if dep.status != "success":
        blockers.append("只有成功状态的发布单适合自动创建回滚计划")
    if not servers:
        blockers.append("发布单缺少目标服务器列表")
    if _env_alias(dep.environment) == "prod":
        warnings.append("生产环境回滚必须二次确认，并建议先导出发布报告")
    related_plans = db.query(ToolPlan).filter(ToolPlan.related_deployment_id == deployment_id, ToolPlan.plan_type == "rollback").order_by(ToolPlan.created_at.desc()).limit(5).all()
    return {
        "deployment_id": deployment_id,
        "ready": ready and not blockers,
        "status": "blocked" if blockers else "warning" if warnings else "passed",
        "deployment": {
            "id": dep.id,
            "system": dep.system,
            "service": dep.service,
            "environment": dep.environment,
            "status": dep.status,
            "servers": servers,
            "version": dep.version,
            "created_by": dep.created_by,
            "started_at": _dt(dep.started_at),
            "finished_at": _dt(dep.finished_at),
        },
        "blockers": blockers,
        "warnings": warnings,
        "related_rollback_plans": [tool_plan_to_dict(row) for row in related_plans],
        "next_actions": [
            {"tool": "ops.create_rollback_plan", "description": "生成回滚计划；不会直接执行回滚。"},
            {"tool": "ops.execute_rollback_plan", "description": "用户确认后执行回滚；critical 写工具会进入任务中心。"},
        ],
    }
