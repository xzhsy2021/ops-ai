"""发布管理 API v2 - Pipeline + SQLite 日志 + asyncio 后台任务"""
import os
import json
import hashlib
import re
import shlex
import uuid
import asyncio
import logging
import time
import threading
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional
from fastapi import APIRouter, HTTPException, Request, Depends, UploadFile, File, Query
from app.api.helpers import api_response, audit
from app.pipeline import PipelineEngine
from app.db import get_db, DeployTaskRepository, DeployLogRepository, DeploymentRepository, PipelineRepository, PipelineStepRepository, DeploymentRuntimeRepository
from app.db.models import Pipeline, PipelineStep, DeployTask, Deployment, NotificationEvent
from app.db.base import SessionLocal
from app.core.auth_v2 import require_auth, require_deploy, require_deploy_for_env, normalize_role, ROLE_ORDER
from app.core.rbac import require_confirmed_high_risk, explain_operation_risk
from app.core.deploy_lock import DeployLock
from app.core import config as AppConfig

from sqlalchemy.orm import Session
from sqlalchemy import text
from app.domain.inventory import inventory
from app.deploy.rollback_health import build_rollback_health_commands
from app.deploy.rollback import RollbackRuntime
from app.deploy.worker import AsyncWorkerHandle
from app.deploy.report import deployment_report_payload, deployment_report_markdown as build_deployment_report_markdown
from app.deploy.history import deployment_list_payload
from app.deploy.logs import deployment_logs_payload, deployment_tasks_payload
from app.deploy.state import task_cancel_requested, normalize_status
from app.deploy.locks import acquire_deployment_locks, release_deployment_locks, list_deployment_locks_payload
from app.deploy.preflight import build_preflight_payload
from app.deploy.schemas import (
    BatchUpdateRequest, DeployRequest, PipelineCreateRequest, PipelineUpdateRequest,
    ResolutionPreviewRequest, StepCreateRequest, StepUpdateRequest,
    SystemEnvironmentPayload, SystemGroupPayload, SystemServicePayload,
)

from app.api.deploy.plans import plans_router
from app.api.deploy.executions import exec_router
from app.api.deploy.history import history_router
from app.api.deploy.precheck import precheck_router
from app.api.deploy._shared import (
    _clean_str_list, _json_safe, _connect_ssh,
    _normalize_service_payload, _config_service_to_response,
    _find_service_index, _find_config_service, _package_service_match, _env_alias,
)
from app.api.deploy._shared import *  # noqa: F401,F403 -- backward compatibility

logger = logging.getLogger(__name__)
deploy_v2_router = APIRouter(prefix="/api/v2/deploy", tags=["发布管理v2"])

deploy_v2_router.include_router(plans_router)
deploy_v2_router.include_router(exec_router)
deploy_v2_router.include_router(history_router)
deploy_v2_router.include_router(precheck_router, prefix="/precheck")


# ==================== Pipeline CRUD ====================

pipeline_v2_router = APIRouter(prefix="/api/v2/pipelines", tags=["Pipeline管理v2"])



PIPELINE_TEMPLATES = [
    {
        "id": "standard_web",
        "name": "标准 Web 发布",
        "strategy": "DIRECT",
        "description": "上传制品、解压部署、重启服务、健康检查",
        "steps": [
            {"type": "upload", "name": "上传制品", "config": {"local_path": "${artifact_path}", "remote_path": "${deploy_path}/packages"}},
            {"type": "deploy", "name": "解压部署", "config": {"deploy_path": "${deploy_path}", "package_path": "${artifact_path}"}},
            {"type": "restart", "name": "重启服务", "config": {"cmd": "${restart_command}"}},
            {"type": "health_check", "name": "健康检查", "config": {"url": "${health_url}", "retries": "5"}},
        ],
    },
    {
        "id": "command_only",
        "name": "命令式发布",
        "strategy": "DIRECT",
        "description": "适合已有远程脚本的轻量发布流程",
        "steps": [
            {"type": "command", "name": "执行发布脚本", "config": {"cmd": "cd ${deploy_path} && ./deploy.sh ${version}", "timeout": "600"}},
            {"type": "health_check", "name": "健康检查", "config": {"url": "${health_url}", "retries": "3"}},
        ],
    },
    {
        "id": "blue_green",
        "name": "蓝绿发布",
        "strategy": "BLUE_GREEN",
        "description": "部署到备用目录，健康检查通过后切换 current 链接",
        "steps": [
            {"type": "upload", "name": "上传制品", "config": {"local_path": "${artifact_path}", "remote_path": "${deploy_path}/releases"}},
            {"type": "deploy", "name": "部署到备用版本", "config": {"deploy_path": "${deploy_path}/releases/${version}", "package_path": "${artifact_path}"}},
            {"type": "health_check", "name": "备用版本检查", "config": {"url": "${health_url}", "retries": "5"}},
            {"type": "switch", "name": "切换流量", "config": {"target": "${deploy_path}/releases/${version}", "link": "${deploy_path}/current"}},
            {"type": "restart", "name": "重启服务", "config": {"cmd": "${restart_command}"}},
        ],
    },
    {
        "id": "dovo_actual_bluegreen",
        "name": "Dovo 分组蓝绿发布",
        "strategy": "DOVO_BLUE_GREEN",
        "description": "按实际 runbook：识别未运行旧程序 → 上传/更新旧程序 → binupdate.sh → 日志检查 → portupdate.sh → 二次日志检查",
        "steps": [
            {"type": "dovo_bluegreen_update", "name": "Dovo 分组蓝绿发布", "config": {"group_code": "${service}", "base_path": "${base_path}", "instances": "${instances}", "file_name": "${file_name}", "update_script": "${update_script}", "switch_script": "${switch_script}", "wait_after_update": "${wait_after_update}"}},
        ],
    },
    {
        "id": "crypto_updatebin_backend",
        "name": "量化后台 updatebin 发布",
        "strategy": "SCRIPTED_BACKEND",
        "description": "按实际 runbook：上传服务包到服务目录 → 执行 updatebin.sh → 检查程序与日志；支持同一服务选择多台服务器执行",
        "steps": [
            {"type": "scripted_service_update", "name": "后台服务 updatebin 发布", "config": {"service_dir": "${service_dir}", "file_name": "${file_name}", "update_script": "${update_script}", "log_dir": "logs", "timeout": "600"}},
        ],
    },
    {
        "id": "crypto_docker_compose",
        "name": "量化 Docker Compose 发布",
        "strategy": "DOCKER_COMPOSE",
        "description": "拉取最新镜像 → docker compose up -d → 检查容器状态与日志",
        "steps": [
            {"type": "docker_compose_update", "name": "Docker Compose 发布", "config": {"compose_dir": "${compose_dir}", "compose_file": "${compose_file}", "timeout": "600", "wait_after_up": 10, "log_tail_lines": 30}},
        ],
    },
    {
        "id": "web_www_script",
        "name": "Web /data/www 发布",
        "strategy": "WEB_SCRIPT",
        "description": "按实际 runbook：上传本地包到 /data/www → 在 /data/www 执行 ./www.sh",
        "steps": [
            {"type": "web_script_update", "name": "Web 静态包发布", "config": {"deploy_path": "${deploy_path}", "file_name": "${file_name}", "update_script": "${update_script}", "timeout": "600"}},
        ],
    },
]


