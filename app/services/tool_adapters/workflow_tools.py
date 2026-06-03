from __future__ import annotations

from typing import Any, Dict

from app.services.tool_registry import registry


@registry.register(
    name="ops.workflow.generate_project_health_brief",
    title="项目健康分析工作流",
    description="聚合项目、状态、巡检、风险和报告上下文，生成单项目轻量健康简报。",
    scopes=["ops:read"],
    risk="low",
    category="workflow",
    write=False,
    ai_callable=True,
    ai_auto_callable=True,
    data_sensitivity="internal",
    output_masking=True,
    recommended_use_cases=["项目健康分析", "自然语言项目问答", "报告生成前摘要"],
    example_prompts=["项目 A 最近健康吗？", "生成项目 A 的健康摘要"],
    input_schema={"type": "object", "properties": {"project_id": {"type": "string"}, "time_range": {"type": "string"}, "save_analysis": {"type": "boolean"}}, "additionalProperties": False},
)
def project_health(args: Dict[str, Any], ctx, db):
    from app.services.ai_workflows import project_health_brief
    from app.services.tool_registry import registry as reg
    return project_health_brief(db, ctx, reg, project_id=args.get("project_id") or "", time_range=args.get("time_range") or "7d", save_analysis=bool(args.get("save_analysis")))


@registry.register(
    name="ops.workflow.analyze_failed_deploy",
    title="发布失败分析工作流",
    description="聚合发布报告、任务、日志和系统状态，生成发布失败分析。不会执行回滚。",
    scopes=["ops:read"],
    risk="medium",
    category="workflow",
    write=False,
    ai_callable=True,
    ai_auto_callable=False,
    data_sensitivity="sensitive",
    output_masking=True,
    input_schema={"type": "object", "properties": {"deployment_id": {"type": "string"}, "include_logs": {"type": "boolean"}, "save_analysis": {"type": "boolean"}}, "required": ["deployment_id"], "additionalProperties": False},
)
def failed_deploy(args: Dict[str, Any], ctx, db):
    from app.services.ai_workflows import failed_deploy_analysis
    from app.services.tool_registry import registry as reg
    return failed_deploy_analysis(db, ctx, reg, deployment_id=args.get("deployment_id") or "", include_logs=args.get("include_logs") is not False, save_analysis=bool(args.get("save_analysis")))


@registry.register(
    name="ops.workflow.inspect_project_security",
    title="项目安全巡检分析工作流",
    description="聚合项目巡检、服务器巡检摘要、风险和整改建议，生成项目安全分析。",
    scopes=["ops:read"],
    risk="medium",
    category="workflow",
    write=False,
    ai_callable=True,
    ai_auto_callable=False,
    data_sensitivity="internal",
    output_masking=True,
    input_schema={"type": "object", "properties": {"project_id": {"type": "string"}, "time_range": {"type": "string"}, "generate_report": {"type": "boolean"}}, "required": ["project_id"], "additionalProperties": False},
)
def project_security(args: Dict[str, Any], ctx, db):
    from app.services.ai_workflows import inspect_project_security
    from app.services.tool_registry import registry as reg
    return inspect_project_security(db, ctx, reg, project_id=args.get("project_id") or "", time_range=args.get("time_range") or "30d", generate_report=bool(args.get("generate_report")))


@registry.register(
    name="ops.workflow.triage_open_risks",
    title="未闭环风险分流工作流",
    description="聚合未闭环风险并生成 P0/P1/P2 优先级建议。",
    scopes=["ops:read"],
    risk="low",
    category="workflow",
    write=False,
    ai_callable=True,
    ai_auto_callable=True,
    data_sensitivity="internal",
    output_masking=True,
    input_schema={"type": "object", "properties": {"risk_level": {"type": "string"}, "target_type": {"type": "string"}, "limit": {"type": "integer"}}, "additionalProperties": False},
)
def risk_triage(args: Dict[str, Any], ctx, db):
    from app.services.ai_workflows import triage_open_risks
    from app.services.tool_registry import registry as reg
    return triage_open_risks(db, ctx, reg, risk_level=args.get("risk_level") or "", target_type=args.get("target_type") or "", limit=args.get("limit") or 50)


@registry.register(
    name="ops.workflow.generate_monthly_ops_report",
    title="月度运维报告工作流",
    description="聚合状态、诊断、巡检、备份、风险和报告记录，生成月度运维复盘。",
    scopes=["ops:read", "audit:read"],
    risk="low",
    category="workflow",
    write=False,
    ai_callable=True,
    ai_auto_callable=False,
    data_sensitivity="internal",
    output_masking=True,
    input_schema={"type": "object", "properties": {"month": {"type": "string"}, "generate_report": {"type": "boolean"}}, "additionalProperties": False},
)
def monthly_report(args: Dict[str, Any], ctx, db):
    from app.services.ai_workflows import monthly_ops_report
    from app.services.tool_registry import registry as reg
    return monthly_ops_report(db, ctx, reg, month=args.get("month") or "", generate_report=args.get("generate_report") is not False)
