from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List

from fastapi import HTTPException
from app.services.tool_registry import registry


def _actor(ctx) -> str:
    return getattr(ctx, "username", "") or getattr(ctx, "token_owner", "") or "mcp-ai"


def _inspection_followup(result: Dict[str, Any]) -> Dict[str, Any]:
    payload = dict(result or {})
    run = payload.get("run") if isinstance(payload.get("run"), dict) else {}
    runs = payload.get("runs") if isinstance(payload.get("runs"), list) else []
    run_ids = [str(item.get("id")) for item in runs if isinstance(item, dict) and item.get("id")]
    if run.get("id"):
        payload["run_id"] = str(run.get("id"))
        run_ids = [payload["run_id"]]
    if run_ids:
        payload["run_ids"] = run_ids
        payload.setdefault("status", str(run.get("status") or ("PARTIAL" if payload.get("failed") else "COMPLETED")))
        payload["next_actions"] = [
            {"tool": "ops.inspection.get_run", "arguments": {"run_id": run_ids[0]}, "description": "Fetch normalized inspection detail."},
            {"tool": "ops.inspection.get_run_raw_output", "arguments": {"run_id": run_ids[0]}, "description": "Fetch raw command output and evidence for the run."},
            {"tool": "ops.inspection.summarize_run", "arguments": {"run_id": run_ids[0]}, "description": "Build an AI-friendly evidence summary."},
        ]
    return payload


def _strings(value: Any) -> List[str]:
    if value is None:
        return []
    raw = value if isinstance(value, list) else [value]
    result: List[str] = []
    seen = set()
    for item in raw:
        text = str(item or "").strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _batch_groups(args: Dict[str, Any]) -> List[str]:
    groups = _strings(args.get("groups"))
    if args.get("group") and not groups:
        groups = _strings([args.get("group")])
    return groups


def _batch_categories(args: Dict[str, Any]) -> List[str]:
    return _strings(args.get("categories"))


def _batch_int(value: Any, default: int, low: int, high: int) -> int:
    try:
        number = int(value)
    except Exception:
        number = default
    return max(low, min(number, high))


def _batch_preview(args: Dict[str, Any]) -> Dict[str, Any]:
    from app.services import inspection_center as svc

    server_ids = _strings(args.get("server_ids"))
    groups = _batch_groups(args)
    all_servers = bool(args.get("all_servers", False))
    if not (server_ids or groups or all_servers):
        raise HTTPException(status_code=400, detail="server_ids / groups / all_servers 至少提供一个")

    skip_disabled = bool(args.get("skip_disabled", True))
    resolved = svc.resolve_servers_for_inspection(
        server_ids,
        all_servers=all_servers,
        skip_disabled=skip_disabled,
        groups=groups or None,
    )
    categories = _batch_categories(args)
    concurrency = _batch_int(args.get("concurrency"), svc.DEFAULT_BATCH_CONCURRENCY, 1, svc.MAX_BATCH_CONCURRENCY)
    batch_size = _batch_int(args.get("batch_size"), svc.DEFAULT_BATCH_SIZE, 1, svc.MAX_BATCH_SIZE)
    command_timeout_seconds = _batch_int(args.get("command_timeout_seconds"), svc.DEFAULT_COMMAND_TIMEOUT_SECONDS, 5, svc.MAX_COMMAND_TIMEOUT_SECONDS)
    run_timeout_seconds = _batch_int(args.get("run_timeout_seconds"), svc.DEFAULT_RUN_TIMEOUT_SECONDS, 30, svc.MAX_RUN_TIMEOUT_SECONDS)
    eligible_ids = _strings(resolved.get("eligible_ids") or [])
    payload = {
        "server_ids": eligible_ids,
        "groups": groups,
        "all_servers": all_servers,
        "categories": categories,
        "concurrency": concurrency,
        "batch_size": batch_size,
        "command_timeout_seconds": command_timeout_seconds,
        "run_timeout_seconds": run_timeout_seconds,
        "skip_disabled": skip_disabled,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    fingerprint = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]
    confirm_text = f"确认巡检 {fingerprint}"
    legacy_text = "CONFIRM ops.inspection.run_servers_batch"
    batch_count = (len(eligible_ids) + batch_size - 1) // batch_size if eligible_ids else 0
    return {
        "status": "confirmation_required",
        "summary": f"将巡检 {len(eligible_ids)} 台服务器，跳过 {resolved.get('skipped_count', 0)} 台；并发 {concurrency}，每批 {batch_size} 台。",
        "eligible": resolved.get("eligible") or [],
        "eligible_ids": eligible_ids,
        "eligible_count": len(eligible_ids),
        "skipped": resolved.get("skipped") or [],
        "skipped_count": int(resolved.get("skipped_count") or 0),
        "total_requested": int(resolved.get("total_requested") or 0),
        "categories": categories,
        "groups": groups,
        "all_servers": all_servers,
        "skip_disabled": skip_disabled,
        "execution_plan": {
            "concurrency": concurrency,
            "batch_size": batch_size,
            "batch_count": batch_count,
            "command_timeout_seconds": command_timeout_seconds,
            "run_timeout_seconds": run_timeout_seconds,
        },
        "confirmation": {
            "confirm_text": confirm_text,
            "expected_confirm_text": confirm_text,
            "legacy_confirm_text": legacy_text,
            "accepted_confirm_texts": [confirm_text, legacy_text],
            "fingerprint": fingerprint,
            "target_count": len(eligible_ids),
            "mode": "one-click-friendly",
            "description": "请确认本次巡检目标、巡检项、并发和超时设置。确认短语为短中文格式，已绑定目标和参数；目标变化后请重新预览。",
        },
    }


