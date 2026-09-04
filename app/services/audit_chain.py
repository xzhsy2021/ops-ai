"""Audit chain reconstruction for OPS/MCP/AI operations.

Iter38 links together the records that were already being written in prior
iterations: generic audit rows, MCP/tool calls, operation jobs, tool plans and
release/deploy records.  It is intentionally read-only and deterministic so it
can be used safely from the UI and from MCP tools.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from sqlalchemy.orm import Session

from app.config.audit import load_audit_logs
from app.db.models import (
    AuditRecord,
    DeployLog,
    Deployment,
    DeployTask,
    ExecutionPlan,
    ExecutionPlanStep,
    OperationJob,
    ToolCallLog,
    ToolPlan,
    ToolPlanEvent,
)

SCHEMA_VERSION = "iter38.audit-chain.v1"


def _display_actor(value: Any) -> str:
    """通道身份 → 人名展示（matrix:default:@jun:hubtel.xyz → Jun（@jun:hubtel.xyz））。

    与 approval_tools._display_approver 同规则；完整 ID 保留括注不丢证据。
    """
    raw = str(value or "").strip()
    if not raw:
        return "-"
    mx = raw
    if mx.startswith("matrix:"):
        parts = mx.split(":", 2)
        if len(parts) == 3:
            mx = parts[2]
    if not (mx.startswith("@") and ":" in mx):
        return raw
    local = mx[1:].split(":", 1)[0]
    if not local:
        return raw
    human = local.replace(".", " ").replace("_", " ").strip().capitalize()
    return f"{human}（{mx}）"


def _iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _now() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


def _safe_json(value: Any, limit: int = 6000) -> Any:
    """Return JSON-compatible data without leaking huge payloads."""
    if value is None:
        return None
    if isinstance(value, (dict, list, str, int, float, bool)):
        data = value
    else:
        data = str(value)
    try:
        text = json.dumps(data, ensure_ascii=False, default=str)
    except Exception:
        text = str(data)
    if len(text) <= limit:
        return data
    return text[:limit] + "..."


def _parse_json_text(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        return text


def _rank_risk(risk: str) -> int:
    return {"read": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}.get(str(risk or "low").lower(), 1)


def _max_risk(values: Iterable[str]) -> str:
    risks = [str(x or "low") for x in values]
    if not risks:
        return "low"
    return sorted(risks, key=_rank_risk, reverse=True)[0]


def _status_rank(status: str) -> int:
    return {"failed": 5, "blocked": 4, "running": 3, "queued": 2, "pending": 2, "success": 1, "completed": 1}.get(str(status or "").lower(), 0)


def _max_status(values: Iterable[str]) -> str:
    statuses = [str(x or "") for x in values if x]
    if not statuses:
        return "unknown"
    return sorted(statuses, key=_status_rank, reverse=True)[0]


def _node(node_id: str, typ: str, title: str, **kwargs) -> Dict[str, Any]:
    data = {"id": node_id, "type": typ, "title": title}
    data.update({k: v for k, v in kwargs.items() if v is not None})
    return data


def _event(time_value: Any, typ: str, title: str, detail: str = "", node_id: str = "", status: str = "", risk_level: str = "", data: Any = None) -> Dict[str, Any]:
    return {
        "time": _iso(time_value),
        "type": typ,
        "title": title,
        "detail": detail or "",
        "node_id": node_id or "",
        "status": status or "",
        "risk_level": risk_level or "",
        "data": _safe_json(data, 2000),
    }


def _tool_call_to_dict(row: ToolCallLog) -> Dict[str, Any]:
    return {
        "id": row.id,
        "tool_name": row.tool_name,
        "client_name": row.client_name,
        "token_id": row.token_id,
        "token_owner": row.token_owner,
        "username": row.username,
        "input_args": _parse_json_text(row.input_args),
        "normalized_args": _parse_json_text(row.normalized_args),
        "result_preview": _parse_json_text(row.result_preview),
        "status": row.status,
        "risk_level": row.risk_level,
        "policy_result": _parse_json_text(row.policy_result),
        "blocked_reason": row.blocked_reason,
        "related_plan_id": row.related_plan_id,
        "related_deployment_id": row.related_deployment_id,
        "related_job_id": row.related_job_id,
        "ip_address": row.ip_address,
        "user_agent": row.user_agent,
        "duration_ms": row.duration_ms,
        "created_at": _iso(row.created_at),
    }


def _job_to_dict(row: OperationJob) -> Dict[str, Any]:
    return {
        "id": row.id,
        "job_type": row.job_type,
        "source": row.source,
        "source_tool": row.source_tool,
        "title": row.title,
        "status": row.status,
        "progress": row.progress or 0,
        "risk_level": row.risk_level,
        "operator": row.operator,
        "target": row.target,
        "request": _safe_json(row.request_json),
        "result": _safe_json(row.result_json),
        "error_message": row.error_message,
        "audit_id": row.audit_id,
        "worker_id": row.worker_id,
        "created_at": _iso(row.created_at),
        "started_at": _iso(row.started_at),
        "finished_at": _iso(row.finished_at),
        "updated_at": _iso(row.updated_at),
    }


def _plan_to_dict(row: ToolPlan) -> Dict[str, Any]:
    return {
        "id": row.id,
        "plan_type": row.plan_type,
        "status": row.status,
        "created_by": row.created_by,
        "source_tool": row.source_tool,
        "system": row.system,
        "service": row.service,
        "environment": row.environment,
        "servers": row.servers or [],
        "package_name": row.package_name,
        "payload": _safe_json(row.payload),
        "confirmation": _safe_json(row.confirmation),
        "precheck": _safe_json(row.precheck),
        "diff": _safe_json(row.diff),
        "risk_level": row.risk_level,
        "confirm_text": row.confirm_text,
        "confirmed_by": row.confirmed_by,
        "confirmed_at": _iso(row.confirmed_at),
        "executed_at": _iso(row.executed_at),
        "related_deployment_id": row.related_deployment_id,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


def _deployment_to_dict(row: Deployment) -> Dict[str, Any]:
    return {
        "id": row.id,
        "system": row.system,
        "service": row.service,
        "environment": row.environment,
        "strategy": row.strategy,
        "status": row.status,
        "servers": row.servers,
        "version": row.version,
        "message": row.message,
        "started_at": _iso(row.started_at),
        "finished_at": _iso(row.finished_at),
        "created_by": row.created_by,
    }


def _load_compat_audit_rows(limit: int = 5000) -> List[Dict[str, Any]]:
    rows = load_audit_logs(max(1, min(limit, 10000)))
    normalized = []
    for row in rows:
        normalized.append({
            "id": str(row.get("id") or ""),
            "action": row.get("action"),
            "target_type": row.get("target_type"),
            "target_name": row.get("target_name"),
            "details": row.get("details"),
            "created_at": row.get("created_at"),
        })
    return normalized


def _collect_from_root(
    db: Session,
    *,
    chain_id: str = "",
    tool_call_id: str = "",
    job_id: str = "",
    plan_id: str = "",
    deployment_id: str = "",
    audit_id: str = "",
) -> Tuple[Dict[str, str], Set[str], Set[str], Set[str], Set[str], Set[str], Set[str]]:
    roots: Dict[str, str] = {}
    tool_ids: Set[str] = set()
    job_ids: Set[str] = set()
    plan_ids: Set[str] = set()
    deployment_ids: Set[str] = set()
    audit_ids: Set[str] = set()
    execution_plan_ids: Set[str] = set()

    if chain_id and ":" in chain_id:
        typ, raw = chain_id.split(":", 1)
        typ = typ.strip().lower()
        raw = raw.strip()
        # 短前缀（如 8 位）解析为全量 id——UUID-hex 类 id 前缀唯一时可定位
        def _resolve(raw_id: str, model, col) -> str:
            if not raw_id or len(raw_id) >= 32:
                return raw_id
            row = db.query(model).filter(col.like(f"{raw_id}%")).first()
            return row.id if row else raw_id
        if typ in {"tool", "tool_call"}:
            tool_call_id = tool_call_id or raw
        elif typ in {"job", "operation_job"}:
            job_id = job_id or _resolve(raw, OperationJob, OperationJob.id)
        elif typ in {"plan", "tool_plan"}:
            plan_id = plan_id or _resolve(raw, ToolPlan, ToolPlan.id)
        elif typ in {"execution_plan", "execution", "eplan"}:
            execution_plan_ids.add(_resolve(raw, ExecutionPlan, ExecutionPlan.id))
        elif typ in {"deployment", "deploy"}:
            deployment_id = deployment_id or raw
        elif typ in {"audit", "audit_log"}:
            audit_id = audit_id or raw

    if tool_call_id:
        roots["type"] = "tool_call"
        roots["id"] = tool_call_id
        tool_ids.add(tool_call_id)
    if job_id:
        roots.setdefault("type", "job")
        roots.setdefault("id", job_id)
        job_ids.add(job_id)
    if plan_id:
        roots.setdefault("type", "plan")
        roots.setdefault("id", plan_id)
        plan_ids.add(plan_id)
    if deployment_id:
        roots.setdefault("type", "deployment")
        roots.setdefault("id", deployment_id)
        deployment_ids.add(deployment_id)
    if audit_id:
        roots.setdefault("type", "audit")
        roots.setdefault("id", audit_id)
        audit_ids.add(audit_id)
    if execution_plan_ids:
        roots.setdefault("type", "execution_plan")
        roots.setdefault("id", sorted(execution_plan_ids)[0])

    # Expand links until stable.  Keep this bounded: all joins are by explicit ids.
    for _ in range(4):
        changed = False
        if tool_ids:
            calls = db.query(ToolCallLog).filter(ToolCallLog.id.in_(tool_ids)).all()
            for call in calls:
                if call.related_job_id and call.related_job_id not in job_ids:
                    job_ids.add(call.related_job_id); changed = True
                if call.related_plan_id and call.related_plan_id not in plan_ids:
                    # related_plan_id 可能指向 ToolPlan 或 ExecutionPlan——按存在性归属
                    rid = call.related_plan_id
                    if db.query(ToolPlan.id).filter(ToolPlan.id == rid).first():
                        plan_ids.add(rid); changed = True
                    elif db.query(ExecutionPlan.id).filter(ExecutionPlan.id == rid).first():
                        execution_plan_ids.add(rid); changed = True
                    else:
                        plan_ids.add(rid); changed = True
                if call.related_deployment_id and call.related_deployment_id not in deployment_ids:
                    deployment_ids.add(call.related_deployment_id); changed = True
        if job_ids:
            jobs = db.query(OperationJob).filter(OperationJob.id.in_(job_ids)).all()
            for job in jobs:
                if job.audit_id and str(job.audit_id) not in audit_ids:
                    audit_ids.add(str(job.audit_id)); changed = True
                req = job.request_json or {}
                args = req.get("arguments") or {}
                for key in ("plan_id", "tool_plan_id"):
                    if args.get(key):
                        rid = str(args[key])
                        if rid not in plan_ids and rid not in execution_plan_ids:
                            if db.query(ToolPlan.id).filter(ToolPlan.id == rid).first():
                                plan_ids.add(rid)
                            elif db.query(ExecutionPlan.id).filter(ExecutionPlan.id == rid).first():
                                execution_plan_ids.add(rid)
                            else:
                                plan_ids.add(rid)
                            changed = True
                # execute_plan 任务的 arguments 含 execution plan_id——上面已按存在性归属
                for key in ("execution_plan_id",):
                    if args.get(key) and str(args[key]) not in execution_plan_ids:
                        execution_plan_ids.add(str(args[key])); changed = True
                if args.get("deployment_id") and str(args["deployment_id"]) not in deployment_ids:
                    deployment_ids.add(str(args["deployment_id"])); changed = True
            linked_calls = db.query(ToolCallLog).filter(ToolCallLog.related_job_id.in_(job_ids)).all()
            for call in linked_calls:
                if call.id not in tool_ids:
                    tool_ids.add(call.id); changed = True
        if plan_ids:
            plans = db.query(ToolPlan).filter(ToolPlan.id.in_(plan_ids)).all()
            for plan in plans:
                if plan.related_deployment_id and plan.related_deployment_id not in deployment_ids:
                    deployment_ids.add(plan.related_deployment_id); changed = True
            linked_calls = db.query(ToolCallLog).filter(ToolCallLog.related_plan_id.in_(plan_ids)).all()
            for call in linked_calls:
                if call.id not in tool_ids:
                    tool_ids.add(call.id); changed = True
        if execution_plan_ids:
            eplans = db.query(ExecutionPlan).filter(ExecutionPlan.id.in_(execution_plan_ids)).all()
            for ep in eplans:
                # 发布链关联：execution_job → 任务链；conversation 内的消息级 tool_calls
                if ep.execution_job_id and ep.execution_job_id not in job_ids:
                    job_ids.add(ep.execution_job_id); changed = True
            # 谁调用了 prepare/execute（按 plan_id 反查，含 ExecutionPlan 归属的）
            linked_calls = db.query(ToolCallLog).filter(ToolCallLog.related_plan_id.in_(execution_plan_ids)).all()
            for call in linked_calls:
                if call.id not in tool_ids:
                    tool_ids.add(call.id); changed = True
            # prepare/execute 的 job arguments 里也带 plan_id（ExecutionPlan id）
            linked_jobs = db.query(OperationJob).filter(
                OperationJob.request_json.isnot(None)
            ).all()
            for job in linked_jobs:
                if job.id in job_ids:
                    continue
                req = job.request_json or {}
                args = req.get("arguments") or {}
                pid = str(args.get("plan_id") or "")
                if pid and pid in execution_plan_ids:
                    job_ids.add(job.id); changed = True
        if deployment_ids:
            plans = db.query(ToolPlan).filter(ToolPlan.related_deployment_id.in_(deployment_ids)).all()
            for plan in plans:
                if plan.id not in plan_ids:
                    plan_ids.add(plan.id); changed = True
            linked_calls = db.query(ToolCallLog).filter(ToolCallLog.related_deployment_id.in_(deployment_ids)).all()
            for call in linked_calls:
                if call.id not in tool_ids:
                    tool_ids.add(call.id); changed = True
        if not changed:
            break
    if not roots:
        roots = {"type": "recent", "id": "recent"}
    return roots, tool_ids, job_ids, plan_ids, deployment_ids, audit_ids, execution_plan_ids


def build_operation_chain(
    db: Session,
    *,
    chain_id: str = "",
    tool_call_id: str = "",
    job_id: str = "",
    plan_id: str = "",
    deployment_id: str = "",
    audit_id: str = "",
    include_raw: bool = False,
) -> Dict[str, Any]:
    root, tool_ids, job_ids, plan_ids, deployment_ids, audit_ids, execution_plan_ids = _collect_from_root(
        db,
        chain_id=chain_id,
        tool_call_id=tool_call_id,
        job_id=job_id,
        plan_id=plan_id,
        deployment_id=deployment_id,
        audit_id=audit_id,
    )

    tool_calls = db.query(ToolCallLog).filter(ToolCallLog.id.in_(tool_ids)).order_by(ToolCallLog.created_at.asc()).all() if tool_ids else []
    jobs = db.query(OperationJob).filter(OperationJob.id.in_(job_ids)).order_by(OperationJob.created_at.asc()).all() if job_ids else []
    plans = db.query(ToolPlan).filter(ToolPlan.id.in_(plan_ids)).order_by(ToolPlan.created_at.asc()).all() if plan_ids else []
    deployments = db.query(Deployment).filter(Deployment.id.in_(deployment_ids)).order_by(Deployment.started_at.asc()).all() if deployment_ids else []
    execution_plans = db.query(ExecutionPlan).filter(ExecutionPlan.id.in_(execution_plan_ids)).order_by(ExecutionPlan.created_at.asc()).all() if execution_plan_ids else []
    execution_steps = db.query(ExecutionPlanStep).filter(ExecutionPlanStep.plan_id.in_(execution_plan_ids)).order_by(ExecutionPlanStep.step_order.asc()).all() if execution_plan_ids else []
    plan_events = db.query(ToolPlanEvent).filter(ToolPlanEvent.plan_id.in_(plan_ids)).order_by(ToolPlanEvent.created_at.asc()).all() if plan_ids else []
    deploy_tasks = db.query(DeployTask).filter(DeployTask.deployment_id.in_(deployment_ids)).order_by(DeployTask.created_at.asc()).all() if deployment_ids else []
    deploy_logs = db.query(DeployLog).filter(DeployLog.deployment_id.in_(deployment_ids)).order_by(DeployLog.created_at.asc()).limit(400).all() if deployment_ids else []

    compat_audit_rows = _load_compat_audit_rows(6000)
    if audit_ids:
        compat_audit_rows = [row for row in compat_audit_rows if str(row.get("id")) in audit_ids]
    else:
        # Pull audit rows likely related to any known entity id/name.  This is a
        # best-effort bridge for legacy audit_logs rows that do not have FKs.
        needles = set(tool_ids) | set(job_ids) | set(plan_ids) | set(deployment_ids)
        for dep in deployments:
            if dep.system:
                needles.add(dep.system)
            if dep.service:
                needles.add(dep.service)
        for plan in plans:
            if plan.system:
                needles.add(plan.system)
            if plan.service:
                needles.add(plan.service)
        lowered = [x.lower() for x in needles if x]
        if lowered:
            filtered = []
            for row in compat_audit_rows:
                text = " ".join(str(row.get(k) or "") for k in ("action", "target_type", "target_name", "details")).lower()
                if any(n in text for n in lowered):
                    filtered.append(row)
            compat_audit_rows = filtered[:80]
        else:
            compat_audit_rows = []

    nodes: List[Dict[str, Any]] = []
    edges: List[Dict[str, Any]] = []
    timeline: List[Dict[str, Any]] = []

    for call in tool_calls:
        nid = f"tool:{call.id}"
        nodes.append(_node(nid, "tool_call", call.tool_name, status=call.status, risk_level=call.risk_level, created_at=_iso(call.created_at), data=_tool_call_to_dict(call)))
        timeline.append(_event(call.created_at, "tool_call", f"工具调用 {call.tool_name}", call.blocked_reason or call.status or "", nid, call.status, call.risk_level, _tool_call_to_dict(call)))
        if call.related_job_id:
            edges.append({"from": nid, "to": f"job:{call.related_job_id}", "relation": "queued_job"})
        if call.related_plan_id:
            edges.append({"from": nid, "to": f"plan:{call.related_plan_id}", "relation": "related_plan"})
        if call.related_deployment_id:
            edges.append({"from": nid, "to": f"deployment:{call.related_deployment_id}", "relation": "related_deployment"})

    for job in jobs:
        nid = f"job:{job.id}"
        nodes.append(_node(nid, "operation_job", job.title or job.id, status=job.status, risk_level=job.risk_level, created_at=_iso(job.created_at), data=_job_to_dict(job)))
        timeline.append(_event(job.created_at, "job_created", f"任务创建 {job.source_tool or job.job_type}", job.title or "", nid, job.status, job.risk_level, _job_to_dict(job)))
        if job.started_at:
            timeline.append(_event(job.started_at, "job_started", "任务开始执行", job.worker_id or "", nid, job.status, job.risk_level))
        if job.finished_at:
            timeline.append(_event(job.finished_at, "job_finished", "任务执行结束", job.error_message or job.status or "", nid, job.status, job.risk_level))
        req = job.request_json or {}
        args = req.get("arguments") or {}
        for key in ("plan_id", "tool_plan_id"):
            if args.get(key):
                edges.append({"from": nid, "to": f"plan:{args[key]}", "relation": "executes_plan"})
        if args.get("deployment_id"):
            edges.append({"from": nid, "to": f"deployment:{args['deployment_id']}", "relation": "operates_deployment"})
        if job.audit_id:
            edges.append({"from": nid, "to": f"tool:{job.audit_id}", "relation": "initial_audit_call"})

    for plan in plans:
        nid = f"plan:{plan.id}"
        nodes.append(_node(nid, "tool_plan", f"计划 {plan.plan_type}", status=plan.status, risk_level=plan.risk_level, created_at=_iso(plan.created_at), data=_plan_to_dict(plan)))
        timeline.append(_event(plan.created_at, "plan_created", f"计划创建 {plan.plan_type}", plan.source_tool or "", nid, plan.status, plan.risk_level, _plan_to_dict(plan)))
        if plan.confirmed_at:
            timeline.append(_event(plan.confirmed_at, "plan_confirmed", "计划人工确认", plan.confirmed_by or "", nid, plan.status, plan.risk_level))
        if plan.executed_at:
            timeline.append(_event(plan.executed_at, "plan_executed", "计划执行", plan.related_deployment_id or "", nid, plan.status, plan.risk_level))
        if plan.related_deployment_id:
            edges.append({"from": nid, "to": f"deployment:{plan.related_deployment_id}", "relation": "created_deployment"})

    for event in plan_events:
        nid = f"plan_event:{event.id}"
        nodes.append(_node(nid, "tool_plan_event", event.event_type, status=event.event_type, created_at=_iso(event.created_at), data={"id": event.id, "plan_id": event.plan_id, "event_type": event.event_type, "actor": event.actor, "message": event.message, "payload": _safe_json(event.payload), "created_at": _iso(event.created_at)}))
        timeline.append(_event(event.created_at, "plan_event", event.event_type, event.message or "", nid, event.event_type, data=event.payload))
        edges.append({"from": f"plan:{event.plan_id}", "to": nid, "relation": "has_event"})

    for dep in deployments:
        nid = f"deployment:{dep.id}"
        nodes.append(_node(nid, "deployment", f"发布 {dep.system}/{dep.service or 'default'}", status=dep.status, created_at=_iso(dep.started_at), data=_deployment_to_dict(dep)))
        timeline.append(_event(dep.started_at, "deployment_started", f"发布开始 {dep.system}/{dep.service or 'default'}", dep.message or "", nid, dep.status, data=_deployment_to_dict(dep)))
        if dep.finished_at:
            timeline.append(_event(dep.finished_at, "deployment_finished", "发布结束", dep.message or dep.status or "", nid, dep.status))

    # ── 当前发布链路：ExecutionPlan（消息级审批 + 冻结步骤）──
    for ep in execution_plans:
        nid = f"execution_plan:{ep.id}"
        scope = f"{ep.system_name or '-'}"
        if ep.service_name:
            scope += f"/{ep.service_name}"
        if ep.environment:
            scope += f"（{ep.environment}）"
        ep_data = {
            "id": ep.id,
            "status": ep.status,
            "risk_level": ep.risk_level,
            "scope": scope,
            "targets": ep.targets or [],
            "package_sha256": ep.package_sha256,
            "package_size_bytes": ep.package_size_bytes,
            "approved_by": _display_actor(ep.approved_by),
            "approved_by_raw": ep.approved_by,
            "requested_by": _display_actor(ep.requested_by),
            "requested_by_raw": ep.requested_by,
            "failure_reason": ep.failure_reason,
            "created_at": _iso(ep.created_at),
            "approved_at": _iso(ep.approved_at),
        }
        nodes.append(_node(nid, "execution_plan", f"执行计划 {scope}", status=ep.status, risk_level=ep.risk_level, created_at=_iso(ep.created_at), data=ep_data))
        timeline.append(_event(ep.created_at, "execution_plan_created", f"计划创建 {scope}", f"风险 {ep.risk_level or '-'} · {len(ep.steps or [])} 步骤", nid, ep.status, ep.risk_level, ep_data))
        if ep.approved_at:
            timeline.append(_event(ep.approved_at, "execution_plan_approved", "计划批准", f"审批人 {_display_actor(ep.approved_by)}", nid, ep.status, ep.risk_level))
        if ep.rejected_at:
            timeline.append(_event(ep.rejected_at, "execution_plan_rejected", "计划拒绝", f"拒绝人 {_display_actor(ep.rejected_by)}", nid, ep.status, ep.risk_level))
        if ep.execution_job_id:
            edges.append({"from": nid, "to": f"job:{ep.execution_job_id}", "relation": "executed_by_job"})
    for step in execution_steps:
        nid = f"execution_step:{step.id}"
        verb = step.action_type or "步骤"
        step_data = {
            "id": step.id,
            "plan_id": step.plan_id,
            "step_key": step.step_key,
            "step_order": step.step_order,
            "action_type": step.action_type,
            "status": step.status,
            "error_message": step.error_message,
            "attempt_count": step.attempt_count,
            "started_at": _iso(step.started_at),
            "finished_at": _iso(step.finished_at),
        }
        nodes.append(_node(nid, "execution_plan_step", f"{verb} {step.step_key}", status=step.status, created_at=_iso(step.created_at), data=step_data))
        detail = step.error_message or ""
        if not detail and step.result:
            r = step.result
            if isinstance(r, dict):
                detail = str(r.get("summary") or r.get("message") or r.get("detail") or "")[:160]
            else:
                detail = str(r)[:160]
        timeline.append(_event(step.finished_at or step.started_at or step.created_at, "execution_step", f"{verb} {step.step_key}", detail or step.status or "", nid, step.status, data=step_data))
        edges.append({"from": f"execution_plan:{step.plan_id}", "to": nid, "relation": "has_step"})

    for task in deploy_tasks:
        nid = f"deploy_task:{task.id}"
        nodes.append(_node(nid, "deploy_task", "发布后台任务", status=task.status, created_at=_iso(task.created_at), data={"id": task.id, "deployment_id": task.deployment_id, "status": task.status, "worker": task.worker, "result": task.result, "payload_json": _parse_json_text(task.payload_json), "lock_key": task.lock_key, "cancel_requested": task.cancel_requested, "created_at": _iso(task.created_at), "started_at": _iso(task.started_at), "finished_at": _iso(task.finished_at)}))
        timeline.append(_event(task.created_at, "deploy_task", "发布后台任务", task.result or task.status or "", nid, task.status))
        if task.deployment_id:
            edges.append({"from": f"deployment:{task.deployment_id}", "to": nid, "relation": "has_task"})

    for log in deploy_logs:
        nid = f"deploy_log:{log.id}"
        timeline.append(_event(log.created_at, "deploy_log", log.step_name or "deploy", log.message, nid, log.level, data={"level": log.level, "message": log.message}))
        if log.deployment_id:
            edges.append({"from": f"deployment:{log.deployment_id}", "to": nid, "relation": "has_log"})

    for row in compat_audit_rows:
        nid = f"audit:{row.get('id')}"
        nodes.append(_node(nid, "audit_log", row.get("action") or "audit", status=row.get("action") or "", created_at=row.get("created_at"), data=row))
        timeline.append(_event(row.get("created_at"), "audit_log", row.get("action") or "audit", row.get("details") or "", nid, row.get("action") or "", data=row))

    timeline.sort(key=lambda x: x.get("time") or "")
    statuses = [n.get("status") for n in nodes if n.get("status")]
    risks = [n.get("risk_level") for n in nodes if n.get("risk_level")]
    operator = ""
    for call in tool_calls:
        operator = call.username or call.token_owner or call.client_name or operator
    for job in jobs:
        operator = operator or job.operator or ""
    for plan in plans:
        operator = operator or plan.created_by or ""
    for ep in execution_plans:
        operator = operator or ep.requested_by or ""
    summary = {
        "root_type": root.get("type"),
        "root_id": root.get("id"),
        "status": _max_status(statuses),
        "risk_level": _max_risk(risks),
        "operator": _display_actor(operator) if operator else "-",
        "operator_raw": operator or "",
        "node_count": len(nodes),
        "edge_count": len(edges),
        "timeline_count": len(timeline),
        "tool_call_count": len(tool_calls),
        "job_count": len(jobs),
        "plan_count": len(plans),
        "deployment_count": len(deployments),
        "execution_plan_count": len(execution_plans),
        "audit_log_count": len(compat_audit_rows),
        "generated_at": _now(),
    }
    data = {
        "schema_version": SCHEMA_VERSION,
        "chain_id": chain_id or f"{root.get('type')}:{root.get('id')}",
        "summary": summary,
        "nodes": nodes,
        "edges": edges,
        "timeline": timeline,
        "evidence": {
            "tool_calls": [_tool_call_to_dict(x) for x in tool_calls],
            "operation_jobs": [_job_to_dict(x) for x in jobs],
            "tool_plans": [_plan_to_dict(x) for x in plans],
            "tool_plan_events": [{"id": x.id, "plan_id": x.plan_id, "event_type": x.event_type, "actor": x.actor, "message": x.message, "payload": _safe_json(x.payload), "created_at": _iso(x.created_at)} for x in plan_events],
            "deployments": [_deployment_to_dict(x) for x in deployments],
            "execution_plans": [
                {
                    "id": x.id, "status": x.status, "risk_level": x.risk_level,
                    "system_name": x.system_name, "service_name": x.service_name,
                    "environment": x.environment, "targets": x.targets or [],
                    "approved_by": _display_actor(x.approved_by), "approved_by_raw": x.approved_by,
                    "requested_by": _display_actor(x.requested_by), "requested_by_raw": x.requested_by,
                    "failure_reason": x.failure_reason,
                    "created_at": _iso(x.created_at), "approved_at": _iso(x.approved_at),
                    "steps": [
                        {
                            "step_key": s.step_key, "step_order": s.step_order,
                            "action_type": s.action_type, "status": s.status,
                            "error_message": s.error_message,
                            "started_at": _iso(s.started_at), "finished_at": _iso(s.finished_at),
                        }
                        for s in (x.steps or [])
                    ],
                }
                for x in execution_plans
            ],
            "deploy_tasks": [{"id": x.id, "deployment_id": x.deployment_id, "status": x.status, "result": x.result, "created_at": _iso(x.created_at), "started_at": _iso(x.started_at), "finished_at": _iso(x.finished_at)} for x in deploy_tasks],
            "audit_logs": compat_audit_rows,
        },
        "guardrails": {
            "mode": "read_only_audit_replay",
            "safe_for_ai": True,
            "does_not_execute_tools": True,
            "high_risk_actions_must_use_task_center": True,
        },
    }
    if not include_raw:
        # Evidence is still present but bounded/sanitized.  Keep field for API
        # stability, no extra raw DB objects are ever returned.
        pass
    return data


def list_operation_chains(db: Session, *, limit: int = 50, kind: str = "", status: str = "", risk: str = "") -> Dict[str, Any]:
    """按 kind 精确取源：指定 kind 时只查该源（修复：旧实现对四源各查 limit 条
    再在内存丢弃，既浪费又导致混合列表时间错位）；不指定 kind 时各源取 limit 条
    后合并按时间排序。status/risk 为精确匹配（大小写不敏感）。"""
    limit = max(1, min(int(limit or 50), 200))
    kind = (kind or "").strip().lower()
    status_f = (status or "").strip()
    risk_f = (risk or "").strip()
    items: List[Dict[str, Any]] = []

    def keep(item: Dict[str, Any]) -> bool:
        if status_f and str(item.get("status") or "").lower() != status_f.lower():
            return False
        if risk_f and str(item.get("risk_level") or "").lower() != risk_f.lower():
            return False
        return True

    if not kind or kind == "tool_call":
        for call in db.query(ToolCallLog).order_by(ToolCallLog.created_at.desc()).limit(limit).all():
            item = {
                "chain_id": f"tool:{call.id}",
                "kind": "tool_call",
                "title": f"工具调用 {call.tool_name}",
                "status": call.status,
                "risk_level": call.risk_level,
                "operator": call.username or call.token_owner or call.client_name or "-",
                "target": call.related_job_id or call.related_plan_id or call.related_deployment_id or call.tool_name,
                "created_at": _iso(call.created_at),
                "related_job_id": call.related_job_id,
                "related_plan_id": call.related_plan_id,
                "related_deployment_id": call.related_deployment_id,
            }
            if keep(item):
                items.append(item)

    if not kind or kind == "job":
        for job in db.query(OperationJob).order_by(OperationJob.created_at.desc()).limit(limit).all():
            item = {
                "chain_id": f"job:{job.id}",
                "kind": "job",
                "title": job.title or f"任务 {job.id}",
                "status": job.status,
                "risk_level": job.risk_level,
                "operator": job.operator or "-",
                "target": job.target or job.source_tool or "-",
                "created_at": _iso(job.created_at),
                "related_job_id": job.id,
            }
            if keep(item):
                items.append(item)

    if not kind or kind == "plan":
        for plan in db.query(ToolPlan).order_by(ToolPlan.created_at.desc()).limit(limit).all():
            item = {
                "chain_id": f"plan:{plan.id}",
                "kind": "plan",
                "title": f"{plan.plan_type} 计划 {plan.system or ''}/{plan.service or ''}".strip(),
                "status": plan.status,
                "risk_level": plan.risk_level,
                "operator": plan.created_by or "-",
                "target": plan.related_deployment_id or plan.system or plan.id,
                "created_at": _iso(plan.created_at),
                "related_plan_id": plan.id,
                "related_deployment_id": plan.related_deployment_id,
            }
            if keep(item):
                items.append(item)

    if not kind or kind == "execution_plan":
        # 当前发布链路：ExecutionPlan（消息级审批 + 冻结步骤执行）
        for ep in db.query(ExecutionPlan).order_by(ExecutionPlan.created_at.desc()).limit(limit).all():
            scope = f"{ep.system_name or '-'}"
            if ep.service_name:
                scope += f"/{ep.service_name}"
            item = {
                "chain_id": f"execution_plan:{ep.id}",
                "kind": "execution_plan",
                "title": f"执行计划 {scope}" + (f"（{ep.environment}）" if ep.environment else ""),
                "status": ep.status,
                "risk_level": ep.risk_level,
                "operator": _display_actor(ep.requested_by or ep.approved_by),
                "operator_raw": ep.requested_by or ep.approved_by or "",
                "target": f"{ep.system_name or '-'}@{ep.environment or '-'} " + ", ".join(str(t) for t in (ep.targets or [])[:3]),
                "created_at": _iso(ep.created_at),
                "related_plan_id": ep.id,
                "step_count": len(ep.steps or []),
            }
            if keep(item):
                items.append(item)

    if not kind or kind == "deployment":
        for dep in db.query(Deployment).order_by(Deployment.started_at.desc()).limit(limit).all():
            item = {
                "chain_id": f"deployment:{dep.id}",
                "kind": "deployment",
                "title": f"发布 {dep.system}/{dep.service or 'default'}",
                "status": dep.status,
                "risk_level": "high" if str(dep.environment or "").lower() in {"prod", "production"} else "medium",
                "operator": dep.created_by or "-",
                "target": dep.servers or dep.system,
                "created_at": _iso(dep.started_at),
                "related_deployment_id": dep.id,
            }
            if keep(item):
                items.append(item)

    items.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    return {
        "schema_version": SCHEMA_VERSION,
        "items": items[:limit],
        "total": len(items[:limit]),
        "filters": {"kind": kind, "status": status_f, "risk": risk_f, "limit": limit},
        "generated_at": _now(),
    }
