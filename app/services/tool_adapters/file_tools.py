from __future__ import annotations

import os
from typing import Any, Dict

from fastapi import HTTPException

from app.services.package_retention import (
    get_package_retention_policy,
    save_package_retention_policy,
    list_packages as list_package_meta,
    package_path,
    safe_package_name,
    sha256_file,
    save_package_base64,
    save_package_fileobj,
    inspect_package_file,
    upsert_package_metadata,
    preview_package_cleanup,
    cleanup_packages,
)
from app.db.models import DeployPackage
from app.services.tool_registry import registry


@registry.register(
    name="ops.list_packages",
    description="查询文件中心的本地发布包，可按系统、服务关键字过滤，并返回回滚保护状态。",
    scopes=["ops:read"],
    input_schema={
        "type": "object",
        "properties": {
            "system": {"type": "string"},
            "service": {"type": "string"},
            "limit": {"type": "integer"},
            "with_retention": {"type": "boolean"},
        },
        "additionalProperties": False,
    },
)
def list_packages(args, ctx, db):
    return list_package_meta(
        db,
        system=args.get("system") or "",
        service=args.get("service") or "",
        limit=int(args.get("limit") or 50),
        with_retention=bool(args.get("with_retention", True)),
    )


@registry.register(
    name="ops.inspect_local_package",
    title="Inspect local deploy package",
    description="检查本地发布包路径、文件名、大小、扩展名、SHA256 和可上传性。stdio MCP 可读取用户本机 local_path；HTTP 工具仅能读取 OPS 后端本机路径。",
    scopes=["ops:read"],
    risk="low",
    category="package_read",
    input_schema={
        "type": "object",
        "properties": {
            "local_path": {"type": "string", "description": "本地包路径，stdio MCP 场景下是用户机器路径"},
            "filename": {"type": "string", "description": "可选，覆盖上传到文件中心的文件名"},
            "calculate_sha256": {"type": "boolean", "description": "是否计算 SHA256，默认 true"},
        },
        "required": ["local_path"],
        "additionalProperties": False,
    },
)
def inspect_local_package(args, ctx, db):
    policy = get_package_retention_policy(db)
    return inspect_package_file(
        args.get("local_path") or "",
        filename=args.get("filename") or "",
        policy=policy,
        calculate_sha256=bool(args.get("calculate_sha256", True)),
    )


@registry.register(
    name="ops.get_package_checksum",
    description="计算本地发布包 SHA256，并同步包元数据。",
    scopes=["ops:read"],
    input_schema={
        "type": "object",
        "properties": {
            "package_name": {"type": "string"},
            "expected_sha256": {"type": "string", "description": "可选，用于校验文件中心包是否与本地包一致"},
        },
        "required": ["package_name"],
        "additionalProperties": False,
    },
)
def get_package_checksum(args, ctx, db):
    name = safe_package_name(args.get("package_name"))
    path = package_path(name)
    expected = str(args.get("expected_sha256") or "").strip().lower()
    if not os.path.exists(path):
        return {"exists": False, "package_name": name, "expected_sha256": expected, "matches_expected": False}
    row = upsert_package_metadata(db, name, path, uploaded_by=ctx.username or ctx.token_owner)
    actual = (row.sha256 or "").lower()
    return {
        "exists": True,
        "package_name": name,
        "size": os.path.getsize(path),
        "size_bytes": os.path.getsize(path),
        "sha256": row.sha256,
        "expected_sha256": expected,
        "matches_expected": bool(expected and actual == expected) if expected else None,
        "verified": bool(expected and actual == expected) if expected else bool(actual),
    }


@registry.register(
    name="ops.get_package_retention_preview",
    description="预览发布包清理候选，不删除文件。会保护运行中、失败重试、回滚候选和最近成功发布包。",
    scopes=["ops:read"],
    risk="medium",
    category="package_retention",
    input_schema={
        "type": "object",
        "properties": {"policy": {"type": "object"}},
        "additionalProperties": False,
    },
)
def get_package_retention_preview(args, ctx, db):
    return preview_package_cleanup(db, args.get("policy") or {})


# ─── Utility: upload_package (used by deploy_tools.py, not registered as AI tool) ───

def upload_package(args, ctx, db):
    """上传发布包到 OPS 文件中心。供 deploy_tools.py 内部调用。"""
    system = args.get("system") or ""
    service = args.get("service") or ""
    uploaded_by = ctx.username or ctx.token_owner or "tool"
    overwrite = bool(args.get("overwrite") or False)
    if args.get("content_base64"):
        filename = args.get("filename") or os.path.basename(args.get("local_path") or "uploaded_package")
        meta = save_package_base64(
            db,
            filename=filename,
            content_base64=args.get("content_base64"),
            system=system,
            service=service,
            uploaded_by=uploaded_by,
            overwrite=overwrite,
        )
        meta["next_actions"] = [
            {"tool": "ops_get_package_checksum", "arguments": {"package_name": meta.get("package_name") or meta.get("name")}, "description": "Verify File Center checksum"},
            {"tool": "ops_create_deploy_plan", "description": "Create a deployment plan with this package"},
        ]
        return meta
    local_path = args.get("local_path") or ""
    if local_path:
        if not os.path.isfile(local_path):
            raise HTTPException(status_code=400, detail="local_path is not readable by OPS backend. Use stdio MCP bridge or content_base64.")
        if args.get("dry_run"):
            policy = get_package_retention_policy(db)
            return inspect_package_file(
                local_path,
                filename=args.get("filename") or os.path.basename(local_path),
                policy=policy,
                calculate_sha256=bool(args.get("calculate_sha256", True)),
            )
        with open(local_path, "rb") as f:
            meta = save_package_fileobj(
                db,
                filename=args.get("filename") or os.path.basename(local_path),
                fileobj=f,
                system=system,
                service=service,
                uploaded_by=uploaded_by,
                overwrite=overwrite,
            )
        meta["next_actions"] = [
            {"tool": "ops_get_package_checksum", "arguments": {"package_name": meta.get("package_name") or meta.get("name")}, "description": "Verify File Center checksum"},
            {"tool": "ops_create_deploy_plan", "description": "Create a deployment plan with this package"},
        ]
        return meta
    raise HTTPException(status_code=400, detail="Provide local_path for stdio MCP or content_base64 for HTTP tools")


