import json
import logging
from typing import Dict, Optional
from fastapi import APIRouter, HTTPException, Request, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from app.api.helpers import api_response, audit
from app.db import get_db
from app.core.auth_v2 import require_auth, require_admin, require_operator, verify_session_token
from app.core.rbac import explain_operation_risk
from app.maintenance.service import CleanupService

logger = logging.getLogger(__name__)

maintenance_router = APIRouter(prefix="/api/v2/maintenance", tags=["数据库维护与受控执行"])


class ConnectionCreate(BaseModel):
    name: str = Field(..., max_length=64)
    environment: str = Field(..., max_length=64)
    db_type: str = Field(default="mysql", max_length=32)
    host: str = Field(..., max_length=255)
    port: int = Field(default=3306)
    username: str = Field(..., max_length=64)
    password: Optional[str] = None
    database_name: Optional[str] = Field(None, max_length=128)
    description: Optional[str] = None
    use_ssh_tunnel: bool = False
    ssh_mode: Optional[str] = Field(None, max_length=16)
    ssh_server_id: Optional[str] = Field(None, max_length=32)
    ssh_server_name: Optional[str] = Field(None, max_length=64)
    ssh_host: Optional[str] = Field(None, max_length=255)
    ssh_port: int = Field(default=22)
    ssh_username: Optional[str] = Field(None, max_length=64)
    ssh_password: Optional[str] = None
    ssh_key_path: Optional[str] = Field(None, max_length=255)
    ssh_key_passphrase: Optional[str] = None
    ssh_key_content: Optional[str] = None
    ssh_remote_bind_host: Optional[str] = Field(None, max_length=255)
    ssh_target_server_name: Optional[str] = Field(None, max_length=128)
    ssh_target_host: Optional[str] = Field(None, max_length=255)
    ssh_target_port: int = Field(default=22)
    ssh_target_username: Optional[str] = Field(None, max_length=64)
    ssh_target_password: Optional[str] = None
    ssh_target_key_path: Optional[str] = Field(None, max_length=255)
    ssh_target_key_passphrase: Optional[str] = None
    allow_dml: bool = False
    allowed_dml_types: Optional[list[str]] = None
    allowed_tables: Optional[list[str]] = None
    blocked_tables: Optional[list[str]] = None
    max_affected_rows_default: int = Field(default=100, ge=1, le=1000)
    require_dml_reason: bool = True


class JobCreate(BaseModel):
    name: str = Field(..., max_length=256)
    environment: str = Field(..., max_length=64)
    connection_id: Optional[str] = None
    connection_name: Optional[str] = Field(None, max_length=64)
    database_name: str = Field(..., max_length=128)
    table_name: str = Field(..., max_length=128)
    date_column: str = Field(..., max_length=128)
    cutoff_time: str = Field(..., max_length=64)
    batch_size: int = Field(default=100000)
    batch_interval_seconds: int = Field(default=3)
    max_delete_rows: Optional[int] = None  # controlled execution threshold; execution stops at this cap
    approval_required: bool = Field(default=True)
    execution_window_start: Optional[str] = Field(None, max_length=8)
    execution_window_end: Optional[str] = Field(None, max_length=8)


class JobApprove(BaseModel):
    pass


class JobStart(BaseModel):
    confirm_text: str


class JobReject(BaseModel):
    reason: Optional[str] = None


class SqlQueryRequest(BaseModel):
    sql: str = Field(..., max_length=20000)
    database_name: Optional[str] = Field(None, max_length=128)
    limit: int = Field(default=100, ge=1, le=1000)
    timeout_seconds: int = Field(default=30, ge=1, le=120)


class SavedSqlRequest(BaseModel):
    name: str = Field(..., max_length=128)
    category: str = Field(default="default", max_length=64)
    description: Optional[str] = None
    connection_id: Optional[str] = None
    database_name: Optional[str] = Field(None, max_length=128)
    sql_text: str = Field(..., max_length=20000)




def _parse_json_text(value):
    if not value:
        return None
    try:
        return json.loads(value)
    except Exception:
        return None

def _get_operator(request: Request) -> str:
    state_username = getattr(getattr(request, "state", None), "username", None)
    if state_username:
        return str(state_username)
    token = request.cookies.get("ops_session_v2")
    if token:
        token_data = verify_session_token(token)
        if token_data and token_data.get("username"):
            return str(token_data["username"])
    return "anonymous"




