from __future__ import annotations

from typing import Any, Dict, List

from fastapi import HTTPException

from app.db.models import InspectionIssue
from app.services.tool_registry import registry


def _issue_to_dict(row: InspectionIssue) -> Dict[str, Any]:
    return {
        "id": row.id,
        "run_id": row.run_id,
        "scope_type": row.scope_type,
        "server_id": row.server_id,
        "project_id": row.project_id,
        "title": row.title,
        "description": row.description,
        "risk_level": row.risk_level,
        "status": row.status,
        "owner_id": row.owner_id,
        "deadline_at": row.deadline_at.isoformat() if row.deadline_at else None,
        "fixed_at": row.fixed_at.isoformat() if row.fixed_at else None,
        "verified_at": row.verified_at.isoformat() if row.verified_at else None,
        "suggestion": row.suggestion,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _query(db, args: Dict[str, Any]):
    q = db.query(InspectionIssue)
    if args.get("status"):
        q = q.filter(InspectionIssue.status == args.get("status"))
    if args.get("risk_level"):
        q = q.filter(InspectionIssue.risk_level == args.get("risk_level"))
    if args.get("scope_type"):
        q = q.filter(InspectionIssue.scope_type == args.get("scope_type"))
    if args.get("server_id"):
        q = q.filter(InspectionIssue.server_id == args.get("server_id"))
    if args.get("project_id"):
        q = q.filter(InspectionIssue.project_id == args.get("project_id"))
    return q


@registry.register(
    name="ops.risk.list",
    title="查询风险问题",
    description="查询来自巡检/诊断/状态等模块的风险问题。当前优先承接巡检风险。",
    scopes=["ops:read"],
    risk="low",
    category="risk",
    write=False,
    ai_callable=True,
    ai_auto_callable=True,
    data_sensitivity="internal",
    output_masking=True,
    example_prompts=["当前有哪些高危风险？", "列出项目 A 未闭环风险"],
    input_schema={
        "type": "object",
        "properties": {
            "status": {"type": "string"},
            "risk_level": {"type": "string"},
            "scope_type": {"type": "string"},
            "server_id": {"type": "string"},
            "project_id": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 200},
        },
        "additionalProperties": False,
    },
)
def list_risks(args: Dict[str, Any], ctx, db):
    limit = max(1, min(int(args.get("limit") or 50), 200))
    rows = _query(db, args).order_by(InspectionIssue.created_at.desc()).limit(limit).all()
    return {"items": [_issue_to_dict(r) for r in rows], "total": len(rows), "source": "inspection_issues"}


@registry.register(
    name="ops.risk.get",
    title="查看风险详情",
    description="查看指定风险问题详情。",
    scopes=["ops:read"],
    risk="low",
    category="risk",
    write=False,
    ai_callable=True,
    ai_auto_callable=True,
    data_sensitivity="internal",
    output_masking=True,
    input_schema={"type": "object", "properties": {"risk_id": {"type": "string"}}, "required": ["risk_id"], "additionalProperties": False},
)
def get_risk(args: Dict[str, Any], ctx, db):
    row = db.query(InspectionIssue).filter(InspectionIssue.id == (args.get("risk_id") or "")).first()
    if not row:
        raise HTTPException(status_code=404, detail="Risk not found")
    return _issue_to_dict(row)


