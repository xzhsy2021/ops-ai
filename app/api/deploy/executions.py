"""发布管理 - 发布执行、日志、回滚、重试等相关路由"""
import os
import json
import uuid
import asyncio
from typing import List, Dict, Any, Optional
from fastapi import APIRouter, HTTPException, Request, Depends, Query
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.orm import Session

from app.api.helpers import api_response, audit
from app.db import get_db, DeployTaskRepository, DeployLogRepository, DeploymentRepository
from app.db.models import DeployTask, Deployment
from app.core.auth_v2 import require_auth, require_deploy_for_env, normalize_role, ROLE_ORDER
from app.core.rbac import require_confirmed_high_risk
from app.core.deploy_lock import DeployLock
from app.deploy.report import deployment_report_payload, deployment_report_markdown as build_deployment_report_markdown
from app.deploy.logs import deployment_logs_payload, deployment_tasks_payload
from app.deploy.logs import _compute_deployment_logs_etag, _compute_task_logs_etag
from app.deploy.state import task_cancel_requested, normalize_status
from app.deploy.locks import acquire_deployment_locks, release_deployment_locks, list_deployment_locks_payload
from app.deploy.stream import sse_generator, SubscriberLimitExceeded

from app.api.deploy._shared import (
    _build_confirmation, _derive_servers, _merge_release_variables,
    _find_config_service, _service_topology, _rollback_plan_for,
    _rollback_candidates_for, _rollback_precheck_for,
    _deployment_to_rollback_candidate, _db_pipeline_steps,
    _default_release_steps, _json_safe, _send_release_notification,
    _assert_environment_server_consistency, _assert_strict_deploy_confirmation,
    _extract_change_reason, _flush_deploy_logs,
    _connect_ssh, _log_rollback_health_commands,
    _env_alias, _rollback_runtime, _service_aliases,
    _notification_settings, _deploy_confirm_text,
    logger, ensure_deploy_worker_running, _deploy_worker,
)
from app.deploy.schemas import DeployRequest
from app.deploy.rollback_health import build_rollback_health_commands
from app.domain.runtime.snapshots import build_deployment_snapshot

exec_router = APIRouter(tags=["发布管理v2-执行"])


@exec_router.post("/execute")
async def deploy_execute_v2(request: Request, db: Session = Depends(get_db)):
    user = require_auth(request, db)
    data = await request.json()
    return await queue_deploy_v2(user, data, db)


