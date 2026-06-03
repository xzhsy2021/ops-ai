from __future__ import annotations

from typing import Any, Dict

from fastapi import HTTPException

from app.services.job_service import get_operation_job, list_operation_jobs
from app.services.tool_registry import registry


@registry.register(
    name="ops.list_jobs",
    title="列出统一任务",
    description="列出统一任务中心中的 MCP/工具任务。只读，用于 AI/MCP 跟踪高风险工具任务进度。",
    scopes=["ops:read"],
    risk="low",
    category="job_read",
    input_schema={
        "type": "object",
        "properties": {
            "status": {"type": "string", "description": "可选状态：queued/running/success/failed"},
            "source_tool": {"type": "string", "description": "可选工具名，例如 ops.delete_backup"},
            "limit": {"type": "integer", "description": "返回数量，默认 50"},
        },
        "additionalProperties": False,
    },
)
def list_jobs_tool(args: Dict[str, Any], ctx, db):
    items = list_operation_jobs(
        db,
        status=str(args.get("status") or ""),
        source_tool=str(args.get("source_tool") or ""),
        limit=int(args.get("limit") or 50),
    )
    return {"summary": f"{len(items)} jobs", "count": len(items), "items": items}


@registry.register(
    name="ops.get_job_status",
    title="查询统一任务状态",
    description="查询统一任务中心中的单个任务详情。只读。",
    scopes=["ops:read"],
    risk="low",
    category="job_read",
    input_schema={
        "type": "object",
        "properties": {
            "job_id": {"type": "string", "description": "统一任务 ID"},
        },
        "required": ["job_id"],
        "additionalProperties": False,
    },
)
def get_job_status_tool(args: Dict[str, Any], ctx, db):
    item = get_operation_job(db, str(args.get("job_id") or ""))
    if not item:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"summary": f"job {item['status']}", "job": item}