@maintenance_router.get("/ssh-server-assets")
def list_ssh_server_assets(request: Request, db: Session = Depends(get_db)):
    """Return server assets usable as SSH bastion/target hosts for DB proxy."""
    require_auth(request, db)
    from app.maintenance.server_assets import list_server_assets
    return api_response(data=list_server_assets(db))


@maintenance_router.get("/connections")
def list_connections(request: Request, db: Session = Depends(get_db), environment: str = None):
    require_auth(request, db)
    svc = CleanupService(db)
    conns = svc.list_connections(environment=environment)
    data = []
    for c in conns:
        data.append({
            "id": c.id,
            "name": c.name,
            "environment": c.environment,
            "db_type": c.db_type,
            "host": c.host,
            "port": c.port,
            "username": c.username,
            "database_name": c.database_name,
            "description": getattr(c, "description", None),
            "use_ssh_tunnel": bool(getattr(c, "use_ssh_tunnel", False)),
            "ssh_mode": getattr(c, "ssh_mode", None) or "manual",
            "ssh_server_id": getattr(c, "ssh_server_id", None),
            "ssh_server_name": getattr(c, "ssh_server_name", None),
            "ssh_host": getattr(c, "ssh_host", None),
            "ssh_port": getattr(c, "ssh_port", None),
            "ssh_username": getattr(c, "ssh_username", None),
            "ssh_key_path": getattr(c, "ssh_key_path", None),
            "ssh_remote_bind_host": getattr(c, "ssh_remote_bind_host", None),
            "ssh_target_server_name": getattr(c, "ssh_target_server_name", None),
            "ssh_target_host": getattr(c, "ssh_target_host", None),
            "ssh_target_port": getattr(c, "ssh_target_port", None),
            "ssh_target_username": getattr(c, "ssh_target_username", None),
            "ssh_target_key_path": getattr(c, "ssh_target_key_path", None),
            "ssh_key_has_content": bool(getattr(c, "ssh_key_content_encrypted", None)),
            "allow_dml": bool(getattr(c, "allow_dml", False)),
            "allowed_dml_types": getattr(c, "allowed_dml_types", None) or [],
            "allowed_tables": getattr(c, "allowed_tables", None) or [],
            "blocked_tables": getattr(c, "blocked_tables", None) or [],
            "max_affected_rows_default": getattr(c, "max_affected_rows_default", 100) or 100,
            "require_dml_reason": bool(getattr(c, "require_dml_reason", True)),
            "created_by": c.created_by,
            "created_at": c.created_at.isoformat() if c.created_at else None,
        })
    return api_response(data=data)


class LogRetentionPreviewRequest(BaseModel):
    retention_days: Optional[Dict[str, int]] = None
    archive: bool = True


class LogRetentionExecuteRequest(BaseModel):
    retention_days: Optional[Dict[str, int]] = None
    archive: bool = True
    confirm_text: str = "EXECUTE LOG RETENTION"


@maintenance_router.get("/log-retention/preview")
def log_retention_preview(request: Request, db: Session = Depends(get_db)):
    require_admin(request, db)
    from app.services.sqlite_cleanup import LogRetentionPolicy
    policy = LogRetentionPolicy(archive=True)
    preview_data = policy.preview(db)
    return api_response(data=preview_data)


@maintenance_router.post("/log-retention/execute")
def log_retention_execute(body: LogRetentionExecuteRequest, request: Request, db: Session = Depends(get_db)):
    require_admin(request, db)
    if body.confirm_text != "EXECUTE LOG RETENTION":
        raise HTTPException(status_code=400, detail="confirm_text must be 'EXECUTE LOG RETENTION'")
    from app.services.sqlite_cleanup import LogRetentionPolicy
    policy = LogRetentionPolicy(retention_days=body.retention_days, archive=body.archive)
    result = policy.execute(db, dry_run=False)
    audit(db, action="log_retention_execute", target_type="system", target_name="log_retention", details=json.dumps({"result": result}))
    return api_response(data=result)


