from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


def _call(registry, db, ctx, tool: str, args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return registry.call(db, tool, args or {}, ctx)
    except Exception as exc:
        return {"ok": False, "tool": tool, "summary": f"工具调用失败：{exc}", "error": str(exc)}


def _unwrap(call_result: Dict[str, Any]) -> Any:
    if isinstance(call_result, dict) and "result" in call_result:
        return call_result.get("result")
    return call_result


def project_health_brief(db, ctx, registry, *, project_id: str = "", time_range: str = "7d", save_analysis: bool = False) -> Dict[str, Any]:
    inspection_runs = _unwrap(_call(registry, db, ctx, "ops.inspection.list_runs", {"scope_type": "PROJECT", "project_id": str(project_id), "limit": 5}))
    risks = _unwrap(_call(registry, db, ctx, "ops.risk.list", {"project_id": str(project_id), "status": "OPEN", "limit": 20}))
    status = _unwrap(_call(registry, db, ctx, "ops.get_system_status", {}))
    run_items = (inspection_runs or {}).get("items") or [] if isinstance(inspection_runs, dict) else []
    risk_items = (risks or {}).get("items") or [] if isinstance(risks, dict) else []
    high = len([r for r in risk_items if r.get("risk_level") == "HIGH"])
    medium = len([r for r in risk_items if r.get("risk_level") == "MEDIUM"])
    score = max(0, 100 - high * 20 - medium * 8 - max(0, len(risk_items) - high - medium) * 2)
    facts = [
        {"id": "fact-inspection", "source_tool": "ops.inspection.list_runs", "ref": f"project_id={project_id}", "content": f"最近巡检记录 {len(run_items)} 条。"},
        {"id": "fact-risks", "source_tool": "ops.risk.list", "ref": f"project_id={project_id}", "content": f"未闭环风险 {len(risk_items)} 个，其中高危 {high} 个，中危 {medium} 个。"},
    ]
    inferences = [{"claim": f"项目当前健康评分约 {score}，主要受未闭环风险数量影响。", "confidence": "medium", "based_on": ["fact-risks", "fact-inspection"]}]
    recommendations = []
    if high:
        recommendations.append({"action": "优先处理高危风险，完成后执行复查并生成项目巡检报告。", "risk": "high", "requires_human_approval": False})
    if not run_items:
        recommendations.append({"action": "该项目近期没有巡检记录，建议执行项目巡检。", "risk": "medium", "requires_human_approval": True})
    recommendations.append({"action": "将该分析结果保存为 AI 分析记录并在报告中心生成项目健康简报。", "risk": "low", "requires_human_approval": False})
    payload = {
        "ok": True,
        "workflow": "ops.workflow.generate_project_health_brief",
        "target": {"type": "project", "id": project_id},
        "summary": f"项目 {project_id or '-'} 健康评分约 {score}，未闭环风险 {len(risk_items)} 个。",
        "score": score,
        "facts": facts,
        "inferences": inferences,
        "recommendations": recommendations,
        "evidence": facts,
        "related_tools": ["ops.inspection.list_runs", "ops.risk.list", "ops.get_system_status"],
        "requires_human_action": bool(high or not run_items),
        "generated_at": _now_iso(),
    }
    if save_analysis:
        saved = _unwrap(_call(registry, db, ctx, "ops.ai.save_analysis", {"analysis_type": "project_health", "target_type": "project", "target_id": str(project_id), "output": payload}))
        payload["analysis"] = saved
    return payload


def failed_deploy_analysis(db, ctx, registry, *, deployment_id: str, include_logs: bool = True, save_analysis: bool = False) -> Dict[str, Any]:
    report = _unwrap(_call(registry, db, ctx, "ops.get_deployment_report", {"deployment_id": deployment_id}))
    tasks = _unwrap(_call(registry, db, ctx, "ops.get_deployment_tasks", {"deployment_id": deployment_id}))
    logs = _unwrap(_call(registry, db, ctx, "ops.get_deployment_logs", {"deployment_id": deployment_id, "limit": 200})) if include_logs else {}
    facts = [
        {"id": "fact-report", "source_tool": "ops.get_deployment_report", "ref": deployment_id, "content": str(report)[:1000]},
        {"id": "fact-tasks", "source_tool": "ops.get_deployment_tasks", "ref": deployment_id, "content": str(tasks)[:1000]},
    ]
    if include_logs:
        facts.append({"id": "fact-logs", "source_tool": "ops.get_deployment_logs", "ref": deployment_id, "content": str(logs)[:1500]})
    payload = {
        "ok": True,
        "workflow": "ops.workflow.analyze_failed_deploy",
        "target": {"type": "deployment", "id": deployment_id},
        "summary": "已完成发布失败证据收集，请结合任务状态与日志关键错误判断根因。",
        "facts": facts,
        "inferences": [{"claim": "发布失败根因需要结合失败步骤、日志关键错误和服务器状态确认。", "confidence": "medium", "based_on": [f["id"] for f in facts]}],
        "recommendations": [{"action": "先完成失败步骤定位和预检，再决定是否回滚；AI 不直接执行回滚。", "requires_human_approval": True, "risk": "high"}],
        "evidence": facts,
        "requires_human_action": True,
        "generated_at": _now_iso(),
    }
    if save_analysis:
        payload["analysis"] = _unwrap(_call(registry, db, ctx, "ops.ai.save_analysis", {"analysis_type": "failed_deploy", "target_type": "deployment", "target_id": deployment_id, "output": payload}))
    return payload


def inspect_project_security(db, ctx, registry, *, project_id: str, time_range: str = "30d", generate_report: bool = False) -> Dict[str, Any]:
    runs = _unwrap(_call(registry, db, ctx, "ops.inspection.list_runs", {"scope_type": "PROJECT", "project_id": str(project_id), "limit": 10}))
    issues = _unwrap(_call(registry, db, ctx, "ops.inspection.list_issues", {"project_id": str(project_id), "limit": 50}))
    issue_items = issues.get("items") if isinstance(issues, dict) else []
    facts = [{"id": "fact-security-issues", "source_tool": "ops.inspection.list_issues", "ref": f"project_id={project_id}", "content": f"巡检风险 {len(issue_items or [])} 个。"}]
    payload = {
        "ok": True,
        "workflow": "ops.workflow.inspect_project_security",
        "target": {"type": "project", "id": project_id},
        "summary": f"项目 {project_id} 安全巡检风险 {len(issue_items or [])} 个。",
        "facts": facts,
        "inferences": [{"claim": "项目安全状态应结合配置、接口、白名单、备份和运行环境风险判断。", "confidence": "medium", "based_on": ["fact-security-issues"]}],
        "recommendations": [{"action": "优先整改高危巡检问题，并在报告中心生成项目巡检报告。", "requires_human_approval": False}],
        "evidence": facts,
        "related_tools": ["ops.inspection.list_runs", "ops.inspection.list_issues"],
        "requires_human_action": bool(issue_items),
    }
    return payload


def triage_open_risks(db, ctx, registry, *, risk_level: str = "", target_type: str = "", limit: int = 50) -> Dict[str, Any]:
    args = {"status": "OPEN", "limit": limit}
    if risk_level:
        args["risk_level"] = risk_level
    if target_type:
        args["scope_type"] = target_type
    return _unwrap(_call(registry, db, ctx, "ops.risk.triage", args))


def monthly_ops_report(db, ctx, registry, *, month: str = "", generate_report: bool = True) -> Dict[str, Any]:
    status = _unwrap(_call(registry, db, ctx, "ops.get_system_status", {}))
    risks = _unwrap(_call(registry, db, ctx, "ops.risk.list", {"status": "OPEN", "limit": 100}))
    reports = _unwrap(_call(registry, db, ctx, "ops.list_reports", {"limit": 20}))
    inspections = _unwrap(_call(registry, db, ctx, "ops.inspection.list_runs", {"limit": 20}))
    facts = [
        {"id": "fact-status", "source_tool": "ops.get_system_status", "ref": "status", "content": str(status)[:1000]},
        {"id": "fact-risks", "source_tool": "ops.risk.list", "ref": "open", "content": f"未闭环风险 {len((risks or {}).get('items') or [])} 个。" if isinstance(risks, dict) else str(risks)[:1000]},
        {"id": "fact-reports", "source_tool": "ops.list_reports", "ref": "recent", "content": f"最近报告 {len((reports or {}).get('items') or [])} 份。" if isinstance(reports, dict) else str(reports)[:1000]},
        {"id": "fact-inspections", "source_tool": "ops.inspection.list_runs", "ref": "recent", "content": f"最近巡检 {len((inspections or {}).get('items') or [])} 次。" if isinstance(inspections, dict) else str(inspections)[:1000]},
    ]
    payload = {
        "ok": True,
        "workflow": "ops.workflow.generate_monthly_ops_report",
        "target": {"type": "month", "id": month or "current"},
        "summary": f"{month or '当前月份'} 运维复盘已生成，包含状态、风险、巡检和报告摘要。",
        "facts": facts,
        "inferences": [{"claim": "月度运维质量应重点关注未闭环风险、失败发布、备份异常和巡检趋势。", "confidence": "medium", "based_on": [f["id"] for f in facts]}],
        "recommendations": [{"action": "将月度复盘保存为 AI 分析并进入报告中心。", "requires_human_approval": False}],
        "evidence": facts,
        "requires_human_action": False,
    }
    if generate_report:
        saved = _unwrap(_call(registry, db, ctx, "ops.ai.save_analysis", {"analysis_type": "monthly_ops_report", "target_type": "month", "target_id": month or "current", "output": payload}))
        if isinstance(saved, dict) and saved.get("id"):
            payload["analysis"] = saved
            payload["report"] = _unwrap(_call(registry, db, ctx, "ops.ai.generate_report_from_analysis", {"analysis_id": saved.get("id")}))
    return payload
