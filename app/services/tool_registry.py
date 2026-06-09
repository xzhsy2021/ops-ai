from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from fastapi import HTTPException

from app.services.tool_schema import validate_schema
from app.services.tool_policy import enforce_tool_policy
from app.services.audit_writer import record_tool_call_async

ToolHandler = Callable[[Dict[str, Any], Any, Any], Any]


@dataclass
class ToolDefinition:
    name: str
    description: str
    input_schema: Dict[str, Any]
    handler: ToolHandler
    scopes: List[str]
    risk: str = "low"
    category: str = "read"
    write: bool = False
    requires_confirmation: bool = False
    enabled: bool = True
    title: str = ""
    output_schema: Dict[str, Any] | None = None
    keywords: List[str] | None = None
    aliases: List[str] | None = None
    streamable: bool = False
    ai_callable: bool = True
    ai_auto_callable: bool = False
    requires_human_approval: bool = False
    data_sensitivity: str = "internal"
    output_masking: bool = True
    recommended_use_cases: List[str] | None = None
    example_prompts: List[str] | None = None
    related_tools: List[str] | None = None

    def to_public_dict(
        self,
        *,
        include_schema: bool = True,
        policy: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        data = {
            "name": self.name,
            "title": self.title or self.name,
            "description": self.description,
            "scopes": self.scopes,
            "risk": self.risk,
            "category": self.category,
            "write": self.write,
            "read_only": not self.write,
            "requires_confirmation": self.requires_confirmation,
            "enabled": self.enabled,
            "streamable": self.streamable,
            "ai_callable": self.ai_callable,
            "ai_auto_callable": self.ai_auto_callable,
            "requires_human_approval": self.requires_human_approval or self.requires_confirmation,
            "data_sensitivity": self.data_sensitivity,
            "output_masking": self.output_masking,
            "recommended_use_cases": self.recommended_use_cases or [],
            "example_prompts": self.example_prompts or [],
            "related_tools": self.related_tools or [],
        }
        try:
            from app.services.tool_policy import ai_tool_level
            data["ai_level"] = ai_tool_level(self)
        except Exception:
            data["ai_level"] = "L4" if self.write and self.risk in {"high", "critical"} else "L1"
        if self.keywords:
            data["keywords"] = self.keywords
        if self.aliases:
            data["aliases"] = self.aliases
        if include_schema:
            data["input_schema"] = self.input_schema
            data["output_schema"] = self.output_schema or _default_output_schema()
        if policy is not None:
            data["available"] = bool(policy.get("allowed"))
            data["blocked_reason"] = policy.get("blocked_reason", "")
            data["policy"] = policy
        return data

    def to_mcp_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema or {"type": "object"},
            "annotations": {
                "title": self.title or self.name,
                "readOnlyHint": not self.write,
                "destructiveHint": bool(self.write and self.risk in {"high", "critical"}),
                "idempotentHint": not self.write,
                "openWorldHint": False,
                "x_ops_risk": self.risk,
                "x_ops_category": self.category,
                "x_ops_ai_callable": self.ai_callable,
                "x_ops_ai_auto_callable": self.ai_auto_callable,
                "x_ops_requires_human_approval": self.requires_human_approval or self.requires_confirmation,
                "x_ops_data_sensitivity": self.data_sensitivity,
            },
        }

    def to_openai_dict(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name.replace(".", "__"),
                "description": self.description,
                "parameters": self.input_schema or {"type": "object", "properties": {}, "additionalProperties": False},
            },
            "x_ops_tool_name": self.name,
            "x_ops_risk": self.risk,
            "x_ops_category": self.category,
            "x_ops_ai_callable": self.ai_callable,
            "x_ops_ai_auto_callable": self.ai_auto_callable,
            "x_ops_requires_human_approval": self.requires_human_approval or self.requires_confirmation,
            "x_ops_data_sensitivity": self.data_sensitivity,
        }

    def to_anthropic_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema or {"type": "object", "properties": {}, "additionalProperties": False},
            "metadata": {
                "risk": self.risk,
                "category": self.category,
                "requires_confirmation": self.requires_confirmation,
                "ai_callable": self.ai_callable,
                "ai_auto_callable": self.ai_auto_callable,
                "requires_human_approval": self.requires_human_approval or self.requires_confirmation,
                "data_sensitivity": self.data_sensitivity,
            },
        }