@maintenance_router.post("/connections")
def create_connection(body: ConnectionCreate, request: Request, db: Session = Depends(get_db)):
    require_admin(request, db)
    svc = CleanupService(db)
    try:
        conn = svc.create_connection(body.model_dump(), _get_operator(request))
        return api_response(data={"id": conn.id, "name": conn.name})
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@maintenance_router.get("/connections/{connection_id}")
def get_connection(connection_id: str, request: Request, db: Session = Depends(get_db)):
    require_auth(request, db)
    svc = CleanupService(db)
    conn = svc.get_connection(connection_id)
    if not conn:
        raise HTTPException(status_code=404, detail="Connection not found")
    return api_response(data={
        "id": conn.id, "name": conn.name, "environment": conn.environment,
        "db_type": conn.db_type, "host": conn.host, "port": conn.port,
        "username": conn.username, "database_name": conn.database_name,
        "description": getattr(conn, "description", None),
        "use_ssh_tunnel": bool(getattr(conn, "use_ssh_tunnel", False)),
        "ssh_mode": getattr(conn, "ssh_mode", None) or "manual",
        "ssh_server_id": getattr(conn, "ssh_server_id", None),
        "ssh_server_name": getattr(conn, "ssh_server_name", None),
        "ssh_host": getattr(conn, "ssh_host", None),
        "ssh_port": getattr(conn, "ssh_port", None),
        "ssh_username": getattr(conn, "ssh_username", None),
        "ssh_key_path": getattr(conn, "ssh_key_path", None),
        "ssh_remote_bind_host": getattr(conn, "ssh_remote_bind_host", None),
        "ssh_key_has_content": bool(getattr(conn, "ssh_key_content_encrypted", None)),
        "allow_dml": bool(getattr(conn, "allow_dml", False)),
        "allowed_dml_types": getattr(conn, "allowed_dml_types", None) or [],
        "allowed_tables": getattr(conn, "allowed_tables", None) or [],
        "blocked_tables": getattr(conn, "blocked_tables", None) or [],
        "max_affected_rows_default": getattr(conn, "max_affected_rows_default", 100) or 100,
        "require_dml_reason": bool(getattr(conn, "require_dml_reason", True)),
    })




@maintenance_router.post("/connections/{connection_id}/test")
def test_connection(connection_id: str, request: Request, db: Session = Depends(get_db)):
    require_auth(request, db)
    svc = CleanupService(db)
    conn = svc.get_connection(connection_id)
    if not conn:
        raise HTTPException(status_code=404, detail="Connection not found")
    executor = svc._connection_executor(conn, conn.database_name)
    try:
        raw = executor._get_connection()
        with raw.cursor() as cur:
            cur.execute("SELECT 1 AS ok")
            row = cur.fetchone()
        return api_response(data={
            "ok": True,
            "via_ssh_tunnel": bool(getattr(conn, "use_ssh_tunnel", False)),
            "result": row,
        }, message="Connection test passed")
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))
    finally:
        executor.close()


@maintenance_router.put("/connections/{connection_id}")
def update_connection(connection_id: str, body: ConnectionCreate, request: Request, db: Session = Depends(get_db)):
    require_admin(request, db)
    svc = CleanupService(db)
    try:
        conn = svc.update_connection(connection_id, body.model_dump())
        return api_response(data={"id": conn.id, "name": conn.name}, message="Connection updated")
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@maintenance_router.delete("/connections/{connection_id}")
def delete_connection(connection_id: str, request: Request, db: Session = Depends(get_db)):
    require_admin(request, db)
    svc = CleanupService(db)
    if not svc.delete_connection(connection_id):
        raise HTTPException(status_code=404, detail="Connection not found")
    return api_response(message="Connection deleted")


class SshKeyUpload(BaseModel):
    key_content: str


@maintenance_router.put("/connections/{connection_id}/ssh-key")
def upload_ssh_key(connection_id: str, body: SshKeyUpload, request: Request, db: Session = Depends(get_db)):
    require_admin(request, db)
    svc = CleanupService(db)
    try:
        svc.set_ssh_key_content(connection_id, body.key_content)
        return api_response(message="SSH key uploaded")
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@maintenance_router.delete("/connections/{connection_id}/ssh-key")
def delete_ssh_key(connection_id: str, request: Request, db: Session = Depends(get_db)):
    require_admin(request, db)
    svc = CleanupService(db)
    try:
        svc.remove_ssh_key_content(connection_id)
        return api_response(message="SSH key deleted")
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@maintenance_router.get("/connections/{connection_id}/tables")
def list_tables(connection_id: str, request: Request, db: Session = Depends(get_db), database_name: str = Query(...)):
    require_auth(request, db)
    svc = CleanupService(db)
    try:
        tables = svc.get_tables(connection_id, database_name)
        return api_response(data=tables)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@maintenance_router.get("/connections/{connection_id}/columns")
