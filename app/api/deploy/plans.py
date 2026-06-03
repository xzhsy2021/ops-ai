"""发布管理 - 发布计划、变量解析、确认等相关路由"""
from typing import Dict, Any, Optional
from fastapi import APIRouter, HTTPException, Request, Depends, Query
from sqlalchemy.orm import Session

from app.api.helpers import api_response, audit
from app.db import get_db, PipelineRepository, PipelineStepRepository
from app.core.auth_v2 import require_auth
from app.pipeline.variables import get_variable_registry, STEP_FIELD_BINDINGS
from app.pipeline.resolver import resolve_pipeline_steps, validate_required_fields

from app.api.deploy._shared import (
    _build_confirmation, _derive_servers, _find_config_service, _merge_release_variables,
    _service_topology, logger,
)
from app.deploy.schemas import DeployRequest, ResolutionPreviewRequest
from app.deploy.preflight import build_preflight_payload

plans_router = APIRouter(tags=["发布管理v2-计划"])


@plans_router.post("/resolve")
async def deploy_resolve_preview(request: Request, db: Session = Depends(get_db)):
    user = require_auth(request, db)
    data = await request.json()
    req = ResolutionPreviewRequest(**data)

    resolved_vars = _merge_release_variables(DeployRequest(
        system=req.system,
        service=req.service,
        environment=req.environment,
        pipeline_id=req.pipeline_id,
        variables=req.runtime_overrides,
    ), db)

    steps = req.steps or []
    effective_pipeline_id = req.pipeline_id
    if not steps and not effective_pipeline_id:
        svc = _find_config_service(req.system, req.service, req.environment)
        effective_pipeline_id = (svc or {}).get("pipeline_id", "")
    if not steps and effective_pipeline_id:
        pipe_repo = PipelineRepository(db)
        pipeline_obj = pipe_repo.get_by_id(effective_pipeline_id)
        if pipeline_obj:
            step_repo = PipelineStepRepository(db)
            db_steps = step_repo.list_by_pipeline(effective_pipeline_id)
            steps = [
                {"id": s.id, "name": s.name, "step_type": s.step_type,
                 "config": s.config or {}, "sort_order": s.sort_order}
                for s in sorted(db_steps, key=lambda x: x.sort_order)
            ]

    resolved_steps, trace = resolve_pipeline_steps(steps, resolved_vars)
    errors = validate_required_fields(resolved_steps)

    audit(
        "deploy.resolve.preview",
        "system",
        req.system,
        f"user={user.get('username')} service={req.service} env={req.environment} steps={len(steps)} errors={len(errors)}",
    )

    return api_response(data={
        "variables": resolved_vars,
        "resolved_steps": resolved_steps,
        "trace": trace,
        "errors": errors,
        "ready": len(errors) == 0,
    })


@plans_router.post("/confirmation")
async def deploy_confirmation(request: Request, db: Session = Depends(get_db)):
    user = require_auth(request, db)
    data = await request.json()
    req = DeployRequest(**data)
    confirmation = _build_confirmation(req, db, user)
    audit(
        "deploy.confirmation",
        "system",
        req.system,
        f"user={user.get('username')} service={req.service} env={req.environment} ready={confirmation.get('ready')} blockers={len(confirmation.get('blockers', []))}",
    )
    return api_response(data=confirmation)


@plans_router.get("/pipeline-bindings")
async def pipeline_binding_metadata(request: Request, db: Session = Depends(get_db)):
    require_auth(request, db)
    registry = get_variable_registry()
    step_fields_mapped = {}
    for st, fields in STEP_FIELD_BINDINGS.items():
        step_fields_mapped[st] = [
            {"field": b["field"], "var": b["var"], "label": b["label"],
             "var_info": next((v for v in registry if v["name"] == b["var"]), None)}
            for b in fields
        ]
    return api_response(data={
        "step_fields": step_fields_mapped,
        "variables": registry,
    })


@plans_router.post("/precheck")
@plans_router.post("/preflight")
async def deploy_precheck(request: Request, db: Session = Depends(get_db)):
    """发布前预检 — 委托给 precheck 模块的共享逻辑。"""
    from app.api.deploy.precheck import _collect_precheck_checks

    require_auth(request, db)
    data = await request.json()
    system = data.get("system", "")
    servers_raw = data.get("servers", [])
    file_name = data.get("file_name", "")
    environment = data.get("environment", "")
    service = data.get("service", "")
    variables = data.get("variables") or {}

    checks, ssh_fail, remote_disks, _ssh_cache, topology, package_match = \
        _collect_precheck_checks(
            system, service, environment, file_name,
            servers_raw, variables, data.get("server_group", ""), db
        )

    pkg = package_match.get("package", {})
    servers_list = [
        str(x) if isinstance(x, str) else str((x or {}).get("name") or "")
        for x in (servers_raw or [])
    ]

    req_for_confirm = DeployRequest(
        system=system, service=service, environment=environment,
        file_name=file_name, servers=servers_list,
        server_group=data.get("server_group", ""), variables=variables,
    )
    confirmation = _build_confirmation(req_for_confirm, db, {})

    return api_response(data=build_preflight_payload(
        checks=checks,
        environment=environment,
        file_name=file_name,
        ssh_fail=ssh_fail,
        servers=servers_list,
        remote_disks=remote_disks,
        topology=topology,
        package=pkg,
        required_confirmation=confirmation.get("required_confirmation") or "",
        requires_confirmation=bool(confirmation.get("requires_confirmation")),
    ))


@plans_router.get("/tool-plans")
async def list_release_tool_plans(
    request: Request,
    plan_type: str = Query("", description="deploy / rollback"),
    status: str = "",
    system: str = "",
    service: str = "",
    environment: str = "",
    limit: int = 50,
    db: Session = Depends(get_db),
):
    require_auth(request, db)
    from app.services.release_plan import list_release_plans

    payload = list_release_plans(
        db,
        plan_type=plan_type,
        status=status,
        system=system,
        service=service,
        environment=environment,
        limit=limit,
    )
    return api_response(data=payload)


@plans_router.get("/tool-plans/{plan_id}/runbook")
async def get_release_tool_plan_runbook(
    plan_id: str,
    request: Request,
    include_events: bool = True,
    db: Session = Depends(get_db),
):
    require_auth(request, db)
    from app.services.release_plan import get_release_plan, release_runbook

    plan = get_release_plan(db, plan_id)
    return api_response(data=release_runbook(plan, include_events=include_events, db=db))