def _validate_batch_confirmation(args: Dict[str, Any]) -> Dict[str, Any]:
    preview = _batch_preview(args)
    confirmation = preview.get("confirmation") or {}
    expected = str(confirmation.get("confirm_text") or "").strip()
    supplied = str(args.get("confirm_text") or "").strip()
    if supplied != expected:
        raise HTTPException(
            status_code=428,
            detail={
                "code": "CONFIRMATION_REQUIRED",
                "message": "批量巡检需要先预览并使用当前中文确认短语",
                "expected_confirm_text": expected,
                "accepted_confirm_texts": [expected],
                "confirmation": confirmation,
                "preview": {
                    "eligible_count": preview.get("eligible_count"),
                    "skipped_count": preview.get("skipped_count"),
                    "categories": preview.get("categories"),
                    "groups": preview.get("groups"),
                },
            },
        )
    return preview


@registry.register(
    name="ops.inspection.list_runs",
    title="查询巡检记录",
    description="查询服务器/项目巡检执行记录，供 AI 分析最近巡检状态。",
    scopes=["ops:read"],
    risk="low",
    category="inspection",
    write=False,
    ai_callable=True,
    ai_auto_callable=True,
    data_sensitivity="internal",
    output_masking=True,
    recommended_use_cases=["巡检摘要", "项目健康分析", "风险分析"],
    example_prompts=["项目 A 最近一次巡检有什么问题？", "列出最近 10 条巡检记录"],
    related_tools=["ops.inspection.get_run", "ops.inspection.list_issues"],
    input_schema={
        "type": "object",
        "properties": {
            "scope_type": {"type": "string"},
            "project_id": {"type": "string"},
            "server_id": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100},
        },
        "additionalProperties": False,
    },
)
def list_runs(args: Dict[str, Any], ctx, db):
    from app.services.inspection_center import list_runs as svc_list_runs
    return svc_list_runs(
        db,
        scope_type=args.get("scope_type") or "",
        server_id=args.get("server_id") or "",
        project_id=args.get("project_id") or "",
        limit=args.get("limit") or 20,
    )


@registry.register(
    name="ops.inspection.get_run",
    title="查看巡检详情",
    description="查看指定巡检记录、巡检项、证据和风险问题。输出已脱敏。",
    scopes=["ops:read"],
    risk="medium",
    category="inspection",
    write=False,
    ai_callable=True,
    ai_auto_callable=False,
    data_sensitivity="sensitive",
    output_masking=True,
    recommended_use_cases=["巡检报告分析", "项目安全分析"],
    related_tools=["ops.inspection.summarize_run", "ops.inspection.generate_report"],
    input_schema={"type": "object", "properties": {"run_id": {"type": "string"}}, "required": ["run_id"], "additionalProperties": False},
)
def get_run(args: Dict[str, Any], ctx, db):
    from app.services.inspection_center import inspection_run_detail
    return inspection_run_detail(db, args.get("run_id") or "")


@registry.register(
    name="ops.inspection.list_issues",
    title="查询巡检风险问题",
    description="查询巡检产生的风险问题，用于 AI 风险分流和整改计划生成。",
    scopes=["ops:read"],
    risk="low",
    category="inspection",
    write=False,
    ai_callable=True,
    ai_auto_callable=True,
    data_sensitivity="internal",
    output_masking=True,
    related_tools=["ops.risk.triage", "ops.inspection.get_issue"],
    input_schema={
        "type": "object",
        "properties": {
            "scope_type": {"type": "string"},
            "risk_level": {"type": "string"},
            "status": {"type": "string"},
            "server_id": {"type": "string"},
            "project_id": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 200},
        },
        "additionalProperties": False,
    },
)
def list_issues(args: Dict[str, Any], ctx, db):
    from app.services.inspection_center import list_issues as svc_list_issues
    return svc_list_issues(
        db,
        scope_type=args.get("scope_type") or "",
        risk_level=args.get("risk_level") or "",
        status=args.get("status") or "",
        server_id=args.get("server_id") or "",
        project_id=args.get("project_id") or "",
        limit=args.get("limit") or 50,
    )


