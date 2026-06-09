from __future__ import annotations

from typing import Any, Dict, List

from app.services.tool_registry import registry


INSPECTION_RECORD_TYPES = [
    "inspection_runs",
    "inspection_issues",
    "inspection_reports",
    "operation_jobs",
    "tool_call_logs",
    "audit_logs",
]


def _as_list(value: Any) -> List[str]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []


def _unwrap_tool_result(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict) and isinstance(value.get("result"), dict):
        return value["result"]
    return value if isinstance(value, dict) else {"value": value}


def _request_contains(request: str, words: List[str]) -> bool:
    lowered = (request or "").lower()
    return any(word and word.lower() in lowered for word in words)


def _infer_all_servers(request: str, args: Dict[str, Any]) -> bool:
    if "all_servers" in args:
        return bool(args.get("all_servers"))
    return _request_contains(request, ["全部服务器", "所有服务器", "全量服务器", "全服务器", "全部机器", "所有机器", "all servers"])


def _infer_grouped(request: str, args: Dict[str, Any]) -> bool:
    for key in ["grouped", "group_by", "group_by_group"]:
        if key in args:
            return bool(args.get(key))
    return _request_contains(request, ["按分组", "分组分批", "分组巡检", "by group", "grouped"])


def _infer_inspection_groups(request: str, explicit_groups: Any, group: Any = "") -> List[str]:
    groups = _as_list(explicit_groups)
    groups.extend(_as_list(group))
    lowered = (request or "").lower()
    if not groups and "crypto" in lowered:
        groups.append("crypto")
    return list(dict.fromkeys(groups))


def _inspection_payload(args: Dict[str, Any], *, include_execution_fields: bool) -> Dict[str, Any]:
    request = str(args.get("request") or args.get("scope_label") or "")
    payload: Dict[str, Any] = {}
    server_ids = _as_list(args.get("server_ids"))
    groups = _infer_inspection_groups(request, args.get("groups"), args.get("group"))
    categories = _as_list(args.get("categories"))
    if server_ids:
        payload["server_ids"] = server_ids
    if groups:
        payload["groups"] = groups
    if categories:
        payload["categories"] = categories
    for key in ["concurrency", "batch_size", "command_timeout_seconds", "run_timeout_seconds"]:
        if args.get(key) not in (None, ""):
            payload[key] = args.get(key)
    for key in ["all_servers", "skip_disabled"]:
        if key in args:
            payload[key] = bool(args.get(key))
    if not server_ids and not groups and _infer_all_servers(request, args):
        payload["all_servers"] = True
    if include_execution_fields:
        if args.get("generate_report") is not None:
            payload["generate_report"] = bool(args.get("generate_report"))
        if args.get("confirm_text"):
            payload["confirm_text"] = str(args.get("confirm_text") or "")
    return payload


def _preview_group_name(item: Dict[str, Any]) -> str:
    for key in ["group", "env", "group_name", "server_group"]:
        value = str(item.get(key) or "").strip()
        if value:
            return value
    return "未分组"


def _preview_server_name(item: Dict[str, Any]) -> str:
    for key in ["name", "server_id", "host", "ip", "id", "asset_id"]:
        value = str(item.get(key) or "").strip()
        if value:
            return value
    return ""


def _build_grouped_plan(preview: Dict[str, Any], args: Dict[str, Any], preview_payload: Dict[str, Any]) -> Dict[str, Any]:
    groups: Dict[str, Dict[str, Any]] = {}
    for item in preview.get("eligible") or []:
        if not isinstance(item, dict):
            continue
        name = _preview_group_name(item)
        bucket = groups.setdefault(name, {"name": name, "target_count": 0, "servers": []})
        bucket["target_count"] += 1
        server_name = _preview_server_name(item)
        if server_name:
            bucket["servers"].append(server_name)
    ordered_groups = sorted(groups.values(), key=lambda item: str(item.get("name") or ""))
    execution_plan = preview.get("execution_plan") or {}
    next_actions: List[Dict[str, Any]] = []
    base_arguments = {
        key: args.get(key)
        for key in ["request", "scope_label", "categories", "concurrency", "batch_size", "command_timeout_seconds", "run_timeout_seconds", "skip_disabled", "generate_report"]
        if args.get(key) not in (None, "")
    }
    for item in ordered_groups:
        group_name = str(item.get("name") or "").strip()
        if not group_name:
            continue
        action_args = dict(base_arguments)
        action_args.pop("all_servers", None)
        action_args["groups"] = [group_name]
        next_actions.append({
            "tool": "ops.workflow.inspect",
            "arguments": action_args,
            "description": f"预览并确认 {group_name} 分组巡检；确认后再次调用同一工具执行该分组。",
        })
    return {
        "strategy": "先全量预览服务器，再按分组逐组预览、确认和执行；每组内部继续按 batch_size/concurrency 分批。",
        "total_groups": len(ordered_groups),
        "total_targets": int(preview.get("eligible_count") or sum(int(item.get("target_count") or 0) for item in ordered_groups)),
        "skipped_count": int(preview.get("skipped_count") or 0),
        "batch_size": execution_plan.get("batch_size") or preview_payload.get("batch_size"),
        "concurrency": execution_plan.get("concurrency") or preview_payload.get("concurrency"),
        "groups": ordered_groups,
        "next_actions": next_actions,
        "final_report_template": {
            "tool": "ops.inspection.generate_report_for_runs",
            "arguments": {"run_ids": [], "format": "md", "title": "全量服务器分组巡检合并报告"},
            "description": "所有分组执行完成后，收集每组返回的 run_ids，填入 run_ids 生成一份全量合并巡检报告。",
        },
    }


