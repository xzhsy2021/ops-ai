from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.helpers import api_response, audit
from app.core.auth_v2 import require_auth, require_operator, require_admin
from app.db import get_db
from app.services.db_query_export import DbQueryExportService, export_media_type

router = APIRouter(prefix="/api/v2/db", tags=["数据库查询与导出"])


class DbQueryPayload(BaseModel):
    sql: str = Field(..., max_length=20000)
    connection_id: Optional[str] = ""
    database_name: Optional[str] = ""
    limit: int = Field(default=100, ge=1, le=5000)
    timeout_seconds: int = Field(default=30, ge=1, le=120)


class DbExportPayload(DbQueryPayload):
    format: str = Field(default="csv", max_length=32)
    filename_hint: Optional[str] = Field(default="", max_length=128)
    table_name: Optional[str] = Field(default="", max_length=128)


class DbExecutePayload(BaseModel):
    sql: str = Field(..., max_length=20000)
    connection_id: Optional[str] = ""
    database_name: Optional[str] = ""
    max_affected_rows: int = Field(default=100, ge=1, le=1000)
    preview_level: Optional[str] = Field(default="standard", max_length=16)
    confirm_text: Optional[str] = ""
    reason: Optional[str] = Field(default="", max_length=1000)


class DeleteDbExportsPayload(BaseModel):
    export_ids: list[str] = Field(default_factory=list, max_length=200)


class DeleteDmlExecutionsPayload(BaseModel):
    execution_ids: list[str] = Field(default_factory=list, max_length=200)


def _operator_from_user(user: Dict[str, Any]) -> str:
    return str(user.get("username") or user.get("id") or "anonymous")


@router.get("/tables")
def list_db_tables(
    request: Request,
    connection_id: str = "",
    database_name: str = "",
    db: Session = Depends(get_db),
):
    require_auth(request, db)
    return api_response(data=DbQueryExportService(db).list_tables(connection_id=connection_id, database_name=database_name))


@router.get("/tables/{table_name}/schema")
def describe_db_table(
    table_name: str,
    request: Request,
    connection_id: str = "",
    database_name: str = "",
    db: Session = Depends(get_db),
):
    require_auth(request, db)
    return api_response(data=DbQueryExportService(db).describe_table(table_name, connection_id=connection_id, database_name=database_name))


@router.post("/query")
def query_db_readonly(payload: DbQueryPayload, request: Request, db: Session = Depends(get_db)):
    user = require_auth(request, db)
    result = DbQueryExportService(db).query_readonly(
        sql=payload.sql,
        operator=_operator_from_user(user),
        connection_id=payload.connection_id or "",
        database_name=payload.database_name or "",
        limit=payload.limit,
        timeout_seconds=payload.timeout_seconds,
    )
    audit("db.query.readonly", "database", result.get("connection_name") or "local_ops_db", f"user={_operator_from_user(user)} rows={result.get('row_count')} source={result.get('source')}")
    return api_response(data=result)


@router.post("/execute/preview")
def preview_db_execute(payload: DbExecutePayload, request: Request, db: Session = Depends(get_db)):
    user = require_admin(request, db)
    result = DbQueryExportService(db).preview_execute_sql(
        sql=payload.sql,
        operator=_operator_from_user(user),
        connection_id=payload.connection_id or "",
        database_name=payload.database_name or "",
        max_affected_rows=payload.max_affected_rows,
        preview_level=payload.preview_level or "standard",
    )
    audit("db.execute.preview", "database", result.get("table_name") or result.get("connection_name") or "local_ops_db", f"user={_operator_from_user(user)} source={result.get('source')} type={result.get('query_type')} estimated={result.get('estimated_affected_rows')}")
    return api_response(data=result, message="DB execute preview generated")


@router.post("/execute")
def execute_db_sql(payload: DbExecutePayload, request: Request, db: Session = Depends(get_db)):
    user = require_admin(request, db)
    result = DbQueryExportService(db).execute_sql(
        sql=payload.sql,
        operator=_operator_from_user(user),
        connection_id=payload.connection_id or "",
        database_name=payload.database_name or "",
        max_affected_rows=payload.max_affected_rows,
        confirm_text=payload.confirm_text or "",
        reason=payload.reason or "",
    )
    audit("db.execute", "database", result.get("table_name") or result.get("connection_name") or "local_ops_db", f"user={_operator_from_user(user)} source={result.get('source')} type={result.get('query_type')} affected={result.get('affected_rows')} reason={payload.reason or ''}")
    return api_response(data=result, message="DB execute completed")