@registry.register(
    name="ops.inspection.get_issue",
    title="查看巡检风险问题详情",
    description="按问题 ID 查看巡检风险问题详情。",
    scopes=["ops:read"],
    risk="low",
    category="inspection",
    write=False,
    ai_callable=True,
    ai_auto_callable=True,
    data_sensitivity="internal",
    output_masking=True,
    input_schema={"type": "object", "properties": {"issue_id": {"type": "string"}}, "required": ["issue_id"], "additionalProperties": False},
)
def get_issue(args: Dict[str, Any], ctx, db):
    issues = list_issues({"limit": 200}, ctx, db).get("items") or []
    issue_id = args.get("issue_id") or ""
    for item in issues:
        if item.get("id") == issue_id:
            return {"item": item}
    return {"item": None, "summary": "Issue not found"}


@registry.register(
    name="ops.inspection.generate_report",
    title="生成巡检报告",
    description="基于指定巡检记录生成报告中心制品。",
    scopes=["ops:read", "audit:read"],
    risk="low",
    category="inspection",
    write=False,
    ai_callable=True,
    ai_auto_callable=False,
    data_sensitivity="internal",
    output_masking=True,
    input_schema={
        "type": "object",
        "properties": {"run_id": {"type": "string"}, "format": {"type": "string", "enum": ["json", "md"]}, "title": {"type": "string"}},
        "required": ["run_id"],
        "additionalProperties": False,
    },
)
def generate_report(args: Dict[str, Any], ctx, db):
    from app.services.report_center import generate_report as svc_generate_report
    return svc_generate_report(db, report_type="inspection", target_id=args.get("run_id") or "", fmt=args.get("format") or "md", created_by=_actor(ctx), title=args.get("title") or "")


@registry.register(
    name="ops.inspection.summarize_run",
    title="总结巡检结果",
    description="将巡检记录转换为 AI 可使用的 facts/inferences/recommendations/evidence 结构。",
    scopes=["ops:read"],
    risk="low",
    category="inspection",
    write=False,
    ai_callable=True,
    ai_auto_callable=True,
    data_sensitivity="internal",
    output_masking=True,
    input_schema={"type": "object", "properties": {"run_id": {"type": "string"}}, "required": ["run_id"], "additionalProperties": False},
)
def summarize_run(args: Dict[str, Any], ctx, db):
    detail = get_run(args, ctx, db)
    run = detail.get("run") or {}
    issues = detail.get("issues") or []
    facts = [
        {"id": "fact-score", "source_tool": "ops.inspection.get_run", "ref": f"run_id={run.get('id')}", "content": f"巡检评分 {run.get('score')}，高危 {run.get('high_count')}，中危 {run.get('medium_count')}，低危 {run.get('low_count')}"}
    ]
    for idx, issue in enumerate(issues[:20], start=1):
        facts.append({"id": f"fact-issue-{idx}", "source_tool": "ops.inspection.get_run", "ref": issue.get("id"), "content": f"{issue.get('risk_level')}：{issue.get('title')} - {issue.get('description')}"})
    recs = [{"action": i.get("suggestion") or "请根据风险问题安排整改与复查", "risk": i.get("risk_level"), "requires_human_approval": False} for i in issues[:20]]
    return {
        "summary": f"巡检 {run.get('scope_type') or '-'} 评分 {run.get('score') or 0}，高危 {run.get('high_count') or 0}，中危 {run.get('medium_count') or 0}，低危 {run.get('low_count') or 0}。",
        "facts": facts,
        "inferences": [{"claim": "巡检风险应按高危优先、生产优先、超期优先处理。", "confidence": "medium", "based_on": [f.get("id") for f in facts]}],
        "recommendations": recs,
        "evidence": facts,
        "confidence": "medium",
    }


@registry.register(
    name="ops.inspection.run_server",
    title="执行服务器巡检",
    description="发起服务器巡检。该操作会连接服务器执行只读巡检命令，AI/MCP token 不允许自动执行。",
    scopes=["ops:read", "ops:write"],
    risk="high",
    category="inspection_execute",
    write=True,
    requires_confirmation=True,
    requires_human_approval=True,
    ai_callable=True,
    ai_auto_callable=False,
    data_sensitivity="sensitive",
    output_masking=True,
    input_schema={"type": "object", "properties": {"server_id": {"type": "string"}, "categories": {"type": "array", "items": {"type": "string"}}, "confirm_text": {"type": "string", "description": "用户确认短语（与高风险工具 expected_confirm_text 匹配）"}, "environment": {"type": "string"}}, "required": ["server_id"], "additionalProperties": False},
)
def run_server(args: Dict[str, Any], ctx, db):
    from app.services.inspection_center import run_server_inspection as svc_run_server
    return _inspection_followup(svc_run_server(db, server_id=args.get("server_id") or "", categories=args.get("categories") or [], created_by=_actor(ctx)))