def _should_return_grouped_preview(args: Dict[str, Any], request: str, preview_payload: Dict[str, Any], preview: Dict[str, Any]) -> bool:
    if not _infer_grouped(request, args):
        return False
    if _as_list(args.get("server_ids")) or _as_list(args.get("groups")) or _as_list(args.get("group")):
        return False
    return bool(preview_payload.get("all_servers") or preview.get("all_servers"))


def _inspection_profile_payload(args: Dict[str, Any], *, include_execution_fields: bool) -> Dict[str, Any]:
    payload = {"profile_id": str(args.get("profile_id") or "").strip()}
    if include_execution_fields and args.get("confirm_text"):
        payload["confirm_text"] = str(args.get("confirm_text") or "")
    return payload


@registry.register(
    name="ops.workflow.inspect",
    title="自然语言服务器巡检工作流",
    description=(
        "Natural-language first OPS inspection workflow. It previews target servers and confirmation text first; "
        "after user confirmation it delegates execution to ops.inspection.run_servers_batch so normal inspection, "
        "job, tool-call and audit records are preserved."
    ),
    scopes=["ops:read"],
    risk="medium",
    category="workflow",
    write=False,
    requires_confirmation=False,
    ai_callable=True,
    ai_auto_callable=False,
    data_sensitivity="sensitive",
    output_masking=True,
    recommended_use_cases=["自然语言服务器巡检", "批量巡检预览", "巡检报告工作流"],
    example_prompts=["巡检 crypto 下测试服务器并生成报告", "执行日常轻量巡检"],
    related_tools=["ops.inspection.preview_servers_batch", "ops.inspection.run_servers_batch", "ops.inspection.generate_report"],
    input_schema={
        "type": "object",
        "properties": {
            "request": {"type": "string", "description": "Natural language inspection request."},
            "scope_label": {"type": "string", "description": "Human-readable target scope label."},
            "profile_id": {"type": "string", "description": "Saved inspection profile id, for example crypto-test-daily."},
            "server_ids": {"type": "array", "items": {"type": "string"}},
            "groups": {"type": "array", "items": {"type": "string"}},
            "group": {"type": "string"},
            "categories": {"type": "array", "items": {"type": "string"}},
            "concurrency": {"type": "integer", "minimum": 1, "maximum": 20},
            "batch_size": {"type": "integer", "minimum": 1, "maximum": 20},
            "all_servers": {"type": "boolean"},
            "grouped": {"type": "boolean", "description": "Return an all-server grouped execution plan before running."},
            "group_by": {"type": "boolean", "description": "Alias for grouped."},
            "group_by_group": {"type": "boolean", "description": "Alias for grouped."},
            "skip_disabled": {"type": "boolean"},
            "command_timeout_seconds": {"type": "integer", "minimum": 5, "maximum": 300},
            "run_timeout_seconds": {"type": "integer", "minimum": 30, "maximum": 1800},
            "generate_report": {"type": "boolean"},
            "confirm_text": {"type": "string", "description": "Confirmation text returned by preview."},
        },
        "additionalProperties": False,
    },
)
def inspect_servers(args: Dict[str, Any], ctx, db):
    from app.services.tool_registry import registry as reg

    args = args or {}
    request = str(args.get("request") or args.get("scope_label") or "")
    records = {
        "preserved": True,
        "types": INSPECTION_RECORD_TYPES,
        "note": "Execution is delegated to underlying inspection tools; records and logs remain queryable in inspection center, reports, jobs and audit pages.",
    }
    child_tools = ["ops.inspection.preview_servers_batch", "ops.inspection.run_servers_batch"]

    if args.get("profile_id"):
        child_tools = ["ops.inspection.profile.preview", "ops.inspection.profile.run"]
        if not args.get("confirm_text"):
            preview_payload = _inspection_profile_payload(args, include_execution_fields=False)
            preview_call = reg.call(db, "ops.inspection.profile.preview", preview_payload, ctx)
            preview = _unwrap_tool_result(preview_call)
            confirm_text = (preview.get("confirmation") or {}).get("confirm_text") if isinstance(preview, dict) else ""
            next_arguments = dict(args)
            if confirm_text:
                next_arguments["confirm_text"] = confirm_text
            return {
                "summary": "Inspection profile preview is ready. Confirm with the returned phrase before execution.",
                "workflow_type": "inspection",
                "source_tool": "ops.workflow.inspect",
                "mode": "preview",
                "request": request,
                "routed_arguments": preview_payload,
                "child_tools": child_tools,
                "records": records,
                "preview": preview,
                "confirmation": preview.get("confirmation") if isinstance(preview, dict) else {},
                "next_actions": [
                    {
                        "tool": "ops.workflow.inspect",
                        "arguments": next_arguments,
                        "description": "Use the returned confirmation text to execute the saved inspection profile.",
                    }
                ],
            }
        run_payload = _inspection_profile_payload(args, include_execution_fields=True)
        run_call = reg.call(db, "ops.inspection.profile.run", run_payload, ctx)
        run_result = _unwrap_tool_result(run_call)
        return {
            "summary": "Inspection profile execution was delegated to the audited profile run tool.",
            "workflow_type": "inspection",
            "source_tool": "ops.workflow.inspect",
            "mode": "run",
            "request": request,
            "routed_arguments": run_payload,
            "child_tools": child_tools,
            "records": records,
            "run": run_result,
            "next_actions": run_result.get("next_actions") if isinstance(run_result, dict) else [],
        }

    if not args.get("confirm_text"):
        preview_payload = _inspection_payload(args, include_execution_fields=False)
        preview_call = reg.call(db, "ops.inspection.preview_servers_batch", preview_payload, ctx)
        preview = _unwrap_tool_result(preview_call)
        confirm_text = (preview.get("confirmation") or {}).get("confirm_text") if isinstance(preview, dict) else ""
        if isinstance(preview, dict) and _should_return_grouped_preview(args, request, preview_payload, preview):
            grouped_plan = _build_grouped_plan(preview, args, preview_payload)
            return {
                "summary": "All-server inspection can be completed, but this request should be executed group by group because the target count is large.",
                "workflow_type": "inspection",
                "source_tool": "ops.workflow.inspect",
                "mode": "grouped_preview",
                "can_complete": True,
                "execution_strategy": "grouped_batch_inspection",
                "request": request,
                "routed_arguments": preview_payload,
                "child_tools": child_tools,
                "records": records,
                "logic_checks": [
                    "Natural language all-server scope was routed to all_servers=true.",
                    "Grouped execution is kept as multiple preview/confirm/run calls so each high-risk execution remains auditable.",
                    "Each group run will preserve inspection_runs, inspection_issues, reports, operation jobs, tool-call logs, and audit logs.",
                    "After all groups finish, collect all returned run_ids and call ops.inspection.generate_report_for_runs for the final merged report.",
                ],
                "preview": preview,
                "confirmation": preview.get("confirmation") or {},
                "grouped_plan": grouped_plan,
                "next_actions": grouped_plan.get("next_actions") or [],
            }
        next_arguments = dict(args)
        if confirm_text:
            next_arguments["confirm_text"] = confirm_text
        return {
            "summary": "Inspection workflow preview is ready. Confirm with the returned short Chinese phrase before execution.",
            "workflow_type": "inspection",
            "source_tool": "ops.workflow.inspect",
            "mode": "preview",
            "request": request,
            "routed_arguments": preview_payload,
            "child_tools": child_tools,
            "records": records,
            "preview": preview,
            "confirmation": preview.get("confirmation") if isinstance(preview, dict) else {},
            "next_actions": [
                {
                    "tool": "ops.workflow.inspect",
                    "arguments": next_arguments,
                    "description": "Use the returned confirmation text to execute the inspection workflow.",
                }
            ],
        }

    run_payload = _inspection_payload(args, include_execution_fields=True)
    run_call = reg.call(db, "ops.inspection.run_servers_batch", run_payload, ctx)
    run_result = _unwrap_tool_result(run_call)
    next_actions = run_result.get("next_actions") if isinstance(run_result, dict) else []
    if args.get("generate_report") and isinstance(run_result, dict) and run_result.get("run_ids"):
        child_tools.append("ops.inspection.generate_report_for_runs")
        next_actions = list(next_actions or [])
        if not any(action.get("tool") == "ops.inspection.generate_report_for_runs" for action in next_actions if isinstance(action, dict)):
            next_actions.append({
                "tool": "ops.inspection.generate_report_for_runs",
                "arguments": {"run_ids": run_result.get("run_ids") or []},
                "description": "Generate one merged inspection report from the completed run records.",
            })
    return {
        "summary": "Inspection workflow execution was delegated to the audited batch inspection tool.",
        "workflow_type": "inspection",
        "source_tool": "ops.workflow.inspect",
        "mode": "run",
        "request": request,
        "routed_arguments": run_payload,
        "child_tools": child_tools,
        "records": records,
        "run": run_result,
        "next_actions": next_actions or [],
    }


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
