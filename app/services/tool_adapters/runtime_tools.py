from __future__ import annotations

from typing import Any, Dict

from fastapi import HTTPException

from app.services.runtime_resources import (
    CLEANUP_CONFIRM_TEXT,
    cleanup_runtime_artifacts,
    get_runtime_usage,
    get_storage_usage,
    preview_runtime_cleanup,
)
from app.services.tool_registry import registry


@registry.register(
    name="ops.get_runtime_usage",
    title="查看运行时资源占用",
    description="查看本地 OPS 进程、终端会话、SSH 连接池、发布任务和近期 MCP 调用的轻量运行状态。",
    scopes=["ops:read"],
    risk="low",
    category="read",
    input_schema={"type": "object", "properties": {}, "additionalProperties": False},
)
def get_runtime_usage_tool(args: Dict[str, Any], ctx, db):
    return get_runtime_usage(db)


@registry.register(
    name="ops.get_storage_usage",
    title="查看本地存储占用",
    description="查看 APP_DATA_DIR、数据库、日志、发布包、备份、密钥和 runtime 目录占用。",
    scopes=["ops:read"],
    risk="low",
    category="read",
    input_schema={"type": "object", "properties": {}, "additionalProperties": False},
)
def get_storage_usage_tool(args: Dict[str, Any], ctx, db):
    return get_storage_usage(db)


@registry.register(
    name="ops.cleanup_runtime_artifacts",
    title="清理过期运行时资源",
    description=(
        "按保留策略清理过期日志、备份、runtime 临时文件、发布历史、MCP 调用记录和可安全删除的发布包。"
        "默认 dry_run=true 仅预览；实际执行必须传 confirm_text=确认清理运行时资源。"
    ),
    scopes=["ops:write"],
    risk="high",
    category="runtime_cleanup",
    write=True,
    requires_confirmation=True,
    input_schema={
        "type": "object",
        "properties": {
            "dry_run": {"type": "boolean", "description": "默认 true，仅预览不删除"},
            "confirm_text": {"type": "string", "description": f"实际清理必须填写：{CLEANUP_CONFIRM_TEXT}"},
            "policy": {"type": "object", "description": "可选临时保留策略覆盖"},
        },
        "additionalProperties": False,
    },
)
def cleanup_runtime_artifacts_tool(args: Dict[str, Any], ctx, db):
    dry_run = bool(args.get("dry_run", True))
    policy = args.get("policy") if isinstance(args.get("policy"), dict) else {}
    if dry_run:
        return preview_runtime_cleanup(db, policy)
    confirm_text = str(args.get("confirm_text") or "")
    if confirm_text.strip() != CLEANUP_CONFIRM_TEXT:
        raise HTTPException(status_code=400, detail=f"cleanup confirmation required: {CLEANUP_CONFIRM_TEXT}")
    return cleanup_runtime_artifacts(
        db,
        dry_run=False,
        confirm_text=confirm_text,
        actor=getattr(ctx, "username", "") or getattr(ctx, "client_name", ""),
        policy=policy,
    )
