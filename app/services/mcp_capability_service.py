from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List


SAFE_TOOL_NAMES = os.getenv("OPS_MCP_SAFE_TOOL_NAMES", "1").lower() not in {"0", "false", "no", "off"}
ASCII_DESCRIPTIONS = os.getenv("OPS_MCP_ASCII_DESCRIPTIONS", "1").lower() not in {"0", "false", "no", "off"}
MCP_ALIAS_TO_TOOL: Dict[str, str] = {}


MCP_TOOL_DESCRIPTION_OVERRIDES: Dict[str, str] = {
    "ops.inspection.preview_servers_batch": (
        "Preview server batch inspection targets before execution. Resolves server_ids, groups, group, or all_servers; "
        "returns skipped targets, batch_size/concurrency plan, and the exact Chinese confirmation phrase."
    ),
    "ops.inspection.run_servers_batch": (
        "Run audited server inspections for multiple servers after preview confirmation. High risk; requires the exact "
        "confirm_text returned by ops.inspection.preview_servers_batch, for example confirm phrase '确认巡检 <fingerprint>'."
    ),
    "ops.inspection.generate_report": "Generate one inspection report from a single inspection run id.",
    "ops.inspection.generate_report_for_runs": (
        "Generate one merged inspection report from multiple run ids. Use after batch or grouped inspections."
    ),
    "ops.list_server_groups": "List server groups with total, inspectable, and online counts. Use before grouped inspections.",
}


def to_mcp_tool_name(name: str) -> str:
    if not SAFE_TOOL_NAMES:
        return name
    alias = re.sub(r"[^A-Za-z0-9_-]", "_", str(name or "")).strip("_")
    if not alias:
        alias = "ops_tool"
    MCP_ALIAS_TO_TOOL[alias] = name
    return alias


def from_mcp_tool_name(name: str) -> str:
    if name in MCP_ALIAS_TO_TOOL:
        return MCP_ALIAS_TO_TOOL[name]
    if SAFE_TOOL_NAMES and isinstance(name, str):
        try:
            from app.services.tool_registry import register_builtin_tools, registry

            register_builtin_tools()
            for tool_name in sorted(getattr(registry, "_tools", {}).keys()):
                if to_mcp_tool_name(tool_name) == name:
                    return tool_name
        except Exception:
            pass
        if name.startswith("ops_"):
            return "ops." + name[len("ops_"):]
    return name