@registry.register(
    name="ops.inspection.profile.list",
    title="查询巡检方案",
    description="查询可复用的服务器巡检方案，供 MCP/AI 选择日巡、周巡、月巡或分组巡检流程。",
    scopes=["ops:read"],
    risk="low",
    category="inspection",
    write=False,
    ai_callable=True,
    ai_auto_callable=True,
    data_sensitivity="internal",
    output_masking=True,
    related_tools=["ops.inspection.profile.preview", "ops.inspection.profile.run"],
    input_schema={
        "type": "object",
        "properties": {"include_disabled": {"type": "boolean"}},
        "additionalProperties": False,
    },
)
def profile_list(args: Dict[str, Any], ctx, db):
    from app.services.inspection_profiles import list_profiles

    return list_profiles(db, include_disabled=bool(args.get("include_disabled", False)))


@registry.register(
    name="ops.inspection.profile.preview",
    title="预览巡检方案目标",
    description="解析巡检方案的实际目标服务器、巡检项和确认短语。执行前应先调用本工具。",
    scopes=["ops:read"],
    risk="low",
    category="inspection",
    write=False,
    ai_callable=True,
    ai_auto_callable=True,
    data_sensitivity="internal",
    output_masking=True,
    related_tools=["ops.inspection.profile.run", "ops.list_server_groups", "ops.list_servers"],
    input_schema={
        "type": "object",
        "properties": {"profile_id": {"type": "string"}},
        "required": ["profile_id"],
        "additionalProperties": False,
    },
)
def profile_preview(args: Dict[str, Any], ctx, db):
    from app.services.inspection_profiles import preview_profile

    return preview_profile(db, args.get("profile_id") or "")


@registry.register(
    name="ops.inspection.profile.run",
    title="执行巡检方案",
    description="按已保存巡检方案执行批量服务器巡检。需要使用 profile.preview 返回的 RUN INSPECTION 短语确认。",
    scopes=["ops:read", "ops:write"],
    risk="high",
    category="inspection_execute",
    write=True,
    requires_confirmation=True,
    requires_human_approval=True,
    ai_callable=True,
    ai_auto_callable=False,
    data_sensitivity="sensitive",
    output_masking=True,
    related_tools=["ops.inspection.profile.preview", "ops.inspection.get_run", "ops.inspection.generate_report"],
    input_schema={
        "type": "object",
        "properties": {
            "profile_id": {"type": "string"},
            "expected_count": {"type": "integer", "minimum": 0},
            "fingerprint": {"type": "string"},
            "confirm_text": {"type": "string", "description": "profile.preview 返回的 RUN INSPECTION 确认短语"},
        },
        "required": ["profile_id"],
        "additionalProperties": False,
    },
)
def profile_run(args: Dict[str, Any], ctx, db):
    from app.services.inspection_profiles import preview_profile, run_profile

    profile_id = args.get("profile_id") or ""
    if not args.get("confirm_text"):
        preview = preview_profile(db, profile_id)
        return {
            **preview,
            "status": "confirmation_required",
            "next_actions": [
                {
                    "tool": "ops.inspection.profile.run",
                    "arguments": {
                        "profile_id": profile_id,
                        "expected_count": preview.get("eligible_count") or 0,
                        "fingerprint": (preview.get("confirmation") or {}).get("fingerprint"),
                        "confirm_text": (preview.get("confirmation") or {}).get("confirm_text"),
                    },
                    "description": "After user approval, call with the exact confirmation phrase.",
                }
            ],
        }
    result = run_profile(db, profile_id, confirm_text=args.get("confirm_text") or "", created_by=_actor(ctx))
    run_ids = result.get("run_ids") or []
    if run_ids:
        result["next_actions"] = [
            {"tool": "ops.inspection.get_run", "arguments": {"run_id": run_ids[0]}, "description": "Fetch the first inspection run detail."},
            {"tool": "ops.inspection.list_issues", "arguments": {"limit": 100}, "description": "Review generated inspection issues."},
        ]
    return result