@router.get("/execute/history")
def list_db_execute_history(
    request: Request,
    db: Session = Depends(get_db),
    connection_id: str = "",
    status: str = "",
    statement_type: str = "",
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    require_auth(request, db)
    return api_response(data=DbQueryExportService(db).list_dml_executions(
        limit=limit,
        offset=offset,
        connection_id=connection_id or "",
        status=status or "",
        statement_type=statement_type or "",
    ))


@router.get("/execute/history/{execution_id}")
def get_db_execute_history(execution_id: str, request: Request, db: Session = Depends(get_db)):
    require_auth(request, db)
    return api_response(data=DbQueryExportService(db).get_dml_execution(execution_id))


@router.delete("/execute/history/{execution_id}")
def delete_db_execute_history(execution_id: str, request: Request, db: Session = Depends(get_db)):
    user = require_admin(request, db)
    result = DbQueryExportService(db).delete_dml_execution(execution_id)
    audit("db.execute.history.delete", "dml_execution", execution_id, f"user={_operator_from_user(user)}")
    return api_response(data=result, message="DML execution history deleted")


@router.post("/execute/history/delete")
def delete_db_execute_histories(payload: DeleteDmlExecutionsPayload, request: Request, db: Session = Depends(get_db)):
    user = require_admin(request, db)
    result = DbQueryExportService(db).delete_dml_executions(payload.execution_ids)
    audit("db.execute.history.delete_many", "dml_execution", ",".join(result.get("execution_ids") or []), f"user={_operator_from_user(user)} deleted={result.get('deleted')}")
    return api_response(data=result, message="DML execution histories deleted")


@router.post("/query/export")
def export_db_query(payload: DbExportPayload, request: Request, db: Session = Depends(get_db)):
    user = require_operator(request, db)
    result = DbQueryExportService(db).export_query_result(
        sql=payload.sql,
        fmt=payload.format,
        operator=_operator_from_user(user),
        filename_hint=payload.filename_hint or "",
        connection_id=payload.connection_id or "",
        database_name=payload.database_name or "",
        limit=payload.limit,
        timeout_seconds=payload.timeout_seconds,
        table_name=payload.table_name or "",
    )
    export = result.get("export") or {}
    audit("db.query.export", "db_export", export.get("id") or "", f"user={_operator_from_user(user)} format={payload.format} rows={(result.get('query_preview') or {}).get('row_count')}")
    return api_response(data=result, message="DB query export generated")


@router.get("/exports")
def list_db_exports(request: Request, limit: int = Query(default=100, ge=1, le=500), offset: int = Query(default=0, ge=0), db: Session = Depends(get_db)):
    require_auth(request, db)
    return api_response(data=DbQueryExportService(db).list_exports(limit=limit, offset=offset))


@router.get("/exports/{export_id}")
def get_db_export(export_id: str, request: Request, db: Session = Depends(get_db)):
    require_auth(request, db)
    return api_response(data=DbQueryExportService(db).get_export(export_id))


@router.get("/exports/{export_id}/download")
def download_db_export(export_id: str, request: Request, db: Session = Depends(get_db)):
    require_auth(request, db)
    svc = DbQueryExportService(db)
    row = svc._get_export_row(export_id)
    path = svc.download_path(export_id)
    return FileResponse(str(path), media_type=export_media_type(row.format), filename=path.name)


@router.delete("/exports/{export_id}")
def delete_db_export(export_id: str, request: Request, db: Session = Depends(get_db)):
    user = require_admin(request, db)
    result = DbQueryExportService(db).delete_export(export_id)
    audit("db.query.export.delete", "db_export", export_id, f"user={_operator_from_user(user)}")
    return api_response(data=result, message="DB export deleted")


@router.post("/exports/delete")
def delete_db_exports(payload: DeleteDbExportsPayload, request: Request, db: Session = Depends(get_db)):
    user = require_admin(request, db)
    result = DbQueryExportService(db).delete_exports(payload.export_ids)
    audit("db.query.export.delete_many", "db_export", ",".join(result.get("export_ids") or []), f"user={_operator_from_user(user)} deleted={result.get('deleted')}")
    return api_response(data=result, message="DB exports deleted")