def _default_output_schema() -> Dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "ok": {"type": "boolean"},
            "summary": {"type": "string"},
            "result": {"type": "object"},
            "next_actions": {"type": "array", "items": {"type": "object"}},
        },
    }


DAILY_OPS_TOOL_NAMES = frozenset({
    "ops.workflow.inspect",
    "ops.workflow.generate_project_health_brief",
    "ops.workflow.analyze_failed_deploy",
    "ops.workflow.inspect_project_security",
    "ops.workflow.triage_open_risks",
    "ops.workflow.generate_monthly_ops_report",
    "ops.describe_capabilities",
    "ops.get_tool_risk_policy",
    "ops.analyze_diagnostics",
    "ops.run_diagnostics",
    "ops.get_system_status",
    "ops.get_build_info",
    "ops.get_recent_errors",
    "ops.export_diagnostics_report",
    "ops.list_jobs",
    "ops.get_job_status",
    "ops.list_operation_chains",
    "ops.get_operation_chain",
    "ops.list_servers",
    "ops.get_server",
    "ops.list_server_groups",
    "ops.list_systems",
    "ops.list_services",
    "ops.list_environments",
    "ops.check_disk",
    "ops.run_health_check",
    "ops.log.search",
    "ops.log.summarize_errors",
    "ops.log.get_recent_exceptions",
    "ops.inspection.profile.list",
    "ops.inspection.profile.preview",
    "ops.inspection.profile.run",
    "ops.inspection.preview_servers_batch",
    "ops.inspection.run_servers_batch",
    "ops.inspection.list_runs",
    "ops.inspection.get_run",
    "ops.inspection.get_run_raw_output",
    "ops.inspection.list_issues",
    "ops.inspection.get_issue",
    "ops.inspection.generate_report",
    "ops.inspection.summarize_run",
    "ops.inspection.run_server",
    "ops.risk.list",
    "ops.risk.get",
    "ops.risk.triage",
    "ops.risk.generate_fix_plan",
    "ops.list_reports",
    "ops.get_report",
    "ops.get_report_summary",
    "ops.list_report_types",
    "ops.generate_report",
    "ops.list_deployments",
    "ops.deploy.aggregate_status",
    "ops.get_deployment_status",
    "ops.get_deployment_report",
    "ops.get_deployment_tasks",
    "ops.get_deployment_logs",
    "ops.list_deploy_plans",
    "ops.get_deploy_plan",
    "ops.generate_release_runbook",
    "ops.get_rollback_readiness",
    "ops.list_packages",
    "ops.inspect_local_package",
    "ops.get_package_checksum",
    "ops.db.list_tables",
    "ops.db.describe_table",
    "ops.db.query_readonly",
    "ops.db.export_query_result",
    "ops.db.list_exports",
    "ops.db.get_export",
    "ops.list_backups",
    "ops.verify_backup",
})


TOOL_PROFILE_ALIASES = {
    "": "daily_ops",
    "default": "daily_ops",
    "daily": "daily_ops",
    "daily_ops": "daily_ops",
    "ops": "daily_ops",
    "expert": "expert",
    "advanced": "expert",
    "admin": "admin_full",
    "admin_full": "admin_full",
    "full": "admin_full",
    "all": "admin_full",
}


EXPERT_BLOCKED_CATEGORIES = {
    "backup_restore",
    "backup_write",
    "config_write",
    "connection_write",
    "db_write",
    "deploy_execute",
    "inspection_config",
    "package_cleanup",
    "package_write",
    "pipeline_write",
    "risk_write",
    "runtime_cleanup",
    "server_write",
    "ssh_key_write",
}


def normalize_tool_profile(profile: str | None = None, *, default: str = "daily_ops") -> str:
    key = str(profile if profile not in (None, "") else default or "daily_ops").strip().lower().replace("-", "_")
    return TOOL_PROFILE_ALIASES.get(key, "daily_ops")


