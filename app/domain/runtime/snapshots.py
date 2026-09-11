from datetime import datetime, timezone
import json
from typing import Any, Dict, Optional, List

from sqlalchemy.orm import Session

from app.db import DeploymentRepository
from app.deploy.logs import deployment_logs_payload, deployment_tasks_payload
from app.deploy.report import deployment_report_payload


def _dt(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _tail_logs(logs: List[Dict[str, Any]], tail: int = 10) -> List[Dict[str, Any]]:
    return logs[-tail:] if len(logs) > tail else logs


def build_deployment_snapshot(
    db: Session,
    deployment_id: str,
    *,
    log_tail: int = 10,
    include_report: bool = True,
) -> Dict[str, Any]:
    repo = DeploymentRepository(db)
    deployment = repo.get_by_id(deployment_id)
    if not deployment:
        return {"deployment_id": deployment_id, "found": False}

    deployment_summary = {
        "id": deployment.id,
        "system": deployment.system,
        "service": deployment.service,
        "environment": deployment.environment,
        "strategy": deployment.strategy,
        "status": deployment.status,
        "version": deployment.version,
        "servers": deployment.servers,
        "message": deployment.message,
        "created_by": deployment.created_by,
        "server_group": deployment.server_group,
        "started_at": _dt(deployment.started_at),
        "finished_at": _dt(deployment.finished_at),
        "created_at": _dt(getattr(deployment, "created_at", deployment.started_at)),
        "updated_at": _dt(getattr(deployment, "updated_at", None)),
    }

    logs_payload = deployment_logs_payload(db, deployment_id, include_task_id=True)
    all_logs: List[Dict[str, Any]] = logs_payload.get("logs", [])
    log_summary = {
        "total_count": len(all_logs),
        "tail": _tail_logs(all_logs, tail=log_tail),
        "error_count": sum(1 for l in all_logs if l.get("level") == "error"),
        "warning_count": sum(1 for l in all_logs if l.get("level") == "warning"),
    }

    tasks_payload = deployment_tasks_payload(db, deployment_id)
    task_list: List[Dict[str, Any]] = tasks_payload.get("tasks", [])
    server_tasks: List[Dict[str, Any]] = tasks_payload.get("server_tasks", [])
    step_tasks: List[Dict[str, Any]] = tasks_payload.get("step_tasks", [])
    distributions: List[Dict[str, Any]] = tasks_payload.get("distributions", [])

    pipeline_step_configs: Dict[str, Dict[str, Any]] = {}
    try:
        from app.db.models import Service, PipelineStep

        service = db.query(Service).filter(
            Service.name == deployment.service,
            Service.system_name == deployment.system,
        ).first()
        if service and service.pipeline_id:
            live_steps = db.query(PipelineStep).filter(
                PipelineStep.pipeline_id == service.pipeline_id,
            ).all()
            for step in live_steps:
                try:
                    pipeline_step_configs[step.name] = json.loads(step.config or "{}")
                except (json.JSONDecodeError, TypeError):
                    pipeline_step_configs[step.name] = {}
    except Exception:
        pass

    enriched_step_tasks: List[Dict[str, Any]] = []
    for st in step_tasks:
        captured_raw = st.get("captured_config")
        if isinstance(captured_raw, str):
            try:
                captured = json.loads(captured_raw)
            except (json.JSONDecodeError, TypeError):
                captured = None
        else:
            captured = captured_raw
        live_config = pipeline_step_configs.get(st.get("step_name"))
        config_drift = False
        if captured is not None and live_config is not None:
            config_drift = captured != live_config
        enriched_step_tasks.append({
            **st,
            "captured_config": captured,
            "live_config": live_config,
            "config_drift": config_drift,
        })

    task_summary = {
        "total_tasks": len(task_list),
        "active_tasks": sum(1 for t in task_list if t.get("status") not in ("success", "failed", "canceled", "cancelled")),
        "server_count": len(server_tasks),
        "step_count": len(step_tasks),
        "distribution_count": len(distributions),
        "tasks": task_list,
        "server_tasks": server_tasks,
        "step_tasks": enriched_step_tasks,
        "distributions": distributions,
    }

    result: Dict[str, Any] = {
        "deployment_id": deployment_id,
        "found": True,
        "snapshot_at": _dt(datetime.now(timezone.utc)),
        "deployment_summary": deployment_summary,
        "log_summary": log_summary,
        "task_summary": task_summary,
    }

    if include_report:
        try:
            report = deployment_report_payload(deployment_id, db)
            result["report"] = report
        except Exception:
            result["report"] = None

    return result


def build_deployments_aggregate(
    db: Session,
    *,
    system: str = "",
    environment: str = "",
    limit: int = 5,
) -> Dict[str, Any]:
    from app.db.models import Deployment
    from app.api.deploy._shared import _deploy_worker

    q = db.query(Deployment).order_by(Deployment.started_at.desc())
    if system:
        q = q.filter(Deployment.system == system)
    if environment:
        q = q.filter(Deployment.environment == environment)

    recent = q.filter(Deployment.status.in_(("success", "failed", "canceled", "cancelled", "rolled_back"))).limit(limit).all()
    active = q.filter(Deployment.status.in_(("running", "pending", "queued"))).all()

    latest_deployments = [
        {
            "id": d.id,
            "system": d.system,
            "service": d.service,
            "environment": d.environment,
            "status": d.status,
            "version": d.version,
            "servers": d.servers,
            "started_at": _dt(d.started_at),
            "finished_at": _dt(d.finished_at),
        }
        for d in recent
    ]

    active_jobs = [
        {
            "id": d.id,
            "system": d.system,
            "service": d.service,
            "environment": d.environment,
            "status": d.status,
        }
        for d in active
    ]

    running_count = db.query(Deployment).filter(Deployment.status == "running").count()

    # AsyncWorkerHandle（app/deploy/worker.py）把生命周期状态收在 status() 里，
    # 没有裸的 `running` 属性；这里读 `.running` 曾抛 AttributeError，导致
    # ops.deploy.aggregate_status 整个接口失败。口径与 app/api/deploy/executions.py
    # 和 app/services/system_health.py 保持一致。
    worker_alive = bool(_deploy_worker and (_deploy_worker.status() or {}).get("running"))

    recent_rollback_count = db.query(Deployment).filter(
        Deployment.status == "rolled_back",
    ).count()

    precheck_enabled = False
    try:
        from app.api.deploy.precheck import deploy_precheck
        precheck_enabled = True
    except Exception:
        pass

    return {
        "snapshot_at": _dt(datetime.now(timezone.utc)),
        "latest_deployments": latest_deployments,
        "active_jobs": active_jobs,
        "active_count": len(active_jobs),
        "running_count": running_count,
        "worker_alive": worker_alive,
        "recent_rollback_count": recent_rollback_count,
        "precheck_enabled": precheck_enabled,
    }