async def queue_deploy_v2(user: Dict[str, Any], data: Dict[str, Any], db: Session) -> Dict[str, Any]:
    """Queue a deploy task from a normalized request dict.

    Shared by the web execute endpoint and the Matrix deploy endpoint so both
    paths run the same confirmation / lock / task / audit logic.
    """
    req = DeployRequest(**data)

    require_deploy_for_env(user, req.environment)
    require_confirmed_high_risk(user, action="deploy", environment=req.environment, payload={**data, **(req.variables or {})})

    if not req.system:
        raise HTTPException(status_code=400, detail="system is required")

    # Docker Compose 部署不需要上传文件，镜像直接从仓库拉取
    svc = _find_config_service(req.system, req.service, req.environment) or {}
    is_docker_compose = (svc.get("template") == "docker_compose" or
                         (svc.get("template_variables") or {}).get("compose_dir"))
    if not is_docker_compose and not req.file_name:
        raise HTTPException(status_code=400, detail="file_name is required")

    # 发版制品格式守卫：文件中心允许任意普通文件入库（如 Matrix 拉取），
    # 但发布必须限定部署包格式（.tar.gz/.zip/...）；docker_compose 无制品跳过。
    from app.services.package_retention import ensure_releasable_artifact
    if not is_docker_compose and req.file_name:
        ensure_releasable_artifact(db, req.file_name)

    req.servers = _derive_servers(req, db)
    req.variables = _merge_release_variables(req, db)
    if not req.servers:
        raise HTTPException(status_code=400, detail="At least one server is required")
    _assert_environment_server_consistency(req.environment, req.servers)

    confirmation = _build_confirmation(req, db, user)
    if confirmation.get("blockers"):
        raise HTTPException(status_code=400, detail="发布前校验未通过：" + "；".join(confirmation.get("blockers", [])))
    _assert_strict_deploy_confirmation(req, data, confirmation, db)

    effective_pipeline_id = req.pipeline_id or (confirmation.get("pipeline_id") or "")
    steps = data.get("steps") or _db_pipeline_steps(db, effective_pipeline_id) or _default_release_steps(req, db)

    deploy_repo = DeploymentRepository(db)
    deployment = deploy_repo.create(
        system=req.system,
        service=req.service,
        environment=req.environment,
        strategy="DIRECT",
        servers=",".join(req.servers),
        created_by=user.get("username"),
        version=req.version or req.file_name,
        server_group=req.server_group,
        status="pending",
    )
    db.commit()
    deployment_id = deployment.id
    try:
        from app.services.package_retention import record_package_reference
        for server_name in (req.servers or [""]):
            record_package_reference(
                db, package_name=req.file_name or req.version, deployment_id=deployment_id,
                system=req.system, service=req.service, environment=req.environment,
                server_name=server_name, usage_type="deploy", commit=False,
            )
        db.commit()
    except Exception as exc:
        logger.warning("Failed to record package reference: %s", exc)

    task_id = uuid.uuid4().hex[:12]
    try:
        lock_keys = acquire_deployment_locks(req, deployment_id, task_id, user.get("username") or "", db)
    except Exception:
        DeploymentRepository(db).update_status(deployment_id, "failed", "Failed to acquire deployment locks")
        raise
    payload = {
        "request": _json_safe(req.model_dump()),
        "steps": _json_safe(steps),
        "created_by": user.get("username"),
        "reason": _extract_change_reason(data),
        "confirmation": {
            "risk_level": confirmation.get("risk_level"),
            "confirm_text": data.get("confirm_text") or data.get("confirmation") or "",
        },
    }
    try:
        task_repo = DeployTaskRepository(db)
        task_repo.create(deployment_id=deployment_id, task_id=task_id, payload_json=json.dumps(payload, ensure_ascii=False), lock_key=",".join(lock_keys))
    except Exception:
        release_deployment_locks(lock_keys, db)
        DeploymentRepository(db).update_status(deployment_id, "failed", "Failed to create deployment task")
        raise

    ensure_deploy_worker_running()
    audit("deploy.execute.v2", "system", req.system, f"user={user.get('username')} service={req.service} env={req.environment} servers={','.join(req.servers)} file={req.file_name} risk={confirmation.get('risk_level')} reason={_extract_change_reason(data) or '-'} worker=queued")
    _send_release_notification("deploy.queued", deployment, {"task_id": task_id}, db)
    return api_response(data={"task_id": task_id, "deployment_id": deployment_id, "queued": True}, message="Deploy queued")


@exec_router.get("/logs/{task_id}")
async def deploy_logs_v2(
    task_id: str,
    request: Request,
    response: Response,
    cursor: str = Query(default=None),
    limit: int = Query(default=int(os.getenv("MAX_LOG_TAIL_LINES", "500")), ge=1, le=2000),
    db: Session = Depends(get_db),
):
    etag = _compute_task_logs_etag(db, task_id)
    response.headers["ETag"] = etag
    response.headers["Cache-Control"] = "private, max-age=5"
    if request.headers.get("If-None-Match") == etag:
        return Response(status_code=304)

    _flush_deploy_logs()
    repo = DeployLogRepository(db)
    if cursor:
        logs, next_cursor = repo.list_by_task_cursor(task_id, cursor=cursor, limit=limit)
    else:
        logs = repo.list_by_task(task_id, limit=limit)
        next_cursor = None
    data = {
        "task_id": task_id,
        "limit": limit,
        "logs": [
            {"level": l.level, "message": l.message, "step_name": l.step_name, "created_at": l.created_at.isoformat()}
            for l in logs
        ]
    }
    if next_cursor:
        data["next_cursor"] = next_cursor
    return api_response(data=data)