def tool_matches_profile(tool: ToolDefinition, profile: str | None = None) -> bool:
    profile_name = normalize_tool_profile(profile)
    if profile_name == "admin_full":
        return True
    if profile_name == "daily_ops":
        return tool.name in DAILY_OPS_TOOL_NAMES
    if profile_name == "expert":
        if tool.name in DAILY_OPS_TOOL_NAMES:
            return True
        if not tool.ai_callable:
            return False
        if tool.category in EXPERT_BLOCKED_CATEGORIES:
            return False
        if tool.write and (tool.risk in {"high", "critical"} or tool.requires_confirmation or tool.requires_human_approval):
            return False
        return True
    return False


def tool_profile_manifest() -> List[Dict[str, Any]]:
    return [
        {
            "key": "daily_ops",
            "name": "Daily OPS",
            "default": True,
            "description": "Natural-language first workflow and read tools for routine AI agent operations.",
        },
        {
            "key": "expert",
            "name": "Expert",
            "default": False,
            "description": "Expanded non-destructive catalog for troubleshooting and advanced analysis.",
        },
        {
            "key": "admin_full",
            "name": "Admin Full",
            "default": False,
            "description": "Full registered tool catalog for OPS web administration and explicit maintenance.",
        },
    ]


class ToolRegistry:
    def __init__(self):
        self._tools: Dict[str, ToolDefinition] = {}
        self._capability_revision = 0

    def register(
        self,
        *,
        name: str,
        description: str,
        input_schema: Dict[str, Any] | None = None,
        scopes: List[str] | None = None,
        risk: str = "low",
        category: str = "read",
        write: bool = False,
        requires_confirmation: bool = False,
        enabled: bool = True,
        title: str = "",
        output_schema: Dict[str, Any] | None = None,
        keywords: List[str] | None = None,
        aliases: List[str] | None = None,
        streamable: bool = False,
        ai_callable: bool = True,
        ai_auto_callable: bool = False,
        requires_human_approval: bool = False,
        data_sensitivity: str = "internal",
        output_masking: bool = True,
        recommended_use_cases: List[str] | None = None,
        example_prompts: List[str] | None = None,
        related_tools: List[str] | None = None,
    ):
        def deco(fn: ToolHandler):
            self._tools[name] = ToolDefinition(
                name=name,
                title=title or name,
                description=description,
                input_schema=input_schema or {"type": "object", "properties": {}, "additionalProperties": False},
                output_schema=output_schema,
                handler=fn,
                scopes=scopes or ["ops:read"],
                risk=risk,
                category=category,
                write=write,
                requires_confirmation=requires_confirmation,
                enabled=enabled,
                keywords=keywords,
                aliases=aliases,
                streamable=streamable,
                ai_callable=ai_callable,
                ai_auto_callable=ai_auto_callable,
                requires_human_approval=requires_human_approval,
                data_sensitivity=data_sensitivity,
                output_masking=output_masking,
                recommended_use_cases=recommended_use_cases,
                example_prompts=example_prompts,
                related_tools=related_tools,
            )
            return fn
        return deco

    def evaluate_policy(self, tool: ToolDefinition, ctx=None, db=None) -> Dict[str, Any]:
        if not tool.enabled:
            return {"allowed": False, "blocked_reason": "Tool disabled", "risk": tool.risk, "category": tool.category}
        if ctx is None or db is None:
            return {"allowed": True, "blocked_reason": "", "risk": tool.risk, "category": tool.category}
        try:
            from app.services.tool_policy import evaluate_tool_policy
            return evaluate_tool_policy(tool, {}, ctx, db)
        except Exception as exc:
            return {"allowed": False, "blocked_reason": str(exc), "risk": tool.risk, "category": tool.category}

    def list_tools(
        self,
        db=None,
        ctx=None,
        *,
        category: str = "",
        risk: str = "",
        profile: str = "",
        include_disabled: bool = False,
        include_schema: bool = True,
        output_format: str = "native",
        limit: int = 100,
        cursor: int = 0,
    ) -> Dict[str, Any] | List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        category = (category or "").strip()
        risk = (risk or "").strip()
        profile_name = normalize_tool_profile(profile, default="admin_full")
        for key in sorted(self._tools.keys()):
            tool = self._tools[key]
            if not tool_matches_profile(tool, profile_name):
                continue
            if category and tool.category != category:
                continue
            if risk and tool.risk != risk:
                continue
            policy = self.evaluate_policy(tool, ctx, db)
            if not include_disabled and not policy.get("allowed"):
                continue
            if output_format == "mcp":
                item = tool.to_mcp_dict()
            elif output_format == "openai":
                item = tool.to_openai_dict()
            elif output_format == "anthropic":
                item = tool.to_anthropic_dict()
            else:
                item = tool.to_public_dict(include_schema=include_schema, policy=policy)
            items.append(item)
        total = len(items)
        try:
            limit = max(1, min(int(limit), 500))
            cursor = max(0, int(cursor))
        except Exception:
            limit, cursor = 100, 0
        page = items[cursor:cursor + limit]
        next_cursor = cursor + limit if cursor + limit < total else None
        # Backwards compatibility: old internal callers expected a plain list.
        if db is None and ctx is None and not category and not risk and not profile and not include_disabled and output_format == "native":
            return page
        return {
            "tools": page,
            "pagination": {"total": total, "limit": limit, "cursor": cursor, "next_cursor": next_cursor},
            "filters": {"category": category, "risk": risk, "profile": profile_name, "include_disabled": include_disabled, "format": output_format},
        }

    def list_categories(self) -> List[str]:
        return sorted({t.category for t in self._tools.values() if t.enabled})

    def capability_version(self, db=None, ctx=None, *, profile: str = "") -> str:
        now_ts = datetime.now(timezone.utc).timestamp()
        profile_name = normalize_tool_profile(profile, default="daily_ops")
        cache_key = (
            f"rev:{self._capability_revision}:"
            f"{profile_name}:"
            f"{getattr(ctx, 'token_id', '')}:"
            f"{getattr(ctx, 'auth_type', '')}:"
            f"{','.join(getattr(ctx, 'scopes', []) or [])}:"
            f"{bool(getattr(ctx, 'allow_write', False))}:"
            f"{bool(getattr(ctx, 'allow_prod', False))}:"
            f"{bool(getattr(ctx, 'is_admin', False))}"
        )
        if hasattr(self, '_cap_version_cache') and self._cap_version_cache.get('key') == cache_key:
            cached_at = self._cap_version_cache.get('ts', 0)
            if now_ts - cached_at < 60:
                return self._cap_version_cache['version']
        payload = {
            "revision": self._capability_revision,
            "profile": profile_name,
            "tools": [
                self._tools[k].to_public_dict(include_schema=True)
                for k in sorted(self._tools.keys())
                if tool_matches_profile(self._tools[k], profile_name)
            ],
        }
        if db is not None:
            try:
                from app.services.tool_policy import get_capability_settings
                payload["settings"] = get_capability_settings(db)
            except Exception:
                payload["settings"] = {}
        if ctx is not None:
            payload["context"] = {
                "auth_type": getattr(ctx, "auth_type", ""),
                "token_id": getattr(ctx, "token_id", ""),
                "scopes": getattr(ctx, "scopes", []),
                "allow_write": getattr(ctx, "allow_write", False),
                "allow_prod": getattr(ctx, "allow_prod", False),
                "is_admin": getattr(ctx, "is_admin", False),
            }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        version = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
        self._cap_version_cache = {"key": cache_key, "ts": now_ts, "version": version}
        return version

    def clear_capability_cache(self):
        self._cap_version_cache = {}

    def invalidate_capability_cache(self):
        self._capability_revision = int(getattr(self, "_capability_revision", 0) or 0) + 1
        self.clear_capability_cache()

    def describe_capabilities(
        self,
        db,
        ctx,
        *,
        category: str = "",
        profile: str = "",
        include_schema: bool = True,
        include_disabled: bool = False,
        output_format: str = "native",
        limit: int = 200,
        cursor: int = 0,
    ) -> Dict[str, Any]:
        from app.services.tool_policy import get_capability_settings
        profile_name = normalize_tool_profile(profile, default="admin_full")
        listed = self.list_tools(
            db,
            ctx,
            category=category,
            profile=profile_name,
            include_disabled=include_disabled,
            include_schema=include_schema,
            output_format=output_format,
            limit=limit,
            cursor=cursor,
        )
        settings = get_capability_settings(db)
        return {
            "server": {
                "name": "ops-capability-server",
                "version": "1.3.0",
                "capability_version": self.capability_version(db, ctx, profile=profile_name),
                "tool_profile": profile_name,
            },
            "auth": {
                "auth_type": getattr(ctx, "auth_type", ""),
                "owner": getattr(ctx, "token_owner", "") or getattr(ctx, "username", ""),
                "client_name": getattr(ctx, "client_name", ""),
                "scopes": getattr(ctx, "scopes", []),
                "allow_write": getattr(ctx, "allow_write", False),
                "allow_prod": getattr(ctx, "allow_prod", False),
            },
            "features": {
                "http_tools": bool(settings.get("http_tools_enabled")),
                "mcp": bool(settings.get("mcp_enabled")),
                "read_only": bool(settings.get("read_only")),
                "deploy_plan": bool(settings.get("allow_deploy_plan")),
                "deploy_execute": bool(settings.get("allow_deploy_execute")),
                "prod_deploy": bool(settings.get("allow_prod_deploy")),
                "rollback": bool(settings.get("allow_rollback")),
                "config_write": bool(settings.get("allow_config_write")),
                "backup_write": bool(settings.get("allow_backup_write")),
                "backup_restore": bool(settings.get("allow_backup_restore")),
                "server_read": bool(settings.get("allow_server_read")),
                "server_write": bool(settings.get("allow_server_write")),
                "package_write": bool(settings.get("allow_package_write")),
                "package_cleanup": bool(settings.get("allow_package_cleanup")),
                "taskize_high_risk_tools": bool(settings.get("taskize_high_risk_tools", True)),
                "ai_capability_layer": True,
                "ai_evidence_chain": True,
                "ai_human_approval": True,
            },
            "tools": listed.get("tools", []) if isinstance(listed, dict) else listed,
            "pagination": listed.get("pagination", {}) if isinstance(listed, dict) else {},
            "filters": listed.get("filters", {}) if isinstance(listed, dict) else {"profile": profile_name},
            "tool_profiles": tool_profile_manifest(),
            "categories": self.list_categories(),
            "policies": {
                "requires_confirmation_risk": ["high", "critical"],
                "raw_shell_enabled": False,
                "require_confirmation": bool(settings.get("require_confirmation", True)),
                "strict_prod_confirmation": bool(settings.get("strict_prod_confirmation", True)),
                "taskize_high_risk_tools": bool(settings.get("taskize_high_risk_tools", True)),
                "ai_capability_layer": True,
                "ai_evidence_chain": True,
                "ai_human_approval": True,
            },
            "resources": [
                {"uri": "ops://capabilities", "name": "Capability Manifest", "description": "当前 Token 可用能力清单"},
                {"uri": "ops://systems", "name": "OPS Systems", "description": "系统列表"},
                {"uri": "ops://deployments/recent", "name": "Recent Deployments", "description": "最近发布历史"},
                {"uri": "ops://tools", "name": "Tool Catalog", "description": "工具目录"},
                {"uri": "ops://ai-diagnostics", "name": "AI Diagnostics Analysis", "description": "只读 AI 诊断分析与安全 MCP 工具链"},
                {"uri": "ops://operation-chains", "name": "Recent Operation Chains", "description": "OPS/MCP/AI 操作链路只读回放索引"},
                {"uri": "ops://reports", "name": "Report Center", "description": "诊断、发布、备份和 MCP/AI 操作链路报告中心"},
                {"uri": "ops://db/exports", "name": "Database Exports", "description": "只读数据库查询导出制品"},
                {"uri": "ops://servers", "name": "Servers", "description": "服务器资产摘要"},
                {"uri": "ops://projects", "name": "Projects", "description": "项目/系统资产摘要"},
                {"uri": "ops://status/overview", "name": "Status Overview", "description": "状态中心总览"},
                {"uri": "ops://risks/open", "name": "Open Risks", "description": "未闭环风险"},
                {"uri": "ops://inspection/recent", "name": "Recent Inspection Runs", "description": "最近巡检记录"},
                {"uri": "ops://diagnosis/recent", "name": "Recent Diagnosis Runs", "description": "最近诊断记录"},
                {"uri": "ops://reports/recent", "name": "Recent Reports", "description": "最近报告"},
                {"uri": "ops://backups/status", "name": "Backup Status", "description": "备份状态摘要"},
                {"uri": "ops://deployments/failed", "name": "Failed Deployments", "description": "最近失败发布"},
                {"uri": "ops://tool-risk-policy", "name": "Tool Risk Policy", "description": "工具风险策略"},
                {"uri": "ops://ai-workflows", "name": "AI Workflows", "description": "AI 工作流目录"},
            ],
            "prompts": [
                {"name": "ops_release_plan", "description": "帮助用户生成发布计划"},
                {"name": "ops_failure_analysis", "description": "帮助分析发布失败原因"},
                {"name": "ops_diagnostic_triage", "description": "使用只读 MCP 工具对 OPS 运行问题进行分流诊断"},
                {"name": "ops_operation_replay", "description": "基于审计证据回放 OPS/MCP/AI 操作链路"},
                {"name": "ops_report_brief", "description": "基于报告中心制品生成只读简报"},
                {"name": "ops_db_export_request", "description": "安全数据库工作流：查询、导出或维护表"},
                {"name": "ops_server_management", "description": "管理 OPS 服务器资产"},
                {"name": "ops_backup_workflow", "description": "安全 OPS 数据库备份工作流（列出、创建、校验、恢复）"},
                {"name": "ops_project_health_brief", "description": "生成项目健康分析，要求事实、推断、建议、证据分离"},
                {"name": "ops_risk_triage", "description": "对未闭环风险进行优先级分流，不直接执行处置"},
                {"name": "ops_monthly_ops_report", "description": "基于状态、诊断、巡检、备份、风险生成月度运维复盘"},
            ],
        }

    def get(self, name: str) -> ToolDefinition:
        tool = self._tools.get(name)
        if not tool or not tool.enabled:
            raise HTTPException(status_code=404, detail=f"Tool not found: {name}")
        return tool

    def call(self, db, tool_name: str, arguments: Dict[str, Any], ctx, stream_callback=None) -> Dict[str, Any]:
        started = time.monotonic()
        tool = self.get(tool_name)
        normalized = {}
        policy_result = {}
        try:
            normalized = validate_schema(arguments or {}, tool.input_schema)
            policy_result = enforce_tool_policy(tool, normalized, ctx, db)
            risk_policy = policy_result.get("risk_policy") if isinstance(policy_result, dict) else {}
            if isinstance(risk_policy, dict) and risk_policy.get("must_create_job"):
                from app.services.job_service import enqueue_tool_job, mark_job_audit_id
                job = enqueue_tool_job(
                    db,
                    tool_def=tool,
                    arguments=normalized,
                    ctx=ctx,
                    policy_result=policy_result,
                    auto_start=True,
                )
                duration_ms = round((time.monotonic() - started) * 1000)
                result = {
                    "job_id": job.get("id"),
                    "job": job,
                    "status": job.get("status"),
                    "task_center_url": f"/tasks?kind=tool&job={job.get('id')}",
                    "summary": "High-risk tool execution was queued as a unified job",
                    "next_actions": [
                        {
                            "type": "open_task_center",
                            "description": "在任务中心查看工具任务进度、结果和审计记录。",
                            "job_id": job.get("id"),
                        }
                    ],
                }
                audit_id = record_tool_call_async(
                    tool_name=tool_name,
                    ctx=ctx,
                    input_args=arguments or {},
                    normalized_args=normalized,
                    result=result,
                    status="queued",
                    risk_level=tool.risk,
                    policy_result=policy_result,
                    related_job_id=job.get("id") or "",
                    duration_ms=duration_ms,
                )
                try:
                    mark_job_audit_id(db, job.get("id") or "", audit_id)
                except Exception:
                    pass
                return {
                    "ok": True,
                    "tool": tool_name,
                    "result": result,
                    "summary": result["summary"],
                    "next_actions": result["next_actions"],
                    "audit_id": audit_id,
                    "job_id": job.get("id"),
                    "risk": tool.risk,
                    "blocked": False,
                    "requires_confirmation": tool.requires_confirmation,
                    "message": "queued",
                }

            result = tool.handler(normalized, ctx, db, stream_callback=stream_callback) if stream_callback and getattr(tool, "streamable", False) else tool.handler(normalized, ctx, db)
            duration_ms = round((time.monotonic() - started) * 1000)
            related_plan_id = ""
            related_deployment_id = ""
            if isinstance(result, dict):
                related_plan_id = str(result.get("plan_id") or result.get("id") or "") if "plan" in tool.category else str(result.get("plan_id") or "")
                related_deployment_id = str(result.get("deployment_id") or "")
            audit_id = record_tool_call_async(
                tool_name=tool_name,
                ctx=ctx,
                input_args=arguments or {},
                normalized_args=normalized,
                result=result,
                status="success",
                risk_level=tool.risk,
                policy_result=policy_result,
                related_plan_id=related_plan_id,
                related_deployment_id=related_deployment_id,
                duration_ms=duration_ms,
            )
            summary = "success"
            next_actions: List[Dict[str, Any]] = []
            if isinstance(result, dict):
                summary = str(result.get("summary") or result.get("message") or summary)
                if isinstance(result.get("next_actions"), list):
                    next_actions = result.get("next_actions")
            return {
                "ok": True,
                "tool": tool_name,
                "result": result,
                "summary": summary,
                "next_actions": next_actions,
                "audit_id": audit_id,
                "risk": tool.risk,
                "blocked": False,
                "requires_confirmation": tool.requires_confirmation,
                "message": "success",
            }
        except HTTPException as exc:
            duration_ms = round((time.monotonic() - started) * 1000)
            try:
                record_tool_call_async(
                    tool_name=tool_name,
                    ctx=ctx,
                    input_args=arguments or {},
                    normalized_args=normalized,
                    result={"detail": exc.detail},
                    status="blocked" if exc.status_code in (400, 401, 403) else "failed",
                    risk_level=tool.risk,
                    policy_result=policy_result,
                    blocked_reason=str(exc.detail),
                    duration_ms=duration_ms,
                )
            except Exception:
                pass
            raise
        except Exception as exc:
            duration_ms = round((time.monotonic() - started) * 1000)
            try:
                record_tool_call_async(
                    tool_name=tool_name,
                    ctx=ctx,
                    input_args=arguments or {},
                    normalized_args=normalized,
                    result={"error": str(exc)},
                    status="failed",
                    risk_level=tool.risk,
                    policy_result=policy_result,
                    blocked_reason=str(exc),
                    duration_ms=duration_ms,
                )
            except Exception:
                pass
            raise


registry = ToolRegistry()

_builtin_registered = False
_builtin_lock = threading.Lock()


def ensure_builtin_registered():
    global _builtin_registered
    if _builtin_registered:
        return registry
    with _builtin_lock:
        if _builtin_registered:
            return registry
        from app.services.tool_adapters import deploy_tools, file_tools, server_tools, audit_tools, config_tools, capability_tools, runtime_tools, diagnostic_tools, backup_tools, job_tools, ai_tools, report_tools, db_tools, inspection_tools, risk_tools, agent_tools, log_tools, workflow_tools, ai_analysis_tools, connection_tools, ssh_key_tools, pipeline_tools, tier_tools  # noqa: F401
        _builtin_registered = True
        registry.invalidate_capability_cache()
        return registry


def register_builtin_tools():
    return ensure_builtin_registered()


def bump_capability_version(db=None):
    registry.invalidate_capability_cache()
    try:
        registry.capability_version(db=db)
    except Exception:
        pass
