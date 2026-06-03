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
    risk="low",
    category="db_read",
    write=False,
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
def db_export_query_result_tool(args, ctx, db):
    actor = getattr(ctx, "username", "") or getattr(ctx, "token_owner", "") or "tool"
    return DbQueryExportService(db).export_query_result(
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


@registry.register(
    name="ops.db.list_exports",
    title="查询数据库导出制品",
    description="查询报告中心中的数据库查询导出制品。只读。",
    scopes=["ops:read", "audit:read"],
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
    scopes=["ops:read", "audit:read"],
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


@registry.register(
    name="ops.db.preview_execute_sql",
    title="预览数据库执行操作",
    description="预览 UPDATE/DELETE/INSERT 执行风险、目标表和预计影响行数。用于报告制品更新、删除等受控数据库维护场景。",
    scopes=["ops:write"],
    risk="high",
    category="db_write",
    write=True,
    requires_confirmation=True,
    input_schema={
        "type": "object",
        "properties": {
            "sql": {"type": "string"},
            "connection_id": {"type": "string", "description": "为空时执行本地 OPS 库维护；传入连接 ID 时执行已配置业务库连接。"},
            "database_name": {"type": "string"},
            "max_affected_rows": {"type": "integer", "minimum": 1, "maximum": 1000},
            "preview_level": {"type": "string", "enum": ["fast", "standard", "full"], "description": "fast 只做语法/风险识别，standard 增加影响行数估算，full 再返回执行前样例。"},
        },
        "required": ["sql"],
        "additionalProperties": False,
    },
)
def db_preview_execute_sql_tool(args, ctx, db):
    actor = getattr(ctx, "username", "") or getattr(ctx, "token_owner", "") or "tool"
    return DbQueryExportService(db).preview_execute_sql(
        sql=args.get("sql") or "",
        operator=actor,
        connection_id=args.get("connection_id") or "",
        database_name=args.get("database_name") or "",
        max_affected_rows=args.get("max_affected_rows") or 100,
        preview_level=args.get("preview_level") or "standard",
    )


@registry.register(
    name="ops.db.execute_sql",
    title="执行数据库写操作",
    description="执行受控 UPDATE/DELETE/INSERT。必须传入确认短语 EXECUTE SQL，并记录审计。适合报告制品删除、状态更新等维护动作。",
    scopes=["ops:write"],
    risk="high",
    category="db_write",
    write=True,
    requires_confirmation=True,
    input_schema={
        "type": "object",
        "properties": {
            "sql": {"type": "string"},
            "connection_id": {"type": "string"},
            "database_name": {"type": "string"},
            "max_affected_rows": {"type": "integer", "minimum": 1, "maximum": 1000},
            "confirm_text": {"type": "string", "description": "必须为 EXECUTE SQL"},
            "reason": {"type": "string"},
        },
        "required": ["sql", "confirm_text"],
        "additionalProperties": False,
    },
)
def db_execute_sql_tool(args, ctx, db):
    actor = getattr(ctx, "username", "") or getattr(ctx, "token_owner", "") or "tool"
    return DbQueryExportService(db).execute_sql(
        sql=args.get("sql") or "",
        operator=actor,
        connection_id=args.get("connection_id") or "",
        database_name=args.get("database_name") or "",
        max_affected_rows=args.get("max_affected_rows") or 100,
        confirm_text=args.get("confirm_text") or "",
        reason=args.get("reason") or "",
    )


@registry.register(
    name="ops.db.preview_dml",
    title="预检 DML 受控执行",
    description="两阶段 DML 工具的第一步：预检 INSERT/UPDATE/DELETE 风险、目标表、WHERE、样例数据和预计影响行数；不会修改数据。",
    scopes=["ops:write"],
    risk="high",
    category="db_write",
    write=True,
    requires_confirmation=True,
    keywords=["预检", "preview", "DML", "写入预检", "修改预检", "删除预检", "precheck write"],
    input_schema={
        "type": "object",
        "properties": {
            "sql": {"type": "string"},
            "connection_id": {"type": "string", "description": "为空时预检本地 OPS 库维护；传入连接 ID 时预检已开启 DML 的业务库连接。"},
            "database_name": {"type": "string"},
            "max_affected_rows": {"type": "integer", "minimum": 1, "maximum": 1000},
            "preview_level": {"type": "string", "enum": ["fast", "standard", "full"], "description": "fast 只做语法/风险识别，standard 增加影响行数估算，full 再返回执行前样例。"},
        },
        "required": ["sql"],
        "additionalProperties": False,
    },
)
def db_preview_dml_tool(args, ctx, db):
    actor = getattr(ctx, "username", "") or getattr(ctx, "token_owner", "") or "tool"
    return DbQueryExportService(db).preview_execute_sql(
        sql=args.get("sql") or "",
        operator=actor,
        connection_id=args.get("connection_id") or "",
        database_name=args.get("database_name") or "",
        max_affected_rows=args.get("max_affected_rows") or 100,
        preview_level=args.get("preview_level") or "standard",
    )


@registry.register(
    name="ops.db.execute_dml",
    title="执行受控 DML",
    description="两阶段 DML 工具的第二步：执行已完成预检的 INSERT/UPDATE/DELETE。必须传入确认短语 EXECUTE SQL 和执行原因，结果进入 DML 历史与审计链路。",
    scopes=["ops:write"],
    risk="high",
    category="db_write",
    write=True,
    requires_confirmation=True,
    keywords=["执行DML", "execute", "写入", "修改数据", "删除数据", "UPDATE", "DELETE", "INSERT"],
    input_schema={
        "type": "object",
        "properties": {
            "sql": {"type": "string"},
            "connection_id": {"type": "string"},
            "database_name": {"type": "string"},
            "max_affected_rows": {"type": "integer", "minimum": 1, "maximum": 1000},
            "confirm_text": {"type": "string", "description": "必须为 EXECUTE SQL"},
            "reason": {"type": "string", "description": "执行原因，必填"},
        },
        "required": ["sql", "confirm_text", "reason"],
        "additionalProperties": False,
    },
)
def db_execute_dml_tool(args, ctx, db):
    actor = getattr(ctx, "username", "") or getattr(ctx, "token_owner", "") or "tool"
    return DbQueryExportService(db).execute_sql(
        sql=args.get("sql") or "",
        operator=actor,
        connection_id=args.get("connection_id") or "",
        database_name=args.get("database_name") or "",
        max_affected_rows=args.get("max_affected_rows") or 100,
        confirm_text=args.get("confirm_text") or "",
        reason=args.get("reason") or "",
    )


@registry.register(
    name="ops.db.list_dml_history",
    title="查询 DML 执行历史",
    description="查询受控 DML 执行历史，包含操作者、连接、目标表、影响行数、状态和执行原因。只读。",
    scopes=["ops:read", "audit:read"],
    risk="low",
    category="db_write_read",
    write=False,
    input_schema={
        "type": "object",
        "properties": {
            "connection_id": {"type": "string"},
            "status": {"type": "string"},
            "statement_type": {"type": "string", "enum": ["INSERT", "UPDATE", "DELETE"]},
            "limit": {"type": "integer", "minimum": 1, "maximum": 200},
            "offset": {"type": "integer", "minimum": 0},
        },
        "additionalProperties": False,
    },
)
def db_list_dml_history_tool(args, ctx, db):
    return DbQueryExportService(db).list_dml_executions(
        connection_id=args.get("connection_id") or "",
        status=args.get("status") or "",
        statement_type=args.get("statement_type") or "",
        limit=args.get("limit") or 50,
        offset=args.get("offset") or 0,
    )


@registry.register(
    name="ops.db.get_dml_execution",
    title="查看 DML 执行详情",
    description="按 execution_id 查看受控 DML 执行详情、执行前样例和审计字段。只读。",
    scopes=["ops:read", "audit:read"],
    risk="low",
    category="db_write_read",
    write=False,
    input_schema={
        "type": "object",
        "properties": {"execution_id": {"type": "string"}},
        "required": ["execution_id"],
        "additionalProperties": False,
    },
)
def db_get_dml_execution_tool(args, ctx, db):
    return DbQueryExportService(db).get_dml_execution(args.get("execution_id") or "")