@registry.register(
    name="ops.inspection.profile.retry_issues",
    title="复巡未关闭风险服务器",
    description="基于未关闭巡检风险问题生成复巡目标，只重跑仍有 OPEN/PROCESSING 风险的服务器。需要使用返回的 RUN 短语确认。",
    scopes=["ops:read", "ops:write"],
    risk="high",
    category="inspection_execute",
    write=True,
    requires_confirmation=True,
    requires_human_approval=True,
    ai_callable=True,
    ai_auto_callable=False,
    data_sensitivity="sensitive",
    output_masking=True,
    related_tools=["ops.inspection.profile.preview", "ops.inspection.list_issues", "ops.inspection.get_run"],
    input_schema={
        "type": "object",
        "properties": {
            "profile_id": {"type": "string", "description": "复用的巡检方案，默认 daily-lite。"},
            "risk_level": {"type": "string", "description": "可选，仅复巡指定风险等级。"},
            "status": {"type": "string", "description": "可选，默认 OPEN + PROCESSING。"},
            "expected_count": {"type": "integer", "minimum": 0},
            "fingerprint": {"type": "string"},
            "confirm_text": {"type": "string", "description": "预览返回的 RUN 确认短语。"},
        },
        "additionalProperties": False,
    },
)
def profile_retry_issues(args: Dict[str, Any], ctx, db):
    from app.services.inspection_profiles import preview_issue_retry, run_issue_retry

    profile_id = args.get("profile_id") or "daily-lite"
    risk_level = args.get("risk_level") or ""
    status = args.get("status") or ""
    if not args.get("confirm_text"):
        preview = preview_issue_retry(db, profile_id=profile_id, risk_level=risk_level, status=status)
        return {
            **preview,
            "status": "confirmation_required",
            "next_actions": [
                {
                    "tool": "ops.inspection.profile.retry_issues",
                    "arguments": {
                        "profile_id": profile_id,
                        "risk_level": risk_level,
                        "status": status,
                        "expected_count": preview.get("eligible_count") or 0,
                        "fingerprint": (preview.get("confirmation") or {}).get("fingerprint"),
                        "confirm_text": (preview.get("confirmation") or {}).get("confirm_text"),
                    },
                    "description": "After user approval, rerun only servers with open inspection issues.",
                }
            ],
        }
    result = run_issue_retry(
        db,
        profile_id=profile_id,
        risk_level=risk_level,
        status=status,
        confirm_text=args.get("confirm_text") or "",
        created_by=_actor(ctx),
    )
    run_ids = result.get("run_ids") or []
    if run_ids:
        result["next_actions"] = [
            {"tool": "ops.inspection.get_run", "arguments": {"run_id": run_ids[0]}, "description": "Fetch the first retry inspection run detail."},
            {"tool": "ops.inspection.list_issues", "arguments": {"limit": 100}, "description": "Review remaining inspection issues after retry."},
        ]
    return result


@registry.register(
    name="ops.inspection.preview_servers_batch",
    title="预览批量服务器巡检",
    description="执行批量服务器巡检前，解析 server_ids / groups / all_servers 的实际目标、跳过项、批次计划和短中文确认短语。AI/MCP 应先调用本工具，再让用户确认。",
    scopes=["ops:read"],
    risk="low",
    category="inspection",
    write=False,
    ai_callable=True,
    ai_auto_callable=True,
    data_sensitivity="internal",
    output_masking=True,
    related_tools=["ops.list_server_groups", "ops.list_servers", "ops.inspection.run_servers_batch"],
    input_schema={
        "type": "object",
        "properties": {
            "server_ids": {"type": "array", "items": {"type": "string"}, "description": "显式选择的服务器 ID/name 列表。"},
            "groups": {"type": "array", "items": {"type": "string"}, "description": "按分组筛选服务器。"},
            "group": {"type": "string", "description": "单分组快捷字段。"},
            "categories": {"type": "array", "items": {"type": "string"}, "description": "巡检分类编码列表。"},
            "concurrency": {"type": "integer", "minimum": 1, "maximum": 20},
            "batch_size": {"type": "integer", "minimum": 1, "maximum": 20},
            "all_servers": {"type": "boolean"},
            "skip_disabled": {"type": "boolean"},
            "command_timeout_seconds": {"type": "integer", "minimum": 5, "maximum": 300},
            "run_timeout_seconds": {"type": "integer", "minimum": 30, "maximum": 1800},
        },
        "additionalProperties": False,
    },
)
def preview_servers_batch(args: Dict[str, Any], ctx, db):
    preview = _batch_preview(args or {})
    preview["next_actions"] = [
        {
            "tool": "ops.inspection.run_servers_batch",
            "arguments": {
                "server_ids": preview.get("eligible_ids") or [],
                "groups": preview.get("groups") or [],
                "categories": preview.get("categories") or [],
                "concurrency": (preview.get("execution_plan") or {}).get("concurrency"),
                "batch_size": (preview.get("execution_plan") or {}).get("batch_size"),
                "command_timeout_seconds": (preview.get("execution_plan") or {}).get("command_timeout_seconds"),
                "run_timeout_seconds": (preview.get("execution_plan") or {}).get("run_timeout_seconds"),
                "skip_disabled": preview.get("skip_disabled"),
                "all_servers": preview.get("all_servers"),
                "confirm_text": (preview.get("confirmation") or {}).get("confirm_text"),
            },
            "description": "用户确认后，用该中文确认短语执行批量巡检。",
        }
    ]
    return preview