def list_columns(
    connection_id: str, request: Request, db: Session = Depends(get_db),
    database_name: str = Query(...), table_name: str = Query(...),
):
    require_auth(request, db)
    svc = CleanupService(db)
    try:
        columns = svc.get_columns(connection_id, database_name, table_name)
        return api_response(data=columns)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))




@maintenance_router.post("/connections/{connection_id}/query/analyze")
def analyze_sql_query(connection_id: str, body: SqlQueryRequest, request: Request, db: Session = Depends(get_db)):
    require_auth(request, db)
    from app.maintenance.sql_query import SqlQueryService, analyze_sql_guard
    svc = SqlQueryService(db)
    try:
        conn = svc._get_connection(connection_id)
        result = analyze_sql_guard(body.sql, getattr(conn, "environment", ""))
        result["connection_name"] = conn.name
        result["environment"] = conn.environment
        result["database_name"] = body.database_name or conn.database_name
        return api_response(data=result)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@maintenance_router.post("/connections/{connection_id}/query/preview")
def preview_sql_query(connection_id: str, body: SqlQueryRequest, request: Request, db: Session = Depends(get_db)):
    require_auth(request, db)
    from app.maintenance.sql_query import SqlQueryService
    svc = SqlQueryService(db)
    try:
        preview = svc.preview(connection_id, body.sql, body.limit, body.timeout_seconds)
        conn = svc._get_connection(connection_id)
        preview["risk"] = explain_operation_risk("sql", conn.environment, conn.name, {"uses_ssh_tunnel": bool(getattr(conn, "use_ssh_tunnel", False))})
        return api_response(data=preview)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@maintenance_router.post("/connections/{connection_id}/query/execute")
def execute_sql_query(connection_id: str, body: SqlQueryRequest, request: Request, db: Session = Depends(get_db)):
    require_operator(request, db)
    from app.maintenance.sql_query import SqlQueryService
    svc = SqlQueryService(db)
    try:
        result = svc.execute(
            connection_id=connection_id,
            sql=body.sql,
            database_name=body.database_name,
            limit=body.limit,
            timeout_seconds=body.timeout_seconds,
            operator=_get_operator(request),
        )
        audit("sql.query.execute", "database", result.get("connection_name"), f"user={_get_operator(request)} rows={result.get('row_count')} duration_ms={result.get('duration_ms')} tunnel={result.get('via_ssh_tunnel')}")
        return api_response(data=result)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@maintenance_router.get("/query/history")
def list_sql_query_history(
    request: Request,
    db: Session = Depends(get_db),
    connection_id: str = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    status: str = None,
    query_type: str = None,
):
    require_auth(request, db)
    from app.maintenance.sql_query import SqlQueryService
    svc = SqlQueryService(db)
    payload = svc.history(connection_id=connection_id, limit=limit, offset=offset, status=status, query_type=query_type)
    items = payload.get("items", [])
    return api_response(data={
        "items": [{
            "id": h.id,
            "connection_id": h.connection_id,
            "connection_name": h.connection_name,
            "database_name": h.database_name,
            "sql_text": h.sql_text,
            "query_type": h.query_type,
            "status": h.status,
            "row_count": h.row_count,
            "duration_ms": h.duration_ms,
            "error_message": h.error_message,
            "executed_by": h.executed_by,
            "created_at": h.created_at.isoformat() if h.created_at else None,
        } for h in items],
        "pagination": payload.get("pagination", {}),
    })


@maintenance_router.get("/query/saved")
def list_saved_sql(request: Request, db: Session = Depends(get_db), connection_id: str = None, category: str = None, keyword: str = None, limit: int = Query(default=200)):
    require_auth(request, db)
    from app.maintenance.sql_query import SavedSqlService
    svc = SavedSqlService(db)
    items = svc.list(connection_id=connection_id, category=category, keyword=keyword, limit=limit)
    return api_response(data=[{
        "id": x.id, "name": x.name, "category": x.category, "description": x.description,
        "connection_id": x.connection_id, "database_name": x.database_name, "sql_text": x.sql_text,
        "created_by": x.created_by, "updated_by": x.updated_by,
        "created_at": x.created_at.isoformat() if x.created_at else None,
        "updated_at": x.updated_at.isoformat() if x.updated_at else None,
    } for x in items])


