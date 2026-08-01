from __future__ import annotations

from typing import Any, Dict

from fastapi import HTTPException

from app.services.backup_service import (
    BackupServiceError,
    create_database_backup,
    delete_database_backup,
    expected_delete_confirm_text,
    expected_restore_confirm_text,
    list_database_backups,
    restore_database_backup,
    verify_backup_file,
)
from app.services.tool_registry import registry


def _actor(ctx) -> str:
    return getattr(ctx, "username", "") or getattr(ctx, "token_owner", "") or getattr(ctx, "client_name", "") or "tool"


def _map_error(exc: BackupServiceError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.message)


@registry.register(
    name="ops.list_backups",
    title="列出数据库备份",
    description="列出 OPS 本地 SQLite 数据库备份文件，包含大小、创建时间、备份类型和恢复/删除确认短语。只读。",
    scopes=["ops:read"],
    risk="low",
    category="backup_read",
    input_schema={
        "type": "object",
        "properties": {
            "verify_latest": {"type": "boolean", "description": "是否对最新备份执行轻量 SQLite quick_check"},
        },
        "additionalProperties": False,
    },
)
def list_backups_tool(args: Dict[str, Any], ctx, db):
    try:
        backups = list_database_backups(verify_latest=bool(args.get("verify_latest", False)))
        return {"summary": f"{len(backups)} backups", "count": len(backups), "backups": backups}
    except BackupServiceError as exc:
        raise _map_error(exc)


@registry.register(
    name="ops.verify_backup",
    title="校验数据库备份",
    description="对指定数据库备份执行 SQLite quick_check 与可选 SHA256 校验，不修改数据。",
    scopes=["ops:read"],
    risk="low",
    category="backup_read",
    input_schema={
        "type": "object",
        "properties": {
            "file": {"type": "string", "description": "备份文件名，例如 ops_backup_20260514_120000.db"},
            "include_checksum": {"type": "boolean", "description": "是否计算 SHA256，默认 true"},
        },
        "required": ["file"],
        "additionalProperties": False,
    },
)
def verify_backup_tool(args: Dict[str, Any], ctx, db):
    try:
        return verify_backup_file(str(args.get("file") or ""), include_checksum=bool(args.get("include_checksum", True)))
    except BackupServiceError as exc:
        raise _map_error(exc)