@registry.register(
    name="ops.inspection.run_servers_batch",
    title="批量/按分组执行服务器巡检",
    description="批量执行服务器巡检。执行前请先调用 ops.inspection.preview_servers_batch，使用返回的短中文确认短语。支持按 server_ids / groups / group / all_servers 选择目标。",
    scopes=["ops:read", "ops:write"],
    risk="high",
    category="inspection_execute",
    write=True,
    requires_confirmation=True,
    requires_human_approval=True,
    ai_callable=True,
    ai_auto_callable=False,
    data_sensitivity="sensitive",
    output_masking=True,
    related_tools=["ops.inspection.preview_servers_batch", "ops.list_servers", "ops.list_server_groups", "ops.inspection.run_server"],
    input_schema={
        "type": "object",
        "properties": {
            "server_ids": {"type": "array", "items": {"type": "string"}, "description": "显式选择的服务器 ID/name 列表。"},
            "groups": {"type": "array", "items": {"type": "string"}, "description": "按分组筛选服务器（与 server_ids 可同时传入合并）。"},
            "group": {"type": "string", "description": "单分组快捷字段，等同 groups=[group]。"},
            "categories": {"type": "array", "items": {"type": "string"}, "description": "巡检分类编码列表：LOGIN_SECURITY / ACCOUNT_SECURITY / COMMAND_HISTORY / PROCESS_PORT / FIREWALL / DISK / SERVICE_STATUS / BACKUP"},
            "concurrency": {"type": "integer", "minimum": 1, "maximum": 20},
            "batch_size": {"type": "integer", "minimum": 1, "maximum": 20},
            "all_servers": {"type": "boolean"},
            "skip_disabled": {"type": "boolean"},
            "command_timeout_seconds": {"type": "integer", "minimum": 5, "maximum": 300},
            "run_timeout_seconds": {"type": "integer", "minimum": 30, "maximum": 1800},
            "generate_report": {"type": "boolean"},
            "confirm_text": {"type": "string", "description": "ops.inspection.preview_servers_batch 返回的短中文确认短语，例如 确认巡检 <fingerprint>。"},
        },
        "additionalProperties": False,
    },
)
def run_servers_batch(args: Dict[str, Any], ctx, db):
    from app.services.inspection_center import run_servers_batch_inspection as svc_run_batch
    args = args or {}
    preview = _validate_batch_confirmation(args)
    groups = preview.get("groups") or []
    result = _inspection_followup(svc_run_batch(
        db,
        server_ids=preview.get("eligible_ids") or [],
        categories=preview.get("categories") or [],
        generate_report=bool(args.get("generate_report", False)),
        created_by=_actor(ctx),
        concurrency=(preview.get("execution_plan") or {}).get("concurrency"),
        batch_size=(preview.get("execution_plan") or {}).get("batch_size"),
        command_timeout_seconds=(preview.get("execution_plan") or {}).get("command_timeout_seconds"),
        run_timeout_seconds=(preview.get("execution_plan") or {}).get("run_timeout_seconds"),
        skip_disabled=bool(preview.get("skip_disabled", True)),
        all_servers=False,
        groups=groups or None,
    ))
    result["preview"] = preview
    result["confirmation"] = preview.get("confirmation")
    return result


@registry.register(
    name="ops.inspection.run_project",
    title="执行项目巡检",
    description="发起项目巡检。AI/MCP token 不允许自动执行。",
    scopes=["ops:read", "ops:write"],
    risk="high",
    category="inspection_execute",
    write=True,
    requires_confirmation=True,
    requires_human_approval=True,
    ai_callable=True,
    ai_auto_callable=False,
    data_sensitivity="sensitive",
    output_masking=True,
    input_schema={"type": "object", "properties": {"project_id": {"type": "string"}, "categories": {"type": "array", "items": {"type": "string"}}, "confirm_text": {"type": "string", "description": "用户确认短语（例如 CONFIRM ops.inspection.run_project）"}}, "required": ["project_id"], "additionalProperties": False},
)
def run_project(args: Dict[str, Any], ctx, db):
    from app.services.inspection_center import run_project_inspection as svc_run_project
    return _inspection_followup(svc_run_project(db, project_id=args.get("project_id") or "", categories=args.get("categories") or [], created_by=_actor(ctx)))


@registry.register(
    name="ops.inspection.run_combined",
    title="执行项目综合巡检",
    description="项目综合巡检预留工具。当前返回项目巡检能力说明，不自动执行高风险动作。",
    scopes=["ops:read", "ops:write"],
    risk="high",
    category="inspection_execute",
    write=True,
    requires_confirmation=True,
    requires_human_approval=True,
    ai_callable=True,
    ai_auto_callable=False,
    data_sensitivity="sensitive",
    output_masking=True,
    input_schema={"type": "object", "properties": {"project_id": {"type": "string"}, "categories": {"type": "array", "items": {"type": "string"}}, "confirm_text": {"type": "string", "description": "用户确认短语（例如 CONFIRM ops.inspection.run_combined）"}}, "required": ["project_id"], "additionalProperties": False},
)
def run_combined(args: Dict[str, Any], ctx, db):
    from app.services.inspection_center import run_project_combined_inspection as svc_run_combined
    return _inspection_followup(svc_run_combined(db, project_id=args.get("project_id") or "", categories=args.get("categories") or [], created_by=_actor(ctx)))


# ============ 巡检项目配置管理（可选/可编辑/可调整） ============