@pipeline_v2_router.get("/templates")
async def list_pipeline_templates(request: Request, db: Session = Depends(get_db)):
    require_auth(request, db)
    return api_response(data=PIPELINE_TEMPLATES)


@pipeline_v2_router.post("/from-template")
async def create_pipeline_from_template(request: Request, db: Session = Depends(get_db)):
    require_auth(request, db)
    data = await request.json()
    template_id = data.get("template_id")
    system_name = data.get("system_name") or "default"
    name = data.get("name")
    tpl = next((t for t in PIPELINE_TEMPLATES if t["id"] == template_id), None)
    if not tpl:
        raise HTTPException(status_code=404, detail="Template not found")
    repo = PipelineRepository(db)
    pipeline = repo.create(
        name=name or tpl["name"],
        system_name=system_name,
        description=data.get("description") or tpl["description"],
        strategy=data.get("strategy") or tpl["strategy"],
    )
    step_repo = PipelineStepRepository(db)
    for i, step_data in enumerate(tpl["steps"]):
        step_repo.create(
            pipeline_id=pipeline.id,
            name=step_data["name"],
            step_type=step_data["type"],
            config=step_data.get("config", {}),
            sort_order=i,
        )
    db.commit()
    return api_response(data={"id": pipeline.id, "name": pipeline.name}, message="Pipeline created from template")


@pipeline_v2_router.get("")
async def list_pipelines(request: Request, system_name: str = "", db: Session = Depends(get_db)):
    require_auth(request, db)
    repo = PipelineRepository(db)
    if system_name:
        pipelines = repo.list_by_system(system_name)
    else:
        pipelines = repo.list_all()
    return api_response(data=[
        {"id": p.id, "name": p.name, "system_name": p.system_name,
         "description": p.description, "strategy": p.strategy,
         "created_at": p.created_at.isoformat() if p.created_at else None}
        for p in pipelines
    ])


@pipeline_v2_router.post("")
async def create_pipeline(request: Request, db: Session = Depends(get_db)):
    require_auth(request, db)
    data = await request.json()
    req = PipelineCreateRequest(**data)

    repo = PipelineRepository(db)
    pipeline = repo.create(
        name=req.name,
        system_name=req.system_name,
        description=req.description,
        strategy=req.strategy,
    )

    step_repo = PipelineStepRepository(db)
    for i, step_data in enumerate(req.steps):
        step_repo.create(
            pipeline_id=pipeline.id,
            name=step_data.get("name", f"step_{i}"),
            step_type=step_data.get("type", "command"),
            config=step_data.get("config", {}),
            sort_order=i,
        )
    db.commit()

    return api_response(data={"id": pipeline.id, "name": pipeline.name}, message="Pipeline created")


@pipeline_v2_router.get("/{pipeline_id}")
async def get_pipeline(pipeline_id: str, request: Request, db: Session = Depends(get_db)):
    require_auth(request, db)
    repo = PipelineRepository(db)
    pipeline = repo.get_by_id(pipeline_id)
    if not pipeline:
        raise HTTPException(status_code=404, detail="Pipeline not found")

    step_repo = PipelineStepRepository(db)
    steps = step_repo.list_by_pipeline(pipeline_id)

    return api_response(data={
        "id": pipeline.id,
        "name": pipeline.name,
        "system_name": pipeline.system_name,
        "description": pipeline.description,
        "strategy": pipeline.strategy,
        "steps": [
            {"id": s.id, "name": s.name, "step_type": s.step_type,
             "config": s.config, "sort_order": s.sort_order}
            for s in steps
        ],
    })



@pipeline_v2_router.put("/batch/update")
async def batch_update_pipelines(req: BatchUpdateRequest, request: Request, db: Session = Depends(get_db)):
    require_auth(request, db)
    repo = PipelineRepository(db)
    success = 0
    failed = 0
    errors: List[Dict[str, str]] = []

    for pid in req.ids:
        try:
            p = repo.get_by_id(pid)
            if not p:
                failed += 1
                errors.append({"id": pid, "error": "not found"})
                continue
            if req.updates.name is not None:
                p.name = req.updates.name
            if req.updates.system_name is not None:
                p.system_name = req.updates.system_name
            if req.updates.description is not None:
                p.description = req.updates.description
            if req.updates.strategy is not None:
                p.strategy = req.updates.strategy
            repo.update(p)
            success += 1
        except Exception as e:
            failed += 1
            errors.append({"id": pid, "error": str(e)})

    return api_response(data={"success": success, "failed": failed, "errors": errors}, message="Batch update completed")


@pipeline_v2_router.put("/{pipeline_id}")
async def update_pipeline(pipeline_id: str, req: PipelineUpdateRequest, request: Request, db: Session = Depends(get_db)):
    require_auth(request, db)
    repo = PipelineRepository(db)
    p = repo.get_by_id(pipeline_id)
    if not p:
        raise HTTPException(status_code=404, detail="Pipeline not found")

    if req.name is not None:
        p.name = req.name
    if req.system_name is not None:
        p.system_name = req.system_name
    if req.description is not None:
        p.description = req.description
    if req.strategy is not None:
        p.strategy = req.strategy
    repo.update(p)

    return api_response(data={"id": p.id}, message="Pipeline updated")


@pipeline_v2_router.delete("/{pipeline_id}")
async def delete_pipeline(pipeline_id: str, request: Request, db: Session = Depends(get_db)):
    require_auth(request, db)
    repo = PipelineRepository(db)
    if not repo.delete(pipeline_id):
        raise HTTPException(status_code=404, detail="Pipeline not found")
    return api_response(message="Pipeline deleted")


@pipeline_v2_router.post("/{pipeline_id}/duplicate")
async def duplicate_pipeline(pipeline_id: str, request: Request, db: Session = Depends(get_db)):
    """复制一个已有的 Pipeline（含步骤）"""
    require_auth(request, db)
    body = await request.json()
    new_name = body.get("new_name", "")

    pipeline_repo = PipelineRepository(db)
    original = pipeline_repo.get_by_id(pipeline_id)
    if not original:
        raise HTTPException(status_code=404, detail="Pipeline not found")

    new_pipeline = Pipeline(
        id=uuid.uuid4().hex,
        name=new_name or f"{original.name} (副本)",
        system_name=original.system_name,
        description=original.description,
        strategy=original.strategy,
    )
    db.add(new_pipeline)
    db.flush()

    for step in original.steps:
        new_step = PipelineStep(
            id=uuid.uuid4().hex,
            pipeline_id=new_pipeline.id,
            name=step.name,
            step_type=step.step_type,
            config=dict(step.config or {}),
            sort_order=step.sort_order,
        )
        db.add(new_step)

    db.commit()
    db.refresh(new_pipeline)
    return api_response(data={
        "id": new_pipeline.id,
        "name": new_pipeline.name,
        "system_name": new_pipeline.system_name,
    }, message="Pipeline duplicated")


