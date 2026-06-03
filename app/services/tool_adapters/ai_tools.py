from __future__ import annotations

from typing import Any, Dict

from app.services.ai_diagnostics import build_ai_diagnostic_analysis
from app.services.tool_registry import registry


@registry.register(
    name="ops.analyze_diagnostics",
    title="AI 诊断助手分析",
    description=(
        "基于系统诊断、最近错误、MCP 工具目录、工具调用审计和统一任务中心生成只读诊断分析。"
        "不会执行发布、恢复、删除、SQL 或终端等高风险动作。"
    ),
    scopes=["ops:read"],
    risk="low",
    category="ai_read",
    input_schema={
        "type": "object",
        "properties": {
            "mode": {"type": "string", "enum": ["summary", "full"], "description": "summary 返回压缩证据；full 返回更完整证据"},
            "focus": {"type": "string", "description": "可选关注方向，例如 deploy、backup、frontend、mcp"},
            "include_report": {"type": "boolean", "description": "是否附带完整 diagnostics_report，默认 false"},
        },
        "additionalProperties": False,
    },
)
def analyze_diagnostics_tool(args: Dict[str, Any], ctx, db):
    return build_ai_diagnostic_analysis(
        db,
        mode=str(args.get("mode") or "summary"),
        focus=str(args.get("focus") or ""),
        include_report=bool(args.get("include_report", False)),
    )
