from __future__ import annotations

from typing import Any, Dict, List, Optional


def _format_tool(tool_def, output_format: str = "native", include_schema: bool = True, policy: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if output_format == "mcp":
        return tool_def.to_mcp_dict()
    elif output_format == "openai":
        return tool_def.to_openai_dict()
    elif output_format == "anthropic":
        return tool_def.to_anthropic_dict()
    else:
        return tool_def.to_public_dict(include_schema=include_schema, policy=policy)


def build_tool_manifest(
    tools: List[Any],
    *,
    output_format: str = "native",
    include_schema: bool = True,
    category: str = "",
    risk: str = "",
    limit: int = 100,
    cursor: int = 0,
    policies: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    category = (category or "").strip()
    risk = (risk or "").strip()
    policies = policies or {}

    items: List[Dict[str, Any]] = []
    for tool_def in tools:
        if category and getattr(tool_def, "category", "") != category:
            continue
        if risk and getattr(tool_def, "risk", "") != risk:
            continue
        tool_policy = policies.get(tool_def.name)
        item = _format_tool(tool_def, output_format=output_format, include_schema=include_schema, policy=tool_policy)
        items.append(item)

    total = len(items)
    try:
        limit = max(1, min(int(limit), 500))
        cursor = max(0, int(cursor))
    except Exception:
        limit, cursor = 100, 0

    page = items[cursor:cursor + limit]
    next_cursor = cursor + limit if cursor + limit < total else None

    return {
        "tools": page,
        "pagination": {"total": total, "limit": limit, "cursor": cursor, "next_cursor": next_cursor},
        "filters": {"category": category, "risk": risk, "format": output_format},
    }
