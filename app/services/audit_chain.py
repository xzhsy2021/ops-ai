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
    OperationJob,
    ToolCallLog,
    ToolPlan,
    ToolPlanEvent,
)

SCHEMA_VERSION = "iter38.audit-chain.v1"


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
) -> Tuple[Dict[str, str], Set[str], Set[str], Set[str], Set[str], Set[str]]:
    roots: Dict[str, str] = {}
    tool_ids: Set[str] = set()
    job_ids: Set[str] = set()
    plan_ids: Set[str] = set()
    deployment_ids: Set[str] = set()
    audit_ids: Set[str] = set()

    if chain_id and ":" in chain_id:
        typ, raw = chain_id.split(":", 1)
        typ = typ.strip().lower()
        raw = raw.strip()
        if typ in {"tool", "tool_call"}:
            tool_call_id = tool_call_id or raw
        elif typ in {"job", "operation_job"}:
            job_id = job_id or raw
        elif typ in {"plan", "tool_plan"}:
            plan_id = plan_id or raw
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

    # Expand links until stable.  Keep this bounded: all joins are by explicit ids.
    for _ in range(4):
        changed = False
        if tool_ids:
            calls = db.query(ToolCallLog).filter(ToolCallLog.id.in_(tool_ids)).all()
            for call in calls:
                if call.related_job_id and call.related_job_id not in job_ids:
                    job_ids.add(call.related_job_id); changed = True
                if call.related_plan_id and call.related_plan_id not in plan_ids:
                    plan_ids.add(call.related_plan_id); changed = True
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
                    if args.get(key) and str(args[key]) not in plan_ids:
                        plan_ids.add(str(args[key])); changed = True
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
    return roots, tool_ids, job_ids, plan_ids, deployment_ids, audit_ids


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
    root, tool_ids, job_ids, plan_ids, deployment_ids, audit_ids = _collect_from_root(
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
    summary = {
        "root_type": root.get("type"),
        "root_id": root.get("id"),
        "status": _max_status(statuses),
        "risk_level": _max_risk(risks),
        "operator": operator or "-",
        "node_count": len(nodes),
        "edge_count": len(edges),
        "timeline_count": len(timeline),
        "tool_call_count": len(tool_calls),
        "job_count": len(jobs),
        "plan_count": len(plans),
        "deployment_count": len(deployments),
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
    limit = max(1, min(int(limit or 50), 200))
    items: List[Dict[str, Any]] = []

    def add_item(item: Dict[str, Any]) -> None:
        if kind and item.get("kind") != kind:
            return
        if status and item.get("status") != status:
            return
        if risk and item.get("risk_level") != risk:
            return
        items.append(item)

    for call in db.query(ToolCallLog).order_by(ToolCallLog.created_at.desc()).limit(limit).all():
        add_item({
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
        })

    for job in db.query(OperationJob).order_by(OperationJob.created_at.desc()).limit(limit).all():
        add_item({
            "chain_id": f"job:{job.id}",
            "kind": "job",
            "title": job.title or f"任务 {job.id}",
            "status": job.status,
            "risk_level": job.risk_level,
            "operator": job.operator or "-",
            "target": job.target or job.source_tool or "-",
            "created_at": _iso(job.created_at),
            "related_job_id": job.id,
        })

    for plan in db.query(ToolPlan).order_by(ToolPlan.created_at.desc()).limit(limit).all():
        add_item({
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
        })

    for dep in db.query(Deployment).order_by(Deployment.started_at.desc()).limit(limit).all():
        add_item({
            "chain_id": f"deployment:{dep.id}",
            "kind": "deployment",
            "title": f"发布 {dep.system}/{dep.service or 'default'}",
            "status": dep.status,
            "risk_level": "high" if str(dep.environment or "").lower() in {"prod", "production"} else "medium",
            "operator": dep.created_by or "-",
            "target": dep.servers or dep.system,
            "created_at": _iso(dep.started_at),
            "related_deployment_id": dep.id,
        })

    items.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    return {
        "schema_version": SCHEMA_VERSION,
        "items": items[:limit],
        "total": len(items[:limit]),
        "filters": {"kind": kind, "status": status, "risk": risk, "limit": limit},
        "generated_at": _now(),
    }