@pipeline_v2_router.post("/{pipeline_id}/steps")
async def add_step(pipeline_id: str, request: Request, db: Session = Depends(get_db)):
    require_auth(request, db)
    pipe_repo = PipelineRepository(db)
    pipeline = pipe_repo.get_by_id(pipeline_id)
    if not pipeline:
        raise HTTPException(status_code=404, detail="Pipeline not found")

    data = await request.json()
    step_repo = PipelineStepRepository(db)
    step = step_repo.create(
        pipeline_id=pipeline_id,
        name=data.get("name", "new step"),
        step_type=data.get("type", "command"),
        config=data.get("config", {}),
        sort_order=data.get("sort_order", 0),
    )
    return api_response(data={"id": step.id}, message="Step added")


@pipeline_v2_router.put("/{pipeline_id}/steps/reorder")
async def reorder_steps(pipeline_id: str, request: Request, db: Session = Depends(get_db)):
    require_auth(request, db)
    data = await request.json()
    step_ids = data.get("step_ids", [])
    step_repo = PipelineStepRepository(db)
    step_repo.reorder(pipeline_id, step_ids)
    return api_response(message="Steps reordered")


@pipeline_v2_router.put("/{pipeline_id}/steps/{step_id}")
async def update_step(pipeline_id: str, step_id: str, request: Request, db: Session = Depends(get_db)):
    require_auth(request, db)
    step_repo = PipelineStepRepository(db)
    step = step_repo.get_by_id(step_id)
    if not step or step.pipeline_id != pipeline_id:
        raise HTTPException(status_code=404, detail="Step not found")

    data = await request.json()
    if data.get("name"):
        step.name = data["name"]
    if data.get("type"):
        step.step_type = data["type"]
    if "config" in data:
        step.config = data["config"]
    if "sort_order" in data:
        step.sort_order = data["sort_order"]
    step_repo.update(step)

    return api_response(data={"id": step.id}, message="Step updated")


@pipeline_v2_router.delete("/{pipeline_id}/steps/{step_id}")
async def delete_step(pipeline_id: str, step_id: str, request: Request, db: Session = Depends(get_db)):
    require_auth(request, db)
    step_repo = PipelineStepRepository(db)
    step = step_repo.get_by_id(step_id)
    if not step or step.pipeline_id != pipeline_id:
        raise HTTPException(status_code=404, detail="Step not found")
    step_repo.delete(step_id)
    return api_response(message="Step deleted")


# ==================== Resource Listing API ====================

resource_v2_router = APIRouter(prefix="/api/v2", tags=["资源管理v2"])


@resource_v2_router.get("/systems")
async def list_systems_v2(request: Request, db: Session = Depends(get_db)):
    from app.db import ServiceRepository
    from app.db.repository import SystemEnvironmentRepository, SystemRepository

    systems = SystemRepository(db).list_all()
    service_repo = ServiceRepository(db)
    environment_repo = SystemEnvironmentRepository(db)
    return api_response(data=[
        {
            "name": system.name,
            "display_name": system.display_name or system.name,
            "strategy": system.strategy or "DIRECT",
            "environment_count": len(environment_repo.list_by_system(system.name)),
            "service_count": len(service_repo.list_by_system(system.name)),
        }
        for system in systems
    ])


@resource_v2_router.get("/systems/{system_name}")
async def get_system_v2(system_name: str, request: Request):
    from app.config.systems import get_system_by_name
    system = get_system_by_name(system_name)
    if not system:
        raise HTTPException(status_code=404, detail=f"System not found: {system_name}")
    svcs = system.get("services", []) or []
    envs = system.get("environments", {}) or {}
    groups = system.get("groups") or system.get("regions") or {}
    # qclaw Element 消息路由配置（透传，可能不存在）
    routing = system.get("message_routing") or {}
    return api_response(data={
        "name": system_name,
        "display_name": system.get("display_name", system_name),
        "strategy": system.get("strategy", "DIRECT"),
        "description": system.get("description", ""),
        "services": svcs,
        "environments": {k: {"name": k, "display_name": v.get("display_name", k), "category": v.get("category", ""), "servers": v.get("servers", []), "variables": v.get("variables", {})} for k, v in envs.items()},
        "groups": groups,
        "variables": system.get("variables", {}),
        "servers": system.get("servers", []),
        "message_routing": {
            "enabled": bool(routing.get("enabled", False)),
            "aliases": list(routing.get("aliases", []) or []),
            "keywords": list(routing.get("keywords", []) or []),
            "priority": int(routing.get("priority", 0) or 0),
        },
        "environment_count": len(envs),
        "service_count": len(svcs),
        "group_count": len(groups),
    })


@resource_v2_router.post("/systems")
async def create_system_v2(payload: Dict[str, Any], request: Request, db: Session = Depends(get_db)):
    from app.config.systems import save_system, get_system_by_name
    user = require_auth(request, db)
    if not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin permission required")
    name = str(payload.get("name", "")).strip()
    if not name:
        raise HTTPException(status_code=400, detail="System name is required")
    if not re.match(r"^[a-zA-Z0-9_.-]+$", name):
        raise HTTPException(status_code=400, detail="System name can only contain letters, digits, underscore, dot, and dash")
    if get_system_by_name(name):
        raise HTTPException(status_code=409, detail=f"System already exists: {name}")
    system = {
        "display_name": str(payload.get("display_name", name)).strip(),
        "strategy": str(payload.get("strategy", "DIRECT")).strip() or "DIRECT",
        "description": str(payload.get("description", "")).strip(),
        "services": payload.get("services", []) or [],
        "environments": payload.get("environments", {}) or {},
        "variables": payload.get("variables", {}) or {},
        "servers": payload.get("servers", []) or [],
    }
    # qclaw Element 消息路由配置（可选）
    routing = payload.get("message_routing")
    if isinstance(routing, dict):
        system["message_routing"] = {
            "enabled": bool(routing.get("enabled", False)),
            "aliases": [str(a).strip() for a in routing.get("aliases", []) if str(a).strip()],
            "keywords": [str(k).strip() for k in routing.get("keywords", []) if str(k).strip()],
            "priority": int(routing.get("priority", 0)),
            "approvers": [str(u).strip() for u in routing.get("approvers", []) if str(u).strip()],
        }
    groups = payload.get("groups") or payload.get("regions") or {}
    if isinstance(groups, dict) and groups:
        system["groups"] = groups
    if not save_system(name, system):
        raise HTTPException(status_code=500, detail="Failed to save system configuration")
    audit("system.create", "system", name, getattr(request.state, "username", ""))
    return api_response(data={"name": name, "display_name": system["display_name"]}, message="System created")