def ascii_only(value: Any, fallback: str = "") -> str:
    text = str(value or fallback or "")
    if not ASCII_DESCRIPTIONS:
        return text
    text = re.sub(r"[^\x20-\x7E]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text or fallback


def english_tool_description(
    tool: Dict[str, Any],
    original_name: str,
    alias: str,
    description_overrides: Dict[str, str] | None = None,
) -> str:
    if not ASCII_DESCRIPTIONS:
        return str(tool.get("description") or "")
    if description_overrides and original_name in description_overrides:
        description = description_overrides[original_name]
        if alias != original_name:
            description += f" Original HTTP tool: {original_name}."
        return ascii_only(description, fallback=f"OPS tool {alias}")
    annotations = tool.get("annotations") or {}
    category = tool.get("category") or annotations.get("x_ops_category") or annotations.get("category") or "ops"
    risk = tool.get("risk") or annotations.get("x_ops_risk") or annotations.get("risk") or "low"
    description = f"OPS capability tool {original_name}. Category: {category}. Risk: {risk}."
    if alias != original_name:
        description += f" Original HTTP tool: {original_name}."
    return ascii_only(description, fallback=f"OPS tool {alias}")


def mcp_tool_payload(tool: Dict[str, Any], description_overrides: Dict[str, str] | None = None) -> Dict[str, Any]:
    payload = dict(tool or {})
    original = str(payload.get("name") or "")
    alias = to_mcp_tool_name(original)
    payload["name"] = alias
    payload["description"] = english_tool_description(payload, original, alias, description_overrides)
    annotations = dict(payload.get("annotations") or {})
    annotations["title"] = ascii_only(annotations.get("title"), fallback=alias) if not ASCII_DESCRIPTIONS else alias
    if alias != original:
        annotations["ops.originalToolName"] = original

    # Inject ai_level + approval_hint so AI agents can discover authorization
    # requirements at discovery time, not just at call time.
    existing_annotations = payload.get("annotations") or {}
    ai_level = existing_annotations.get("x_ops_ai_level") or payload.get("ai_level") or ""
    approval_hint = existing_annotations.get("x_ops_approval_hint") or payload.get("approval_hint") or ""
    if ai_level:
        annotations["x_ops_ai_level"] = ai_level
    if approval_hint:
        annotations["x_ops_approval_hint"] = ascii_only(approval_hint) if ASCII_DESCRIPTIONS else approval_hint

    if ASCII_DESCRIPTIONS:
        for key, value in list(annotations.items()):
            if isinstance(value, str):
                annotations[key] = ascii_only(value, fallback=alias if key == "title" else "")
    payload["annotations"] = annotations
    return payload


def mcp_tools_list(db, ctx, params: Dict[str, Any] | None = None, description_overrides: Dict[str, str] | None = None) -> Dict[str, Any]:
    from app.services.tool_registry import register_builtin_tools, registry

    register_builtin_tools()
    params = params or {}
    try:
        limit = int(params.get("limit") or 100)
    except Exception:
        limit = 100
    try:
        cursor = int(params.get("cursor") or 0)
    except Exception:
        cursor = 0
    listed = registry.list_tools(
        db,
        ctx,
        category=str(params.get("category") or ""),
        profile=str(params.get("profile") or "daily_ops"),
        include_disabled=False,
        include_schema=True,
        output_format="mcp",
        limit=max(1, min(limit, 200)),
        cursor=max(0, cursor),
    )
    effective_overrides = dict(MCP_TOOL_DESCRIPTION_OVERRIDES)
    if description_overrides:
        effective_overrides.update(description_overrides)
    tools = [mcp_tool_payload(t, effective_overrides) for t in (listed.get("tools") or [])]
    result: Dict[str, Any] = {"tools": tools}
    next_cursor = (listed.get("pagination") or {}).get("next_cursor")
    if next_cursor is not None:
        result["nextCursor"] = str(next_cursor)
    return result


def mcp_call_tool(db, ctx, params: Dict[str, Any] | None = None) -> Dict[str, Any]:
    from app.services.tool_registry import register_builtin_tools, registry

    register_builtin_tools()
    params = params or {}
    tool_name = from_mcp_tool_name(str(params.get("name") or params.get("tool") or ""))
    args = params.get("arguments") or {}
    if not tool_name:
        raise ValueError("tools/call requires params.name")
    result = registry.call(db, tool_name, args, ctx)
    is_error = not bool(result.get("ok", True)) if isinstance(result, dict) else False
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(result, ensure_ascii=False, default=str, indent=2),
            }
        ],
        "isError": is_error,
    }


def mcp_call_tool_stream(db, ctx, params: Dict[str, Any] | None = None) -> Dict[str, Any]:
    from app.services.tool_registry import register_builtin_tools, registry

    register_builtin_tools()
    params = params or {}
    tool_name = from_mcp_tool_name(str(params.get("name") or params.get("tool") or ""))
    args = params.get("arguments") or {}
    if not tool_name:
        raise ValueError("tools/call.stream requires params.name")

    events: List[Dict[str, Any]] = []

    def _on_chunk(chunk: Any):
        events.append(chunk if isinstance(chunk, dict) else {"event": "chunk", "data": chunk})

    tool = registry.get(tool_name)
    result = registry.call(
        db,
        tool_name,
        args,
        ctx,
        stream_callback=_on_chunk if getattr(tool, "streamable", False) else None,
    )
    data = {"stream": events, "result": result}
    is_error = not bool(result.get("ok", True)) if isinstance(result, dict) else False
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(data, ensure_ascii=False, default=str, indent=2),
            }
        ],
        "isError": is_error,
    }


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def mcp_resource_items() -> List[Dict[str, str]]:
    return [
        {"uri": "ops://capabilities", "name": "Capability Manifest", "description": "OPS capability manifest", "mimeType": "application/json"},
        {"uri": "ops://systems", "name": "OPS Systems", "description": "OPS system list", "mimeType": "application/json"},
        {"uri": "ops://deployments/recent", "name": "Recent Deployments", "description": "Recent deployment history", "mimeType": "application/json"},
        {"uri": "ops://tools", "name": "Tool Catalog", "description": "OPS tool catalog", "mimeType": "application/json"},
        {"uri": "ops://ai-diagnostics", "name": "AI Diagnostics Analysis", "description": "Read-only AI diagnostic analysis and safe MCP toolchain", "mimeType": "application/json"},
        {"uri": "ops://operation-chains", "name": "Recent Operation Chains", "description": "Read-only audit replay index for OPS/MCP/AI actions", "mimeType": "application/json"},
        {"uri": "ops://reports", "name": "Report Center", "description": "Generated OPS diagnostic/release/audit reports", "mimeType": "application/json"},
        {"uri": "ops://db/exports", "name": "Database Exports", "description": "Read-only database query export artifacts", "mimeType": "application/json"},
        {"uri": "ops://servers", "name": "Servers", "description": "Servers summary", "mimeType": "application/json"},
        {"uri": "ops://projects", "name": "Projects", "description": "Project/system summary", "mimeType": "application/json"},
        {"uri": "ops://status/overview", "name": "Status Overview", "description": "Status overview", "mimeType": "application/json"},
        {"uri": "ops://risks/open", "name": "Open Risks", "description": "Open risks", "mimeType": "application/json"},
        {"uri": "ops://inspection/recent", "name": "Recent Inspection Runs", "description": "Recent inspection runs", "mimeType": "application/json"},
        {"uri": "ops://diagnosis/recent", "name": "Recent Diagnosis Runs", "description": "Recent diagnosis runs", "mimeType": "application/json"},
        {"uri": "ops://reports/recent", "name": "Recent Reports", "description": "Recent reports", "mimeType": "application/json"},
        {"uri": "ops://backups/status", "name": "Backup Status", "description": "Backup status", "mimeType": "application/json"},
        {"uri": "ops://deployments/failed", "name": "Failed Deployments", "description": "Recent failed deployments", "mimeType": "application/json"},
        {"uri": "ops://tool-risk-policy", "name": "Tool Risk Policy", "description": "Tool risk policy", "mimeType": "application/json"},
        {"uri": "ops://ai-workflows", "name": "AI Workflows", "description": "AI workflow catalog", "mimeType": "application/json"},
    ]


