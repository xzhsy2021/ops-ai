"""Guarded database query, export and controlled execution service.

Read-only query/export remains the default path. Admin-only execute endpoints
allow single-statement UPDATE/DELETE/INSERT under preview, confirmation,
row-limit protection and audit logging for team maintenance scenarios.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from app.core.config import get_runtime_path
from app.db.models import DmlExecutionLog, NotificationEvent, ReportArtifact
from app.maintenance.sql_query import (
    MAX_RETURN_ROWS,
    add_limit_if_needed,
    analyze_sql_guard,
    analyze_write_sql_guard,
    estimate_write_count_sql,
    extract_update_columns,
    extract_write_table,
    infer_query_type,
    mask_sql_for_history,
    summarize_write_where,
    validate_readonly_sql,
    validate_write_sql,
)

SCHEMA_VERSION = "iter40.db-query-export.v1"
DEFAULT_LIMIT = 100
MAX_EXPORT_ROWS = 5000
EXPORT_FORMATS = {"csv", "json", "md", "markdown", "sql_query", "xlsx"}
SENSITIVE_FIELD_PATTERNS = re.compile(r"(password|passwd|pwd|secret|token|api[_-]?key|private[_-]?key|key_content|credential)", re.IGNORECASE)
DENIED_TABLES = {
    "config_kv",
    "tool_tokens",
    "schema_migrations",
    "users",
}
WRITE_ALLOWED_TABLES = {
    # local OPS operational metadata that admins may maintain from Database Workbench
    "report_artifacts",
    "notification_events",
    "audit_records",
    "tool_call_logs",
    "deploy_logs",
}
MAX_WRITE_AFFECTED_ROWS = 1000


def _now_dt() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _iso(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    if value is None:
        return None
    return str(value)


def _exports_dir() -> Path:
    path = Path(get_runtime_path("DB_EXPORT_DIR", "exports/db"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_filename(value: str, fallback: str = "db-export") -> str:
    name = re.sub(r"[^A-Za-z0-9._@+\-=\u4e00-\u9fff]+", "_", str(value or fallback)).strip("._")
    return name[:120] or fallback


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _json_default(value: Any):
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _normalize_format(fmt: str) -> str:
    fmt = (fmt or "csv").lower().strip().lstrip(".")
    if fmt == "markdown":
        return "md"
    if fmt == "query.sql":
        return "sql_query"
    if fmt not in EXPORT_FORMATS:
        raise HTTPException(status_code=400, detail=f"Unsupported export format: {fmt}")
    return fmt


def _is_sensitive_column(name: str) -> bool:
    return bool(SENSITIVE_FIELD_PATTERNS.search(str(name or "")))


def _mask_value(value: Any) -> str:
    if value in (None, ""):
        return ""
    return "***MASKED***"


def _mask_rows(columns: List[str], rows: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[str]]:
    sensitive = [c for c in columns if _is_sensitive_column(c)]
    if not sensitive:
        return rows, []
    masked: List[Dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        for c in sensitive:
            if c in item:
                item[c] = _mask_value(item.get(c))
        masked.append(item)
    return masked, sensitive


def _referenced_denied_table(sql: str) -> Optional[str]:
    lowered = f" {str(sql or '').lower()} "
    for table in DENIED_TABLES:
        if re.search(r"\b" + re.escape(table.lower()) + r"\b", lowered):
            return table
    return None


def _extract_table_hint(sql: str) -> str:
    match = re.search(r"\bfrom\s+([`\"\[]?)([A-Za-z0-9_.\-]+)\1", sql or "", re.IGNORECASE)
    if not match:
        return "query_result"
    return match.group(2).split(".")[-1].strip("`\"[]") or "query_result"


def _quote_identifier(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _sql_literal(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def _verification_sql_for_dml(normalized_sql: str, table_name: str, query_type: str, where_summary: str) -> str:
    """Generate a safe follow-up query for humans/agents to verify a DML result."""
    table = table_name or extract_write_table(normalized_sql) or "target_table"
    qtype = (query_type or infer_query_type(normalized_sql) or "").lower()
    where = (where_summary or "").strip()
    if qtype == "delete" and where:
        return f"SELECT COUNT(*) AS cnt FROM {table} WHERE {where} LIMIT 1"
    if qtype == "update" and where:
        return f"SELECT * FROM {table} WHERE {where} LIMIT 20"
    if qtype == "insert":
        return f"SELECT * FROM {table} ORDER BY 1 DESC LIMIT 20"
    return f"SELECT * FROM {table} LIMIT 20"


def _markdown_table(columns: List[str], rows: List[Dict[str, Any]], max_rows: int = 1000) -> str:
    def cell(value: Any) -> str:
        text_value = json.dumps(value, ensure_ascii=False, default=_json_default) if isinstance(value, (dict, list)) else str(value if value is not None else "")
        return text_value.replace("|", "/").replace("\n", " ")[:1000]
    lines = ["| " + " | ".join(columns) + " |", "|" + "|".join(["---"] * len(columns)) + "|"]
    for row in rows[:max_rows]:
        lines.append("| " + " | ".join(cell(row.get(c)) for c in columns) + " |")
    return "\n".join(lines) + "\n"


def _write_export_file(path: Path, fmt: str, *, columns: List[str], rows: List[Dict[str, Any]], sql: str, table_name: str, metadata: Dict[str, Any]) -> None:
    if fmt == "csv":
        with path.open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        return
    if fmt == "json":
        payload = {"schema_version": SCHEMA_VERSION, "metadata": metadata, "columns": columns, "rows": rows}
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default), encoding="utf-8")
        return
    if fmt == "md":
        lines = [f"# 数据库查询导出", "", f"- 表/来源：{table_name}", f"- 行数：{len(rows)}", f"- 生成时间：{metadata.get('generated_at')}", "", "## SQL", "", "```sql", sql, "```", "", "## 查询结果", "", _markdown_table(columns, rows)]
        path.write_text("\n".join(lines), encoding="utf-8")
        return
    if fmt == "sql_query":
        lines = ["-- Generated by OPS DB Export", f"-- Created at: {metadata.get('generated_at')}", "-- Query type: readonly", f"-- Rows previewed: {len(rows)}", "", sql.rstrip(";") + ";", ""]
        path.write_text("\n".join(lines), encoding="utf-8")
        return
    if fmt == "xlsx":
        try:
            from openpyxl import Workbook
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"openpyxl unavailable: {exc}")
        wb = Workbook()
        ws = wb.active
        ws.title = "query_result"
        ws.append(columns)
        for row in rows:
            ws.append([row.get(c) for c in columns])
        meta = wb.create_sheet("metadata")
        for key, value in metadata.items():
            meta.append([key, json.dumps(value, ensure_ascii=False, default=_json_default) if isinstance(value, (dict, list)) else value])
        wb.save(path)
        return
    raise HTTPException(status_code=400, detail=f"Unsupported export format: {fmt}")


def _report_to_dict(row: ReportArtifact) -> Dict[str, Any]:
    return {
        "id": row.id,
        "export_id": row.id,
        "report_type": row.report_type,
        "title": row.title,
        "target_type": row.target_type,
        "target_id": row.target_id,
        "status": row.status,
        "format": row.format,
        "file_path": row.file_path,
        "size_bytes": row.size_bytes or 0,
        "sha256": row.sha256,
        "summary": row.summary,
        "metadata": row.metadata_json or {},
        "created_by": row.created_by,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
        "download_url": f"/api/v2/db/exports/{row.id}/download",
        "report_download_url": f"/api/v2/reports/{row.id}/download",
    }


class DbQueryExportService:
    def __init__(self, db: Session):
        self.db = db

    def list_tables(self, *, connection_id: str = "", database_name: str = "") -> Dict[str, Any]:
        if connection_id:
            from app.maintenance.service import CleanupService
            tables = CleanupService(self.db).get_tables(connection_id, database_name)
            return {"source": "connection", "connection_id": connection_id, "database_name": database_name, "tables": tables}
        insp = inspect(self.db.bind)
        tables = []
        for name in sorted(insp.get_table_names()):
            tables.append({"name": name, "denied": name in DENIED_TABLES, "column_count": len(insp.get_columns(name))})
        return {"source": "local_ops_db", "database_name": "ops", "tables": tables}

    def describe_table(self, table_name: str, *, connection_id: str = "", database_name: str = "") -> Dict[str, Any]:
        table_name = str(table_name or "").strip()
        if not table_name:
            raise HTTPException(status_code=400, detail="table_name is required")
        if table_name in DENIED_TABLES:
            raise HTTPException(status_code=403, detail=f"Table is not available for AI/MCP DB tools: {table_name}")
        if connection_id:
            from app.maintenance.service import CleanupService
            columns = CleanupService(self.db).get_columns(connection_id, database_name, table_name)
            for c in columns:
                name = c.get("name") if isinstance(c, dict) else str(c)
                if isinstance(c, dict):
                    c["sensitive"] = _is_sensitive_column(name)
            return {"source": "connection", "connection_id": connection_id, "database_name": database_name, "table_name": table_name, "columns": columns}
        insp = inspect(self.db.bind)
        if table_name not in insp.get_table_names():
            raise HTTPException(status_code=404, detail="Table not found")
        columns = []
        for c in insp.get_columns(table_name):
            columns.append({
                "name": c.get("name"),
                "type": str(c.get("type")),
                "nullable": bool(c.get("nullable")),
                "default": str(c.get("default")) if c.get("default") is not None else None,
                "sensitive": _is_sensitive_column(c.get("name")),
            })
        return {"source": "local_ops_db", "database_name": "ops", "table_name": table_name, "columns": columns}

    def _ensure_local_write_allowed(self, table_name: str) -> None:
        if not table_name:
            raise HTTPException(status_code=400, detail="Cannot detect target table")
        lowered = table_name.lower()
        if lowered in DENIED_TABLES:
            raise HTTPException(status_code=403, detail=f"Table is protected and cannot be modified from OPS Database Workbench: {table_name}")
        if lowered not in WRITE_ALLOWED_TABLES:
            raise HTTPException(status_code=403, detail=f"当前版本仅允许维护 OPS 运维元数据表，目标表未开放写入：{table_name}")

    def _count_local_write_candidates(self, sql: str) -> Optional[int]:
        count_sql = estimate_write_count_sql(sql)
        if not count_sql:
            return None
        try:
            result = self.db.execute(text(count_sql))
            row = result.mappings().first()
            if not row:
                return 0
            return int(row.get("cnt") or 0)
        except Exception:
            return None

    def _count_external_write_candidates(self, executor, sql: str) -> Optional[int]:
        count_sql = estimate_write_count_sql(sql)
        if not count_sql:
            return None
        try:
            raw_conn = executor._get_connection()
            with raw_conn.cursor() as cur:
                cur.execute(count_sql)
                row = cur.fetchone() or {}
                return int(row.get("cnt") or 0)
        except Exception:
            return None


    def _sample_local_write_candidates(self, sql: str) -> List[Dict[str, Any]]:
        sample_sql = _select_sample_sql(sql, 20)
        if not sample_sql:
            return []
        try:
            result = self.db.execute(text(sample_sql))
            rows = [dict(r) for r in result.mappings().all()]
            rows, _ = _mask_rows(list(result.keys()), rows)
            return rows
        except Exception:
            return []

    def _sample_external_write_candidates(self, executor, sql: str) -> List[Dict[str, Any]]:
        sample_sql = _select_sample_sql(sql, 20)
        if not sample_sql:
            return []
        try:
            raw_conn = executor._get_connection()
            with raw_conn.cursor() as cur:
                cur.execute(sample_sql)
                rows = [dict(r) for r in (cur.fetchall() or [])]
                columns = [d[0] for d in (cur.description or [])]
            rows, _ = _mask_rows(columns, rows)
            return rows
        except Exception:
            return []

    def _ensure_external_write_allowed(self, conn, normalized_sql: str, table_name: str, query_type: str, requested_max_rows: int) -> tuple[int, List[str]]:
        warnings: List[str] = []
        if not bool(getattr(conn, "allow_dml", False)):
            raise HTTPException(status_code=403, detail=f"连接 {conn.name} 未开启 DML 执行能力，请在连接配置中显式开启。")
        allowed_types = [str(x).lower() for x in _json_list(getattr(conn, "allowed_dml_types", None))]
        if allowed_types and query_type.lower() not in allowed_types:
            raise HTTPException(status_code=403, detail=f"连接 {conn.name} 未允许 {query_type.upper()} 操作。")
        allowed_tables = [str(x).lower() for x in _json_list(getattr(conn, "allowed_tables", None))]
        blocked_tables = [str(x).lower() for x in _json_list(getattr(conn, "blocked_tables", None))]
        if table_name.lower() in blocked_tables:
            raise HTTPException(status_code=403, detail=f"目标表 {table_name} 已在连接策略中禁止写入。")
        if allowed_tables and table_name.lower() not in allowed_tables:
            raise HTTPException(status_code=403, detail=f"目标表 {table_name} 不在连接允许表范围内。")
        conn_cap = int(getattr(conn, "max_affected_rows_default", None) or MAX_WRITE_AFFECTED_ROWS)
        cap = max(1, min(int(requested_max_rows or conn_cap), conn_cap, MAX_WRITE_AFFECTED_ROWS))
        if int(requested_max_rows or cap) > cap:
            warnings.append(f"连接策略将最大影响行数限制为 {cap}")
        return cap, warnings

    def _record_dml_execution(self, *, preview: Dict[str, Any], operator: str, affected_rows: Optional[int], duration_ms: Optional[int], status: str, reason: str = "", error_message: str = "", audit_id: str = "") -> Dict[str, Any]:
        row = DmlExecutionLog(
            id=uuid4().hex,
            preview_id=str(preview.get("operation_id") or ""),
            source=preview.get("source") or "local_ops_db",
            connection_id=preview.get("connection_id") or "",
            connection_name=preview.get("connection_name") or "local_ops_db",
            database_name=preview.get("database_name") or "ops",
            environment=preview.get("environment") or "",
            statement_type=str(preview.get("query_type") or "").upper(),
            table_name=preview.get("table_name") or "",
            history_sql=preview.get("history_sql") or mask_sql_for_history(preview.get("normalized_sql") or ""),
            where_summary=preview.get("where_summary") or "",
            changed_columns=preview.get("changed_columns") or [],
            before_sample_json=preview.get("before_sample_rows") or [],
            estimated_affected_rows=preview.get("estimated_affected_rows"),
            affected_rows=affected_rows,
            max_affected_rows=preview.get("max_affected_rows"),
            risk_level=preview.get("risk_level") or "high",
            status=status,
            reason=reason or "",
            operator=operator or "",
            duration_ms=duration_ms,
            audit_id=audit_id or "",
            error_message=error_message or "",
            created_at=_now_dt(),
        )
        self.db.add(row)
        self.db.commit()
        return self._dml_log_to_dict(row)

    def _dml_log_to_dict(self, row: DmlExecutionLog) -> Dict[str, Any]:
        return {
            "id": row.id,
            "execution_id": row.id,
            "preview_id": row.preview_id,
            "source": row.source,
            "connection_id": row.connection_id,
            "connection_name": row.connection_name,
            "database_name": row.database_name,
            "environment": row.environment,
            "statement_type": row.statement_type,
            "query_type": str(row.statement_type or "").lower(),
            "table_name": row.table_name,
            "history_sql": row.history_sql,
            "where_summary": row.where_summary,
            "changed_columns": row.changed_columns or [],
            "before_sample_rows": row.before_sample_json or [],
            "estimated_affected_rows": row.estimated_affected_rows,
            "affected_rows": row.affected_rows,
            "max_affected_rows": row.max_affected_rows,
            "risk_level": row.risk_level,
            "status": row.status,
            "reason": row.reason,
            "operator": row.operator,
            "duration_ms": row.duration_ms,
            "audit_id": row.audit_id,
            "error_message": row.error_message,
            "created_at": _iso(row.created_at),
        }

    def list_dml_executions(self, *, limit: int = 50, offset: int = 0, connection_id: str = "", status: str = "", statement_type: str = "") -> Dict[str, Any]:
        q = self.db.query(DmlExecutionLog)
        if connection_id:
            q = q.filter(DmlExecutionLog.connection_id == connection_id)
        if status:
            q = q.filter(DmlExecutionLog.status == status)
        if statement_type:
            q = q.filter(DmlExecutionLog.statement_type == statement_type.upper())
        total = q.count()
        rows = q.order_by(DmlExecutionLog.created_at.desc()).offset(max(0, int(offset or 0))).limit(max(1, min(int(limit or 50), 200))).all()
        return {"items": [self._dml_log_to_dict(r) for r in rows], "pagination": {"limit": limit, "offset": offset, "total": total}}

    def get_dml_execution(self, execution_id: str) -> Dict[str, Any]:
        row = self.db.query(DmlExecutionLog).filter(DmlExecutionLog.id == execution_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="DML execution not found")
        return self._dml_log_to_dict(row)

    def preview_execute_sql(
        self,
        *,
        sql: str,
        operator: str = "",
        connection_id: str = "",
        database_name: str = "",
        max_affected_rows: int = MAX_WRITE_AFFECTED_ROWS,
        preview_level: str = "standard",
    ) -> Dict[str, Any]:
        normalized = validate_write_sql(sql)
        table_name = extract_write_table(normalized)
        query_type = normalized.split(" ", 1)[0].lower()
        requested_max_rows = max(1, min(int(max_affected_rows or MAX_WRITE_AFFECTED_ROWS), MAX_WRITE_AFFECTED_ROWS))
        level = (preview_level or "standard").lower().strip()
        if level not in {"fast", "standard", "full"}:
            level = "standard"
        source = "connection" if connection_id else "local_ops_db"
        connection_name = "local_ops_db"
        environment = "local"
        database_display = database_name or "ops"
        estimated = None
        before_sample: List[Dict[str, Any]] = []
        policy_warnings: List[str] = []
        if connection_id:
            from app.maintenance.sql_query import SqlQueryService
            svc = SqlQueryService(self.db)
            conn = svc._get_connection(connection_id)
            connection_name = conn.name
            environment = getattr(conn, "environment", "") or ""
            database_display = database_name or conn.database_name or ""
            effective_cap, policy_warnings = self._ensure_external_write_allowed(conn, normalized, table_name, query_type, requested_max_rows)
            max_affected_rows = effective_cap
            executor = svc._executor(conn, database_display)
            try:
                if level in {"standard", "full"}:
                    estimated = self._count_external_write_candidates(executor, normalized)
                if level == "full":
                    before_sample = self._sample_external_write_candidates(executor, normalized)
            finally:
                try:
                    executor.close()
                except Exception:
                    pass
        else:
            self._ensure_local_write_allowed(table_name)
            max_affected_rows = requested_max_rows
            if level in {"standard", "full"}:
                estimated = self._count_local_write_candidates(normalized)
            if level == "full":
                before_sample = self._sample_local_write_candidates(normalized)
        guard = analyze_write_sql_guard(normalized, environment, max_affected_rows=max_affected_rows)
        if policy_warnings:
            guard.setdefault("warnings", []).extend(policy_warnings)
        if guard.get("blockers"):
            raise HTTPException(status_code=400, detail="; ".join(guard.get("blockers") or []))
        over_limit = estimated is not None and estimated > max_affected_rows
        if over_limit:
            guard.setdefault("warnings", []).append(f"预计影响 {estimated} 行，超过当前保护阈值 {max_affected_rows}，执行时将被阻断")
        changed_columns = extract_update_columns(normalized)
        where_summary = summarize_write_where(normalized)
        risk_level = guard.get("risk_level") or ("critical" if query_type == "delete" else "high")
        if query_type == "delete" and estimated is not None and estimated > max(10, max_affected_rows // 2):
            risk_level = "critical"
        verification_sql = _verification_sql_for_dml(normalized, table_name, query_type, where_summary)
        return {
            "schema_version": SCHEMA_VERSION,
            "operation_id": uuid4().hex,
            "preview_id": uuid4().hex,
            "preview_level": level,
            "source": source,
            "connection_id": connection_id or "",
            "connection_name": connection_name,
            "environment": environment,
            "database_name": database_display,
            "query_type": guard.get("query_type"),
            "statement_type": str(guard.get("query_type") or "").upper(),
            "table_name": table_name,
            "target_tables": [table_name] if table_name else [],
            "where_summary": where_summary,
            "changed_columns": changed_columns,
            "normalized_sql": normalized,
            "history_sql": mask_sql_for_history(normalized),
            "estimated_affected_rows": estimated,
            "max_affected_rows": max_affected_rows,
            "over_limit": over_limit,
            "risk": guard,
            "risk_level": risk_level,
            "confirm_text": guard.get("confirm_text") or "EXECUTE SQL",
            "write": True,
            "executable": not over_limit,
            "before_sample_rows": before_sample,
            "before_sample_count": len(before_sample),
            "sample_note": "UPDATE / DELETE 执行前样例最多展示 20 行，敏感字段会脱敏。" if before_sample else "INSERT 或无法安全抽样时不展示执行前样例。",
            "protections": guard.get("protections") or [],
            "verification_sql": verification_sql,
            "ok": True,
            "summary": f"已完成 {str(query_type).upper()} 预检：目标表 {table_name or '-'}，预计影响 {estimated if estimated is not None else '未估算'} 行。",
            "next_actions": [
                "核对连接、数据库、目标表和 WHERE 条件",
                "如需查看执行前样例，请使用 full 预检",
                "确认最大影响行数和执行原因后再执行",
                "执行后使用 verification_sql 验证结果",
            ],
            "message": "执行前请核对目标库、目标表、WHERE 条件、预计影响行数和保护阈值。",
        }

    def execute_sql(
        self,
        *,
        sql: str,
        operator: str = "",
        connection_id: str = "",
        database_name: str = "",
        max_affected_rows: int = MAX_WRITE_AFFECTED_ROWS,
        confirm_text: str = "",
        reason: str = "",
    ) -> Dict[str, Any]:
        preview = self.preview_execute_sql(
            sql=sql,
            operator=operator,
            connection_id=connection_id,
            database_name=database_name,
            max_affected_rows=max_affected_rows,
            preview_level="standard",
        )
        if (confirm_text or "").strip() != preview.get("confirm_text"):
            raise HTTPException(status_code=400, detail=f"确认短语不匹配，请传入 {preview.get('confirm_text')}")
        if not (reason or "").strip():
            raise HTTPException(status_code=400, detail="执行 DML 必须填写执行原因")
        if not preview.get("executable"):
            raise HTTPException(status_code=400, detail="预计影响行数超过保护阈值，已阻断执行")
        normalized = preview["normalized_sql"]
        started = time.time()
        affected_rows = 0
        status = "success"
        error = ""
        execution_log: Dict[str, Any] | None = None
        try:
            if connection_id:
                from app.maintenance.sql_query import SqlQueryService
                svc = SqlQueryService(self.db)
                conn = svc._get_connection(connection_id)
                executor = svc._executor(conn, database_name or conn.database_name)
                raw_conn = executor._get_connection()
                try:
                    with raw_conn.cursor() as cur:
                        cur.execute(normalized)
                        affected_rows = int(cur.rowcount if cur.rowcount is not None else 0)
                    if affected_rows > int(preview["max_affected_rows"]):
                        raw_conn.rollback()
                        raise HTTPException(status_code=400, detail=f"实际影响 {affected_rows} 行，超过保护阈值 {preview['max_affected_rows']}，已回滚")
                    raw_conn.commit()
                except Exception:
                    try:
                        raw_conn.rollback()
                    except Exception:
                        pass
                    raise
                finally:
                    try:
                        executor.close()
                    except Exception:
                        pass
            else:
                result = self.db.execute(text(normalized))
                affected_rows = int(result.rowcount if result.rowcount is not None else 0)
                if affected_rows > int(preview["max_affected_rows"]):
                    self.db.rollback()
                    raise HTTPException(status_code=400, detail=f"实际影响 {affected_rows} 行，超过保护阈值 {preview['max_affected_rows']}，已回滚")
                self.db.add(NotificationEvent(
                    event_type="db_execute.completed",
                    target=preview.get("table_name") or "database",
                    status="success",
                    message=f"数据库执行完成：{preview.get('query_type')} / {affected_rows} 行",
                    payload={"source": preview.get("source"), "table_name": preview.get("table_name"), "affected_rows": affected_rows, "operator": operator, "reason": reason},
                    created_at=_now_dt(),
                ))
                self.db.commit()
        except HTTPException as exc:
            status = "failed"
            error = str(exc.detail)
            try:
                self.db.rollback()
                duration_ms = int((time.time() - started) * 1000)
                execution_log = self._record_dml_execution(preview=preview, operator=operator, affected_rows=affected_rows, duration_ms=duration_ms, status="failed", reason=reason or "", error_message=error)
            except Exception:
                pass
            raise
        except Exception as exc:
            status = "failed"
            error = str(exc)
            try:
                self.db.rollback()
            except Exception:
                pass
            try:
                duration_ms = int((time.time() - started) * 1000)
                execution_log = self._record_dml_execution(preview=preview, operator=operator, affected_rows=affected_rows, duration_ms=duration_ms, status="failed", reason=reason or "", error_message=error)
            except Exception:
                pass
            raise HTTPException(status_code=400, detail=error)
        duration_ms = int((time.time() - started) * 1000)
        execution_log = self._record_dml_execution(preview=preview, operator=operator, affected_rows=affected_rows, duration_ms=duration_ms, status=status, reason=reason or "", error_message=error)
        return {
            "schema_version": SCHEMA_VERSION,
            "operation_id": preview.get("operation_id"),
            "execution_id": execution_log.get("execution_id") if execution_log else preview.get("operation_id"),
            "source": preview.get("source"),
            "connection_id": connection_id or "",
            "connection_name": preview.get("connection_name"),
            "environment": preview.get("environment"),
            "database_name": preview.get("database_name"),
            "query_type": preview.get("query_type"),
            "statement_type": preview.get("statement_type"),
            "table_name": preview.get("table_name"),
            "where_summary": preview.get("where_summary"),
            "changed_columns": preview.get("changed_columns") or [],
            "before_sample_rows": preview.get("before_sample_rows") or [],
            "normalized_sql": normalized,
            "history_sql": mask_sql_for_history(normalized),
            "affected_rows": affected_rows,
            "estimated_affected_rows": preview.get("estimated_affected_rows"),
            "max_affected_rows": preview.get("max_affected_rows"),
            "duration_ms": duration_ms,
            "status": status,
            "write": True,
            "reason": reason or "",
            "protections": preview.get("protections") or [],
            "verification_sql": preview.get("verification_sql") or _verification_sql_for_dml(normalized, preview.get("table_name") or "", preview.get("query_type") or "", preview.get("where_summary") or ""),
            "ok": True,
            "next_actions": [
                "使用 verification_sql 验证数据结果",
                "如结果不符合预期，停止继续执行同类 SQL 并人工复核",
                "可在 SQL 执行历史中按 execution_id 回看本次执行",
            ],
            "audit_id": execution_log.get("audit_id") if execution_log else "",
            "execution_log": execution_log,
            "summary": f"数据库执行完成：{preview.get('query_type')} {preview.get('table_name')}，影响 {affected_rows} 行。",
        }

    def query_readonly(
        self,
        *,
        sql: str,
        operator: str = "",
        connection_id: str = "",
        database_name: str = "",
        limit: int = DEFAULT_LIMIT,
        timeout_seconds: int = 30,
    ) -> Dict[str, Any]:
        denied = _referenced_denied_table(sql)
        if denied:
            raise HTTPException(status_code=403, detail=f"Table is not available for AI/MCP DB tools: {denied}")
        limit = max(1, min(int(limit or DEFAULT_LIMIT), MAX_RETURN_ROWS))
        if connection_id:
            from app.maintenance.sql_query import SqlQueryService
            result = SqlQueryService(self.db).execute(
                connection_id=connection_id,
                sql=sql,
                database_name=database_name or None,
                limit=limit,
                timeout_seconds=timeout_seconds,
                operator=operator or "db_tool",
            )
            columns = list(result.get("columns") or [])
            rows = list(result.get("rows") or [])
            rows, sensitive = _mask_rows(columns, rows)
            result.update({"rows": rows, "sensitive_columns_masked": sensitive, "query_id": uuid4().hex, "source": "connection"})
            return result

        guard = analyze_sql_guard(sql, "local")
        if guard.get("blockers"):
            raise HTTPException(status_code=400, detail="; ".join(guard.get("blockers") or []))
        normalized = validate_readonly_sql(sql)
        executable_sql = add_limit_if_needed(normalized, limit)
        started = time.time()
        try:
            result = self.db.execute(text(executable_sql))
            mappings = result.mappings().all()
            columns = list(result.keys())
            rows = [dict(r) for r in mappings]
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        rows, sensitive = _mask_rows(columns, rows)
        duration_ms = int((time.time() - started) * 1000)
        return {
            "schema_version": SCHEMA_VERSION,
            "query_id": uuid4().hex,
            "source": "local_ops_db",
            "connection_id": "",
            "connection_name": "local_ops_db",
            "database_name": "ops",
            "query_type": "select",
            "readonly": True,
            "normalized_sql": normalized,
            "executable_sql": executable_sql,
            "history_sql": mask_sql_for_history(normalized),
            "columns": columns,
            "rows": rows,
            "row_count": len(rows),
            "duration_ms": duration_ms,
            "limit": limit,
            "sensitive_columns_masked": sensitive,
            "protections": [
                "只允许 SELECT/WITH 单条只读查询",
                f"返回行数上限 {limit}",
                "敏感字段自动脱敏",
                "导出制品会写入报告中心和审计链路",
            ],
        }

    def export_query_result(
        self,
        *,
        sql: str,
        fmt: str = "csv",
        operator: str = "",
        filename_hint: str = "",
        connection_id: str = "",
        database_name: str = "",
        limit: int = DEFAULT_LIMIT,
        timeout_seconds: int = 30,
        table_name: str = "",
    ) -> Dict[str, Any]:
        fmt = _normalize_format(fmt)
        export_limit = max(1, min(int(limit or DEFAULT_LIMIT), MAX_EXPORT_ROWS))
        result = self.query_readonly(sql=sql, operator=operator, connection_id=connection_id, database_name=database_name, limit=export_limit, timeout_seconds=timeout_seconds)
        columns = list(result.get("columns") or [])
        rows = list(result.get("rows") or [])
        if len(rows) > MAX_EXPORT_ROWS:
            raise HTTPException(status_code=400, detail=f"Export row count exceeds max {MAX_EXPORT_ROWS}")
        table = table_name or _extract_table_hint(result.get("normalized_sql") or sql)
        generated_at = _now_dt()
        ext = {"sql_query": "query.sql", "md": "md"}.get(fmt, fmt)
        base = _safe_filename(filename_hint or table or "query_result")
        export_id = uuid4().hex
        path = _exports_dir() / f"db-export-{generated_at.strftime('%Y%m%d-%H%M%S')}-{base}-{export_id[:8]}.{ext}"
        metadata = {
            "schema_version": SCHEMA_VERSION,
            "generated_at": generated_at.isoformat(),
            "generated_by": operator or "system",
            "source": result.get("source"),
            "connection_id": connection_id or "",
            "connection_name": result.get("connection_name") or "local_ops_db",
            "database_name": result.get("database_name") or database_name or "ops",
            "table_name": table,
            "format": fmt,
            "row_count": len(rows),
            "columns": columns,
            "sensitive_columns_masked": result.get("sensitive_columns_masked") or [],
            "sql": result.get("executable_sql") or sql,
            "readonly": True,
        }
        _write_export_file(path, fmt, columns=columns, rows=rows, sql=result.get("executable_sql") or sql, table_name=table, metadata=metadata)
        size = path.stat().st_size
        sha = _sha256_file(path)
        title = f"数据库查询导出 · {base} · {fmt}"
        summary = f"数据库查询导出 {fmt}，{len(rows)} 行，来源 {metadata.get('connection_name')}。"
        row = ReportArtifact(
            id=export_id,
            report_type="db_query_export",
            title=title,
            target_type="database",
            target_id=metadata.get("connection_name") or "local_ops_db",
            status="ready",
            format=fmt,
            file_path=str(path),
            size_bytes=size,
            sha256=sha,
            summary=summary,
            metadata_json={**metadata, "sha256": sha, "size_bytes": size},
            created_by=operator or "system",
            created_at=generated_at,
            updated_at=generated_at,
        )
        self.db.add(row)
        self.db.add(NotificationEvent(
            event_type="db_export.created",
            target=export_id,
            status="success",
            message=f"数据库查询导出已生成：{fmt} / {len(rows)} 行",
            payload={"export_id": export_id, "format": fmt, "row_count": len(rows), "sha256": sha},
            created_at=generated_at,
        ))
        self.db.commit()
        self.db.refresh(row)
        return {"export": _report_to_dict(row), "query_preview": {"columns": columns, "row_count": len(rows), "masked_columns": metadata.get("sensitive_columns_masked")}}

    def list_exports(self, *, limit: int = 100) -> Dict[str, Any]:
        limit = max(1, min(int(limit or 100), 500))
        rows = self.db.query(ReportArtifact).filter(ReportArtifact.report_type == "db_query_export").order_by(ReportArtifact.created_at.desc()).limit(limit).all()
        return {"items": [_report_to_dict(r) for r in rows], "total": len(rows), "formats": sorted(EXPORT_FORMATS)}

    def get_export(self, export_id: str) -> Dict[str, Any]:
        return _report_to_dict(self._get_export_row(export_id))

    def _get_export_row(self, export_id: str) -> ReportArtifact:
        row = self.db.query(ReportArtifact).filter(ReportArtifact.id == export_id, ReportArtifact.report_type == "db_query_export").first()
        if not row:
            raise HTTPException(status_code=404, detail="DB export not found")
        return row

    def download_path(self, export_id: str) -> Path:
        row = self._get_export_row(export_id)
        if not row.file_path:
            raise HTTPException(status_code=404, detail="Export file path is empty")
        path = Path(row.file_path).resolve()
        root = _exports_dir().resolve()
        try:
            path.relative_to(root)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid export path")
        if not path.exists() or not path.is_file():
            raise HTTPException(status_code=404, detail="Export file not found")
        return path

    def delete_export(self, export_id: str) -> Dict[str, Any]:
        row = self._get_export_row(export_id)
        try:
            path = self.download_path(export_id)
            path.unlink(missing_ok=True)
        except HTTPException:
            pass
        self.db.delete(row)
        self.db.commit()
        return {"id": export_id, "deleted": True}


def export_media_type(fmt: str) -> str:
    fmt = _normalize_format(fmt)
    if fmt == "csv":
        return "text/csv; charset=utf-8"
    if fmt == "json":
        return "application/json"
    if fmt in {"md"}:
        return "text/markdown; charset=utf-8"
    if fmt in {"sql_query"}:
        return "application/sql; charset=utf-8"
    if fmt == "xlsx":
        return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return "application/octet-stream"