@resource_v2_router.put("/systems/{system_name}")
async def update_system_v2(system_name: str, payload: Dict[str, Any], request: Request, db: Session = Depends(get_db)):
    from app.config.systems import save_system, get_system_by_name
    user = require_auth(request, db)
    if not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin permission required")
    system = get_system_by_name(system_name)
    if not system:
        raise HTTPException(status_code=404, detail=f"System not found: {system_name}")
    system = dict(system)
    if "display_name" in payload:
        system["display_name"] = str(payload["display_name"]).strip()
    if "strategy" in payload:
        system["strategy"] = str(payload["strategy"]).strip() or system.get("strategy", "DIRECT")
    if "description" in payload:
        system["description"] = str(payload["description"]).strip()
    if "variables" in payload:
        system["variables"] = payload["variables"] if isinstance(payload["variables"], dict) else {}
    if "servers" in payload:
        system["servers"] = payload["servers"] if isinstance(payload["servers"], list) else []
    if "message_routing" in payload:
        # qclaw Element 消息路由配置：{enabled, aliases, keywords, priority}
        routing = payload["message_routing"]
        if isinstance(routing, dict):
            system["message_routing"] = {
                "enabled": bool(routing.get("enabled", False)),
                "aliases": [str(a).strip() for a in routing.get("aliases", []) if str(a).strip()],
                "keywords": [str(k).strip() for k in routing.get("keywords", []) if str(k).strip()],
                "priority": int(routing.get("priority", 0)),
            }
        else:
            system.pop("message_routing", None)
    if "services" in payload:
        system["services"] = payload["services"] if isinstance(payload["services"], list) else system.get("services", [])
    if "environments" in payload:
        system["environments"] = payload["environments"] if isinstance(payload["environments"], dict) else system.get("environments", {})
    groups = payload.get("groups") or payload.get("regions")
    if groups is not None:
        if isinstance(groups, dict):
            system["groups"] = groups
        else:
            system.pop("groups", None)
            system.pop("regions", None)
    if not save_system(system_name, system):
        raise HTTPException(status_code=500, detail="Failed to save system configuration")
    audit("system.update", "system", system_name, getattr(request.state, "username", ""))
    return api_response(data={"name": system_name, "display_name": system.get("display_name", system_name)}, message="System updated")


@resource_v2_router.delete("/systems/{system_name}")
async def delete_system_v2(system_name: str, request: Request, db: Session = Depends(get_db)):
    from app.config.systems import delete_system, get_system_by_name
    user = require_auth(request, db)
    if not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin permission required")
    if not get_system_by_name(system_name):
        raise HTTPException(status_code=404, detail=f"System not found: {system_name}")
    from app.deploy.state import TERMINAL_STATUSES
    from app.db.models import Deployment
    active_deployments = db.query(Deployment).filter(
        Deployment.system == system_name,
        Deployment.status.notin_(TERMINAL_STATUSES)
    ).count()
    if active_deployments > 0:
        raise HTTPException(
            status_code=409,
            detail=f"System '{system_name}' has {active_deployments} active deployment(s). Please wait for them to complete before deleting.",
        )
    if not delete_system(system_name):
        raise HTTPException(status_code=500, detail="Failed to delete system configuration")
    audit("system.delete", "system", system_name, getattr(request.state, "username", ""))
    return api_response(data={"name": system_name}, message="System deleted")


@resource_v2_router.get("/services")
async def list_services_v2(system: str = "", environment: str = "", db: Session = Depends(get_db)):
    from app.db import ServiceRepository
    from app.db.repository import ServerGroupRepository, SystemEnvironmentRepository, SystemRepository

    result: List[Dict[str, Any]] = []
    seen = set()

    def add_service(item: Dict[str, Any]):
        key = (item.get("system_name", ""), item.get("name", ""))
        if not key[1] or key in seen:
            return
        seen.add(key)
        result.append(item)

    repo = ServiceRepository(db)
    environment_cfg: Dict[str, Any] = {}
    if system and environment:
        environment_row = SystemEnvironmentRepository(db).get_by_name(system, environment)
        if environment_row is not None:
            environment_cfg = {
                "service_overrides": environment_row.service_overrides or {},
                "servers": environment_row.servers or [],
            }
    db_services = repo.list_by_system(system) if system else repo.list_all()
    for s in db_services:
        override = (environment_cfg.get("service_overrides") or {}).get(s.name, {})
        template_variables = dict(s.template_variables or {})
        if isinstance(override, dict):
            template_variables.update(override.get("template_variables") or {})
        add_service({
            "id": s.id,
            "name": s.name,
            "display_name": s.display_name or s.name,
            "system_name": s.system_name,
            "repo": override.get("repo", s.repo) if isinstance(override, dict) else s.repo,
            "template": override.get("template", s.template) if isinstance(override, dict) else s.template,
            "pipeline_id": override.get("pipeline_id", s.pipeline_id or "") if isinstance(override, dict) else s.pipeline_id or "",
            "template_variables": template_variables,
            "servers": (override.get("servers") if isinstance(override, dict) else None) or s.servers or environment_cfg.get("servers") or [],
            "servers_by_env": template_variables.get("servers_by_env", {}),
            "server_keywords": template_variables.get("server_keywords", []),
            "source": "db",
        })

    system_names = {row.name for row in SystemRepository(db).list_all()}
    for group in ServerGroupRepository(db).list_all():
        matching_system = next(
            (name for name in system_names if group.name.startswith(f"{name}-")),
            "",
        )
        if not matching_system or (system and matching_system != system):
            continue
        group_code = group.name[len(matching_system) + 1:]
        metadata = group.metadata_json or {}
        add_service({
                "id": f"group:{matching_system}:{group_code}",
                "name": group_code,
                "display_name": group.display_name or group_code.upper(),
                "system_name": matching_system,
                "repo": "",
                "template": "dovo_bluegreen_update" if matching_system == "dovo" else "grouped_release",
                "template_variables": {**metadata, "group_code": group_code},
                "servers": list(group.server_names or []),
                "servers_by_env": metadata.get("servers_by_env", {}),
                "server_keywords": metadata.get("server_keywords", []),
                "source": "database_group",
            })

    return api_response(data=result)


@resource_v2_router.get("/systems/{system_name}/services")
async def list_system_services_v2(system_name: str, request: Request, db: Session = Depends(get_db)):
    require_auth(request, db)
    from app.db import ServiceRepository

    repo = ServiceRepository(db)
    services = repo.list_by_system(system_name)
    return api_response(data=[
        _db_service_to_response(s)
        for s in services
    ])