@exec_router.get("/tasks/{task_id}")
async def deploy_task_status(task_id: str, db: Session = Depends(get_db)):
    repo = DeployTaskRepository(db)
    task = repo.get_by_id(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return api_response(data={
        "task_id": task.id,
        "deployment_id": task.deployment_id,
        "status": task.status,
        "started_at": task.started_at.isoformat() if task.started_at else None,
        "finished_at": task.finished_at.isoformat() if task.finished_at else None,
        "result": task.result,
    })


@exec_router.get("/deployments/{deployment_id}/logs")
async def deployment_logs(
    deployment_id: str,
    request: Request,
    response: Response,
    cursor: str = Query(default=None),
    limit: int = Query(default=int(os.getenv("MAX_LOG_TAIL_LINES", "500")), ge=1, le=5000),
    db: Session = Depends(get_db),
):
    etag = _compute_deployment_logs_etag(db, deployment_id)
    response.headers["ETag"] = etag
    response.headers["Cache-Control"] = "private, max-age=5"
    if request.headers.get("If-None-Match") == etag:
        return Response(status_code=304)

    _flush_deploy_logs()
    repo = DeployLogRepository(db)
    if cursor:
        logs, next_cursor = repo.list_by_deployment_cursor(deployment_id, cursor=cursor, limit=limit)
    else:
        logs = repo.list_by_deployment(deployment_id, limit=limit)
        next_cursor = None
    items = []
    for log in logs:
        item = {
            "level": log.level,
            "message": log.message,
            "step_name": log.step_name,
            "created_at": log.created_at.isoformat() if log.created_at else None,
            "task_id": log.task_id,
        }
        items.append(item)
    data = {"deployment_id": deployment_id, "logs": items, "limit": limit, "count": len(items)}
    if next_cursor:
        data["next_cursor"] = next_cursor
    return api_response(data=data)


@exec_router.get("/deployments/{deployment_id}/tasks")
async def deployment_tasks(deployment_id: str, db: Session = Depends(get_db)):
    return api_response(data=deployment_tasks_payload(db, deployment_id))


@exec_router.post("/deployments/{deployment_id}/cancel")
async def cancel_deployment(deployment_id: str, request: Request, db: Session = Depends(get_db)):
    user = require_auth(request, db)
    deployment = DeploymentRepository(db).get_by_id(deployment_id)
    if not deployment:
        raise HTTPException(status_code=404, detail="Deployment not found")
    username = user.get("username")
    role = normalize_role(user.get("role"), bool(user.get("is_admin")))
    is_owner = bool(username and getattr(deployment, "created_by", None) == username)
    is_operator = ROLE_ORDER.get(role, 0) >= ROLE_ORDER["operator"]
    if not (user.get("is_admin") or is_operator or is_owner):
        raise HTTPException(status_code=403, detail="Only deployment owner, operator or admin can cancel deployment")
    tasks = DeployTaskRepository(db).list_by_deployment(deployment_id)
    for task in tasks:
        DeployTaskRepository(db).request_cancel(task.id)
        if normalize_status(task.status) == "pending":
            DeployTaskRepository(db).update_status(task.id, "canceled", result="Canceled before worker start")
    DeploymentRepository(db).update_status(deployment_id, "canceled", "Cancel requested")
    for task in tasks:
        lock_keys = [x for x in str(getattr(task, "lock_key", "") or "").split(",") if x]
        if normalize_status(task.status) == "pending":
            release_deployment_locks(lock_keys, db)
    audit("deploy.cancel.v2", "deployment", deployment_id, f"user={user.get('username')}")
    _send_release_notification("deploy.canceled", deployment, {"requested_by": user.get("username")}, db)
    return api_response(message="Cancel requested")


@exec_router.get("/worker/status")
async def deploy_worker_status(request: Request, db: Session = Depends(get_db)):
    require_auth(request, db)
    pending = db.query(DeployTask).filter(DeployTask.status == "pending").count()
    running = db.query(DeployTask).filter(DeployTask.status == "running").count()
    status = _deploy_worker.status()
    status.update({"pending": pending, "active": running})
    return api_response(data=status)


@exec_router.get("/deployments/{deployment_id}/rollback-plan")
async def rollback_plan_v2(deployment_id: str, request: Request, db: Session = Depends(get_db)):
    require_auth(request, db)
    deployment = DeploymentRepository(db).get_by_id(deployment_id)
    if not deployment:
        raise HTTPException(status_code=404, detail="Deployment not found")
    servers = [x.strip() for x in (deployment.servers or "").split(",") if x.strip()]
    req = DeployRequest(
        system=deployment.system,
        service=deployment.service or "",
        environment=deployment.environment or "",
        version=deployment.version or "",
        servers=servers,
        file_name=deployment.version or "",
        variables={},
    )
    variables = _merge_release_variables(req, db)
    plan = _rollback_plan_for(deployment.system, deployment.service or "", deployment.environment or "", servers, variables, db)
    candidates = _rollback_candidates_for(deployment, db)
    precheck = _rollback_precheck_for(deployment, plan, db)
    return api_response(data={
        "deployment_id": deployment_id,
        "current": _deployment_to_rollback_candidate(deployment),
        "plan": plan,
        "precheck": precheck,
        "candidates": candidates,
    })


@exec_router.post("/rollback/{deployment_id}")
async def rollback_deployment_v2(deployment_id: str, request: Request, db: Session = Depends(get_db)):
    user = require_auth(request, db)
    deploy_repo = DeploymentRepository(db)
    deployment = deploy_repo.get_by_id(deployment_id)
    if not deployment:
        raise HTTPException(status_code=404, detail="Deployment not found")

    if deployment.status != "success":
        raise HTTPException(status_code=400, detail="Only successful deployments can be rolled back")

    require_deploy_for_env(user, deployment.environment)
    body = {}
    try:
        body = await request.json()
    except Exception:
        body = {}
    require_confirmed_high_risk(user, action="rollback", environment=deployment.environment, payload=body)

    servers_for_check = [x.strip() for x in (deployment.servers or "").split(",") if x.strip()]
    check_req = DeployRequest(system=deployment.system, service=deployment.service or "", environment=deployment.environment or "", version=deployment.version or "", servers=servers_for_check, file_name=deployment.version or "", variables={})
    check_variables = _merge_release_variables(check_req, db)
    check_plan = _rollback_plan_for(deployment.system, deployment.service or "", deployment.environment or "", servers_for_check, check_variables, db)
    check = _rollback_precheck_for(deployment, check_plan, db)
    if check.get("blockers"):
        raise HTTPException(status_code=400, detail="; ".join(check.get("blockers") or []))

    lock_key = f"{deployment.system}:{deployment.service or 'default'}:rollback"
    if not DeployLock.acquire(lock_key):
        raise HTTPException(status_code=409, detail="Rollback already in progress for this deployment")

    task_id = uuid.uuid4().hex[:12]
    task_repo = DeployTaskRepository(db)
    task_repo.create(deployment_id=deployment_id, task_id=task_id, lock_key=lock_key)

    asyncio.create_task(_rollback_runtime().run(task_id, deployment_id, lock_keys=[lock_key]))
    audit("deploy.rollback.v2", "deployment", deployment_id, f"user={user.get('username')}")
    return api_response(data={"task_id": task_id, "deployment_id": deployment_id}, message="Rollback started")


@exec_router.post("/deployments/{deployment_id}/retry")
async def retry_deployment_v2(deployment_id: str, request: Request, db: Session = Depends(get_db)):
    user = require_auth(request, db)
    deployment = DeploymentRepository(db).get_by_id(deployment_id)
    if not deployment:
        raise HTTPException(status_code=404, detail="Deployment not found")
    require_deploy_for_env(user, deployment.environment)
    body = {}
    try:
        body = await request.json()
    except Exception:
        body = {}
    require_confirmed_high_risk(user, action="deploy retry", environment=deployment.environment, payload=body)
    if deployment.status not in ("failed", "cancelled", "canceled", "success"):
        raise HTTPException(status_code=400, detail="Only finished deployments can be retried")
    servers = [x.strip() for x in (deployment.servers or "").split(",") if x.strip()]
    retry_req = DeployRequest(
        system=deployment.system,
        service=deployment.service or "",
        environment=deployment.environment or "",
        version=deployment.version or "",
        servers=servers,
        file_name=body.get("file_name") or deployment.version or "retry-artifact",
        variables=body.get("variables") or {},
    )
    steps = body.get("steps") or _default_release_steps(retry_req, db)
    new_deployment = DeploymentRepository(db).create(
        system=retry_req.system, service=retry_req.service, environment=retry_req.environment,
        strategy=deployment.strategy or "DIRECT", servers=",".join(retry_req.servers),
        created_by=user.get("username"), version=retry_req.version,
        server_group=deployment.server_group,
    )
    new_deployment.status = "pending"
    db.commit()
    try:
        from app.services.package_retention import record_package_reference
        for server_name in (retry_req.servers or [""]):
            record_package_reference(
                db, package_name=retry_req.file_name or retry_req.version, deployment_id=new_deployment.id,
                system=retry_req.system, service=retry_req.service, environment=retry_req.environment,
                server_name=server_name, usage_type="retry", commit=False,
            )
        db.commit()
    except Exception as exc:
        logger.warning("Failed to record retry package reference: %s", exc)
    task_id = uuid.uuid4().hex[:12]
    try:
        lock_keys = acquire_deployment_locks(retry_req, new_deployment.id, task_id, user.get("username") or "", db)
    except Exception:
        DeploymentRepository(db).update_status(new_deployment.id, "failed", "Failed to acquire deployment locks")
        raise
    payload = {"request": _json_safe(retry_req.model_dump()), "steps": _json_safe(steps), "retry_of": deployment_id}
    try:
        DeployTaskRepository(db).create(deployment_id=new_deployment.id, task_id=task_id, payload_json=json.dumps(payload, ensure_ascii=False), lock_key=",".join(lock_keys))
    except Exception:
        release_deployment_locks(lock_keys, db)
        raise
    ensure_deploy_worker_running()
    audit("deploy.retry.v2", "deployment", deployment_id, f"user={user.get('username')} new_deployment={new_deployment.id}")
    return api_response(data={"task_id": task_id, "deployment_id": new_deployment.id}, message="Retry queued")


@exec_router.get("/deployments/{deployment_id}/rollback-readiness")
async def get_deployment_rollback_readiness(
    deployment_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Read-only rollback readiness check used by web UI and MCP clients."""
    require_auth(request, db)
    from app.services.release_plan import rollback_readiness

    return api_response(data=rollback_readiness(db, deployment_id))


@exec_router.get("/deployments/{deployment_id}/aggregate")
async def deployment_aggregate(deployment_id: str, request: Request, db: Session = Depends(get_db)):
    require_auth(request, db)
    result = build_deployment_snapshot(db, deployment_id, log_tail=20, include_report=True)
    if not result.get("found"):
        raise HTTPException(status_code=404, detail="Deployment not found")
    return api_response(data=result)


@exec_router.get("/deployments/{deployment_id}/stream")
async def deployment_logs_stream(
    deployment_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    require_auth(request, db)
    deployment = DeploymentRepository(db).get_by_id(deployment_id)
    if not deployment:
        raise HTTPException(status_code=404, detail="Deployment not found")
    try:
        return StreamingResponse(
            sse_generator(deployment_id),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
    except SubscriberLimitExceeded:
        raise HTTPException(status_code=503, detail="SSE subscriber limit exceeded for this deployment")