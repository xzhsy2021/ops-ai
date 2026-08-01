from __future__ import annotations
from app.services.tool_registry import registry

@registry.register(
    name="ops.list_pipelines",
    description="列出发布流程/Pipeline配置。Use when user asks about deployment pipelines. 中文: 查看Pipeline列表/流程列表.",
    scopes=["ops:read"],
    risk="low",
    category="pipeline_read",
    write=False,
    ai_callable=True,
    ai_auto_callable=True,
    data_sensitivity="internal",
    input_schema={
        "type": "object",
        "properties": {
            "system": {"type": "string", "description": "按系统过滤"},
            "limit": {"type": "integer", "description": "最大返回数量", "default": 100},
        },
    },
)
def list_pipelines_tool(args, ctx, db):
    from app.domain.inventory.services import InventoryReadService
    inv = InventoryReadService()
    limit = min(int(args.get("limit") or 100), 500)
    keyword = args.get("keyword", "")
    try:
        pipelines = inv.list_pipelines(db, keyword=keyword, limit=limit)
        items = list(pipelines) if isinstance(pipelines, list) else []
        return {"items": items, "total": len(items)}
    except Exception as e:
        return {"items": [], "total": 0, "error": str(e)}


@registry.register(
    name="ops.get_pipeline",
    description="获取单个 Pipeline 详情。中文: 查看Pipeline详情/流程详情.",
    scopes=["ops:read"],
    risk="low",
    category="pipeline_read",
    write=False,
    ai_callable=True,
    ai_auto_callable=False,
    data_sensitivity="internal",
    input_schema={
        "type": "object",
        "properties": {
            "pipeline_id": {"type": "string", "description": "Pipeline ID"},
        },
        "required": ["pipeline_id"],
    },
)
def get_pipeline_tool(args, ctx, db):
    from app.domain.inventory.services import InventoryReadService
    inv = InventoryReadService()
    pipeline_id = args.get("pipeline_id", "")
    try:
        p = inv.get_pipeline(db, pipeline_id)
        return p if p else {"found": False, "pipeline_id": pipeline_id}
    except Exception as e:
        return {"found": False, "pipeline_id": pipeline_id, "error": str(e)}


