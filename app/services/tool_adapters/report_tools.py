from __future__ import annotations

from app.services.report_center import get_report, list_report_types, list_reports, report_to_dict, report_summary
from app.services.tool_registry import registry


@registry.register(
    name="ops.list_reports",
    title="查询报告中心",
    description="查询报告中心中的诊断、发布、备份和 MCP/AI 操作链路报告。只读。",
    scopes=["ops:read", "audit:read"],
    risk="low",
    category="report_read",
    write=False,
    input_schema={
        "type": "object",
        "properties": {
            "report_type": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 500},
        },
        "additionalProperties": False,
    },
)
def list_reports_tool(args, ctx, db):
    return list_reports(db, report_type=args.get("report_type") or "", limit=args.get("limit") or 100)


@registry.register(
    name="ops.get_report",
    title="查看报告元数据",
    description="按报告 ID 查看报告中心元数据和下载链接。只读，不读取文件正文。",
    scopes=["ops:read", "audit:read"],
    risk="low",
    category="report_read",
    write=False,
    input_schema={
        "type": "object",
        "properties": {"report_id": {"type": "string"}},
        "required": ["report_id"],
        "additionalProperties": False,
    },
)
def get_report_tool(args, ctx, db):
    return report_to_dict(get_report(db, args.get("report_id") or ""))


@registry.register(
    name="ops.get_report_summary",
    title="查看报告中心概览",
    description="查看报告中心数量、类型、最近报告和总大小。只读。",
    scopes=["ops:read", "audit:read"],
    risk="low",
    category="report_read",
    write=False,
    input_schema={"type": "object", "properties": {}, "additionalProperties": False},
)
def report_summary_tool(args, ctx, db):
    return report_summary(db)


@registry.register(
    name="ops.list_report_types",
    title="查询可生成报告类型",
    description="查询报告中心支持的报告类型、目标和格式。只读。",
    scopes=["ops:read", "audit:read"],
    risk="low",
    category="report_read",
    write=False,
    input_schema={"type": "object", "properties": {}, "additionalProperties": False},
)
def list_report_types_tool(args, ctx, db):
    return list_report_types()