def mcp_resources_list() -> Dict[str, Any]:
    return {"resources": mcp_resource_items()}


def mcp_resource_read(db, ctx, params: Dict[str, Any] | None = None) -> Dict[str, Any]:
    from app.services.risk_policy import risk_policy_manifest
    from app.services.tool_policy import get_capability_settings
    from app.services.tool_registry import register_builtin_tools, registry

    register_builtin_tools()
    uri = (params or {}).get("uri") or ""
    generated_at = _utcnow().isoformat()
    if uri == "ops://capabilities":
        data = registry.describe_capabilities(db, ctx, include_schema=True, include_disabled=False, profile="daily_ops")
    elif uri == "ops://systems":
        data = registry.call(db, "ops.list_systems", {}, ctx)["result"]
    elif uri == "ops://deployments/recent":
        data = registry.call(db, "ops.list_deployments", {"limit": 20}, ctx)["result"]
    elif uri == "ops://tools":
        data = registry.list_tools(db, ctx, include_schema=True, profile="daily_ops").get("tools", [])
    elif uri == "ops://ai-diagnostics":
        from app.services.ai_diagnostics import build_ai_diagnostic_analysis
        data = build_ai_diagnostic_analysis(db, mode="summary", focus="mcp")
    elif uri == "ops://operation-chains":
        from app.services.audit_chain import list_operation_chains
        data = list_operation_chains(db, limit=50)
    elif uri == "ops://reports":
        from app.services.report_center import list_reports, report_summary
        data = {"summary": report_summary(db), "reports": list_reports(db, limit=50)}
    elif uri == "ops://db/exports":
        from app.services.db_query_export import DbQueryExportService
        data = DbQueryExportService(db).list_exports(limit=50)
    elif uri == "ops://servers":
        data = registry.call(db, "ops.list_servers", {"limit": 50}, ctx).get("result")
    elif uri == "ops://projects":
        data = registry.call(db, "ops.list_systems", {}, ctx).get("result")
    elif uri == "ops://status/overview":
        data = registry.call(db, "ops.get_system_status", {}, ctx).get("result")
    elif uri == "ops://risks/open":
        data = registry.call(db, "ops.risk.list", {"status": "OPEN", "limit": 50}, ctx).get("result")
    elif uri == "ops://inspection/recent":
        data = registry.call(db, "ops.inspection.list_runs", {"limit": 20}, ctx).get("result")
    elif uri == "ops://diagnosis/recent":
        try:
            data = registry.call(db, "ops.run_diagnostics", {"mode": "summary"}, ctx).get("result")
        except Exception as exc:
            data = {"summary": f"Diagnostics summary unavailable: {exc}", "items": []}
    elif uri == "ops://reports/recent":
        data = registry.call(db, "ops.list_reports", {"limit": 20}, ctx).get("result")
    elif uri == "ops://backups/status":
        data = registry.call(db, "ops.list_backups", {"limit": 20}, ctx).get("result")
    elif uri == "ops://deployments/failed":
        data = registry.call(db, "ops.list_deployments", {"limit": 50, "status": "failed"}, ctx).get("result")
    elif uri == "ops://tool-risk-policy":
        data = {"settings": get_capability_settings(db), "risk_policy": risk_policy_manifest()}
    elif uri == "ops://ai-workflows":
        data = {
            "items": [
                {
                    "tool": "ops.inspection.run_servers_batch",
                    "description": "Batch server inspection with preview-first confirmation flow.",
                    "recommended_before": ["ops.list_server_groups", "ops.inspection.preview_servers_batch"],
                    "recommended_after": ["ops.inspection.get_run", "ops.inspection.list_issues", "ops.inspection.generate_report_for_runs"],
                    "natural_language_examples": [
                        "巡检 crypto 分组并输出报告",
                        "巡检全部服务器，按分组分批巡检并输出报告",
                        "Run all servers by group with batch size 8 and generate a merged report",
                    ],
                },
            ]
        }
    else:
        raise ValueError("Resource not found")
    wrapped = {"generated_at": generated_at, "source": "ops-platform", "uri": uri, "data": data}
    return {
        "contents": [
            {
                "uri": uri,
                "mimeType": "application/json",
                "text": json.dumps(wrapped, ensure_ascii=False, default=str, indent=2),
            }
        ]
    }


