from __future__ import annotations

import threading
import traceback
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.db.base import SessionLocal
from app.db.models import OperationJob
from app.services.tool_context import ToolContext
from app.domain.runtime import runtime_job_to_dict


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _actor_from_ctx(ctx: ToolContext) -> str:
    return (
        getattr(ctx, "username", "")
        or getattr(ctx, "token_owner", "")
        or getattr(ctx, "client_name", "")
        or "tool"
    )


def _source_from_ctx(ctx: ToolContext) -> str:
    if getattr(ctx, "auth_type", "") == "tool_token":
        return "mcp"
    return getattr(ctx, "auth_type", "") or "tool"


def _target_from_args(args: Dict[str, Any]) -> str:
    for key in ("file", "file_name", "package_name", "deployment_id", "plan_id", "system", "server_name"):
        value = args.get(key)
        if value:
            return str(value)
    return "-"


def _safe_ctx_payload(ctx: ToolContext) -> Dict[str, Any]:
    payload = ctx.to_audit_dict()
    # Preserve enough identity for a worker thread to re-evaluate policy. Avoid
    # copying request objects or any non-serializable runtime state.
    return {
        "username": payload.get("username", ""),
        "user_id": payload.get("user_id", ""),
        "role": payload.get("role", "readonly"),
        "is_admin": bool(payload.get("is_admin")),
        "can_deploy": bool(payload.get("can_deploy")),
        "auth_type": payload.get("auth_type", "session"),
        "token_id": payload.get("token_id", ""),
        "token_name": payload.get("token_name", ""),
        "token_owner": payload.get("token_owner", ""),
        "scopes": payload.get("scopes") or [],
        "allow_write": bool(payload.get("allow_write")),
        "allow_prod": bool(payload.get("allow_prod")),
        "client_name": payload.get("client_name", ""),
        "ip_address": payload.get("ip_address", ""),
        "user_agent": payload.get("user_agent", ""),
    }


def _ctx_from_payload(data: Dict[str, Any]) -> ToolContext:
    allowed = ToolContext.__dataclass_fields__.keys()
    return ToolContext(**{k: v for k, v in (data or {}).items() if k in allowed})


def job_to_dict(job: OperationJob) -> Dict[str, Any]:
    return {
        "id": job.id,
        "job_type": job.job_type,
        "source": job.source,
        "source_tool": job.source_tool,
        "title": job.title,
        "status": job.status,
        "progress": job.progress or 0,
        "risk_level": job.risk_level,
        "operator": job.operator or "-",
        "target": job.target or "-",
        "request": job.request_json or {},
        "result": job.result_json or {},
        "error_message": job.error_message or "",
        "audit_id": job.audit_id or "",
        "worker_id": job.worker_id or "",
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
    }


def list_operation_jobs(
    db: Session,
    *,
    status: str = "",
    source_tool: str = "",
    limit: int = 100,
) -> List[Dict[str, Any]]:
    q = db.query(OperationJob)
    if status:
        q = q.filter(OperationJob.status == status)
    if source_tool:
        q = q.filter(OperationJob.source_tool == source_tool)
    rows = q.order_by(OperationJob.created_at.desc()).limit(max(1, min(int(limit or 100), 500))).all()
    return [runtime_job_to_dict("tool", row) for row in rows]


def get_operation_job(db: Session, job_id: str) -> Optional[Dict[str, Any]]:
    row = db.query(OperationJob).filter(OperationJob.id == job_id).first()
    return runtime_job_to_dict("tool", row) if row else None


def mark_job_audit_id(db: Session, job_id: str, audit_id: str) -> None:
    job = db.query(OperationJob).filter(OperationJob.id == job_id).first()
    if not job:
        return
    job.audit_id = audit_id
    job.updated_at = _now()
    db.commit()


def create_tool_job(
    db: Session,
    *,
    tool_def,
    arguments: Dict[str, Any],
    ctx: ToolContext,
    policy_result: Dict[str, Any],
) -> Dict[str, Any]:
    job = OperationJob(
        id=uuid.uuid4().hex,
        job_type="mcp_tool",
        source=_source_from_ctx(ctx),
        source_tool=getattr(tool_def, "name", ""),
        title=f"工具执行 {getattr(tool_def, 'title', '') or getattr(tool_def, 'name', '')}",
        status="queued",
        progress=0,
        risk_level=getattr(tool_def, "risk", "high") or "high",
        operator=_actor_from_ctx(ctx),
        target=_target_from_args(arguments or {}),
        request_json={
            "tool": getattr(tool_def, "name", ""),
            "arguments": arguments or {},
            "context": _safe_ctx_payload(ctx),
            "policy": policy_result or {},
        },
        result_json={},
        created_at=_now(),
        updated_at=_now(),
    )
    db.add(job)
    db.commit()
    return job_to_dict(job)