@registry.register(
    name="ops.risk.triage",
    title="未闭环风险分流",
    description="对未闭环风险按生产/高危/超期/重复等维度生成处理优先级建议，不改变风险状态。",
    scopes=["ops:read"],
    risk="low",
    category="risk",
    write=False,
    ai_callable=True,
    ai_auto_callable=True,
    data_sensitivity="internal",
    output_masking=True,
    related_tools=["ops.risk.list", "ops.risk.generate_fix_plan"],
    input_schema={"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 200}}, "additionalProperties": False},
)
def triage_risks(args: Dict[str, Any], ctx, db):
    data = list_risks({"status": "OPEN", "limit": args.get("limit") or 100}, ctx, db)
    items = data.get("items") or []
    rank = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    sorted_items = sorted(items, key=lambda x: (rank.get(str(x.get("risk_level") or "LOW"), 9), str(x.get("created_at") or "")))
    priorities = []
    for idx, item in enumerate(sorted_items[:20], start=1):
        level = item.get("risk_level") or "LOW"
        priorities.append({
            "risk_id": item.get("id"),
            "priority": "P0" if level == "HIGH" else "P1" if level == "MEDIUM" else "P2",
            "title": item.get("title"),
            "risk_level": level,
            "reason": "高危优先处理" if level == "HIGH" else "中低危按影响范围和整改期限处理",
            "suggestion": item.get("suggestion") or "请指定负责人处理并复查。",
        })
    return {"summary": f"当前未闭环风险 {len(items)} 个，建议优先处理 {len([x for x in items if x.get('risk_level') == 'HIGH'])} 个高危风险。", "priorities": priorities, "recommendations": priorities}


@registry.register(
    name="ops.risk.generate_fix_plan",
    title="生成风险整改计划",
    description="为风险问题生成整改计划。只生成建议，不直接执行处置。",
    scopes=["ops:read"],
    risk="medium",
    category="risk",
    write=False,
    ai_callable=True,
    ai_auto_callable=False,
    data_sensitivity="internal",
    output_masking=True,
    input_schema={"type": "object", "properties": {"risk_id": {"type": "string"}}, "required": ["risk_id"], "additionalProperties": False},
)
def generate_fix_plan(args: Dict[str, Any], ctx, db):
    risk = get_risk(args, ctx, db)
    return {
        "summary": f"风险 {risk.get('title')} 的整改计划已生成。",
        "facts": [{"id": "fact-risk", "source_tool": "ops.risk.get", "ref": risk.get("id"), "content": risk.get("description")}],
        "inferences": [{"claim": f"该问题风险等级为 {risk.get('risk_level')}，状态为 {risk.get('status')}。", "confidence": "high", "based_on": ["fact-risk"]}],
        "recommendations": [
            {"action": risk.get("suggestion") or "请根据风险描述进行修复。", "requires_human_approval": False, "risk": risk.get("risk_level")},
            {"action": "修复后执行复查并将状态流转为 VERIFIED。", "requires_human_approval": True, "risk": "medium"},
        ],
        "evidence": [{"source_tool": "ops.risk.get", "ref": risk.get("id")}],
        "confidence": "medium",
    }


def _approval_only(name: str):
    def handler(args: Dict[str, Any], ctx, db):
        return {"ok": False, "summary": f"{name} 需要人工审批，请在风险中心操作。", "requires_human_approval": True}
    return handler


registry.register(
    name="ops.risk.update_status", title="更新风险状态", description="更新风险状态。高风险写操作，需要人工审批。", scopes=["ops:write"], risk="high", category="risk_write", write=True, requires_confirmation=True, requires_human_approval=True, ai_callable=True, ai_auto_callable=False, input_schema={"type": "object", "properties": {"risk_id": {"type": "string"}, "status": {"type": "string"}}, "required": ["risk_id", "status"], "additionalProperties": False},
)(_approval_only("更新风险状态"))
registry.register(
    name="ops.risk.verify", title="复查风险", description="复查风险。高风险写操作，需要人工审批。", scopes=["ops:write"], risk="high", category="risk_write", write=True, requires_confirmation=True, requires_human_approval=True, ai_callable=True, ai_auto_callable=False, input_schema={"type": "object", "properties": {"risk_id": {"type": "string"}}, "required": ["risk_id"], "additionalProperties": False},
)(_approval_only("复查风险"))
registry.register(
    name="ops.risk.ignore", title="忽略风险", description="忽略风险。高风险写操作，需要人工审批。", scopes=["ops:write"], risk="high", category="risk_write", write=True, requires_confirmation=True, requires_human_approval=True, ai_callable=True, ai_auto_callable=False, input_schema={"type": "object", "properties": {"risk_id": {"type": "string"}, "reason": {"type": "string"}}, "required": ["risk_id"], "additionalProperties": False},
)(_approval_only("忽略风险"))