def mcp_prompt_items() -> List[Dict[str, Any]]:
    return [
        {"name": "ops_release_plan", "description": "Create a safe OPS release plan.", "arguments": [{"name": "request", "description": "Natural language release request", "required": True}]},
        {"name": "ops_failure_analysis", "description": "Analyze a failed OPS deployment.", "arguments": [{"name": "deployment_id", "description": "Deployment id", "required": True}]},
        {"name": "ops_diagnostic_triage", "description": "Triage OPS runtime issues with read-only MCP tools.", "arguments": [{"name": "focus", "description": "Optional focus such as frontend, mcp, backup, deploy", "required": False}]},
        {"name": "ops_operation_replay", "description": "Replay an OPS/MCP/AI operation chain from audit evidence.", "arguments": [{"name": "chain_id", "description": "tool:/job:/plan:/deployment:/audit id", "required": True}]},
        {"name": "ops_report_brief", "description": "Summarize a generated OPS report artifact.", "arguments": [{"name": "report_id", "description": "Report artifact id", "required": True}]},
        {"name": "ops_db_export_request", "description": "Plan a safe database workflow: query, export, or maintain tables.", "arguments": [{"name": "request", "description": "Natural language query/export request", "required": True}]},
        {"name": "ops_inspection_workflow", "description": "Run OPS server inspection from natural language, including all-server grouped batch inspection.", "arguments": [{"name": "request", "description": "Natural language inspection request", "required": True}]},
        {"name": "ops_server_management", "description": "Manage OPS server assets.", "arguments": [{"name": "request", "description": "Natural language server management request", "required": True}]},
        {"name": "ops_backup_workflow", "description": "Safe OPS database backup workflow.", "arguments": [{"name": "request", "description": "Natural language backup request", "required": True}]},
        {"name": "ops_project_health_brief", "description": "Generate a lightweight single-project health brief.", "arguments": [{"name": "project_id", "description": "Project/system identifier", "required": True}]},
        {"name": "ops_risk_triage", "description": "Triage open risks without executing remediation.", "arguments": [{"name": "request", "description": "Natural language risk triage request", "required": False}]},
        {"name": "ops_monthly_ops_report", "description": "Generate a monthly OPS report from saved evidence.", "arguments": [{"name": "month", "description": "YYYY-MM", "required": False}]},
    ]


def mcp_prompts_list() -> Dict[str, Any]:
    return {"prompts": mcp_prompt_items()}