@resource_v2_router.post("/systems/{system_name}/services")
async def create_system_service_v2(system_name: str, payload: SystemServicePayload, request: Request, db: Session = Depends(get_db)):
    user = require_auth(request, db)
    from app.db import ServiceRepository

    if not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin permission required")
    new_service = _normalize_service_payload(payload)
    repo = ServiceRepository(db)
    if repo.get_by_name(new_service["name"], system_name):
        raise HTTPException(status_code=409, detail=f"Service already exists: {new_service['name']}")
    created = repo.create(
        name=new_service["name"],
        system_name=system_name,
        display_name=new_service.get("display_name") or new_service["name"],
        repo=new_service.get("repo") or "",
        template=new_service.get("template") or "generic_backend_direct",
        pipeline_id=new_service.get("pipeline_id") or "",
        template_variables=new_service.get("template_variables") or {},
        servers=new_service.get("servers") or [],
    )
    audit("system.service.create", "service", f"{system_name}/{created.name}", getattr(request.state, "username", ""))
    return api_response(data=_db_service_to_response(created), message="Service created")


@resource_v2_router.put("/systems/{system_name}/services/{service_name}")
async def update_system_service_v2(system_name: str, service_name: str, payload: SystemServicePayload, request: Request, db: Session = Depends(get_db)):
    user = require_auth(request, db)
    from app.db import ServiceRepository

    if not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin permission required")
    repo = ServiceRepository(db)
    existing = repo.get_by_name(service_name, system_name)
    if not existing:
        raise HTTPException(status_code=404, detail=f"Service not found: {service_name}")
    updated_payload = _normalize_service_payload(payload)
    new_name = updated_payload["name"]
    if new_name != service_name:
        if repo.get_by_name(new_name, system_name):
            raise HTTPException(status_code=409, detail=f"Service already exists: {new_name}")
        existing.name = new_name
    existing.display_name = updated_payload.get("display_name") or new_name
    existing.template = updated_payload.get("template") or existing.template
    existing.repo = updated_payload.get("repo") or existing.repo
    existing.pipeline_id = updated_payload.get("pipeline_id") or existing.pipeline_id
    existing.servers = updated_payload.get("servers") or existing.servers
    existing.template_variables = updated_payload.get("template_variables") or existing.template_variables
    repo.update(existing)
    audit("system.service.update", "service", f"{system_name}/{service_name}", getattr(request.state, "username", ""))
    return api_response(data=_db_service_to_response(existing), message="Service updated")


@resource_v2_router.delete("/systems/{system_name}/services/{service_name}")
async def delete_system_service_v2(system_name: str, service_name: str, request: Request, db: Session = Depends(get_db)):
    user = require_auth(request, db)
    from app.db import ServiceRepository

    if not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin permission required")
    repo = ServiceRepository(db)
    existing = repo.get_by_name(service_name, system_name)
    if not existing:
        raise HTTPException(status_code=404, detail=f"Service not found: {service_name}")
    try:
        from app.db.models import Deployment
        dep_count = db.query(Deployment).filter(
            Deployment.system == system_name,
            Deployment.service == service_name,
            Deployment.status.in_(["running", "pending", "queued"]),
        ).count()
        if dep_count > 0:
            raise HTTPException(
                status_code=409,
                detail=f"Service '{service_name}' has {dep_count} active deployment(s). Please wait for them to complete or cancel them first.",
            )
    except HTTPException:
        raise
    except Exception as _e:
        logger.warning("Failed to check active deployments for service %s/%s: %s", system_name, service_name, _e)
    if not repo.delete(existing.id):
        raise HTTPException(status_code=500, detail="Failed to delete service")
    audit("system.service.delete", "service", f"{system_name}/{service_name}", getattr(request.state, "username", ""))
    return api_response(data={"name": existing.name}, message="Service deleted")


def _normalize_env_name(name: str) -> str:
    value = str(name or "").strip()
    if not value:
        raise HTTPException(status_code=400, detail="Environment name is required")
    if len(value) > 64:
        raise HTTPException(status_code=400, detail="Environment name is too long")
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-.")
    if any(ch not in allowed for ch in value):
        raise HTTPException(status_code=400, detail="环境标识仅支持英文、数字、下划线、短横线和点号；显示名称可使用中文")
    return value


def _env_response(env_name: str, env_cfg: Dict[str, Any], source_type: str) -> Dict[str, Any]:
    variables = env_cfg.get("variables", {}) if isinstance(env_cfg, dict) else {}
    return {
        "id": env_name,
        "name": env_name,
        "display_name": (env_cfg.get("display_name") if isinstance(env_cfg, dict) else "") or env_name,
        "type": source_type,
        "category": (env_cfg.get("category") if isinstance(env_cfg, dict) else "") or variables.get("category", ""),
        "description": (env_cfg.get("description") if isinstance(env_cfg, dict) else "") or "",
        "variables": variables,
        "servers": env_cfg.get("servers", []) if isinstance(env_cfg, dict) else [],
        "deploy_path": env_cfg.get("base_path", env_cfg.get("deploy_path", "")) if isinstance(env_cfg, dict) else "",
        "health_url": variables.get("health_url", "") if isinstance(variables, dict) else "",
    }


def _default_environment_records() -> List[Dict[str, Any]]:
    return [
        {
            "id": "test",
            "name": "test",
            "display_name": "测试环境",
            "type": "default",
            "category": "test",
            "description": "默认测试场景",
            "variables": {"environment": "test", "category": "test"},
            "servers": [],
            "deploy_path": "",
            "health_url": "",
        },
        {
            "id": "prod",
            "name": "prod",
            "display_name": "生产环境",
            "type": "default",
            "category": "prod",
            "description": "默认生产场景",
            "variables": {"environment": "prod", "category": "prod"},
            "servers": [],
            "deploy_path": "",
            "health_url": "",
        },
    ]


@resource_v2_router.get("/environments")
async def list_environments_v2(system: str = "", db: Session = Depends(get_db)):
    from app.db import EnvironmentRepository
    from app.db import ServiceRepository
    from app.db.repository import SystemEnvironmentRepository

    result: List[Dict[str, Any]] = []

    def add_env(item: Dict[str, Any]):
        if not item.get("name"):
            return
        if any(e.get("name") == item.get("name") for e in result):
            return
        result.append(item)

    db_repo = EnvironmentRepository(db)
    db_envs = db_repo.list_all()
    for e in db_envs:
        variables = e.variables or {}
        add_env({
            "id": e.id,
            "name": e.name,
            "display_name": variables.get("display_name") or e.name,
            "type": "global",
            "category": variables.get("category", ""),
            "description": variables.get("description", ""),
            "variables": variables,
        })

    system_environments = (
        SystemEnvironmentRepository(db).list_by_system(system)
        if system else SystemEnvironmentRepository(db).list_all()
    )
    for row in system_environments:
        add_env(_env_response(row.name, {
            "display_name": row.display_name,
            "category": row.category,
            "description": row.description,
            "base_path": row.base_path,
            "servers": row.servers or [],
            "variables": row.variables or {},
        }, "system"))

    if system:
        # Service mappings can define an environment before a dedicated row is created.
        for svc in ServiceRepository(db).list_by_system(system):
            tv = svc.template_variables or {}
            env_map = tv.get("servers_by_env") or {}
            if isinstance(env_map, dict):
                for env_name in env_map.keys():
                    if env_name:
                        add_env({
                            "id": str(env_name),
                            "name": str(env_name),
                            "display_name": str(env_name),
                            "type": "service",
                            "category": _env_alias(str(env_name)) if _env_alias(str(env_name)) in {"test", "prod"} else "custom",
                            "description": "由服务环境服务器映射自动发现",
                            "variables": {"environment": str(env_name)},
                            "servers": [],
                            "deploy_path": "",
                            "health_url": "",
                        })

    for env in _default_environment_records():
        if not any(e.get("name") == env["name"] for e in result):
            result.insert(0, env)

    return api_response(data=result)