@registry.register(
    name="ops.inspection.list_item_configs",
    title="查询巡检项目配置",
    description="查询可启用/禁用、可编辑、可调整顺序与规则关联的巡检项目（巡检项）。供 AI 决定要运行哪些巡检项。",
    scopes=["ops:read"],
    risk="low",
    category="inspection_config",
    write=False,
    ai_callable=True,
    ai_auto_callable=True,
    data_sensitivity="internal",
    output_masking=True,
    recommended_use_cases=["巡检项管理", "AI 调整巡检范围"],
    related_tools=["ops.inspection.update_item_config", "ops.inspection.toggle_item_config"],
    input_schema={
        "type": "object",
        "properties": {
            "scope_type": {"type": "string", "enum": ["SERVER", "PROJECT", "COMBINED"]},
            "include_disabled": {"type": "boolean"},
        },
        "additionalProperties": False,
    },
)
def list_item_configs(args: Dict[str, Any], ctx, db):
    from app.services.inspection_item_config import list_item_configs as svc
    items = svc(
        db,
        scope_type=args.get("scope_type") or "SERVER",
        include_disabled=bool(args.get("include_disabled", True)),
    )
    return {"items": items, "total": len(items)}


@registry.register(
    name="ops.inspection.get_item_config",
    title="查看巡检项目配置",
    description="按 ID 查看单个巡检项目配置详情，包含关联规则。",
    scopes=["ops:read"],
    risk="low",
    category="inspection_config",
    write=False,
    ai_callable=True,
    ai_auto_callable=True,
    data_sensitivity="internal",
    output_masking=True,
    input_schema={"type": "object", "properties": {"item_id": {"type": "string"}}, "required": ["item_id"], "additionalProperties": False},
)
def get_item_config(args: Dict[str, Any], ctx, db):
    from app.services.inspection_item_config import get_item_config as svc
    item = svc(db, args.get("item_id") or "")
    return {"item": item}


@registry.register(
    name="ops.inspection.update_item_config",
    title="更新巡检项目配置",
    description="更新巡检项目（巡检项）的名称、描述、是否启用、顺序、配置等。AI/MCP token 可调用以调整巡检范围。",
    scopes=["ops:read", "ops:write"],
    risk="medium",
    category="inspection_config",
    write=True,
    requires_confirmation=True,
    requires_human_approval=False,
    ai_callable=True,
    ai_auto_callable=False,
    data_sensitivity="internal",
    output_masking=True,
    input_schema={
        "type": "object",
        "properties": {
            "item_id": {"type": "string"},
            "item_name": {"type": "string"},
            "description": {"type": "string"},
            "enabled": {"type": "boolean"},
            "sort_order": {"type": "integer"},
            "config_json": {"type": "object"},
            "confirm_text": {"type": "string", "description": "用户确认短语（例如 CONFIRM ops.inspection.update_item_config）"},
        },
        "required": ["item_id"],
        "additionalProperties": False,
    },
)
def update_item_config(args: Dict[str, Any], ctx, db):
    from app.services.inspection_item_config import update_item_config as svc
    item_id = args.get("item_id") or ""
    payload = {k: v for k, v in args.items() if k not in {"item_id", "confirm_text"}}
    result = svc(db, item_id, payload)
    if not result:
        return {"item": None, "summary": f"项目 {item_id} 不存在"}
    return {"item": result, "summary": f"已更新巡检项目 {result.get('item_code')}"}


@registry.register(
    name="ops.inspection.toggle_item_config",
    title="启用/禁用巡检项目",
    description="快速切换巡检项目的启用状态。",
    scopes=["ops:read", "ops:write"],
    risk="medium",
    category="inspection_config",
    write=True,
    requires_confirmation=True,
    requires_human_approval=False,
    ai_callable=True,
    ai_auto_callable=False,
    data_sensitivity="internal",
    output_masking=True,
    input_schema={"type": "object", "properties": {"item_id": {"type": "string"}, "confirm_text": {"type": "string", "description": "用户确认短语（例如 CONFIRM ops.inspection.toggle_item_config）"}}, "required": ["item_id"], "additionalProperties": False},
)
def toggle_item_config(args: Dict[str, Any], ctx, db):
    from app.services.inspection_item_config import toggle_item_config as svc
    item_id = args.get("item_id") or ""
    result = svc(db, item_id)
    if not result:
        return {"item": None, "summary": f"项目 {item_id} 不存在"}
    return {"item": result, "summary": f"已{'启用' if result.get('enabled') else '禁用'} {result.get('item_code')}"}


@registry.register(
    name="ops.inspection.update_item_rules",
    title="调整巡检项目关联规则",
    description="调整巡检项目与巡检规则的多对多关联（可启用/禁用/排序）。",
    scopes=["ops:read", "ops:write"],
    risk="medium",
    category="inspection_config",
    write=True,
    requires_confirmation=True,
    requires_human_approval=False,
    ai_callable=True,
    ai_auto_callable=False,
    data_sensitivity="internal",
    output_masking=True,
    input_schema={
        "type": "object",
        "properties": {
            "item_id": {"type": "string"},
            "rules": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "rule_code": {"type": "string"},
                        "enabled": {"type": "boolean"},
                        "sort_order": {"type": "integer"},
                        "config_override": {"type": "object"},
                    },
                },
            },
            "confirm_text": {"type": "string", "description": "用户确认短语（例如 CONFIRM ops.inspection.update_item_rules）"},
        },
        "required": ["item_id", "rules"],
        "additionalProperties": False,
    },
)
def update_item_rules(args: Dict[str, Any], ctx, db):
    from app.services.inspection_item_config import update_item_rules as svc
    item_id = args.get("item_id") or ""
    rules = args.get("rules") or []
    result = svc(db, item_id, rules)
    if not result:
        return {"item": None, "summary": f"项目 {item_id} 不存在"}
    return {"item": result, "summary": f"已更新 {len(rules)} 条规则关联"}