def enqueue_tool_job(
    db: Session,
    *,
    tool_def,
    arguments: Dict[str, Any],
    ctx: ToolContext,
    policy_result: Dict[str, Any],
    auto_start: bool = True,
) -> Dict[str, Any]:
    job = create_tool_job(db, tool_def=tool_def, arguments=arguments, ctx=ctx, policy_result=policy_result)
    if auto_start:
        start_job_worker(job["id"])
    return job


def start_job_worker(job_id: str) -> None:
    thread = threading.Thread(target=_execute_tool_job, args=(job_id,), name=f"ops-job-{job_id[:8]}", daemon=True)
    thread.start()


def _set_job_status(
    db: Session,
    job_id: str,
    *,
    status: str,
    progress: Optional[int] = None,
    result: Any = None,
    error: str = "",
    worker_id: str = "",
    started: bool = False,
    finished: bool = False,
) -> None:
    job = db.query(OperationJob).filter(OperationJob.id == job_id).first()
    if not job:
        return
    job.status = status
    if progress is not None:
        job.progress = progress
    if result is not None:
        job.result_json = result if isinstance(result, dict) else {"value": result}
    if error:
        job.error_message = error
    if worker_id:
        job.worker_id = worker_id
    if started and not job.started_at:
        job.started_at = _now()
    if finished:
        job.finished_at = _now()
    job.updated_at = _now()
    db.commit()


def _execute_tool_job(job_id: str) -> None:
    worker_id = threading.current_thread().name
    db = SessionLocal()
    try:
        job = db.query(OperationJob).filter(OperationJob.id == job_id).first()
        if not job:
            return
        if job.status not in {"queued", "retrying"}:
            return
        _set_job_status(db, job_id, status="running", progress=10, worker_id=worker_id, started=True)
        request = job.request_json or {}
        tool_name = str(request.get("tool") or job.source_tool or "")
        args = dict(request.get("arguments") or {})
        ctx = _ctx_from_payload(request.get("context") or {})

        from app.services.tool_policy import enforce_tool_policy
        from app.services.tool_registry import register_builtin_tools, registry
        from app.services.audit_writer import record_tool_call_async

        register_builtin_tools()
        tool = registry.get(tool_name)
        policy_result = enforce_tool_policy(tool, args, ctx, db)
        _set_job_status(db, job_id, status="running", progress=35)
        result = tool.handler(args, ctx, db)
        _set_job_status(db, job_id, status="running", progress=90, result={"result": result})
        try:
            record_tool_call_async(
                tool_name=tool_name,
                ctx=ctx,
                input_args=args,
                normalized_args=args,
                result={"job_id": job_id, "result": result},
                status="success",
                risk_level=getattr(tool, "risk", "high"),
                policy_result=policy_result,
                related_job_id=job_id,
            )
        except Exception:
            pass
        _set_job_status(db, job_id, status="success", progress=100, result={"result": result}, finished=True)
    except Exception as exc:
        error = str(exc) or exc.__class__.__name__
        trace = traceback.format_exc(limit=8)
        try:
            _set_job_status(db, job_id, status="failed", progress=100, error=error, result={"error": error, "traceback": trace}, finished=True)
        except Exception:
            pass
        try:
            from app.services.audit_writer import record_tool_call_async
            from app.services.tool_registry import registry
            request = (db.query(OperationJob).filter(OperationJob.id == job_id).first().request_json or {}) if db else {}
            ctx = _ctx_from_payload((request or {}).get("context") or {})
            tool_name = str((request or {}).get("tool") or "")
            risk = getattr(registry.get(tool_name), "risk", "high") if tool_name else "high"
            record_tool_call_async(
                tool_name=tool_name or "unknown",
                ctx=ctx,
                input_args=(request or {}).get("arguments") or {},
                normalized_args=(request or {}).get("arguments") or {},
                result={"job_id": job_id, "error": error},
                status="failed",
                risk_level=risk,
                blocked_reason=error,
                related_job_id=job_id,
            )
        except Exception:
            pass
    finally:
        try:
            db.close()
        except Exception:
            pass