def _normalize_environment_payload(payload: SystemEnvironmentPayload, existing: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    category = str(payload.category or "custom").strip() or "custom"
    variables = dict(existing.get("variables", {}) if isinstance(existing, dict) else {})
    variables.update(payload.variables or {})
    variables["environment"] = _normalize_env_name(payload.name)
    variables["category"] = category
    if payload.display_name.strip():
        variables["display_name"] = payload.display_name.strip()
    return {
        "display_name": payload.display_name.strip() or payload.name.strip(),
        "category": category,
        "description": payload.description.strip(),
        "base_path": payload.base_path.strip() or (existing or {}).get("base_path", ""),
        "servers": [str(x).strip() for x in (payload.servers or []) if str(x).strip()],
        "variables": variables,
    }


@resource_v2_router.post("/systems/{system_name}/environments")
async def create_system_environment_v2(system_name: str, payload: SystemEnvironmentPayload, request: Request, db: Session = Depends(get_db)):
    user = require_auth(request, db)
    from app.db.repository import SystemEnvironmentRepository, SystemRepository

    if not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin permission required")
    env_name = _normalize_env_name(payload.name)
    if SystemRepository(db).get_by_name(system_name) is None:
        raise HTTPException(status_code=404, detail=f"System not found: {system_name}")
    repo = SystemEnvironmentRepository(db)
    if repo.get_by_name(system_name, env_name) is not None:
        raise HTTPException(status_code=409, detail=f"Environment already exists: {env_name}")
    env_cfg = _normalize_environment_payload(payload)
    repo.create(
        system_name=system_name,
        name=env_name,
        display_name=env_cfg["display_name"],
        category=env_cfg["category"],
        description=env_cfg["description"],
        base_path=env_cfg["base_path"],
        servers=env_cfg["servers"],
        variables=env_cfg["variables"],
    )
    db.commit()
    audit("system.environment.create", "environment", f"{system_name}/{env_name}", getattr(request.state, "username", ""))
    return api_response(data=_env_response(env_name, env_cfg, "system"), message="Environment created")


@resource_v2_router.put("/systems/{system_name}/environments/{env_name}")
async def update_system_environment_v2(system_name: str, env_name: str, payload: SystemEnvironmentPayload, request: Request, db: Session = Depends(get_db)):
    user = require_auth(request, db)
    from app.db.repository import SystemEnvironmentRepository, SystemRepository

    if not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin permission required")
    current_name = _normalize_env_name(env_name)
    new_name = _normalize_env_name(payload.name or env_name)
    if SystemRepository(db).get_by_name(system_name) is None:
        raise HTTPException(status_code=404, detail=f"System not found: {system_name}")
    repo = SystemEnvironmentRepository(db)
    existing_row = repo.get_by_name(system_name, current_name)
    if existing_row is None:
        raise HTTPException(status_code=404, detail=f"Environment not found: {current_name}")
    if new_name != current_name and repo.get_by_name(system_name, new_name) is not None:
        raise HTTPException(status_code=409, detail=f"Environment already exists: {new_name}")
    existing = {
        "display_name": existing_row.display_name,
        "category": existing_row.category,
        "description": existing_row.description,
        "base_path": existing_row.base_path,
        "servers": existing_row.servers or [],
        "variables": existing_row.variables or {},
    }
    env_cfg = _normalize_environment_payload(payload, existing)
    existing_row.name = new_name
    existing_row.display_name = env_cfg["display_name"]
    existing_row.category = env_cfg["category"]
    existing_row.description = env_cfg["description"]
    existing_row.base_path = env_cfg["base_path"]
    existing_row.servers = env_cfg["servers"]
    existing_row.variables = env_cfg["variables"]
    db.commit()
    audit("system.environment.update", "environment", f"{system_name}/{current_name}->{new_name}", getattr(request.state, "username", ""))
    return api_response(data=_env_response(new_name, env_cfg, "system"), message="Environment updated")


@resource_v2_router.delete("/systems/{system_name}/environments/{env_name}")
async def delete_system_environment_v2(system_name: str, env_name: str, request: Request, db: Session = Depends(get_db)):
    user = require_auth(request, db)
    from app.db.repository import SystemEnvironmentRepository, SystemRepository

    if not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin permission required")
    env_name = _normalize_env_name(env_name)
    if env_name in {"test", "prod"}:
        raise HTTPException(status_code=400, detail="Default environments test/prod cannot be deleted")
    if SystemRepository(db).get_by_name(system_name) is None:
        raise HTTPException(status_code=404, detail=f"System not found: {system_name}")
    repo = SystemEnvironmentRepository(db)
    row = repo.get_by_name(system_name, env_name)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Environment not found: {env_name}")
    try:
        from app.db.models import Deployment
        dep_count = db.query(Deployment).filter(
            Deployment.system == system_name,
            Deployment.environment == env_name,
            Deployment.status.in_(["running", "pending", "queued"]),
        ).count()
        if dep_count > 0:
            raise HTTPException(
                status_code=409,
                detail=f"Environment '{env_name}' has {dep_count} active deployment(s). Please wait for them to complete or cancel them first.",
            )
    except HTTPException:
        raise
    except Exception as _e:
        logger.warning("Failed to check active deployments for environment %s/%s: %s", system_name, env_name, _e)
    repo.delete(row.id)
    db.commit()
    audit("system.environment.delete", "environment", f"{system_name}/{env_name}", getattr(request.state, "username", ""))
    return api_response(data={"name": env_name}, message="Environment deleted")


@resource_v2_router.get("/systems/{system_name}/groups")
async def list_system_groups_v2(system_name: str, environment: str = "", request: Request = None):
    from app.config.systems import get_all_groups, get_system_by_name
    if not get_system_by_name(system_name):
        raise HTTPException(status_code=404, detail=f"System not found: {system_name}")
    groups = get_all_groups(system_name, environment=environment or "")
    return api_response(data={
        code: {
            "code": code,
            "display_name": cfg.get("display_name", code) if isinstance(cfg, dict) else str(cfg),
            "server": cfg.get("server", "") if isinstance(cfg, dict) else "",
            "servers": cfg.get("servers", []) if isinstance(cfg, dict) else [],
            "server_keywords": cfg.get("server_keywords", []) if isinstance(cfg, dict) else [],
            "servers_by_env": cfg.get("servers_by_env", {}) if isinstance(cfg, dict) else {},
            "variables": cfg.get("variables", {}) if isinstance(cfg, dict) else {},
        }
        for code, cfg in groups.items()
    })


@resource_v2_router.get("/systems/{system_name}/groups/{group_code}")
async def get_system_group_v2(system_name: str, group_code: str, request: Request = None):
    from app.config.systems import get_group, get_system_by_name
    if not get_system_by_name(system_name):
        raise HTTPException(status_code=404, detail=f"System not found: {system_name}")
    cfg = get_group(system_name, group_code)
    if not cfg:
        raise HTTPException(status_code=404, detail=f"Group not found: {group_code}")
    return api_response(data={
        "code": group_code,
        "display_name": cfg.get("display_name", group_code) if isinstance(cfg, dict) else str(cfg),
        "server": cfg.get("server", "") if isinstance(cfg, dict) else "",
        "servers": cfg.get("servers", []) if isinstance(cfg, dict) else [],
        "server_keywords": cfg.get("server_keywords", []) if isinstance(cfg, dict) else [],
        "servers_by_env": cfg.get("servers_by_env", {}) if isinstance(cfg, dict) else {},
        "variables": cfg.get("variables", {}) if isinstance(cfg, dict) else {},
    })


@resource_v2_router.post("/systems/{system_name}/groups")
async def create_system_group_v2(system_name: str, payload: SystemGroupPayload, request: Request, db: Session = Depends(get_db)):
    from app.config.systems import save_group, get_system_by_name, get_group
    user = require_auth(request, db)
    if not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin permission required")
    group_code = str(payload.code or "").strip()
    if not group_code:
        raise HTTPException(status_code=400, detail="Group code is required")
    if not re.match(r"^[a-zA-Z0-9_.-]+$", group_code):
        raise HTTPException(status_code=400, detail="Group code can only contain letters, digits, underscore, dot, and dash")
    if not get_system_by_name(system_name):
        raise HTTPException(status_code=404, detail=f"System not found: {system_name}")
    if get_group(system_name, group_code):
        raise HTTPException(status_code=409, detail=f"Group already exists: {group_code}")
    group_cfg = {
        "display_name": str(payload.display_name or group_code).strip(),
        "server": str(payload.server or "").strip(),
        "servers": payload.servers or [],
        "server_keywords": payload.server_keywords or [],
        "servers_by_env": payload.servers_by_env or {},
        "variables": payload.variables or {},
    }
    if not save_group(system_name, group_code, group_cfg):
        raise HTTPException(status_code=500, detail="Failed to save group configuration")
    audit("system.group.create", "group", f"{system_name}/{group_code}", getattr(request.state, "username", ""))
    return api_response(data={"code": group_code, "display_name": group_cfg["display_name"]}, message="Group created")


@resource_v2_router.put("/systems/{system_name}/groups/{group_code}")
async def update_system_group_v2(system_name: str, group_code: str, payload: SystemGroupPayload, request: Request, db: Session = Depends(get_db)):
    from app.config.systems import save_group, get_group, get_system_by_name
    user = require_auth(request, db)
    if not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin permission required")
    if not get_system_by_name(system_name):
        raise HTTPException(status_code=404, detail=f"System not found: {system_name}")
    existing = get_group(system_name, group_code)
    if not existing:
        raise HTTPException(status_code=404, detail=f"Group not found: {group_code}")
    existing = dict(existing) if isinstance(existing, dict) else {}
    new_code = str(payload.code or group_code).strip()
    if new_code != group_code and get_group(system_name, new_code):
        raise HTTPException(status_code=409, detail=f"Group already exists: {new_code}")
    group_cfg = {
        "display_name": str(payload.display_name or existing.get("display_name", group_code)).strip(),
        "server": str(payload.server or existing.get("server", "")).strip(),
        "servers": payload.servers if payload.servers else existing.get("servers", []),
        "server_keywords": payload.server_keywords if payload.server_keywords else existing.get("server_keywords", []),
        "servers_by_env": payload.servers_by_env if payload.servers_by_env else existing.get("servers_by_env", {}),
        "variables": payload.variables if payload.variables else existing.get("variables", {}),
    }
    if new_code != group_code:
        from app.config.systems import delete_group
        delete_group(system_name, group_code)
    if not save_group(system_name, new_code, group_cfg):
        raise HTTPException(status_code=500, detail="Failed to save group configuration")
    audit("system.group.update", "group", f"{system_name}/{group_code}", getattr(request.state, "username", ""))
    return api_response(data={"code": new_code, "display_name": group_cfg["display_name"]}, message="Group updated")


@resource_v2_router.delete("/systems/{system_name}/groups/{group_code}")
async def delete_system_group_v2(system_name: str, group_code: str, request: Request, db: Session = Depends(get_db)):
    from app.config.systems import delete_group, get_group, get_system_by_name
    user = require_auth(request, db)
    if not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin permission required")
    if not get_system_by_name(system_name):
        raise HTTPException(status_code=404, detail=f"System not found: {system_name}")
    if not get_group(system_name, group_code):
        raise HTTPException(status_code=404, detail=f"Group not found: {group_code}")
    Deployment = None
    try:
        from app.db.models import Deployment
    except Exception:
        pass
    if Deployment:
        dep_count = db.query(Deployment).filter(
            Deployment.system == system_name,
            Deployment.server_group == group_code,
            Deployment.status.in_(["running", "pending", "queued"]),
        ).count()
        if dep_count > 0:
            raise HTTPException(
                status_code=409,
                detail=f"Group '{group_code}' has {dep_count} active deployment(s). Please wait for them to complete or cancel them first.",
            )
    if not delete_group(system_name, group_code):
        raise HTTPException(status_code=500, detail="Failed to delete group configuration")
    audit("system.group.delete", "group", f"{system_name}/{group_code}", getattr(request.state, "username", ""))
    return api_response(data={"code": group_code}, message="Group deleted")


@resource_v2_router.get("/systems/{system_name}/variable-inheritance")
async def get_system_variable_inheritance_v2(system_name: str, service: str = "", environment: str = "", request: Request = None, db: Session = Depends(get_db)):
    from app.config.systems import get_variable_inheritance, get_system_by_name
    if not get_system_by_name(system_name):
        raise HTTPException(status_code=404, detail=f"System not found: {system_name}")
    chain = get_variable_inheritance(system_name, service_name=service or None, environment=environment or None)
    return api_response(data=chain)


@resource_v2_router.get("/step-types")
async def list_step_types():
    from app.pipeline.factory import StepFactory
    return api_response(data=StepFactory.list_types())


@resource_v2_router.get("/files/deploy-packages")
async def list_deploy_packages(system: str = "", service: str = "", db: Session = Depends(get_db)):
    from app.services.package_retention import list_packages as list_package_meta
    files = list_package_meta(db, system=system or "", service=service or "", limit=500, with_retention=True)
    svc = _find_config_service(system, service) if system and service else None
    if system or service:
        for info in files:
            info["match"] = _package_service_match(info.get("package_name") or info.get("name"), svc, service).get("status")
    return api_response(data=files)


@resource_v2_router.get("/files/checksum/{file_name:path}")
async def checksum_file(file_name: str, db: Session = Depends(get_db)):
    from app.services.package_retention import safe_package_name, package_path, upsert_package_metadata
    name = safe_package_name(file_name)
    fp = package_path(name)
    if not os.path.isfile(fp):
        raise HTTPException(status_code=404, detail="File not found")
    row = upsert_package_metadata(db, name, fp)
    return api_response(data={"sha256": row.sha256, "name": name})


@resource_v2_router.post("/files/browse/{server_name}")
async def browse_remote(request: Request, server_name: str):
    data = await request.json()
    path = data.get("path", "/data/web/app")
    srv = inventory.get_server(server_name)
    if not srv:
        raise HTTPException(status_code=404, detail=f"Server '{server_name}' not found")
    try:
        ssh = _connect_ssh(srv)
        if not ssh:
            raise HTTPException(status_code=503, detail=f"Cannot connect to {server_name}")
        cmd = f"ls -alh --time-style=long-iso '{path}' 2>/dev/null | tail -n +2"
        exit_code, out, err = ssh.exec(cmd, timeout=10)
        items = []
        if exit_code == 0:
            for line in (out or "").splitlines():
                parts = line.split()
                if len(parts) >= 8:
                    is_dir = line.startswith("d")
                    items.append({
                        "name": parts[7],
                        "is_dir": is_dir,
                        "size": parts[4],
                        "modified": " ".join(parts[5:7]),
                    })
        ssh.close()
        return api_response(data={"path": path, "items": items})
    except Exception as e:
        return api_response(data={"path": path, "items": [], "error": str(e)})


@resource_v2_router.post("/files/upload")
async def upload_file(request: Request, file: UploadFile = File(...), system: str = "", service: str = "", overwrite: bool = False, db: Session = Depends(get_db)):
    from app.services.package_retention import save_package_fileobj
    actor = getattr(request.state, "username", "") or "web"
    try:
        await file.seek(0)
    except Exception:
        pass
    meta = save_package_fileobj(
        db,
        filename=file.filename or "uploaded_file",
        fileobj=file.file,
        system=system or "",
        service=service or "",
        uploaded_by=actor,
        overwrite=overwrite,
    )
    audit("file.upload.deploy_package", "file", meta.get("package_name") or meta.get("name"), f"size={meta.get('size_bytes') or meta.get('size')} sha256={meta.get('sha256')}")
    return api_response(data=meta, message="Upload successful")




@resource_v2_router.get("/files/packages/retention")
async def get_package_retention(db: Session = Depends(get_db)):
    from app.services.package_retention import get_package_retention_policy
    return api_response(data=get_package_retention_policy(db))


@resource_v2_router.put("/files/packages/retention")
async def update_package_retention(payload: Dict[str, Any], request: Request, db: Session = Depends(get_db)):
    from app.core.auth_v2 import require_auth
    from app.services.package_retention import save_package_retention_policy
    user = require_auth(request, db)
    if not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin permission required")
    policy = save_package_retention_policy(db, payload or {})
    audit("package.retention.update", "file", "deploy_packages", f"user={getattr(request.state, 'username', '')}")
    return api_response(data=policy, message="Package retention policy updated")


@resource_v2_router.post("/files/packages/cleanup/preview")
async def preview_package_cleanup_api(payload: Dict[str, Any] | None = None, db: Session = Depends(get_db)):
    from app.services.package_retention import preview_package_cleanup
    return api_response(data=preview_package_cleanup(db, (payload or {}).get("policy") or payload or {}))


@resource_v2_router.post("/files/packages/cleanup")
async def cleanup_package_api(payload: Dict[str, Any] | None = None, request: Request = None, db: Session = Depends(get_db)):
    from app.core.auth_v2 import require_auth
    from app.services.package_retention import cleanup_packages
    user = require_auth(request, db)
    if not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin permission required")
    payload = payload or {}
    result = cleanup_packages(db, payload.get("policy") or {}, dry_run=bool(payload.get("dry_run", True)), actor=getattr(request.state, "username", ""))
    audit("package.retention.cleanup", "file", "deploy_packages", f"dry_run={result.get('dry_run')} count={result.get('summary', {}).get('cleanup_count')}")
    return api_response(data=result, message="Package cleanup completed" if not result.get("dry_run") else "Package cleanup preview completed")


@resource_v2_router.delete("/files/packages/{package_name:path}")
async def delete_package_api(package_name: str, request: Request = None, db: Session = Depends(get_db)):
    from app.core.auth_v2 import require_auth
    from app.services.package_retention import safe_package_name, delete_package
    user = require_auth(request, db)
    if not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin permission required")
    name = safe_package_name(package_name)
    result = delete_package(db, name, actor=getattr(request.state, "username", ""))
    audit("package.delete", "file", name, f"manual=true file_deleted={result.get('file_deleted')}")
    return api_response(data=result, message="Package deleted")


@resource_v2_router.post("/files/packages/{package_name:path}/protect")
async def protect_package_api(package_name: str, payload: Dict[str, Any] | None = None, request: Request = None, db: Session = Depends(get_db)):
    from app.core.auth_v2 import require_auth
    from app.services.package_retention import safe_package_name, package_path, upsert_package_metadata
    from app.db.models import DeployPackage
    user = require_auth(request, db)
    if not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin permission required")
    name = safe_package_name(package_name)
    row = db.query(DeployPackage).filter(DeployPackage.package_name == name).first()
    if not row and os.path.isfile(package_path(name)):
        row = upsert_package_metadata(db, name, uploaded_by=getattr(request.state, "username", ""))
    if not row:
        raise HTTPException(status_code=404, detail="Package not found")
    row.protected = bool((payload or {}).get("protected", True))
    db.commit()
    audit("package.protect", "file", name, f"protected={row.protected}")
    return api_response(data={"package_name": name, "protected": bool(row.protected)}, message="Package protection updated")


@resource_v2_router.post("/files/upload-remote/{server_name}")
async def upload_remote(request: Request, server_name: str, file: UploadFile = File(...)):
    srv = inventory.get_server(server_name)
    if not srv:
        raise HTTPException(status_code=404, detail=f"Server '{server_name}' not found")
    form = await request.form()
    remote_path = form.get("remote_path", f"/tmp/{file.filename}")
    content = await file.read()
    ssh = None
    try:
        ssh = _connect_ssh(srv)
        if not ssh:
            raise HTTPException(status_code=503, detail=f"Cannot connect to {server_name}")
        sftp = ssh.client.open_sftp()
        with sftp.file(str(remote_path), "wb") as f:
            f.write(content)
        sftp.close()
        ssh.close()
        import hashlib
        sha = hashlib.sha256()
        sha.update(content)
        return api_response(data={
            "name": file.filename,
            "remote_path": str(remote_path),
            "size": len(content),
            "sha256": sha.hexdigest(),
        }, message="Remote upload successful")
    except Exception as e:
        if ssh:
            try:
                ssh.close()
            except Exception:
                pass
        raise HTTPException(status_code=500, detail=str(e))
