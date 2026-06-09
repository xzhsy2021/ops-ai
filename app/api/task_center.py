"""任务中心与审计 API

聚合发布任务、SQL 查询、数据库清理任务，让运维人员可以在一个入口查看
运行中/成功/失败/待审批的任务，并补充审计日志检索能力。
"""
import io
import csv

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.helpers import api_response, audit
from app.core.auth_v2 import require_auth, require_admin
from app.db import get_db
from app.config.audit import load_audit_logs
from app.core.rbac import explain_operation_risk
from app.services.audit_chain import build_operation_chain, list_operation_chains
from app.domain.runtime import count_runtime_jobs, delete_runtime_tasks, list_runtime_jobs, get_runtime_job_detail, reconcile_stale_operation_jobs

router = APIRouter(prefix="/api/v2/tasks", tags=["任务中心"])
audit_router = APIRouter(prefix="/api/v2/audit", tags=["审计"])


@router.get("")
async def list_tasks(request: Request, status: str = "", kind: str = "", limit: int = 100, offset: int = 0, db: Session = Depends(get_db)):
    user = require_auth(request, db)
    limit = max(1, min(limit, 500))
    offset = max(0, int(offset or 0))
    if not kind or kind in ("tool", "mcp_tool", "job", "mcp"):
        reconcile_stale_operation_jobs(db)
    total = count_runtime_jobs(db, status=status, kind=kind)
    tasks = list_runtime_jobs(db, status=status, kind=kind, limit=limit, offset=offset)
    for task in tasks:
        if task["kind"] == "deploy":
            d = task.get("detail") or {}
            task["detail"]["risk"] = explain_operation_risk("deploy", d.get("environment"), d.get("system"))
    audit("tasks.list", "task_center", kind or "all", f"user={user.get('username')} status={status} limit={limit} offset={offset}")
    return api_response(data={"items": tasks, "total": total, "limit": limit, "offset": offset, "statuses": ["queued", "pending", "running", "success", "failed", "cancelled", "paused"]})


@router.post("/delete")
async def delete_tasks(request: Request, db: Session = Depends(get_db)):
    user = require_auth(request, db)
    data = await request.json()
    items = data.get("items") or []
    confirm_text = str(data.get("confirm_text") or "")
    force = bool(data.get("force", False))
    result = delete_runtime_tasks(db, items, confirm_text=confirm_text, actor=user.get("username") or "", force=force)
    audit(
        "tasks.delete_many",
        "task_center",
        ",".join(f"{item.get('kind')}:{item.get('id')}" for item in result.get("items", [])),
        f"user={user.get('username')} force={force} deleted={result.get('deleted')}",
    )
    return api_response(data=result, message="Tasks deleted")


@router.delete("/{kind}/{item_id}")
async def delete_task(kind: str, item_id: str, request: Request, db: Session = Depends(get_db)):
    user = require_auth(request, db)
    data = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    force = bool((data or {}).get("force", False))
    result = delete_runtime_tasks(
        db,
        [{"kind": kind, "id": item_id}],
        confirm_text=str((data or {}).get("confirm_text") or ""),
        actor=user.get("username") or "",
        force=force,
    )
    audit(
        "tasks.delete",
        "task_center",
        f"{kind}:{item_id}",
        f"user={user.get('username')} force={force} deleted={result.get('deleted')}",
    )
    return api_response(data=result, message="Task deleted")


@router.get("/{kind}/{item_id}")
async def task_detail(kind: str, item_id: str, request: Request, db: Session = Depends(get_db)):
    require_auth(request, db)
    result = get_runtime_job_detail(db, kind, item_id)
    if not result:
        raise HTTPException(status_code=404, detail=f"{kind} not found")
    return api_response(data=result)


@audit_router.get("/operation-chains")
async def list_audit_operation_chains(
    request: Request,
    limit: int = 50,
    kind: str = "",
    status: str = "",
    risk: str = "",
    db: Session = Depends(get_db),
):
    """List recent reconstructed operation chains.

This endpoint is read-only and links tool calls, unified jobs, tool plans,
release records and legacy audit logs for replay/triage.
    """
    user = require_admin(request, db)
    data = list_operation_chains(db, limit=limit, kind=kind, status=status, risk=risk)
    audit("audit.operation_chains.list", "audit", kind or "all", f"user={user.get('username')} limit={limit} status={status} risk={risk}")
    return api_response(data=data)


@audit_router.get("/operation-chains/{chain_id:path}")
async def get_audit_operation_chain(chain_id: str, request: Request, include_raw: bool = False, db: Session = Depends(get_db)):
    """Return a reconstructed read-only operation chain by prefix id.

Supported chain id prefixes: tool:, job:, plan:, deployment:, audit:.
    """
    user = require_admin(request, db)
    data = build_operation_chain(db, chain_id=chain_id, include_raw=include_raw)
    if data.get("summary", {}).get("node_count", 0) == 0 and data.get("summary", {}).get("timeline_count", 0) == 0:
        raise HTTPException(status_code=404, detail="Operation chain not found")
    audit("audit.operation_chain.view", "audit", chain_id, f"user={user.get('username')}")
    return api_response(data=data)


@audit_router.get("/operation-chain")
async def get_audit_operation_chain_by_ref(
    request: Request,
    tool_call_id: str = "",
    job_id: str = "",
    plan_id: str = "",
    deployment_id: str = "",
    audit_id: str = "",
    db: Session = Depends(get_db),
):
    user = require_admin(request, db)
    data = build_operation_chain(
        db,
        tool_call_id=tool_call_id,
        job_id=job_id,
        plan_id=plan_id,
        deployment_id=deployment_id,
        audit_id=audit_id,
    )
    audit("audit.operation_chain.view", "audit", data.get("chain_id") or "by_ref", f"user={user.get('username')}")
    return api_response(data=data)


@audit_router.get("")
async def list_audit(request: Request, response: Response, limit: int = 200, offset: int = 0, action: str = "", since_id: str = "", db: Session = Depends(get_db)):
    require_admin(request, db)
    limit = max(1, min(limit, 1000))
    offset = max(0, int(offset or 0))
    rows = load_audit_logs(5000)
    if action:
        rows = [r for r in rows if action.lower() in (r.get("action") or "").lower()]
    if since_id:
        try:
            sid = int(since_id)
            rows = [r for r in rows if int(r.get("id", 0)) < sid]
        except (ValueError, TypeError):
            pass
    total = len(rows)
    rows = rows[offset:offset + limit]
    from app.api.helpers import compute_list_etag, check_etag_not_modified
    etag = compute_list_etag(rows, "audit")
    not_modified = check_etag_not_modified(request, etag)
    if not_modified:
        return not_modified
    response.headers["ETag"] = etag
    return api_response(data={"items": rows, "total": total, "limit": limit, "offset": offset})


@audit_router.get("/export")
async def export_audit_csv(request: Request, limit: int = 1000, action: str = "", db: Session = Depends(get_db)):
    require_admin(request, db)
    rows = load_audit_logs(max(1, min(limit, 5000)))
    if action:
        rows = [r for r in rows if action.lower() in (r.get("action") or "").lower()]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=["id", "created_at", "action", "target_type", "target_name", "details"])
    writer.writeheader()
    for row in rows:
        writer.writerow({k: row.get(k, "") for k in writer.fieldnames})
    buf.seek(0)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=audit_logs.csv"})
