from __future__ import annotations

from typing import Any, Dict

from app.services.diagnostics import build_diagnostics, build_startup_checks
from app.services.tool_registry import registry


@registry.register(
    name="ops.run_diagnostics",
    title="运行本地安装诊断",
    description="检查前端构建、静态资源、运行目录、SQLite、Worker、MCP 工具 schema/权限与资源占用，适合交付包和升级后自检。",
    scopes=["ops:read"],
    risk="low",
    category="read",
    input_schema={
        "type": "object",
        "properties": {
            "mode": {"type": "string", "enum": ["full", "startup", "summary"], "description": "full 返回完整诊断；startup 仅返回启动自检；summary 返回轻量摘要"},
        },
        "additionalProperties": False,
    },
)
def run_diagnostics_tool(args: Dict[str, Any], ctx, db):
    mode = str(args.get("mode") or "full")
    if mode == "startup":
        return build_startup_checks(db)
    data = build_diagnostics(db)
    if mode == "summary":
        sections = data.get("sections", {})
        return {
            "status": data.get("status"),
            "generated_at": data.get("generated_at"),
            "startup_summary": sections.get("startup", {}).get("summary"),
            "health_summary": sections.get("health", {}).get("summary"),
            "runtime": {
                "uptime_seconds": sections.get("runtime", {}).get("process", {}).get("uptime_seconds"),
                "active_terminals": sections.get("runtime", {}).get("terminal", {}).get("active_sessions"),
                "recent_tool_calls_24h": sections.get("runtime", {}).get("recent_tool_calls_24h"),
                "cache": sections.get("runtime", {}).get("cache"),
            },
            "storage": {
                "total_managed_size_human": sections.get("storage", {}).get("total_managed_size_human"),
                "cache": sections.get("storage", {}).get("cache"),
            },
            "mcp": {
                "status": sections.get("mcp", {}).get("status"),
                "tool_count": sections.get("mcp", {}).get("tool_count"),
                "schema_errors": sections.get("mcp", {}).get("schema_errors", [])[:5],
            },
        }
    return data


@registry.register(
    name="ops.get_system_status",
    title="读取系统状态",
    description="读取系统健康状态、数据库、运行目录、备份、磁盘和发布 Worker 摘要。只读，可供 MCP/AI 自动调用。",
    scopes=["ops:read"],
    risk="low",
    category="read",
    input_schema={"type": "object", "properties": {}, "additionalProperties": False},
)
def get_system_status_tool(args: Dict[str, Any], ctx, db):
    from app.services.system_health import build_system_health
    return build_system_health(db)


@registry.register(
    name="ops.get_build_info",
    title="读取前后端构建信息",
    description="读取前端构建时间、dist 状态、后端启动时间和版本信息，用于判断构建产物是否过期。",
    scopes=["ops:read"],
    risk="low",
    category="read",
    input_schema={"type": "object", "properties": {}, "additionalProperties": False},
)
def get_build_info_tool(args: Dict[str, Any], ctx, db):
    from app.services.build_info import get_build_info
    return get_build_info()


@registry.register(
    name="ops.get_recent_errors",
    title="读取最近错误日志",
    description="聚合本地日志中的最近错误与警告摘要，返回来源、级别、时间、模块和摘要。只读。",
    scopes=["ops:read"],
    risk="low",
    category="read",
    input_schema={
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "description": "返回条数，1-200，默认 20"},
            "include_warnings": {"type": "boolean", "description": "是否包含 warn/warning 级别，默认 true"},
        },
        "additionalProperties": False,
    },
)
def get_recent_errors_tool(args: Dict[str, Any], ctx, db):
    from app.services.error_log import get_recent_errors
    return get_recent_errors(limit=int(args.get("limit") or 20), include_warnings=bool(args.get("include_warnings", True)))


@registry.register(
    name="ops.export_diagnostics_report",
    title="生成诊断报告 JSON",
    description="生成单文件 JSON 诊断报告内容，包含系统概览、健康状态、构建信息、最近错误、MCP 自检和修复建议。只读审计。",
    scopes=["ops:read"],
    risk="low",
    category="read",
    input_schema={"type": "object", "properties": {}, "additionalProperties": False},
)
def export_diagnostics_report_tool(args: Dict[str, Any], ctx, db):
    from app.services.diagnostics import build_diagnostics_report
    report = build_diagnostics_report(db)
    return {
        "summary": "diagnostics report generated",
        "schema_version": report.get("schema_version"),
        "generated_at": report.get("generated_at"),
        "status": (report.get("diagnostics") or {}).get("status"),
        "report": report,
    }
