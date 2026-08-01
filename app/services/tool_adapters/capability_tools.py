from __future__ import annotations

from typing import Any, Dict

from app.services.tool_registry import registry


@registry.register(
    name="ops.describe_capabilities",
    title="描述当前可用能力",
    description=(
        "返回当前 Token/用户可自动发现和调用的 OPS 能力清单。"
        "可按 category 过滤；include_schema=true 时返回完整参数 schema。"
        "此工具用于不支持 MCP tools/list 的 Agent 先了解自己能调用哪些工具。"
        "默认 profile=ai_full 返回全部工具（含 L4 高危工具的授权引导），"
        "与 MCP tools/list 保持一致；可传 profile=daily_ops 仅返回日常运维白名单。"
    ),
    scopes=["ops:read"],
    risk="low",
    category="read",
    input_schema={
        "type": "object",
        "properties": {
            "profile": {"type": "string", "description": "Tool exposure profile: daily_ops, expert, or admin_full."},
            "category": {"type": "string", "description": "可选工具分类，例如 app、deploy_plan、server_read"},
            "include_schema": {"type": "boolean", "description": "是否返回 input_schema 和 output_schema"},
            "include_disabled": {"type": "boolean", "description": "是否返回被策略禁用/权限不足的工具及原因"},
            "limit": {"type": "integer", "description": "返回工具数量上限"},
            "cursor": {"type": "integer", "description": "分页游标，上一页 pagination.next_cursor"},
        },
        "additionalProperties": False,
    },
)
def describe_capabilities(args: Dict[str, Any], ctx, db):
    # 默认使用 ai_full：与 MCP bridge 的 tools/list 默认 profile 保持一致。
    # 否则 AI agent 调用此工具时只能看到 daily_ops 白名单（73 个），
    # 而 tools/list 返回 182 个，造成能力发现不一致。
    # 可通过 OPS_DESCRIBE_DEFAULT_PROFILE 环境变量覆盖。
    import os
    default_profile = os.getenv("OPS_DESCRIBE_DEFAULT_PROFILE", "ai_full")
    return registry.describe_capabilities(
        db,
        ctx,
        category=args.get("category") or "",
        profile=args.get("profile") or default_profile,
        include_schema=bool(args.get("include_schema", True)),
        include_disabled=bool(args.get("include_disabled", False)),
        limit=int(args.get("limit") or 200),
        cursor=int(args.get("cursor") or 0),
    )


@registry.register(
    name="ops.get_tool_risk_policy",
    title="查看 MCP 工具风险策略",
    description="返回 OPS/MCP 工具风险等级、确认规则和任务化建议。只读，用于 Agent 在执行前理解风险边界。",
    scopes=["ops:read"],
    risk="low",
    category="read",
    input_schema={"type": "object", "properties": {}, "additionalProperties": False},
)
def get_tool_risk_policy(args: Dict[str, Any], ctx, db):
    from app.services.risk_policy import risk_policy_manifest
    from app.services.tool_policy import get_capability_settings
    return {
        **risk_policy_manifest(),
        "settings": get_capability_settings(db),
    }
