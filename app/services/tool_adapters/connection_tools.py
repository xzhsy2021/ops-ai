from __future__ import annotations

from app.db.models import DatabaseConnection
from app.maintenance.service import CleanupService
from app.services.tool_registry import registry

_MASK = "***"


def _conn_summary(c: DatabaseConnection) -> dict:
    """轻量连接摘要，不含密码等敏感字段。"""
    return {
        "id": c.id,
        "name": c.name,
        "environment": c.environment,
        "db_type": c.db_type,
        "host": c.host,
        "port": c.port,
        "username": c.username,
        "database_name": c.database_name,
        "use_ssh_tunnel": bool(getattr(c, "use_ssh_tunnel", False)),
        "ssh_mode": getattr(c, "ssh_mode", None) or "manual",
        "ssh_server_id": getattr(c, "ssh_server_id", None),
        "ssh_server_name": getattr(c, "ssh_server_name", None),
        "allow_dml": bool(getattr(c, "allow_dml", False)),
        "allowed_dml_types": getattr(c, "allowed_dml_types", None) or [],
        "max_affected_rows_default": getattr(c, "max_affected_rows_default", 100) or 100,
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }


def _conn_detail(c: DatabaseConnection) -> dict:
    """完整连接详情，密码字段脱敏。"""
    return {
        "id": c.id,
        "name": c.name,
        "environment": c.environment,
        "db_type": c.db_type,
        "host": c.host,
        "port": c.port,
        "username": c.username,
        "password": _MASK,
        "database_name": c.database_name,
        "description": getattr(c, "description", None),
        "use_ssh_tunnel": bool(getattr(c, "use_ssh_tunnel", False)),
        "ssh_mode": getattr(c, "ssh_mode", None) or "manual",
        "ssh_server_id": getattr(c, "ssh_server_id", None),
        "ssh_server_name": getattr(c, "ssh_server_name", None),
        "ssh_host": getattr(c, "ssh_host", None),
        "ssh_port": getattr(c, "ssh_port", None),
        "ssh_username": getattr(c, "ssh_username", None),
        "ssh_password": _MASK if getattr(c, "ssh_password_encrypted", None) else None,
        "ssh_key_path": getattr(c, "ssh_key_path", None),
        "ssh_key_passphrase": _MASK if getattr(c, "ssh_key_passphrase_encrypted", None) else None,
        "ssh_key_has_content": bool(getattr(c, "ssh_key_content_encrypted", None)),
        "ssh_remote_bind_host": getattr(c, "ssh_remote_bind_host", None),
        "ssh_target_server_name": getattr(c, "ssh_target_server_name", None),
        "ssh_target_host": getattr(c, "ssh_target_host", None),
        "ssh_target_port": getattr(c, "ssh_target_port", None),
        "ssh_target_username": getattr(c, "ssh_target_username", None),
        "ssh_target_password": _MASK if getattr(c, "ssh_target_password_encrypted", None) else None,
        "ssh_target_key_path": getattr(c, "ssh_target_key_path", None),
        "ssh_target_key_passphrase": _MASK if getattr(c, "ssh_target_key_passphrase_encrypted", None) else None,
        "allow_dml": bool(getattr(c, "allow_dml", False)),
        "allowed_dml_types": getattr(c, "allowed_dml_types", None) or [],
        "allowed_tables": getattr(c, "allowed_tables", None) or [],
        "blocked_tables": getattr(c, "blocked_tables", None) or [],
        "max_affected_rows_default": getattr(c, "max_affected_rows_default", 100) or 100,
        "require_dml_reason": bool(getattr(c, "require_dml_reason", True)),
        "created_by": c.created_by,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
    }


@registry.register(
    name="ops.list_connections",
    title="查询数据库连接列表",
    description="List database connections - 列出已配置的数据库连接，支持按关键字和环境筛选。返回摘要信息，不含密码。",
    scopes=["ops:read"],
    risk="low",
    category="connection_read",
    write=False,
    ai_callable=True,
    ai_auto_callable=True,
    data_sensitivity="internal",
    input_schema={
        "type": "object",
        "properties": {
            "keyword": {"type": "string", "description": "按名称关键字模糊筛选"},
            "env": {"type": "string", "description": "按环境精确筛选"},
            "limit": {"type": "integer", "default": 100, "description": "返回数量上限，最大 500"},
        },
        "additionalProperties": False,
    },
)
def list_connections_tool(args, ctx, db):
    keyword = str(args.get("keyword") or "").strip()
    env = str(args.get("env") or "").strip()
    limit = min(max(int(args.get("limit") or 100), 1), 500)
    query = db.query(DatabaseConnection)
    if keyword:
        query = query.filter(DatabaseConnection.name.ilike(f"%{keyword}%"))
    if env:
        query = query.filter(DatabaseConnection.environment == env)
    rows = query.order_by(DatabaseConnection.name).limit(limit).all()
    return {"items": [_conn_summary(c) for c in rows], "total": len(rows), "summary": f"查询到 {len(rows)} 个数据库连接"}


@registry.register(
    name="ops.get_connection",
    title="查询数据库连接详情",
    description="Get database connection detail - 获取指定数据库连接的完整配置信息，密码字段脱敏显示。",
    scopes=["ops:read"],
    risk="low",
    category="connection_read",
    write=False,
    ai_callable=True,
    ai_auto_callable=False,
    data_sensitivity="sensitive",
    input_schema={
        "type": "object",
        "properties": {
            "connection_id": {"type": "string", "description": "数据库连接 ID"},
        },
        "required": ["connection_id"],
        "additionalProperties": False,
    },
)
def get_connection_tool(args, ctx, db):
    conn = db.query(DatabaseConnection).filter(DatabaseConnection.id == args.get("connection_id")).first()
    if not conn:
        return {"ok": False, "error": "Connection not found", "summary": "数据库连接未找到"}
    return {"ok": True, "item": _conn_detail(conn), "summary": f"连接 {conn.name} 详情"}


@registry.register(
    name="ops.test_connection",
    title="测试数据库连接",
    description="Test database connection - 测试指定数据库连接是否可用。尝试建立连接并执行 SELECT 1 验证。",
    scopes=["ops:read"],
    risk="medium",
    category="connection_read",
    write=False,
    ai_callable=True,
    ai_auto_callable=False,
    data_sensitivity="sensitive",
    input_schema={
        "type": "object",
        "properties": {
            "connection_id": {"type": "string", "description": "要测试的数据库连接 ID"},
        },
        "required": ["connection_id"],
        "additionalProperties": False,
    },
)
def test_connection_tool(args, ctx, db):
    connection_id = args.get("connection_id")
    svc = CleanupService(db)
    conn = svc.get_connection(connection_id)
    if not conn:
        return {"ok": False, "error": "Connection not found", "summary": "数据库连接未找到"}
    executor = svc._connection_executor(conn, conn.database_name)
    try:
        raw = executor._get_connection()
        with raw.cursor() as cur:
            cur.execute("SELECT 1 AS ok")
            row = cur.fetchone()
        return {
            "ok": True,
            "connection_id": connection_id,
            "connection_name": conn.name,
            "db_type": conn.db_type,
            "host": conn.host,
            "port": conn.port,
            "database_name": conn.database_name,
            "via_ssh_tunnel": bool(getattr(conn, "use_ssh_tunnel", False)),
            "result": row,
            "summary": f"连接 {conn.name} 测试通过",
        }
    except Exception as e:
        return {
            "ok": False,
            "connection_id": connection_id,
            "connection_name": conn.name,
            "error": str(e),
            "summary": f"连接 {conn.name} 测试失败: {e}",
        }
    finally:
        executor.close()