@maintenance_router.post("/query/saved")
def create_saved_sql(body: SavedSqlRequest, request: Request, db: Session = Depends(get_db)):
    require_operator(request, db)
    from app.maintenance.sql_query import SavedSqlService
    svc = SavedSqlService(db)
    try:
        x = svc.create(body.model_dump(), _get_operator(request))
        audit("sql.saved.create", "saved_sql", x.name, f"user={_get_operator(request)}")
        return api_response(data={"id": x.id, "name": x.name}, message="Saved SQL created")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@maintenance_router.put("/query/saved/{item_id}")
def update_saved_sql(item_id: str, body: SavedSqlRequest, request: Request, db: Session = Depends(get_db)):
    require_operator(request, db)
    from app.maintenance.sql_query import SavedSqlService
    svc = SavedSqlService(db)
    try:
        x = svc.update(item_id, body.model_dump(), _get_operator(request))
        audit("sql.saved.update", "saved_sql", x.name, f"user={_get_operator(request)}")
        return api_response(data={"id": x.id, "name": x.name}, message="Saved SQL updated")
    except ValueError as e:
        raise HTTPException(status_code=404 if "not found" in str(e).lower() else 400, detail=str(e))


@maintenance_router.delete("/query/saved/{item_id}")
def delete_saved_sql(item_id: str, request: Request, db: Session = Depends(get_db)):
    require_operator(request, db)
    from app.maintenance.sql_query import SavedSqlService
    svc = SavedSqlService(db)
    if not svc.delete(item_id):
        raise HTTPException(status_code=404, detail="Saved SQL not found")
    audit("sql.saved.delete", "saved_sql", item_id, f"user={_get_operator(request)}")
    return api_response(message="Saved SQL deleted")


@maintenance_router.get("/jobs")
def list_jobs(request: Request, db: Session = Depends(get_db), status: str = None, limit: int = Query(default=50)):
    require_auth(request, db)
    svc = CleanupService(db)
    jobs = svc.list_jobs(status=status, limit=limit)
    data = []
    for j in jobs:
        data.append({
            "id": j.id, "name": j.name, "environment": j.environment,
            "connection_name": j.connection_name, "database_name": j.database_name,
            "table_name": j.table_name, "date_column": j.date_column,
            "cutoff_time": j.cutoff_time, "batch_size": j.batch_size,
            "matched_rows": j.matched_rows, "deleted_rows": j.deleted_rows,
            "status": j.status, "risk_level": j.risk_level,
            "created_by": j.created_by,
            "created_at": j.created_at.isoformat() if j.created_at else None,
            "started_at": j.started_at.isoformat() if j.started_at else None,
            "finished_at": j.finished_at.isoformat() if j.finished_at else None,
        })
    return api_response(data=data)


@maintenance_router.post("/jobs")
def create_job(body: JobCreate, request: Request, db: Session = Depends(get_db)):
    require_operator(request, db)
    svc = CleanupService(db)
    try:
        job = svc.create_job(body.model_dump(), _get_operator(request))
        return api_response(data={"id": job.id, "name": job.name, "status": job.status})
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@maintenance_router.get("/jobs/{job_id}")
def get_job(job_id: str, request: Request, db: Session = Depends(get_db)):
    require_auth(request, db)
    svc = CleanupService(db)
    job = svc.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return api_response(data={
        "id": job.id, "name": job.name, "environment": job.environment,
        "connection_id": job.connection_id, "connection_name": job.connection_name,
        "database_name": job.database_name, "table_name": job.table_name,
        "date_column": job.date_column, "cutoff_time": job.cutoff_time,
        "batch_size": job.batch_size, "batch_interval_seconds": job.batch_interval_seconds,
        "max_delete_rows": job.max_delete_rows,
        "matched_rows": job.matched_rows, "deleted_rows": job.deleted_rows,
        "estimated_batches": job.estimated_batches,
        "status": job.status, "risk_level": job.risk_level,
        "dry_run_result": job.dry_run_result,
        "dry_run": _parse_json_text(job.dry_run_result),
        "last_error": job.last_error,
        "approval_required": job.approval_required,
        "approved_by": job.approved_by,
        "approved_at": job.approved_at.isoformat() if job.approved_at else None,
        "execution_window_start": job.execution_window_start,
        "execution_window_end": job.execution_window_end,
        "created_by": job.created_by,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
    })


