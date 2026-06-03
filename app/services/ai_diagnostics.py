"""Deterministic AI-assistant style diagnostics built on read-only MCP tools.

This module intentionally does not call any external LLM.  It packages the
existing OPS diagnostics, MCP/tool catalog, tool audit trail, and operation job
state into a safe analysis that an AI client can consume through MCP.  The
analysis is read-only and never executes high-risk actions.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from sqlalchemy.orm import Session

from app.db.models import OperationJob, ToolCallLog
from app.services.diagnostics import build_diagnostics, build_diagnostics_report
from app.services.tool_context import ToolContext
from app.services.tool_registry import register_builtin_tools, registry


def _now() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


def _rank(severity: str) -> int:
    return {"ok": 0, "low": 1, "info": 1, "warn": 2, "medium": 2, "degraded": 2, "error": 3, "high": 3, "unhealthy": 4, "critical": 4}.get(str(severity or "").lower(), 0)


def _max_severity(values: List[str]) -> str:
    if not values:
        return "ok"
    ordered = sorted(values, key=_rank, reverse=True)
    top = ordered[0]
    if top in {"unhealthy", "critical"}:
        return "critical"
    if top in {"error", "high"}:
        return "high"
    if top in {"warn", "medium", "degraded"}:
        return "medium"
    return "low" if top not in {"ok", "healthy"} else "ok"


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value or default)
    except Exception:
        return default


def _safe_text(value: Any, limit: int = 360) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ")
    text = " ".join(text.split())
    return text[:limit] + ("..." if len(text) > limit else "")


def _diagnostic_ctx() -> ToolContext:
    return ToolContext(
        username="ai-diagnostics",
        role="admin",
        is_admin=True,
        can_deploy=True,
        auth_type="diagnostics",
        scopes=["*"],
        allow_write=False,
        allow_prod=False,
        client_name="ops-ai-diagnostics",
    )


def _recent_tool_failures(db: Session, limit: int = 8) -> List[Dict[str, Any]]:
    rows = (
        db.query(ToolCallLog)
        .filter(ToolCallLog.status.in_(["failed", "blocked"]))
        .order_by(ToolCallLog.created_at.desc())
        .limit(max(1, min(limit, 50)))
        .all()
    )
    return [
        {
            "id": r.id,
            "tool_name": r.tool_name,
            "status": r.status,
            "risk_level": r.risk_level,
            "blocked_reason": _safe_text(r.blocked_reason, 260),
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "related_job_id": r.related_job_id,
        }
        for r in rows
    ]


def _recent_jobs(db: Session, limit: int = 8) -> Dict[str, Any]:
    rows = db.query(OperationJob).order_by(OperationJob.created_at.desc()).limit(max(1, min(limit, 50))).all()
    failed = [r for r in rows if r.status == "failed"]
    running = [r for r in rows if r.status in {"queued", "running"}]
    return {
        "recent": [
            {
                "id": r.id,
                "status": r.status,
                "source_tool": r.source_tool,
                "risk_level": r.risk_level,
                "progress": r.progress,
                "operator": r.operator,
                "error_message": _safe_text(r.error_message, 300),
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            }
            for r in rows
        ],
        "failed_count": len(failed),
        "active_count": len(running),
    }


def _tool_catalog_snapshot(db: Session) -> Dict[str, Any]:
    register_builtin_tools()
    ctx = _diagnostic_ctx()
    listed = registry.list_tools(db, ctx, include_disabled=True, include_schema=False, limit=900)
    tools = listed.get("tools", []) if isinstance(listed, dict) else []
    risk_counts: Dict[str, int] = {}
    category_counts: Dict[str, int] = {}
    read_only_tools: List[str] = []
    guarded_tools: List[str] = []
    for item in tools:
        risk = str(item.get("risk") or "low")
        category = str(item.get("category") or "read")
        risk_counts[risk] = risk_counts.get(risk, 0) + 1
        category_counts[category] = category_counts.get(category, 0) + 1
        if not item.get("write") and risk in {"read", "low"}:
            read_only_tools.append(str(item.get("name") or ""))
        if item.get("write") and risk in {"high", "critical"}:
            guarded_tools.append(str(item.get("name") or ""))
    return {
        "tool_count": len(tools),
        "risk_counts": risk_counts,
        "category_counts": category_counts,
        "read_only_tools": sorted([x for x in read_only_tools if x])[:80],
        "guarded_tools": sorted([x for x in guarded_tools if x])[:80],
        "capability_version": registry.capability_version(db, ctx),
    }


def _build_findings(diagnostics: Dict[str, Any], tool_failures: List[Dict[str, Any]], jobs: Dict[str, Any]) -> List[Dict[str, Any]]:
    sections = diagnostics.get("sections", {}) or {}
    startup = sections.get("startup", {}) or {}
    health = sections.get("health", {}) or {}
    build_info = sections.get("build_info", {}) or {}
    recent_errors = sections.get("recent_errors", {}) or {}
    mcp = sections.get("mcp", {}) or {}
    findings: List[Dict[str, Any]] = []

    startup_errors = _as_int((startup.get("summary") or {}).get("errors"))
    startup_warnings = _as_int((startup.get("summary") or {}).get("warnings"))
    if startup_errors or startup_warnings:
        findings.append({
            "key": "startup_checks",
            "severity": "high" if startup_errors else "medium",
            "title": "启动自检存在异常或警告",
            "detail": f"启动自检错误 {startup_errors} 项，警告 {startup_warnings} 项。",
            "evidence": startup.get("summary") or {},
            "related_tools": ["ops.run_diagnostics", "ops.get_system_status"],
            "next_steps": ["先查看 startup.checks 中 status=error/warn 的项", "修复运行目录、数据库连接或前端构建问题后重新运行诊断"],
        })

    health_errors = _as_int((health.get("summary") or {}).get("errors"))
    health_warnings = _as_int((health.get("summary") or {}).get("warnings"))
    if health_errors or health_warnings:
        findings.append({
            "key": "system_health",
            "severity": "high" if health_errors else "medium",
            "title": "运行健康检查未完全通过",
            "detail": f"运行健康错误 {health_errors} 项，警告 {health_warnings} 项。",
            "evidence": health.get("summary") or {},
            "related_tools": ["ops.get_system_status", "ops.run_diagnostics"],
            "next_steps": ["优先处理数据库、备份、磁盘和 Worker 检查项", "处理后在系统状态页重新检查"],
        })

    frontend = build_info.get("frontend") or {}
    if build_info.get("status") in {"warn", "error"} or frontend.get("dist_stale"):
        findings.append({
            "key": "frontend_build",
            "severity": "medium" if build_info.get("status") != "error" else "high",
            "title": "前端构建产物需要关注",
            "detail": build_info.get("message") or "frontend/dist 可能不存在、不完整或早于源码变更。",
            "evidence": {"dist_stale": frontend.get("dist_stale"), "built_at": frontend.get("built_at"), "latest_source_file": frontend.get("latest_source_file")},
            "related_tools": ["ops.get_build_info", "ops.run_diagnostics"],
            "next_steps": ["执行 cd frontend && npm install && npm run build", "重启后端并 Ctrl+F5 强刷浏览器", "重新检查 /system/diagnostics 的构建版本块"],
        })

    error_count = _as_int(recent_errors.get("error_count"))
    warning_count = _as_int(recent_errors.get("warning_count"))
    if error_count:
        first = (recent_errors.get("items") or [{}])[0]
        findings.append({
            "key": "recent_errors",
            "severity": "high",
            "title": "最近日志存在错误",
            "detail": f"扫描到错误 {error_count} 条，警告 {warning_count} 条。最新错误：{_safe_text(first.get('summary') or first.get('raw'), 220)}",
            "evidence": {"error_count": error_count, "warning_count": warning_count, "latest": first},
            "related_tools": ["ops.get_recent_errors", "ops.export_diagnostics_report"],
            "next_steps": ["展开最近错误查看模块和来源", "按时间顺序定位第一个异常，而不是最后一个连锁异常", "修复后重新导出诊断报告"],
        })

    if mcp.get("status") in {"warn", "error"} or mcp.get("schema_errors"):
        findings.append({
            "key": "mcp_self_check",
            "severity": "medium" if mcp.get("status") == "warn" else "high",
            "title": "MCP 工具自检需要关注",
            "detail": mcp.get("message") or "MCP 工具 schema、权限或审计表存在异常。",
            "evidence": {"schema_errors": (mcp.get("schema_errors") or [])[:10], "tool_count": mcp.get("tool_count")},
            "related_tools": ["ops.describe_capabilities", "ops.get_tool_risk_policy"],
            "next_steps": ["查看 schema_errors 中的工具名", "确认工具注册、input_schema.type=object、权限策略和审计表迁移是否正常"],
        })

    if tool_failures:
        findings.append({
            "key": "tool_call_failures",
            "severity": "medium",
            "title": "近期 MCP / Tool 调用存在失败或拦截",
            "detail": f"最近失败或拦截工具调用 {len(tool_failures)} 条。",
            "evidence": tool_failures[:5],
            "related_tools": ["ops.list_tool_calls", "ops.get_tool_risk_policy"],
            "next_steps": ["区分 failed 与 blocked：blocked 通常是权限/确认/风险策略导致", "根据 blocked_reason 调整 token 权限或补齐确认短语"],
        })

    if jobs.get("failed_count") or jobs.get("active_count"):
        findings.append({
            "key": "operation_jobs",
            "severity": "medium" if not jobs.get("failed_count") else "high",
            "title": "统一任务中心存在活动或失败任务",
            "detail": f"活动任务 {jobs.get('active_count') or 0} 个，失败任务 {jobs.get('failed_count') or 0} 个。",
            "evidence": jobs.get("recent", [])[:5],
            "related_tools": ["ops.list_jobs", "ops.get_job_status"],
            "next_steps": ["优先查看 failed 任务的 error_message", "高风险工具任务必须在任务中心确认结果和审计链路"],
        })

    if not findings:
        findings.append({
            "key": "baseline_ok",
            "severity": "low",
            "title": "当前基础诊断未发现明显阻断项",
            "detail": "启动自检、运行健康、构建信息、最近错误与 MCP 自检未发现高优先级异常。",
            "evidence": {"diagnostics_status": diagnostics.get("status")},
            "related_tools": ["ops.run_diagnostics", "ops.get_system_status"],
            "next_steps": ["保持定期导出诊断报告", "发布前继续使用预检、运行手册和任务中心审计链路"],
        })
    return findings


def _safe_toolchain(focus: str = "") -> List[Dict[str, Any]]:
    focus = (focus or "").lower()
    steps = [
        {"order": 1, "tool": "ops.get_system_status", "purpose": "读取系统健康摘要", "risk": "low", "auto_allowed": True},
        {"order": 2, "tool": "ops.run_diagnostics", "purpose": "读取完整诊断上下文", "risk": "low", "auto_allowed": True},
        {"order": 3, "tool": "ops.get_recent_errors", "purpose": "聚合最近错误和警告", "risk": "low", "auto_allowed": True},
        {"order": 4, "tool": "ops.get_build_info", "purpose": "判断前端 dist 与后端版本是否一致", "risk": "low", "auto_allowed": True},
        {"order": 5, "tool": "ops.list_jobs", "purpose": "查看高风险工具任务是否失败或仍在运行", "risk": "low", "auto_allowed": True},
    ]
    if any(key in focus for key in ["deploy", "release", "发布", "回滚"]):
        steps.extend([
            {"order": 6, "tool": "ops.list_deploy_plans", "purpose": "查看最近发布计划", "risk": "low", "auto_allowed": True},
            {"order": 7, "tool": "ops.generate_release_runbook", "purpose": "生成发布运行手册和质量门禁摘要", "risk": "low", "auto_allowed": True},
        ])
    if any(key in focus for key in ["backup", "restore", "备份", "恢复"]):
        steps.extend([
            {"order": 6, "tool": "ops.list_backups", "purpose": "读取备份列表和恢复确认短语", "risk": "low", "auto_allowed": True},
            {"order": 7, "tool": "ops.verify_backup", "purpose": "校验目标备份 quick_check / sha256", "risk": "low", "auto_allowed": True},
        ])
    if any(key in focus for key in ["db", "database", "sql", "export", "数据库", "导出"]):
        steps.extend([
            {"order": 6, "tool": "ops.db.list_tables", "purpose": "读取可查询表列表，先确认表名和敏感限制", "risk": "low", "auto_allowed": True},
            {"order": 7, "tool": "ops.db.describe_table", "purpose": "查看目标表字段、类型和敏感字段标记", "risk": "low", "auto_allowed": True},
            {"order": 8, "tool": "ops.db.query_readonly", "purpose": "执行 SELECT/WITH 只读预览查询", "risk": "low", "auto_allowed": True},
            {"order": 9, "tool": "ops.db.export_query_result", "purpose": "按用户要求导出 CSV/JSON/XLSX/Markdown/SQL 文件", "risk": "medium", "auto_allowed": False},
        ])
    if any(key in focus for key in ["audit", "replay", "chain", "审计", "回放", "链路"]):
        steps.extend([
            {"order": 6, "tool": "ops.list_operation_chains", "purpose": "列出最近 OPS/MCP/AI 操作链路", "risk": "low", "auto_allowed": True},
            {"order": 7, "tool": "ops.get_operation_chain", "purpose": "重构单次操作的审计回放时间线", "risk": "low", "auto_allowed": True},
        ])
    return steps


def build_ai_diagnostic_analysis(
    db: Session,
    *,
    mode: str = "summary",
    focus: str = "",
    include_report: bool = False,
) -> Dict[str, Any]:
    """Build a read-only AI diagnostic analysis and MCP toolchain plan."""
    diagnostics = build_diagnostics(db)
    tool_failures = _recent_tool_failures(db, limit=8)
    jobs = _recent_jobs(db, limit=8)
    catalog = _tool_catalog_snapshot(db)
    findings = _build_findings(diagnostics, tool_failures, jobs)
    severity = _max_severity([f.get("severity", "low") for f in findings])
    blockers = [f for f in findings if f.get("severity") in {"high", "critical"}]
    status = "attention_required" if blockers else "degraded" if any(f.get("severity") == "medium" for f in findings) else "ok"

    evidence = {
        "diagnostics_status": diagnostics.get("status"),
        "diagnostics_summary": diagnostics.get("summary") or {},
        "tool_failures": tool_failures,
        "jobs": jobs,
        "mcp_catalog": catalog,
    }
    analysis: Dict[str, Any] = {
        "schema_version": "iter37.ai-diagnostics.v1",
        "generated_at": _now(),
        "mode": mode or "summary",
        "focus": focus or "general",
        "status": status,
        "severity": severity,
        "headline": "需要优先处理高风险诊断项" if blockers else "当前诊断未发现阻断项" if status == "ok" else "存在可处理的中低风险问题",
        "summary": {
            "finding_count": len(findings),
            "blocker_count": len(blockers),
            "safe_read_tools": len(catalog.get("read_only_tools") or []),
            "guarded_write_tools": len(catalog.get("guarded_tools") or []),
            "failed_tool_calls": len(tool_failures),
            "failed_jobs": jobs.get("failed_count") or 0,
            "active_jobs": jobs.get("active_count") or 0,
        },
        "findings": findings,
        "safe_mcp_toolchain": _safe_toolchain(focus),
        "guardrails": {
            "mode": "read_only_analysis",
            "ai_auto_allowed_risks": ["read", "low"],
            "requires_human_confirmation": ["medium", "high", "critical"],
            "must_use_task_center": ["high", "critical"],
            "never_bypass": ["RiskConfirmDialog", "RiskPolicy", "ToolPolicy", "OperationJob", "tool_call_logs", "operation_chain_replay"],
            "high_risk_examples": catalog.get("guarded_tools", [])[:20],
        },
        "recommended_next_actions": [
            {"priority": "P0" if blockers else "P1", "action": "先处理 findings 中 severity=high/critical 的项", "type": "manual_triage"},
            {"priority": "P1", "action": "导出 JSON 诊断报告并附带当前页面截图", "type": "evidence_collection", "tool": "ops.export_diagnostics_report"},
            {"priority": "P1", "action": "使用 safe_mcp_toolchain 中的只读工具补齐证据", "type": "mcp_readonly"},
            {"priority": "P2", "action": "任何写入、发布、恢复、删除操作必须走确认和任务中心", "type": "guardrail"},
            {"priority": "P2", "action": "问题处理后用 ops.list_operation_chains / ops.get_operation_chain 回放完整证据链", "type": "audit_replay"},
        ],
        "evidence": evidence if mode == "full" else {
            "diagnostics_status": evidence["diagnostics_status"],
            "diagnostics_summary": evidence["diagnostics_summary"],
            "tool_failure_count": len(tool_failures),
            "job_summary": {"failed_count": jobs.get("failed_count"), "active_count": jobs.get("active_count")},
            "mcp_catalog": catalog,
        },
    }
    if include_report:
        analysis["diagnostics_report"] = build_diagnostics_report(db)
    return analysis