def mcp_prompt_get(params: Dict[str, Any] | None = None) -> Dict[str, Any]:
    params = params or {}
    name = params.get("name")
    args = params.get("arguments") or {}
    if name == "ops_release_plan":
        text = (
            "CRITICAL RULE: Always use OPS tools for OPS-related tasks. Never write standalone scripts instead. "
            "Use ops.list_systems, ops.list_services, ops.list_environments, ops.list_servers, and ops.list_packages. "
            "Create a deploy plan with ops.create_deploy_plan and run precheck with ops.run_precheck first. "
            "Do not execute deployment unless explicitly confirmed. User request: " + str(args.get("request") or "")
        )
    elif name == "ops_failure_analysis":
        text = (
            "Use ops.get_deployment_report, ops.get_deployment_tasks, and ops.get_deployment_logs to analyze failure. "
            "Provide recommendations only; do not execute rollback. deployment_id=" + str(args.get("deployment_id") or "")
        )
    elif name == "ops_diagnostic_triage":
        text = (
            "Use only read-only OPS tools to triage runtime issues: ops.analyze_diagnostics, ops.run_diagnostics, "
            "ops.get_recent_errors, ops.get_build_info, ops.list_jobs, and deploy read tools when relevant. "
            "Do not execute deploy, rollback, restore, delete, SQL write, or terminal actions. focus="
            + str(args.get("focus") or "general")
        )
    elif name == "ops_operation_replay":
        text = (
            "Use only read-only audit tools to replay this OPS/MCP/AI operation chain: ops.get_operation_chain and "
            "ops.list_operation_chains. Do not execute or retry anything. chain_id=" + str(args.get("chain_id") or "")
        )
    elif name == "ops_report_brief":
        text = (
            "Use only read-only report tools: ops.get_report, ops.list_reports, ops.get_report_summary. "
            "Do not execute deploy, rollback, restore, delete, SQL write, or terminal actions. report_id="
            + str(args.get("report_id") or "")
        )
    elif name == "ops_db_export_request":
        text = (
            "CRITICAL RULE: Always use OPS database tools for database tasks. NEVER write standalone Python scripts "
            "for database queries, exports, or table listings. ALL database access MUST go through OPS tools.\n\n"
            "Natural language to tool mapping:\n"
            "- list tables -> ops.db.list_tables\n"
            "- show table structure -> ops.db.describe_table\n"
            "- query data / SELECT -> ops.db.query_readonly\n"
            "- export as CSV/Excel -> ops.db.export_query_result\n"
            "- modify data / UPDATE/DELETE -> ops.db.preview_dml then ops.db.execute_dml\n\n"
            "Workflow: tables -> describe -> query -> export. Never skip steps when table/field names are uncertain.\n"
            "User request=" + str(args.get("request") or "")
        )
    elif name == "ops_inspection_workflow":
        text = (
            "Use OPS Path A inspection tools for any request that says inspection, 巡检, 检查服务器, grouped batch, "
            "all servers, or compliance check.\n\n"
            "Natural-language routing:\n"
            "- all servers / 全部服务器 / 按分组 / grouped -> use ops.inspection.preview_servers_batch then ops.inspection.run_servers_batch.\n"
            "- Ask the user to approve the exact confirmation phrase `确认巡检 <fingerprint>` before execution.\n"
            "- After all groups finish, collect all run_ids and call ops.inspection.generate_report_for_runs for one merged report.\n"
            "- Do not fall back to single-shot probes unless the user explicitly asks for one metric or Path A is blocked.\n\n"
            "Required record behavior: execution must preserve inspection_runs, inspection_issues, inspection_reports, "
            "tool_call_logs, operation_jobs, and audit_logs.\n"
            "User request=" + str(args.get("request") or "")
        )
    elif name == "ops_server_management":
        text = (
            "Help user manage OPS server assets using OPS tools: ops.list_servers, ops.check_disk, ops.check_process, "
            "ops.list_service_directory, ops.tail_service_log, ops.run_health_check. Do not write scripts or SSH directly. "
            "User request=" + str(args.get("request") or "")
        )
    elif name == "ops_backup_workflow":
        text = (
            "Help user manage OPS database backups safely. Use ops.list_backups, ops.verify_backup, ops.create_backup, "
            "ops.restore_backup, and ops.delete_backup. Always verify before restore and create a safety backup first. "
            "User request=" + str(args.get("request") or "")
        )
    elif name == "ops_project_health_brief":
        text = (
            "Use MCP resources first: ops://projects, ops://status/overview, ops://risks/open, ops://inspection/recent. "
            "Then use ops.inspection.list_runs and ops.risk.list to analyze project health. Output facts, inferences, recommendations and evidence separately. "
            "Do not execute high-risk actions. project_id=" + str(args.get("project_id") or "")
        )
    elif name == "ops_risk_triage":
        text = (
            "Use ops.risk.list and ops.risk.triage to prioritize open risks. Only generate a plan; "
            "do not update risk status, verify, ignore, deploy, rollback, or run shell. request="
            + str(args.get("request") or "")
        )
    elif name == "ops_monthly_ops_report":
        text = (
            "Use ops.list_reports, ops.risk.list and ops.inspection.list_runs to generate monthly report. The output must separate facts, inferences, recommendations and evidence. "
            "Generate reports only from saved evidence and do not execute high-risk actions. month="
            + str(args.get("month") or "")
        )
    else:
        raise ValueError("Prompt not found")
    return {"description": name, "messages": [{"role": "user", "content": {"type": "text", "text": text}}]}