@maintenance_router.post("/jobs/{job_id}/dry-run")
def dry_run_job(job_id: str, request: Request, db: Session = Depends(get_db)):
    require_operator(request, db)
    svc = CleanupService(db)
    try:
        result = svc.dry_run(job_id, _get_operator(request))
        return api_response(data=result)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@maintenance_router.post("/jobs/{job_id}/submit")
def submit_job(job_id: str, request: Request, db: Session = Depends(get_db)):
    require_operator(request, db)
    svc = CleanupService(db)
    try:
        svc.submit_for_approval(job_id, _get_operator(request))
        return api_response(message="Job submitted for approval")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@maintenance_router.post("/jobs/{job_id}/approve")
def approve_job(job_id: str, request: Request, db: Session = Depends(get_db)):
    require_admin(request, db)
    svc = CleanupService(db)
    try:
        svc.approve(job_id, _get_operator(request))
        return api_response(message="Job approved")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@maintenance_router.post("/jobs/{job_id}/reject")
def reject_job(job_id: str, body: JobReject, request: Request, db: Session = Depends(get_db)):
    require_admin(request, db)
    svc = CleanupService(db)
    try:
        svc.reject(job_id, _get_operator(request))
        return api_response(message="Job rejected")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@maintenance_router.get("/jobs/{job_id}/start-plan")
def start_plan_job(job_id: str, request: Request, db: Session = Depends(get_db)):
    require_operator(request, db)
    svc = CleanupService(db)
    try:
        return api_response(data=svc.build_start_plan(job_id))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@maintenance_router.post("/jobs/{job_id}/start")
def start_job(job_id: str, body: JobStart, request: Request, db: Session = Depends(get_db)):
    require_admin(request, db)
    svc = CleanupService(db)
    try:
        svc.start(job_id, body.confirm_text, _get_operator(request))
        return api_response(message="Job started")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@maintenance_router.post("/jobs/{job_id}/pause")
def pause_job(job_id: str, request: Request, db: Session = Depends(get_db)):
    require_operator(request, db)
    svc = CleanupService(db)
    try:
        svc.pause(job_id, _get_operator(request))
        return api_response(message="Job paused")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@maintenance_router.post("/jobs/{job_id}/resume")
def resume_job(job_id: str, request: Request, db: Session = Depends(get_db)):
    require_operator(request, db)
    svc = CleanupService(db)
    try:
        svc.resume(job_id, _get_operator(request))
        return api_response(message="Job resumed")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@maintenance_router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str, request: Request, db: Session = Depends(get_db)):
    require_operator(request, db)
    svc = CleanupService(db)
    try:
        svc.cancel(job_id, _get_operator(request))
        return api_response(message="Job cancelled")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@maintenance_router.get("/jobs/{job_id}/batches")
def get_batches(job_id: str, request: Request, db: Session = Depends(get_db)):
    require_auth(request, db)
    svc = CleanupService(db)
    batches = svc.get_batches(job_id)
    data = []
    for b in batches:
        data.append({
            "id": b.id, "job_id": b.job_id, "batch_no": b.batch_no,
            "affected_rows": b.affected_rows, "duration_ms": b.duration_ms,
            "remaining_rows": b.remaining_rows, "sql_text": b.sql_text,
            "status": b.status, "error_message": b.error_message,
            "started_at": b.started_at.isoformat() if b.started_at else None,
            "finished_at": b.finished_at.isoformat() if b.finished_at else None,
        })
    return api_response(data=data)


@maintenance_router.get("/jobs/{job_id}/events")
def get_events(job_id: str, request: Request, db: Session = Depends(get_db)):
    require_auth(request, db)
    svc = CleanupService(db)
    events = svc.get_events(job_id)
    data = []
    for e in events:
        data.append({
            "id": e.id, "job_id": e.job_id, "event_type": e.event_type,
            "operator": e.operator, "message": e.message, "details": e.details,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        })
    return api_response(data=data)
