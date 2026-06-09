from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List

from fastapi import HTTPException

from app.db import DeployTaskRepository, DeploymentRepository, PipelineRepository, DeployLogRepository, DeploymentRuntimeRepository
from app.db.models import ToolPlan
from app.services.tool_registry import registry
from app.services.audit_writer import record_plan_event_async
from app.services.tool_adapters.file_tools import list_packages, upload_package, get_package_checksum
from app.services.package_retention import record_package_reference
from app.deploy.report import deployment_report_payload, deployment_report_markdown
from app.deploy.history import deployment_list_payload
from app.deploy.logs import deployment_logs_payload, deployment_tasks_payload
from app.deploy.preflight import plan_precheck_payload
from app.deploy.tool_response import tool_result
from app.deploy.schemas import DeployRequest
from app.deploy.locks import acquire_deployment_locks, release_deployment_locks
from app.deploy.state import normalize_status
from app.api.deploy._shared import (
    _build_confirmation,
    _deploy_confirm_text,
    _derive_servers,
    _merge_release_variables,
    _db_pipeline_steps,
    _default_release_steps,
    _invalid_worker_payload_reason,
    ensure_deploy_worker_running,
    _rollback_plan_for,
)
from app.core.auth_v2 import require_deploy_for_env
from app.core.deploy_lock import DeployLock


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _json_safe(value: Any) -> Any:
    try:
        json.dumps(value, ensure_ascii=False, default=str)
        return value
    except Exception:
        return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _is_prod(environment: str) -> bool:
    return (environment or "").lower() in {"prod", "production", "online", "live", "release", "生产", "线上"}


def _extract_reason(args: Dict[str, Any]) -> str:
    variables = args.get("variables") if isinstance(args.get("variables"), dict) else {}
    return str(
        args.get("reason")
        or args.get("change_reason")
        or variables.get("reason")
        or variables.get("change_reason")
        or variables.get("release_reason")
        or ""
    ).strip()


def _precheck_with_runtime_guards(plan: ToolPlan, confirmation: Dict[str, Any]) -> Dict[str, Any]:
    precheck = plan_precheck_payload(plan, confirmation)
    extra_checks: List[Dict[str, Any]] = []
    for key in _lock_keys(plan.system or "", plan.service or "", plan.environment or "", list(plan.servers or [])):
        if DeployLock.is_locked(key):
            extra_checks.append({"name": "发布锁", "status": "error", "detail": f"存在发布锁冲突: {key}"})
            break
    if plan.status == "executed":
        extra_checks.append({"name": "计划状态", "status": "error", "detail": "该发布计划已执行，不能重复提交"})
    if not extra_checks:
        extra_checks.append({"name": "任务冲突", "status": "ok", "detail": "未发现同服务发布锁冲突"})
    if extra_checks:
        from app.deploy.preflight import build_preflight_payload
        checks = list(precheck.get("checks") or []) + extra_checks
        precheck = build_preflight_payload(
            checks=checks,
            environment=plan.environment or "",
            file_name=plan.package_name or "",
            servers=list(plan.servers or []),
            package=((confirmation.get("package_match") or {}).get("package") or {}),
            topology=(confirmation.get("topology") or {}),
            required_confirmation=plan.confirm_text or confirmation.get("confirm_text") or "",
            requires_confirmation=bool(confirmation.get("requires_confirmation")),
        )
        precheck["ok"] = bool(precheck.get("ready"))
    return precheck


def _plan_to_dict(plan: ToolPlan) -> Dict[str, Any]:
    return {
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
        "payload": plan.payload or {},
        "confirmation": plan.confirmation or {},
        "precheck": plan.precheck or {},
        "diff": plan.diff or {},
        "risk_level": plan.risk_level,
        "confirm_text": plan.confirm_text,
        "confirmed_by": plan.confirmed_by,
        "confirmed_at": plan.confirmed_at.isoformat() if plan.confirmed_at else None,
        "executed_at": plan.executed_at.isoformat() if plan.executed_at else None,
        "related_deployment_id": plan.related_deployment_id,
        "created_at": plan.created_at.isoformat() if plan.created_at else None,
        "updated_at": plan.updated_at.isoformat() if plan.updated_at else None,
    }


