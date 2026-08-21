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
    description=(
        "查询统一任务中心中的单个任务详情。用于轮询任务化工具（如 ops.inspection.run_security_daily、"
        "ops.inspection.run_servers_batch 等）的执行结果：status 为 success/failed 即完成，"
        "result 含执行结果与逐台明细，error_message 含失败原因。任务可能耗时数分钟，可反复轮询直到完成。只读。"
        "中文: 查询任务状态/任务结果/轮询任务/任务完成了吗. "
    ),
    scopes=["ops:read"],
    risk="low",
    category="job_read",
    related_tools=["ops.list_jobs", "ops.inspection.run_security_daily", "ops.security_report.collect"],
    keywords=["查询任务", "任务状态", "任务结果", "轮询任务", "任务完成"],
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
