from __future__ import annotations

from app.services.db_query_export import DbQueryExportService
from app.services.tool_registry import registry


@registry.register(
    name="ops.db.list_tables",
    title="查询数据库表列表",
    description="查询 OPS 本地库或已配置数据库连接的表列表。只读。",
    scopes=["ops:read"],
    risk="low",
    category="db_read",
    write=False,
    keywords=["列出表", "有哪些表", "table list", "show tables", "list tables", "查看表", "数据库表"],
    input_schema={
        "type": "object",
        "properties": {
            "connection_id": {"type": "string", "description": "可选。为空时查询 OPS 本地库。"},
            "database_name": {"type": "string"},
        },
        "additionalProperties": False,
    },
)
def db_list_tables_tool(args, ctx, db):
    return DbQueryExportService(db).list_tables(connection_id=args.get("connection_id") or "", database_name=args.get("database_name") or "")


@registry.register(
    name="ops.db.describe_table",
    title="查看数据库表结构",
    description="查看表字段、类型和敏感字段标记。只读。",
    scopes=["ops:read"],
    risk="low",
    category="db_read",
    write=False,
    keywords=["表结构", "字段", "列", "describe", "schema", "columns", "desc table", "查看表结构", "有哪些字段"],
    input_schema={
        "type": "object",
        "properties": {
            "table_name": {"type": "string"},
            "connection_id": {"type": "string"},
            "database_name": {"type": "string"},
        },
        "required": ["table_name"],
        "additionalProperties": False,
    },
)
def db_describe_table_tool(args, ctx, db):
    return DbQueryExportService(db).describe_table(args.get("table_name") or "", connection_id=args.get("connection_id") or "", database_name=args.get("database_name") or "")


@registry.register(
    name="ops.db.query_readonly",
    title="执行只读数据库查询",
    description="执行 SELECT/WITH 只读查询，自动限制行数、拦截危险语句、脱敏敏感字段并进入工具审计。",
    scopes=["ops:read"],
    risk="medium",
    category="db_read",
    write=False,
    data_sensitivity="sensitive",
    keywords=["查询数据", "SELECT", "select", "查数据", "query", "读数据", "查询"],
    input_schema={
        "type": "object",
        "properties": {
            "sql": {"type": "string"},
            "connection_id": {"type": "string"},
            "database_name": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 1000},
            "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 120},
        },
        "required": ["sql"],
        "additionalProperties": False,
    },
)
def db_query_readonly_tool(args, ctx, db):
    actor = getattr(ctx, "username", "") or getattr(ctx, "token_owner", "") or "tool"
    return DbQueryExportService(db).query_readonly(
        sql=args.get("sql") or "",
        operator=actor,
        connection_id=args.get("connection_id") or "",
        database_name=args.get("database_name") or "",
        limit=args.get("limit") or 100,
        timeout_seconds=args.get("timeout_seconds") or 30,
    )


@registry.register(
    name="ops.db.export_query_result",
    title="导出数据库查询结果",
    description="将只读查询结果导出为 CSV/JSON/XLSX/Markdown/SQL Query，并写入报告中心。不会生成可写入数据的 INSERT Dump。",
    scopes=["ops:read"],
    risk="medium",
    category="db_export",
    write=False,
    requires_confirmation=False,
    data_sensitivity="sensitive",
    streamable=True,
    keywords=["导出", "CSV", "Excel", "下载", "export", "xlsx", "json", "markdown", "导出数据", "下载数据", "导出CSV", "导出Excel"],
    input_schema={
        "type": "object",
        "properties": {
            "sql": {"type": "string"},
            "format": {"type": "string", "enum": ["csv", "json", "xlsx", "md", "markdown", "sql_query"]},
            "filename_hint": {"type": "string"},
            "table_name": {"type": "string"},
            "connection_id": {"type": "string"},
            "database_name": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 5000},
            "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 120},
        },
        "required": ["sql"],
        "additionalProperties": False,
    },
)
def db_export_query_result_tool(args, ctx, db, stream_callback=None):
    actor = getattr(ctx, "username", "") or getattr(ctx, "token_owner", "") or "tool"
    if stream_callback:
        stream_callback({"event": "chunk", "data": {"stage": "export_started", "format": args.get("format") or "csv"}})
    result = DbQueryExportService(db).export_query_result(
        sql=args.get("sql") or "",
        fmt=args.get("format") or "csv",
        operator=actor,
        filename_hint=args.get("filename_hint") or "",
        table_name=args.get("table_name") or "",
        connection_id=args.get("connection_id") or "",
        database_name=args.get("database_name") or "",
        limit=args.get("limit") or 100,
        timeout_seconds=args.get("timeout_seconds") or 30,
    )
    if stream_callback:
        export_info = result.get("export") if isinstance(result, dict) else {}
        stream_callback({"event": "chunk", "data": {"stage": "export_ready", "export_id": (export_info or {}).get("id"), "format": (export_info or {}).get("format")}})
    return result


@registry.register(
    name="ops.db.list_exports",
    title="查询数据库导出制品",
    description="查询报告中心中的数据库查询导出制品。只读。",
    scopes=["ops:read"],
    risk="low",
    category="db_export_read",
    write=False,
    input_schema={
        "type": "object",
        "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 500}},
        "additionalProperties": False,
    },
)
def db_list_exports_tool(args, ctx, db):
    return DbQueryExportService(db).list_exports(limit=args.get("limit") or 100)


@registry.register(
    name="ops.db.get_export",
    title="查看数据库导出元数据",
    description="按导出 ID 查看数据库导出制品元数据、SHA256 和下载路径。只读。",
    scopes=["ops:read"],
    risk="low",
    category="db_export_read",
    write=False,
    input_schema={
        "type": "object",
        "properties": {"export_id": {"type": "string"}},
        "required": ["export_id"],
        "additionalProperties": False,
    },
)
def db_get_export_tool(args, ctx, db):
    return DbQueryExportService(db).get_export(args.get("export_id") or "")