@registry.register(
    name="ops.inspection.get_run_raw_output",
    title="获取巡检记录原始数据",
    description="获取巡检记录的原始巡检项执行数据（raw_output），包含命令输出、结果描述、风险等级和证据快照。",
    scopes=["ops:read"],
    risk="medium",
    category="inspection",
    write=False,
    ai_callable=True,
    ai_auto_callable=True,
    data_sensitivity="sensitive",
    output_masking=True,
    recommended_use_cases=["巡检深度分析", "巡检证据回溯"],
    related_tools=["ops.inspection.get_run", "ops.inspection.summarize_run"],
    input_schema={"type": "object", "properties": {"run_id": {"type": "string"}}, "required": ["run_id"], "additionalProperties": False},
)
def get_run_raw_output(args: Dict[str, Any], ctx, db):
    from app.services.inspection_item_config import get_run_raw_output as svc
    items = svc(db, args.get("run_id") or "")
    return {"items": items, "total": len(items)}


# ============ 巡检记录 / 风险问题 清理（解决孤儿 RUNNING 记录 & 风险问题清理） ============


@registry.register(
    name="ops.inspection.delete_runs",
    title="删除巡检记录",
    description="按 run_id 列表删除巡检记录。运行中/等待中（RUNNING/PENDING）记录需传 force=True 才能删除（适用于清理后台执行器异常退出后遗留的孤儿记录）。同时按 delete_reports 控制是否级联删除未被其他记录引用的巡检报告。",
    scopes=["ops:read", "ops:write"],
    risk="high",
    category="inspection_execute",
    write=True,
    requires_confirmation=True,
    requires_human_approval=True,
    ai_callable=True,
    ai_auto_callable=False,
    data_sensitivity="sensitive",
    output_masking=True,
    recommended_use_cases=["清理孤儿巡检记录", "巡检台账维护"],
    related_tools=["ops.inspection.list_runs", "ops.inspection.delete_issue"],
    input_schema={
        "type": "object",
        "properties": {
            "run_ids": {"type": "array", "items": {"type": "string"}},
            "delete_reports": {"type": "boolean"},
            "force": {"type": "boolean", "description": "是否强制删除 RUNNING/PENDING 状态的记录"},
            "confirm_text": {"type": "string", "description": "用户确认短语（例如 CONFIRM ops.inspection.delete_runs）"},
        },
        "required": ["run_ids"],
        "additionalProperties": False,
    },
)
def delete_runs(args: Dict[str, Any], ctx, db):
    from app.services.inspection_center import delete_inspection_runs as svc
    run_ids = args.get("run_ids") or []
    if not isinstance(run_ids, list) or not run_ids:
        return {"summary": "run_ids 不能为空"}
    result = svc(
        db,
        run_ids=run_ids,
        delete_reports=bool(args.get("delete_reports", True)),
        force=bool(args.get("force", False)),
    )
    summary = f"已删除 {result.get('deleted', 0)} 条巡检记录（强制={result.get('deleted', 0) and args.get('force', False)}），删除报告 {result.get('deleted_reports', 0)} 份"
    if result.get("skipped"):
        summary += f"，跳过 {len(result['skipped'])} 条"
    return {"result": result, "summary": summary}


@registry.register(
    name="ops.inspection.delete_issue",
    title="删除风险问题",
    description="按 issue_id 硬删除单条风险问题。删除后不可恢复，仅用于清理历史脏数据或误报。",
    scopes=["ops:read", "ops:write"],
    risk="medium",
    category="inspection_execute",
    write=True,
    requires_confirmation=True,
    requires_human_approval=False,
    ai_callable=True,
    ai_auto_callable=False,
    data_sensitivity="sensitive",
    output_masking=True,
    recommended_use_cases=["清理误报风险", "历史脏数据维护"],
    related_tools=["ops.inspection.list_issues", "ops.inspection.update_issue"],
    input_schema={"type": "object", "properties": {"issue_id": {"type": "string"}, "confirm_text": {"type": "string", "description": "用户确认短语（例如 CONFIRM ops.inspection.delete_issue）"}}, "required": ["issue_id"], "additionalProperties": False},
)
def delete_issue(args: Dict[str, Any], ctx, db):
    from app.services.inspection_center import delete_issue as svc
    issue_id = args.get("issue_id") or ""
    result = svc(db, issue_id)
    return {"result": result, "summary": f"风险问题 {issue_id} 已删除"}
