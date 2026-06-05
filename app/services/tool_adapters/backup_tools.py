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


@registry.register(
    name="ops.create_backup",
    title="创建数据库备份",
    description="创建并校验 OPS SQLite 数据库备份。属于中风险写操作，默认受 Capability Server 写权限和确认策略控制。",
    scopes=["ops:write"],
    risk="medium",
    category="backup_write",
    write=True,
    requires_confirmation=True,
    input_schema={
        "type": "object",
        "properties": {
            "reason": {"type": "string", "description": "备份原因，例如 before_upgrade、manual、before_restore"},
            "prefix": {"type": "string", "description": "可选文件名前缀，必须以 ops_backup 开头；默认 ops_backup_mcp"},
            "confirm_text": {"type": "string", "description": "中风险确认短语：CREATE BACKUP"},
        },
        "required": ["confirm_text"],
        "additionalProperties": False,
    },
)
def create_backup_tool(args: Dict[str, Any], ctx, db):
    try:
        return create_database_backup(
            actor=_actor(ctx),
            reason=str(args.get("reason") or "mcp"),
            prefix=str(args.get("prefix") or "ops_backup_mcp"),
        )
    except BackupServiceError as exc:
        raise _map_error(exc)


@registry.register(
    name="ops.restore_backup",
    title="恢复数据库备份",
    description="从指定 SQLite 备份恢复数据库。严重风险；必须传 confirm_text=RESTORE <file>，执行前会创建安全备份。",
    scopes=["ops:write"],
    risk="critical",
    category="backup_restore",
    write=True,
    requires_confirmation=True,
    requires_human_approval=True,
    data_sensitivity="sensitive",
    input_schema={
        "type": "object",
        "properties": {
            "file": {"type": "string", "description": "备份文件名"},
            "confirm_text": {"type": "string", "description": "强确认短语：RESTORE <file>"},
            "create_safety_backup": {"type": "boolean", "description": "恢复前是否创建安全备份，默认 true"},
        },
        "required": ["file", "confirm_text"],
        "additionalProperties": False,
    },
)
def restore_backup_tool(args: Dict[str, Any], ctx, db):
    file_name = str(args.get("file") or "")
    try:
        try:
            db.close()
        except Exception:
            pass
        return restore_database_backup(
            file_name,
            confirm_text=str(args.get("confirm_text") or ""),
            actor=_actor(ctx),
            create_safety_backup=bool(args.get("create_safety_backup", True)),
        )
    except BackupServiceError as exc:
        expected = expected_restore_confirm_text(file_name) if file_name else "RESTORE <file>"
        raise HTTPException(status_code=exc.status_code, detail=f"{exc.message} expected={expected}")


@registry.register(
    name="ops.delete_backup",
    title="删除数据库备份",
    description="删除指定数据库备份文件。高风险；必须传 confirm_text=DELETE <file>。",
    scopes=["ops:write"],
    risk="high",
    category="backup_write",
    write=True,
    requires_confirmation=True,
    requires_human_approval=True,
    data_sensitivity="sensitive",
    input_schema={
        "type": "object",
        "properties": {
            "file": {"type": "string", "description": "备份文件名"},
            "confirm_text": {"type": "string", "description": "强确认短语：DELETE <file>"},
        },
        "required": ["file", "confirm_text"],
        "additionalProperties": False,
    },
)
def delete_backup_tool(args: Dict[str, Any], ctx, db):
    file_name = str(args.get("file") or "")
    try:
        return delete_database_backup(file_name, confirm_text=str(args.get("confirm_text") or ""), actor=_actor(ctx))
    except BackupServiceError as exc:
        expected = expected_delete_confirm_text(file_name) if file_name else "DELETE <file>"
        raise HTTPException(status_code=exc.status_code, detail=f"{exc.message} expected={expected}")
