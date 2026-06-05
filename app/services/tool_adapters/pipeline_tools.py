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


@registry.register(
    name="ops.create_pipeline",
    description="创建发布流程/Pipeline。High risk; requires human approval. 中文: 创建Pipeline/新增流程.",
    scopes=["server:write"],
    risk="high",
    category="pipeline_write",
    write=True,
    requires_confirmation=True,
    requires_human_approval=True,
    ai_callable=True,
    ai_auto_callable=False,
    data_sensitivity="internal",
    input_schema={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Pipeline名称"},
            "system": {"type": "string", "description": "所属系统"},
            "description": {"type": "string", "description": "描述"},
            "steps": {"type": "array", "description": "步骤列表", "items": {"type": "object"}},
        },
        "required": ["name"],
    },
)
def create_pipeline_tool(args, ctx, db):
    from config_manager import save_config, load_config
    name = args.get("name", "")
    if not name:
        return {"ok": False, "error": "name is required"}
    try:
        config = load_config()
        pipelines = config.get("pipelines", {})
        if name in pipelines:
            return {"ok": False, "error": f"Pipeline '{name}' already exists"}
        pipeline_data = {
            "name": name,
            "system": args.get("system", ""),
            "description": args.get("description", ""),
            "steps": args.get("steps", []),
        }
        pipelines[name] = pipeline_data
        config["pipelines"] = pipelines
        save_config(config)
        return {"ok": True, "name": name}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@registry.register(
    name="ops.update_pipeline",
    description="更新发布流程/Pipeline配置。High risk; requires human approval. 中文: 更新Pipeline/修改流程.",
    scopes=["server:write"],
    risk="high",
    category="pipeline_write",
    write=True,
    requires_confirmation=True,
    requires_human_approval=True,
    ai_callable=True,
    ai_auto_callable=False,
    data_sensitivity="internal",
    input_schema={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Pipeline名称"},
            "system": {"type": "string", "description": "所属系统"},
            "description": {"type": "string", "description": "描述"},
            "steps": {"type": "array", "description": "步骤列表", "items": {"type": "object"}},
        },
        "required": ["name"],
    },
)
def update_pipeline_tool(args, ctx, db):
    from config_manager import save_config, load_config
    name = args.get("name", "")
    if not name:
        return {"ok": False, "error": "name is required"}
    try:
        config = load_config()
        pipelines = config.get("pipelines", {})
        if name not in pipelines:
            return {"ok": False, "error": f"Pipeline '{name}' not found"}
        existing = pipelines[name]
        if args.get("system") is not None:
            existing["system"] = args["system"]
        if args.get("description") is not None:
            existing["description"] = args["description"]
        if args.get("steps") is not None:
            existing["steps"] = args["steps"]
        pipelines[name] = existing
        config["pipelines"] = pipelines
        save_config(config)
        return {"ok": True, "name": name}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@registry.register(
    name="ops.delete_pipeline",
    description="删除发布流程/Pipeline。Critical risk; requires human approval. 中文: 删除Pipeline/移除流程.",
    scopes=["server:write"],
    risk="critical",
    category="pipeline_write",
    write=True,
    requires_confirmation=True,
    requires_human_approval=True,
    ai_callable=True,
    ai_auto_callable=False,
    data_sensitivity="internal",
    input_schema={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Pipeline名称"},
            "confirm_text": {"type": "string", "description": "确认短语: DELETE <name>"},
        },
        "required": ["name", "confirm_text"],
    },
)
def delete_pipeline_tool(args, ctx, db):
    from config_manager import save_config, load_config
    name = args.get("name", "")
    confirm = args.get("confirm_text", "")
    expected = f"DELETE {name}"
    if confirm != expected:
        return {"ok": False, "error": f"confirm_text must be '{expected}'"}
    try:
        config = load_config()
        pipelines = config.get("pipelines", {})
        if name not in pipelines:
            return {"ok": False, "error": f"Pipeline '{name}' not found"}
        del pipelines[name]
        config["pipelines"] = pipelines
        save_config(config)
        return {"ok": True, "name": name}
    except Exception as e:
        return {"ok": False, "error": str(e)}