def _get_plan(db, plan_id: str) -> ToolPlan:
    plan = db.query(ToolPlan).filter(ToolPlan.id == plan_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Tool plan not found")
    return plan


def _lock_keys(system: str, service: str, environment: str, servers: List[str]) -> List[str]:
    base = f"{system}:{service or 'default'}:{environment or 'default'}"
    keys = [base]
    keys.extend(f"{base}:server:{s}" for s in servers)
    return keys


def _acquire_locks(keys: List[str]):
    acquired = []
    for key in keys:
        if not DeployLock.acquire(key):
            for x in acquired:
                DeployLock.release(x)
            raise HTTPException(status_code=409, detail=f"Deploy lock conflict: {key}")
        acquired.append(key)
    return acquired


@registry.register(
    name="ops.select_latest_package",
    description="根据服务关键字选择文件中心最新发布包。",
    scopes=["ops:read", "deploy:plan"],
    risk="medium",
    category="deploy_plan",
    input_schema={
        "type": "object",
        "properties": {"service": {"type": "string"}, "limit": {"type": "integer"}},
        "required": ["service"],
        "additionalProperties": False,
    },
)
def select_latest_package(args, ctx, db):
    packages = list_packages({"service": args.get("service"), "limit": args.get("limit") or 5}, ctx, db)
    return {"selected": packages[0] if packages else None, "candidates": packages}


@registry.register(
    name="ops.create_deploy_plan",
    description="创建发布计划并生成确认信息，不执行发布。",
    scopes=["ops:read", "deploy:plan"],
    risk="medium",
    category="deploy_plan",
    requires_confirmation=True,
    input_schema={
        "type": "object",
        "properties": {
            "system": {"type": "string"},
            "service": {"type": "string"},
            "environment": {"type": "string"},
            "package_name": {"type": "string"},
            "servers": {"type": "array", "items": {"type": "string"}},
            "pipeline_id": {"type": "string"},
            "server_group": {"type": "string"},
            "variables": {"type": "object"},
            "reason": {"type": "string"},
            "change_reason": {"type": "string"},
        },
        "required": ["system", "service", "environment", "package_name"],
        "additionalProperties": False,
    },
)
def create_deploy_plan(args, ctx, db):
    req = DeployRequest(
        system=args.get("system"),
        service=args.get("service") or "",
        environment=args.get("environment") or "",
        file_name=args.get("package_name") or "",
        servers=args.get("servers") or [],
        pipeline_id=args.get("pipeline_id") or "",
        server_group=args.get("server_group") or "",
        variables=args.get("variables") or {},
    )
    req.servers = _derive_servers(req, db)
    req.variables = _merge_release_variables(req, db)
    confirmation = _build_confirmation(req, db, ctx.to_audit_dict())
    steps = _db_pipeline_steps(db, req.pipeline_id) or _default_release_steps(req, db)
    confirm_text = confirmation.get("required_confirmation") or confirmation.get("confirm_text") or _deploy_confirm_text(req)
    if confirm_text:
        confirmation["required_confirmation"] = confirm_text
        confirmation["confirm_text"] = confirm_text
    plan = ToolPlan(
        plan_type="deploy",
        status="blocked" if confirmation.get("blockers") else "ready",
        created_by=ctx.username or ctx.token_owner,
        source_tool="ops.create_deploy_plan",
        system=req.system,
        service=req.service,
        environment=req.environment,
        servers=req.servers,
        package_name=req.file_name,
        pipeline_id=req.pipeline_id,
        payload={"request": _json_safe(req.model_dump()), "steps": _json_safe(steps), "reason": _extract_reason(args)},
        confirmation=confirmation,
        precheck={},
        risk_level=confirmation.get("risk_level") or ("critical" if _is_prod(req.environment) else "medium"),
        confirm_text=confirm_text,
    )
    db.add(plan)
    db.commit()
    db.refresh(plan)
    record_package_reference(
        db,
        package_name=req.file_name,
        system=req.system,
        service=req.service,
        environment=req.environment,
        usage_type="plan",
        commit=True,
    )
    record_plan_event_async(plan.id, "created", ctx.username or ctx.token_owner, "发布计划已生成", confirmation)
    return _plan_to_dict(plan)


@registry.register(
    name="ops.get_deploy_confirmation",
    description="获取发布计划确认信息。",
    scopes=["ops:read", "deploy:plan"],
    risk="medium",
    category="deploy_plan",
    input_schema={
        "type": "object",
        "properties": {"plan_id": {"type": "string"}},
        "required": ["plan_id"],
        "additionalProperties": False,
    },
)
def get_deploy_confirmation(args, ctx, db):
    plan = _get_plan(db, args.get("plan_id"))
    confirmation = plan.confirmation or {}
    return {
        "plan_id": plan.id,
        "confirmation": confirmation,
        "ready": not bool(confirmation.get("blockers")),
        "risk_level": plan.risk_level or confirmation.get("risk_level") or "medium",
        "requires_confirmation": bool(confirmation.get("requires_confirmation") or plan.confirm_text),
        "required_confirmation": plan.confirm_text or confirmation.get("required_confirmation") or confirmation.get("confirm_text") or "",
        "blockers": confirmation.get("blockers") or [],
        "warnings": confirmation.get("warnings") or [],
    }


@registry.register(
    name="ops.run_precheck",
    description="基于发布计划返回结构化预检摘要。此工具不执行发布。",
    scopes=["ops:read", "deploy:precheck"],
    risk="medium",
    category="deploy_plan",
    input_schema={
        "type": "object",
        "properties": {"plan_id": {"type": "string"}},
        "required": ["plan_id"],
        "additionalProperties": False,
    },
)
def run_precheck(args, ctx, db):
    plan = _get_plan(db, args.get("plan_id"))
    precheck = _precheck_with_runtime_guards(plan, plan.confirmation or {})
    plan.precheck = precheck
    plan.status = "prechecked" if precheck.get("ok") else "blocked"
    plan.updated_at = _utcnow()
    db.commit()
    record_plan_event_async(plan.id, "precheck", ctx.username or ctx.token_owner, "发布计划预检完成", plan.precheck)
    return {"plan_id": plan.id, **(plan.precheck or {})}


@registry.register(
    name="ops.prepare_release_from_local_package",
    title="Prepare release from local package",
    description="检查/上传本地发布包，创建发布计划并运行预检，返回确认信息；不会执行发布。stdio MCP local_path 会从用户本机流式上传到文件中心。",
    scopes=["package:write", "deploy:plan", "deploy:precheck"],
    risk="high",
    category="package_write",
    write=True,
    requires_confirmation=True,
    input_schema={
        "type": "object",
        "properties": {
            "local_path": {"type": "string", "description": "stdio MCP 本地路径；HTTP 场景仅适用于 OPS 后端本机路径"},
            "filename": {"type": "string"},
            "content_base64": {"type": "string"},
            "package_name": {"type": "string", "description": "已在文件中心存在的包名；提供后可跳过上传"},
            "system": {"type": "string"},
            "service": {"type": "string"},
            "environment": {"type": "string"},
            "servers": {"type": "array", "items": {"type": "string"}},
            "pipeline_id": {"type": "string"},
            "server_group": {"type": "string"},
            "variables": {"type": "object"},
            "overwrite": {"type": "boolean"},
            "calculate_sha256": {"type": "boolean"},
            "dry_run": {"type": "boolean", "description": "只检查本地包和准备参数，不上传、不创建计划"},
            "expected_sha256": {"type": "string", "description": "可选，本地检查得到的 SHA256，用于上传后校验"}
        },
        "required": ["system", "service", "environment"],
        "additionalProperties": False,
    },
)
def prepare_release_from_local_package(args, ctx, db):
    package_name = args.get("package_name") or ""
    upload_result: Dict[str, Any] = {}
    checksum_result: Dict[str, Any] = {}
    local_inspection: Dict[str, Any] = {}

    if args.get("dry_run"):
        if args.get("local_path"):
            upload_result = upload_package({
                "local_path": args.get("local_path"),
                "filename": args.get("filename") or "",
                "dry_run": True,
                "calculate_sha256": args.get("calculate_sha256", True),
                "system": args.get("system") or "",
                "service": args.get("service") or "",
                "overwrite": bool(args.get("overwrite")),
            }, ctx, db)
            local_inspection = upload_result
            package_name = upload_result.get("package_name") or upload_result.get("name") or package_name
        return {
            "dry_run": True,
            "package_name": package_name,
            "local_inspection": local_inspection,
            "summary": "Prepared release dry-run only; no package uploaded and no deploy plan created",
            "next_actions": [
                {"tool": "ops.prepare_release_from_local_package", "description": "Run again with dry_run=false to upload, plan and precheck"}
            ],
        }

    if not package_name:
        upload_args = {
            "local_path": args.get("local_path") or "",
            "filename": args.get("filename") or "",
            "content_base64": args.get("content_base64") or "",
            "system": args.get("system") or "",
            "service": args.get("service") or "",
            "overwrite": bool(args.get("overwrite")),
            "calculate_sha256": args.get("calculate_sha256", True),
        }
        upload_result = upload_package(upload_args, ctx, db)
        package_name = upload_result.get("package_name") or upload_result.get("name") or upload_args.get("filename") or ""
    if not package_name:
        raise HTTPException(status_code=400, detail="package_name is required or upload input must produce a package")

    checksum_result = get_package_checksum({
        "package_name": package_name,
        "expected_sha256": args.get("expected_sha256") or (upload_result or {}).get("sha256") or "",
    }, ctx, db)

    plan = create_deploy_plan({
        "system": args.get("system") or "",
        "service": args.get("service") or "",
        "environment": args.get("environment") or "",
        "package_name": package_name,
        "servers": args.get("servers") or [],
        "pipeline_id": args.get("pipeline_id") or "",
        "server_group": args.get("server_group") or "",
        "variables": args.get("variables") or {},
    }, ctx, db)
    precheck = run_precheck({"plan_id": plan["plan_id"]}, ctx, db)
    confirmation = get_deploy_confirmation({"plan_id": plan["plan_id"]}, ctx, db)
    blockers = list((confirmation.get("confirmation") or {}).get("blockers") or []) + list(precheck.get("checks") or [])
    blocking_checks = [c for c in (precheck.get("checks") or []) if c.get("status") == "error"]
    return {
        "summary": "Release package uploaded, deploy plan created, and precheck completed. Deployment has not been executed.",
        "package": {
            "package_name": package_name,
            "upload": upload_result,
            "checksum": checksum_result,
        },
        "plan": plan,
        "precheck": precheck,
        "confirmation": confirmation.get("confirmation") or {},
        "ready": bool(confirmation.get("ready")) and bool(precheck.get("ok")),
        "blockers": (confirmation.get("confirmation") or {}).get("blockers") or [c.get("detail") for c in blocking_checks],
        "warnings": (confirmation.get("confirmation") or {}).get("warnings") or [],
        "confirm_text": plan.get("confirm_text"),
        "next_actions": [
            {"tool": "ops_execute_deploy_plan", "arguments": {"plan_id": plan["plan_id"], "confirm_text": plan.get("confirm_text")}, "description": "Only call after the user explicitly confirms deployment"},
            {"tool": "ops_get_deploy_confirmation", "arguments": {"plan_id": plan["plan_id"]}, "description": "Review confirmation again"},
        ],
    }


@registry.register(
    name="ops.execute_deploy_plan",
    description="执行已经确认且通过预检的发布计划。默认仅测试/非生产环境开放。",
    scopes=["deploy:execute"],
    risk="high",
    category="deploy_execute",
    write=True,
    requires_confirmation=True,
    requires_human_approval=True,
    data_sensitivity="sensitive",
    input_schema={
        "type": "object",
        "properties": {"plan_id": {"type": "string"}, "confirm_text": {"type": "string"}, "reason": {"type": "string"}, "change_reason": {"type": "string"}},
        "required": ["plan_id", "confirm_text"],
        "additionalProperties": False,
    },
)
def execute_deploy_plan(args, ctx, db):
    plan = _get_plan(db, args.get("plan_id"))
    if plan.plan_type != "deploy":
        raise HTTPException(status_code=400, detail="Plan is not a deploy plan")
    if args.get("confirm_text") != plan.confirm_text:
        raise HTTPException(status_code=400, detail=f"确认短语不匹配，应输入: {plan.confirm_text}")
    confirmation = plan.confirmation or {}
    if confirmation.get("blockers"):
        raise HTTPException(status_code=400, detail="发布计划存在阻断项，不能执行")
    precheck = _precheck_with_runtime_guards(plan, confirmation)
    plan.precheck = precheck
    if precheck.get("ok") is False:
        db.commit()
        raise HTTPException(status_code=400, detail="发布计划预检未通过，不能执行")
    if _is_prod(plan.environment) and not _extract_reason(args):
        raise HTTPException(status_code=400, detail="生产环境发布必须填写 reason 或 change_reason")
    if _is_prod(plan.environment) and not ctx.allow_prod and not ctx.is_admin:
        raise HTTPException(status_code=403, detail="当前 Tool Token 不允许生产发布")
    require_deploy_for_env({"role": ctx.role, "is_admin": ctx.is_admin, "can_deploy": ctx.can_deploy or ctx.allow_write}, plan.environment)

    payload = plan.payload or {}
    invalid_reason = _invalid_worker_payload_reason(payload)
    if invalid_reason:
        raise HTTPException(status_code=400, detail=invalid_reason)
    req = DeployRequest(**(payload.get("request") or {}))
    req.servers = plan.servers or req.servers
    steps = payload.get("steps") or _db_pipeline_steps(db, req.pipeline_id) or _default_release_steps(req, db)
    deployment = DeploymentRepository(db).create(
        system=req.system,
        service=req.service,
        environment=req.environment,
        strategy="DIRECT",
        servers=",".join(req.servers),
        created_by=ctx.username or ctx.token_owner,
        version=req.version or req.file_name,
        server_group=req.server_group,
    )
    deployment.status = "pending"
    db.commit()
    for server_name in (req.servers or [""]):
        record_package_reference(
            db,
            package_name=req.file_name or req.version,
            deployment_id=deployment.id,
            system=req.system,
            service=req.service,
            environment=req.environment,
            server_name=server_name,
            usage_type="deploy",
            commit=False,
        )
    db.commit()
    task_id = uuid.uuid4().hex[:12]
    try:
        keys = acquire_deployment_locks(req, deployment.id, task_id, ctx.username or ctx.token_owner or "", db)
    except HTTPException:
        DeploymentRepository(db).update_status(deployment.id, "failed", "Failed to acquire deployment locks")
        raise
    except Exception:
        DeploymentRepository(db).update_status(deployment.id, "failed", "Failed to acquire deployment locks")
        raise
    task_payload = {"request": _json_safe(req.model_dump()), "steps": _json_safe(steps), "created_by": ctx.username or ctx.token_owner, "tool_plan_id": plan.id, "reason": _extract_reason(args), "precheck": _json_safe(plan.precheck or {})}
    try:
        DeployTaskRepository(db).create(deployment_id=deployment.id, task_id=task_id, payload_json=json.dumps(task_payload, ensure_ascii=False), lock_key=",".join(keys))
    except Exception:
        release_deployment_locks(keys, db)
        DeploymentRepository(db).update_status(deployment.id, "failed", "Failed to create deployment task")
        raise
    plan.status = "executed"
    plan.confirmed_by = ctx.username or ctx.token_owner
    plan.confirmed_at = _utcnow()
    plan.executed_at = _utcnow()
    plan.related_deployment_id = deployment.id
    plan.updated_at = _utcnow()
    db.commit()
    ensure_deploy_worker_running()
    record_plan_event_async(plan.id, "executed", ctx.username or ctx.token_owner, "发布计划已提交 Worker", {"deployment_id": deployment.id, "task_id": task_id, "reason": _extract_reason(args)})
    return {"plan_id": plan.id, "deployment_id": deployment.id, "task_id": task_id, "queued": True, "precheck": plan.precheck or {}, "reason": _extract_reason(args)}


@registry.register(
    name="ops.cancel_deployment",
    description="请求取消发布任务。",
    scopes=["deploy:execute"],
    risk="high",
    category="deploy_execute",
    write=True,
    requires_confirmation=True,
    requires_human_approval=True,
    input_schema={
        "type": "object",
        "properties": {"deployment_id": {"type": "string"}},
        "required": ["deployment_id"],
        "additionalProperties": False,
    },
)
def cancel_deployment(args, ctx, db):
    deployment_id = args.get("deployment_id")
    tasks = DeployTaskRepository(db).list_by_deployment(deployment_id)
    for task in tasks:
        DeployTaskRepository(db).request_cancel(task.id)
        if normalize_status(task.status) == "pending":
            DeployTaskRepository(db).update_status(task.id, "canceled", result="Canceled before worker start")
            lock_keys = [x for x in str(getattr(task, "lock_key", "") or "").split(",") if x]
            release_deployment_locks(lock_keys, db)
    if tasks:
        DeploymentRepository(db).update_status(deployment_id, "canceled", "Cancel requested")
    return {"deployment_id": deployment_id, "cancel_requested": len(tasks), "task_ids": [t.id for t in tasks]}


@registry.register(
    name="ops.get_deployment_status",
    description="查询发布单状态。",
    scopes=["ops:read"],
    input_schema={
        "type": "object",
        "properties": {"deployment_id": {"type": "string"}},
        "required": ["deployment_id"],
        "additionalProperties": False,
    },
)
def get_deployment_status(args, ctx, db):
    dep = DeploymentRepository(db).get_by_id(args.get("deployment_id"))
    if not dep:
        return {"found": False}
    tasks = DeployTaskRepository(db).list_by_deployment(dep.id)
    return {
        "found": True,
        "id": dep.id,
        "system": dep.system,
        "service": dep.service,
        "environment": dep.environment,
        "status": dep.status,
        "servers": dep.servers,
        "version": dep.version,
        "message": dep.message,
        "started_at": dep.started_at.isoformat() if dep.started_at else None,
        "finished_at": dep.finished_at.isoformat() if dep.finished_at else None,
        "tasks": [{"id": t.id, "status": t.status, "result": t.result, "created_at": t.created_at.isoformat() if t.created_at else None} for t in tasks],
    }


@registry.register(
    name="ops.list_deployments",
    description="查询发布历史，默认分页返回，支持按系统、服务、环境、状态、操作者和关键词筛选。",
    scopes=["ops:read"],
    input_schema={
        "type": "object",
        "properties": {
            "system": {"type": "string"},
            "service": {"type": "string"},
            "environment": {"type": "string"},
            "status": {"type": "string"},
            "created_by": {"type": "string"},
            "q": {"type": "string"},
            "limit": {"type": "integer"},
            "offset": {"type": "integer"},
        },
        "additionalProperties": False,
    },
)
def list_deployments(args, ctx, db):
    payload = deployment_list_payload(
        db,
        system=args.get("system") or "",
        service=args.get("service") or "",
        environment=args.get("environment") or "",
        status=args.get("status") or "",
        created_by=args.get("created_by") or "",
        q=args.get("q") or "",
        limit=args.get("limit") or 30,
        offset=args.get("offset") or 0,
    )
    return tool_result(
        data=payload,
        summary=f"返回 {len(payload.get('items') or [])} 条发布记录，共 {payload.get('pagination', {}).get('total', 0)} 条",
        message="deployments listed",
    )


@registry.register(
    name="ops.deploy.aggregate_status",
    title="查询发布聚合状态",
    description="获取发布中心的聚合健康状态：最近发布、活动任务、运行中发布、回滚次数、worker 存活与预检开关。可按 system/environment 过滤。只读。",
    scopes=["ops:read"],
    risk="low",
    category="deploy_read",
    write=False,
    ai_callable=True,
    ai_auto_callable=True,
    data_sensitivity="internal",
    output_masking=True,
    example_prompts=["当前发布中心整体健康状况", "有哪些发布正在运行", "最近 5 条发布记录"],
    input_schema={
        "type": "object",
        "properties": {
            "system": {"type": "string", "description": "按系统名过滤"},
            "environment": {"type": "string", "description": "按环境过滤（dev/staging/prod 等）"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50, "description": "最近发布返回数量，默认 5"},
        },
        "additionalProperties": False,
    },
)
def deploy_aggregate_status_tool(args, ctx, db):
    from app.domain.runtime.snapshots import build_deployments_aggregate

    payload = build_deployments_aggregate(
        db,
        system=args.get("system") or "",
        environment=args.get("environment") or "",
        limit=int(args.get("limit") or 5),
    )
    latest = payload.get("latest_deployments") or []
    active = payload.get("active_jobs") or []
    return tool_result(
        data=payload,
        summary=f"发布聚合状态：最近 {len(latest)} 条，活动 {len(active)} 条，运行中 {payload.get('running_count', 0)} 条",
        message="deploy aggregate status",
    )


@registry.register(
    name="ops.get_deployment_report",
    description="查询发布报告，包含摘要、失败归因、建议、服务器任务、步骤任务和日志摘要。",
    scopes=["ops:read"],
    input_schema={
        "type": "object",
        "properties": {
            "deployment_id": {"type": "string"},
            "format": {"type": "string", "enum": ["json", "summary", "markdown"]},
        },
        "required": ["deployment_id"],
        "additionalProperties": False,
    },
)
def get_deployment_report(args, ctx, db):
    payload = deployment_report_payload(args.get("deployment_id"), db)
    fmt = str(args.get("format") or "json").lower()
    suggestions = payload.get("suggestions", []) or []
    summary_text = payload.get("summary_text") or f"发布报告 {payload.get('id') or ''}"
    if fmt == "summary":
        data = {
            "deployment_id": payload.get("id"),
            "summary": payload.get("summary"),
            "summary_text": summary_text,
            "failure_analysis": payload.get("failure_analysis"),
            "failed_servers": payload.get("failed_servers", []),
            "failed_steps": payload.get("failed_steps", []),
            "suggestions": suggestions,
            "log_summary": payload.get("log_summary"),
            "rollback_available": payload.get("rollback_available"),
        }
        return tool_result(data=data, summary=summary_text, message="deployment report summary", suggestions=suggestions, deployment_id=payload.get("id"))
    if fmt == "markdown":
        data = {
            "deployment_id": payload.get("id"),
            "summary_text": summary_text,
            "markdown": deployment_report_markdown(payload),
        }
        return tool_result(data=data, summary=summary_text, message="deployment report markdown", suggestions=suggestions, deployment_id=payload.get("id"))
    return tool_result(data=payload, summary=summary_text, message="deployment report", suggestions=suggestions, deployment_id=payload.get("id"))


@registry.register(
    name="ops.get_deployment_tasks",
    description="查询发布单的服务器任务、步骤任务和包分发状态。",
    scopes=["ops:read"],
    input_schema={
        "type": "object",
        "properties": {"deployment_id": {"type": "string"}},
        "required": ["deployment_id"],
        "additionalProperties": False,
    },
)
def get_deployment_tasks(args, ctx, db):
    deployment_id = args.get("deployment_id")
    payload = deployment_tasks_payload(db, deployment_id)
    dep = DeploymentRepository(db).get_by_id(deployment_id)
    if not dep:
        raise HTTPException(status_code=404, detail="Deployment not found")
    payload["deployment_status"] = dep.status
    return payload


@registry.register(
    name="ops.get_deployment_logs",
    description="查询发布单日志，默认返回最近 200 行。",
    scopes=["ops:read"],
    streamable=True,
    input_schema={
        "type": "object",
        "properties": {"deployment_id": {"type": "string"}, "limit": {"type": "integer"}, "level": {"type": "string"}},
        "required": ["deployment_id"],
        "additionalProperties": False,
    },
)
def get_deployment_logs(args, ctx, db, stream_callback=None):
    deployment_id = args.get("deployment_id")
    limit = min(max(int(args.get("limit") or 200), 1), 1000)
    payload = deployment_logs_payload(db, deployment_id, include_task_id=False, limit=limit)
    logs = payload["logs"]
    if args.get("level"):
        logs = [x for x in logs if x.get("level") == args.get("level")]
    rows = [
        {"time": x.get("created_at"), "level": x.get("level"), "step_name": x.get("step_name"), "message": x.get("message")}
        for x in logs
    ]
    if stream_callback:
        for row in rows:
            stream_callback({"event": "chunk", "data": row})
    return tool_result(
        data={"deployment_id": deployment_id, "logs": rows, "limit": limit, "count": len(rows)},
        summary=f"返回最近 {len(rows)} 行发布日志",
        message="deployment logs tail",
    )


@registry.register(
    name="ops.create_rollback_plan",
    description="基于成功发布单创建回滚计划，不执行回滚。",
    scopes=["ops:read", "deploy:plan"],
    risk="high",
    category="deploy_plan",
    requires_confirmation=True,
    input_schema={
        "type": "object",
        "properties": {"deployment_id": {"type": "string"}},
        "required": ["deployment_id"],
        "additionalProperties": False,
    },
)
def create_rollback_plan(args, ctx, db):
    deployment_id = args.get("deployment_id")
    dep = DeploymentRepository(db).get_by_id(deployment_id)
    if not dep:
        raise HTTPException(status_code=404, detail="Deployment not found")
    servers = [x.strip() for x in (dep.servers or "").split(",") if x.strip()]
    req = DeployRequest(system=dep.system, service=dep.service or "", environment=dep.environment or "", version=dep.version or "", servers=servers, file_name=dep.version or "", variables={})
    variables = _merge_release_variables(req, db)
    rollback = _rollback_plan_for(dep.system, dep.service or "", dep.environment or "", servers, variables, db)
    blockers = []
    if dep.status != "success":
        blockers.append("只有成功状态的发布单可以自动回滚")
    if not rollback.get("safe") or not rollback.get("command"):
        blockers.append("当前发布缺少可自动执行的回滚方案")
    confirm_text = f"确认回滚 {deployment_id}"
    plan = ToolPlan(
        plan_type="rollback",
        status="blocked" if blockers else "ready",
        created_by=ctx.username or ctx.token_owner,
        source_tool="ops.create_rollback_plan",
        system=dep.system,
        service=dep.service or "",
        environment=dep.environment or "",
        servers=servers,
        package_name=dep.version or "",
        payload={"deployment_id": deployment_id, "rollback_plan": _json_safe(rollback), "blockers": blockers},
        confirmation={"deployment_id": deployment_id, "rollback_plan": _json_safe(rollback), "blockers": blockers, "warnings": [], "ready": not blockers},
        risk_level="critical" if _is_prod(dep.environment) else "high",
        confirm_text=confirm_text,
        related_deployment_id=deployment_id,
    )
    db.add(plan)
    db.commit()
    db.refresh(plan)
    record_plan_event_async(plan.id, "created", ctx.username or ctx.token_owner, "回滚计划已生成", plan.confirmation)
    return _plan_to_dict(plan)


@registry.register(
    name="ops.execute_rollback_plan",
    description="执行已确认的回滚计划。执行被提交到后台 Worker，不接受模型传入任意命令。",
    scopes=["deploy:execute"],
    risk="critical",
    category="deploy_execute",
    write=True,
    requires_confirmation=True,
    requires_human_approval=True,
    data_sensitivity="sensitive",
    input_schema={
        "type": "object",
        "properties": {"plan_id": {"type": "string"}, "confirm_text": {"type": "string"}},
        "required": ["plan_id", "confirm_text"],
        "additionalProperties": False,
    },
)
def execute_rollback_plan(args, ctx, db):
    plan = _get_plan(db, args.get("plan_id"))
    if plan.plan_type != "rollback":
        raise HTTPException(status_code=400, detail="Plan is not a rollback plan")
    if args.get("confirm_text") != plan.confirm_text:
        raise HTTPException(status_code=400, detail=f"确认短语不匹配，应输入: {plan.confirm_text}")
    confirmation = plan.confirmation or {}
    if confirmation.get("blockers"):
        raise HTTPException(status_code=400, detail="回滚计划存在阻断项，不能执行")
    if _is_prod(plan.environment) and not ctx.allow_prod and not ctx.is_admin:
        raise HTTPException(status_code=403, detail="当前 Tool Token 不允许生产回滚")
    require_deploy_for_env({"role": ctx.role, "is_admin": ctx.is_admin, "can_deploy": ctx.can_deploy or ctx.allow_write}, plan.environment)
    deployment_id = (plan.payload or {}).get("deployment_id") or plan.related_deployment_id
    if not deployment_id:
        raise HTTPException(status_code=400, detail="Rollback plan missing deployment_id")
    lock_key = f"{plan.system}:{plan.service or 'default'}:rollback"
    acquired = _acquire_locks([lock_key])
    task_id = uuid.uuid4().hex[:12]
    payload = {"action": "rollback", "tool_plan_id": plan.id, "lock_key": ",".join(acquired)}
    try:
        DeployTaskRepository(db).create(deployment_id=deployment_id, task_id=task_id, payload_json=json.dumps(payload, ensure_ascii=False), lock_key=",".join(acquired))
    except Exception:
        for key in acquired:
            DeployLock.release(key)
        raise
    plan.status = "executed"
    plan.confirmed_by = ctx.username or ctx.token_owner
    plan.confirmed_at = _utcnow()
    plan.executed_at = _utcnow()
    plan.related_deployment_id = deployment_id
    plan.updated_at = _utcnow()
    db.commit()
    ensure_deploy_worker_running()
    record_plan_event_async(plan.id, "executed", ctx.username or ctx.token_owner, "回滚计划已提交 Worker", {"deployment_id": deployment_id, "task_id": task_id})
    return {"plan_id": plan.id, "deployment_id": deployment_id, "task_id": task_id, "queued": True}

# ---------------------------------------------------------------------------
# Iter36 read-only release orchestration tools
# ---------------------------------------------------------------------------

@registry.register(
    name="ops.list_deploy_plans",
    description="列出由 MCP/工具生成的发布或回滚计划，便于 AI/客户端查看待确认计划。",
    scopes=["ops:read", "deploy:plan"],
    risk="low",
    category="deploy_read",
    input_schema={
        "type": "object",
        "properties": {
            "plan_type": {"type": "string", "enum": ["", "deploy", "rollback"]},
            "status": {"type": "string"},
            "system": {"type": "string"},
            "service": {"type": "string"},
            "environment": {"type": "string"},
            "limit": {"type": "integer"},
        },
        "additionalProperties": False,
    },
)
def list_deploy_plans(args, ctx, db):
    from app.services.release_plan import list_release_plans

    return list_release_plans(
        db,
        plan_type=args.get("plan_type") or "",
        status=args.get("status") or "",
        system=args.get("system") or "",
        service=args.get("service") or "",
        environment=args.get("environment") or "",
        limit=args.get("limit") or 50,
    )


@registry.register(
    name="ops.get_deploy_plan",
    description="读取单个发布/回滚计划详情。该工具只读，不执行发布或回滚。",
    scopes=["ops:read", "deploy:plan"],
    risk="low",
    category="deploy_read",
    input_schema={
        "type": "object",
        "properties": {"plan_id": {"type": "string"}, "include_events": {"type": "boolean"}},
        "required": ["plan_id"],
        "additionalProperties": False,
    },
)
def get_deploy_plan(args, ctx, db):
    from app.services.release_plan import get_release_plan, release_runbook

    plan = get_release_plan(db, args.get("plan_id"))
    return release_runbook(plan, include_events=bool(args.get("include_events")), db=db)


@registry.register(
    name="ops.generate_release_runbook",
    description="基于已保存的发布计划生成发布运行手册、质量门禁、MCP 执行链和下一步建议。只读，不执行发布。",
    scopes=["ops:read", "deploy:precheck"],
    risk="low",
    category="deploy_read",
    input_schema={
        "type": "object",
        "properties": {"plan_id": {"type": "string"}, "include_events": {"type": "boolean"}},
        "required": ["plan_id"],
        "additionalProperties": False,
    },
)
def generate_release_runbook(args, ctx, db):
    from app.services.release_plan import get_release_plan, release_runbook

    plan = get_release_plan(db, args.get("plan_id"))
    return release_runbook(plan, include_events=bool(args.get("include_events")), db=db)


@registry.register(
    name="ops.get_rollback_readiness",
    description="只读检查指定发布单是否适合创建回滚计划，返回阻断项、警告和下一步建议。",
    scopes=["ops:read", "deploy:plan"],
    risk="low",
    category="deploy_read",
    input_schema={
        "type": "object",
        "properties": {"deployment_id": {"type": "string"}},
        "required": ["deployment_id"],
        "additionalProperties": False,
    },
)
def get_rollback_readiness(args, ctx, db):
    from app.services.release_plan import rollback_readiness

    return rollback_readiness(db, args.get("deployment_id"))
