from __future__ import annotations

from typing import Any, Dict

from app.services.tool_registry import registry


def _actor(ctx) -> str:
    return getattr(ctx, "username", "") or getattr(ctx, "token_owner", "") or "mcp-ai"


@registry.register(
    name="ops.ai.save_analysis",
    title="保存 AI 分析结果",
    description="将 AI 工作流输出保存为平台 AI 分析记录，保留事实、推断、建议和证据链。",
    scopes=["ops:read"],
    risk="low",
    category="ai_analysis",
    write=False,
    ai_callable=True,
    ai_auto_callable=False,
    data_sensitivity="internal",
    output_masking=True,
    input_schema={
        "type": "object",
        "properties": {
            "analysis_type": {"type": "string"},
            "target_type": {"type": "string"},
            "target_id": {"type": "string"},
            "source_type": {"type": "string"},
            "source_id": {"type": "string"},
            "prompt_name": {"type": "string"},
            "input_refs": {"type": "string"},
            "output": {"type": "object"},
        },
        "required": ["analysis_type", "output"],
        "additionalProperties": False,
    },
)
def save_ai_analysis(args: Dict[str, Any], ctx, db):
    from app.services.ai_analysis import save_analysis
    return save_analysis(
        db,
        analysis_type=args.get("analysis_type") or "general",
        target_type=args.get("target_type") or "",
        target_id=args.get("target_id") or "",
        source_type=args.get("source_type") or "",
        source_id=args.get("source_id") or "",
        prompt_name=args.get("prompt_name") or "",
        input_refs=args.get("input_refs") or "",
        output=args.get("output") or {},
        created_by=_actor(ctx),
    )


@registry.register(
    name="ops.ai.get_analysis",
    title="查看 AI 分析记录",
    description="读取 AI 分析记录和证据链。",
    scopes=["ops:read"],
    risk="low",
    category="ai_analysis",
    write=False,
    ai_callable=True,
    ai_auto_callable=True,
    data_sensitivity="internal",
    output_masking=True,
    input_schema={"type": "object", "properties": {"analysis_id": {"type": "string"}}, "required": ["analysis_id"], "additionalProperties": False},
)
def get_ai_analysis(args: Dict[str, Any], ctx, db):
    from app.services.ai_analysis import get_analysis
    return get_analysis(db, args.get("analysis_id") or "")


@registry.register(
    name="ops.ai.list_analysis",
    title="查询 AI 分析记录",
    description="查询平台中沉淀的 AI 分析记录。",
    scopes=["ops:read"],
    risk="low",
    category="ai_analysis",
    write=False,
    ai_callable=True,
    ai_auto_callable=True,
    data_sensitivity="internal",
    output_masking=True,
    input_schema={"type": "object", "properties": {"analysis_type": {"type": "string"}, "target_type": {"type": "string"}, "target_id": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 200}}, "additionalProperties": False},
)
def list_ai_analysis(args: Dict[str, Any], ctx, db):
    from app.services.ai_analysis import list_analysis
    return list_analysis(db, analysis_type=args.get("analysis_type") or "", target_type=args.get("target_type") or "", target_id=args.get("target_id") or "", limit=args.get("limit") or 100)


@registry.register(
    name="ops.ai.generate_report_from_analysis",
    title="AI 分析生成报告",
    description="将 AI 分析记录生成报告中心制品。",
    scopes=["ops:read", "audit:read"],
    risk="low",
    category="ai_analysis",
    write=False,
    ai_callable=True,
    ai_auto_callable=False,
    data_sensitivity="internal",
    output_masking=True,
    input_schema={"type": "object", "properties": {"analysis_id": {"type": "string"}, "title": {"type": "string"}}, "required": ["analysis_id"], "additionalProperties": False},
)
def generate_ai_analysis_report(args: Dict[str, Any], ctx, db):
    from app.services.ai_analysis import generate_report_from_analysis
    return generate_report_from_analysis(db, args.get("analysis_id") or "", title=args.get("title") or "", created_by=_actor(ctx))


@registry.register(
    name="ops.ai.find_similar_analysis",
    title="查找相似 AI 分析",
    description="基于目标类型和分析类型查找相似历史分析记录。",
    scopes=["ops:read"],
    risk="low",
    category="ai_analysis",
    write=False,
    ai_callable=True,
    ai_auto_callable=True,
    data_sensitivity="internal",
    output_masking=True,
    input_schema={"type": "object", "properties": {"analysis_type": {"type": "string"}, "target_type": {"type": "string"}, "target_id": {"type": "string"}, "limit": {"type": "integer"}}, "additionalProperties": False},
)
def similar_analysis(args: Dict[str, Any], ctx, db):
    from app.services.ai_analysis import list_analysis
    return list_analysis(db, analysis_type=args.get("analysis_type") or "", target_type=args.get("target_type") or "", target_id=args.get("target_id") or "", limit=args.get("limit") or 10)
