from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.helpers import api_response, audit
from app.core.auth_v2 import require_auth
from app.db import get_db
from app.db.base import SessionLocal
from app.services import inspection_center as svc

router = APIRouter(prefix="/api/v2/inspection", tags=["巡检中心"])


class RunServerPayload(BaseModel):
    server_id: Optional[str] = Field(default=None)
    server_ids: Optional[List[str]] = Field(default=None)
    groups: Optional[List[str]] = Field(default=None, description="按分组筛选服务器（与 server_ids 二选一，可同时传入合并）。")
    group: Optional[str] = Field(default=None, description="单分组快捷字段，等同 groups=[group]。")
    categories: Optional[List[str]] = None
    generate_report: bool = False
    concurrency: Optional[int] = None
    batch_size: Optional[int] = None
    command_timeout_seconds: Optional[int] = None
    run_timeout_seconds: Optional[int] = None
    skip_disabled: bool = True
    all_servers: bool = False


class RunProjectPayload(BaseModel):
    project_id: Optional[str] = Field(default=None)
    categories: Optional[List[str]] = None
    include_server_summary: bool = True
    generate_report: bool = False


class UpdateIssuePayload(BaseModel):
    status: Optional[str] = None
    owner_id: Optional[str] = None
    suggestion: Optional[str] = None


class UpdateRulePayload(BaseModel):
    rule_code: Optional[str] = None
    rule_name: Optional[str] = None
    category: Optional[str] = None
    scope_type: Optional[str] = None
    risk_level: Optional[str] = None
    enabled: Optional[bool] = None
    deleted: Optional[bool] = None
    description: Optional[str] = None
    suggestion: Optional[str] = None
    rule_content: Optional[str] = None
    config: Optional[Dict[str, Any]] = None


class GenerateReportPayload(BaseModel):
    format: str = "md"
    title: str = ""


class GenerateRunsReportPayload(BaseModel):
    run_ids: List[str] = Field(default_factory=list)
    format: str = "md"
    title: str = ""


class DeleteRunsPayload(BaseModel):
    run_ids: List[str] = Field(default_factory=list)
    delete_reports: bool = True
    force: bool = False


class DeleteLedgerPayload(BaseModel):
    period: str = "daily"
    date_from: str = ""
    date_to: str = ""
    scope_type: str = ""
    server_id: str = ""
    project_id: str = ""
    status: str = ""
    delete_reports: bool = True
    force: bool = False


class PeriodicReportPayload(BaseModel):
    period: str = "daily"  # daily / weekly / monthly
    date_from: str = ""
    date_to: str = ""
    scope_type: str = ""
    format: str = "md"
    title: str = ""


class InspectionProfilePayload(BaseModel):
    profile_id: str = Field(default="")
    confirm_text: str = Field(default="")
    expected_count: Optional[int] = None
    fingerprint: str = Field(default="")


class ProjectRelationPayload(BaseModel):
    id: Optional[str] = None
    project_id: Optional[str] = None
    server_id: Optional[str] = None
    deploy_role: Optional[str] = "APP"
    deploy_path: Optional[str] = ""
    config_path: Optional[str] = ""
    log_path: Optional[str] = ""
    backup_path: Optional[str] = ""
    runtime_user: Optional[str] = ""
    main_port: Optional[str] = ""
    active: bool = True


def _payload_dict(payload: Any) -> Dict[str, Any]:
    """Normalize loose JSON payloads from old/new frontends.

    Older builds and browser caches may send serverIds/ids/servers, or even a raw
    array.  Accepting a loose payload here prevents FastAPI 422 validation errors
    and lets the API return actionable 400 messages only when the normalized
    server list is empty.
    """
    if payload is None:
        return {}
    if isinstance(payload, BaseModel):
        try:
            return payload.model_dump(exclude_none=True)
        except Exception:
            return payload.dict(exclude_none=True)
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, list):
        return {"server_ids": payload}
    return {}


def _normalize_string_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        parts = [value]
    elif isinstance(value, (list, tuple, set)):
        parts = list(value)
    else:
        parts = [value]
    result: List[str] = []
    seen = set()
    for item in parts:
        if isinstance(item, dict):
            raw = item.get("id") or item.get("server_id") or item.get("serverId") or item.get("name") or item.get("host") or item.get("ip")
        else:
            raw = item
        text = str(raw or "").strip()
        if text and text not in seen:
            result.append(text)
            seen.add(text)
    return result


def _normalize_run_server_payload(payload: Any) -> Dict[str, Any]:
    data = _payload_dict(payload)
    server_ids = (
        data.get("server_ids")
        or data.get("serverIds")
        or data.get("ids")
        or data.get("servers")
        or data.get("targets")
        or data.get("selectedServerIds")
        or data.get("selected_servers")
    )
    server_id = data.get("server_id") or data.get("serverId") or data.get("id")
    normalized_ids = _normalize_string_list(server_ids)
    if not normalized_ids and server_id:
        normalized_ids = _normalize_string_list(server_id)
    groups = data.get("groups") or data.get("group_names") or data.get("groupNames") or data.get("serverGroups")
    if not groups and data.get("group"):
        groups = [data.get("group")]
    normalized_groups = [str(g or "").strip() for g in (groups or []) if str(g or "").strip()]
    categories = data.get("categories") or data.get("category_codes") or data.get("categoryCodes") or []
    normalized_categories = _normalize_string_list(categories)
    generate_report = data.get("generate_report")
    if generate_report is None:
        generate_report = data.get("generateReport", False)
    all_servers = data.get("all_servers")
    if all_servers is None:
        all_servers = data.get("allServers") or data.get("selectAll") or data.get("all") or False
    skip_disabled = data.get("skip_disabled")
    if skip_disabled is None:
        skip_disabled = data.get("skipDisabled", True)
    return {
        "server_ids": normalized_ids,
        "server_id": normalized_ids[0] if normalized_ids else "",
        "groups": normalized_groups,
        "categories": normalized_categories or None,
        "generate_report": bool(generate_report),
        "all_servers": bool(all_servers),
        "skip_disabled": bool(skip_disabled),
        "concurrency": data.get("concurrency") or data.get("max_concurrency") or data.get("maxConcurrency"),
        "batch_size": data.get("batch_size") or data.get("batchSize"),
        "command_timeout_seconds": data.get("command_timeout_seconds") or data.get("commandTimeoutSeconds") or data.get("timeout_seconds") or data.get("timeoutSeconds"),
        "run_timeout_seconds": data.get("run_timeout_seconds") or data.get("runTimeoutSeconds"),
    }



def _run_server_background(run_id: str, server_id: str, categories: Optional[List[str]] = None, command_timeout_seconds: int | None = None, run_timeout_seconds: int | None = None):
    db = SessionLocal()
    try:
        svc.execute_server_inspection_run(db, run_id=run_id, server_id=server_id, categories=categories, command_timeout_seconds=command_timeout_seconds or svc.DEFAULT_COMMAND_TIMEOUT_SECONDS, run_timeout_seconds=run_timeout_seconds or svc.DEFAULT_RUN_TIMEOUT_SECONDS)
    finally:
        db.close()


def _run_servers_batch_background(run_specs: List[Dict[str, Any]], concurrency: int | None = None, batch_size: int | None = None, command_timeout_seconds: int | None = None, run_timeout_seconds: int | None = None):
    svc.execute_server_inspection_runs_batch(run_specs, concurrency=concurrency or svc.DEFAULT_BATCH_CONCURRENCY, batch_size=batch_size or svc.DEFAULT_BATCH_SIZE, command_timeout_seconds=command_timeout_seconds or svc.DEFAULT_COMMAND_TIMEOUT_SECONDS, run_timeout_seconds=run_timeout_seconds or svc.DEFAULT_RUN_TIMEOUT_SECONDS)


def _run_project_background(run_id: str, project_id: str, categories: Optional[List[str]] = None, include_server_summary: bool = True):
    db = SessionLocal()
    try:
        svc.execute_project_inspection_run(db, run_id=run_id, project_id=project_id, categories=categories, include_server_summary=include_server_summary)
    finally:
        db.close()


@router.get("/overview")
def overview(request: Request, db: Session = Depends(get_db)):
    require_auth(request, db)
    return api_response(data=svc.overview(db))


@router.get("/categories")
def categories(request: Request, db: Session = Depends(get_db)):
    require_auth(request, db)
    server_cats = svc.list_categories(db, scope="server")
    project_cats = svc.list_categories(db, scope="project")
    return api_response(data={"server": server_cats, "project": project_cats})


@router.get("/servers")
def servers(request: Request, db: Session = Depends(get_db)):
    require_auth(request, db)
    return api_response(data=svc.list_servers())


@router.get("/server-groups")
def server_groups(request: Request, db: Session = Depends(get_db)):
    """Return the aggregated server groups so the UI / MCP can filter inspections by group."""
    require_auth(request, db)
    return api_response(data=svc.list_server_groups())


@router.get("/projects")
def projects(request: Request, db: Session = Depends(get_db)):
    require_auth(request, db)
    return api_response(data=svc.list_projects_with_relations(db))


@router.get("/profiles")
def profiles(request: Request, include_disabled: bool = False, db: Session = Depends(get_db)):
    require_auth(request, db)
    from app.services.inspection_profiles import list_profiles

    return api_response(data=list_profiles(db, include_disabled=include_disabled))


@router.post("/profiles/preview")
def profile_preview(payload: Any = Body(default_factory=dict), request: Request = None, db: Session = Depends(get_db)):
    require_auth(request, db)
    data = _payload_dict(payload)
    profile_id = str(data.get("profile_id") or data.get("profileId") or "").strip()
    if not profile_id:
        raise HTTPException(status_code=400, detail="profile_id is required")
    from app.services.inspection_profiles import preview_profile

    result = preview_profile(db, profile_id)
    return api_response(data=result, message=result.get("summary") or "巡检方案预览已生成")


@router.post("/profiles/run")
def profile_run(payload: Any = Body(default_factory=dict), request: Request = None, db: Session = Depends(get_db)):
    user = require_auth(request, db)
    data = _payload_dict(payload)
    profile_id = str(data.get("profile_id") or data.get("profileId") or "").strip()
    if not profile_id:
        raise HTTPException(status_code=400, detail="profile_id is required")
    from app.services.inspection_profiles import run_profile

    result = run_profile(
        db,
        profile_id,
        confirm_text=str(data.get("confirm_text") or data.get("confirmText") or ""),
        created_by=user.get("username") or "",
    )
    audit("inspection.profile.run", "inspection", profile_id, f"user={user.get('username')} success={result.get('success')} failed={result.get('failed')} report={result.get('report', {}).get('id') if isinstance(result.get('report'), dict) else ''}")
    return api_response(data=result, message=result.get("summary") or "巡检方案执行完成")


@router.post("/profiles/retry-issues")
def profile_retry_issues(payload: Any = Body(default_factory=dict), request: Request = None, db: Session = Depends(get_db)):
    user = require_auth(request, db)
    data = _payload_dict(payload)
    profile_id = str(data.get("profile_id") or data.get("profileId") or "daily-lite").strip() or "daily-lite"
    risk_level = str(data.get("risk_level") or data.get("riskLevel") or "").strip()
    status = str(data.get("status") or "").strip()
    confirm_text = str(data.get("confirm_text") or data.get("confirmText") or "").strip()
    from app.services.inspection_profiles import preview_issue_retry, run_issue_retry

    if not confirm_text:
        result = preview_issue_retry(db, profile_id=profile_id, risk_level=risk_level, status=status)
        return api_response(data={**result, "status": "confirmation_required"}, message=result.get("summary") or "Issue retry preview generated")

    result = run_issue_retry(
        db,
        profile_id=profile_id,
        risk_level=risk_level,
        status=status,
        confirm_text=confirm_text,
        created_by=user.get("username") or "",
    )
    audit("inspection.profile.retry_issues", "inspection", profile_id, f"user={user.get('username')} runs={len(result.get('run_ids') or [])} report={result.get('report', {}).get('id') if isinstance(result.get('report'), dict) else ''}")
    return api_response(data=result, message=result.get("summary") or "Issue retry inspection completed")


@router.get("/project-server-relations")
def project_server_relations(request: Request, project_id: str = "", db: Session = Depends(get_db)):
    require_auth(request, db)
    return api_response(data=svc.list_project_server_relations(db, project_id=project_id))


@router.post("/project-server-relations")
def save_project_server_relation(payload: ProjectRelationPayload, request: Request, db: Session = Depends(get_db)):
    user = require_auth(request, db)
    result = svc.upsert_project_server_relation(db, payload.model_dump(exclude_none=False), updated_by=user.get("username") or "")
    audit("inspection.project_relation.save", "inspection", result.get("id", ""), f"user={user.get('username')} project={result.get('project_id')} server={result.get('server_id')}")
    return api_response(data=result, message="项目部署路径配置已保存")


@router.patch("/project-server-relations/{relation_id}")
def update_project_server_relation(relation_id: str, payload: ProjectRelationPayload, request: Request, db: Session = Depends(get_db)):
    user = require_auth(request, db)
    data = payload.model_dump(exclude_none=False)
    data["id"] = relation_id
    result = svc.upsert_project_server_relation(db, data, updated_by=user.get("username") or "")
    audit("inspection.project_relation.update", "inspection", result.get("id", ""), f"user={user.get('username')} project={result.get('project_id')} server={result.get('server_id')}")
    return api_response(data=result, message="项目部署路径配置已更新")


@router.post("/servers/{server_id}/start")
def start_server(server_id: str, payload: RunServerPayload, background_tasks: BackgroundTasks, request: Request, db: Session = Depends(get_db)):
    user = require_auth(request, db)
    target_server = server_id or payload.server_id or ""
    svc.assert_server_inspectable(target_server)
    run = svc.create_server_inspection_run(db, server_id=target_server, categories=payload.categories, created_by=user.get("username") or "")
    background_tasks.add_task(_run_server_background, run.id, target_server, payload.categories, payload.command_timeout_seconds, payload.run_timeout_seconds)
    audit("inspection.server.start", "inspection", run.id, f"user={user.get('username')} server={server_id or payload.server_id}")
    return api_response(data=svc.inspection_run_detail(db, run.id), message="服务器巡检已启动")


@router.post("/servers/batch-start")
def start_servers_batch(background_tasks: BackgroundTasks, request: Request, payload: Any = Body(default_factory=dict), db: Session = Depends(get_db)):
    user = require_auth(request, db)
    normalized = _normalize_run_server_payload(payload)
    resolved = svc.resolve_servers_for_inspection(
        normalized["server_ids"],
        all_servers=normalized.get("all_servers", False),
        skip_disabled=normalized.get("skip_disabled", True),
        groups=normalized.get("groups") or None,
    )
    unique = resolved["eligible_ids"]
    if not unique and not resolved.get("skipped"):
        raise HTTPException(status_code=400, detail="server_ids is required")
    runs = []
    run_specs = []
    for sid in unique:
        run = svc.create_server_inspection_run(db, server_id=sid, categories=normalized["categories"], created_by=user.get("username") or "")
        run_obj = svc.inspection_run_detail(db, run.id).get("run")
        runs.append(run_obj)
        run_specs.append({"run_id": run.id, "server_id": sid, "categories": normalized["categories"]})
    if run_specs:
        background_tasks.add_task(_run_servers_batch_background, run_specs, normalized.get("concurrency"), normalized.get("batch_size"), normalized.get("command_timeout_seconds"), normalized.get("run_timeout_seconds"))
    audit("inspection.server.batch_start", "inspection", "batch", f"user={user.get('username')} eligible={len(runs)} skipped={resolved.get('skipped_count', 0)}")
    summary = f"已启动 {len(runs)} 台在线/启用服务器巡检，跳过 {resolved.get('skipped_count', 0)} 台停用/离线服务器。"
    return api_response(data={"summary": summary, "runs": runs, "run_ids": [r.get("id") for r in runs if r], "skipped": resolved.get("skipped", []), "eligible": len(runs), "skipped_count": resolved.get("skipped_count", 0), "concurrency": normalized.get("concurrency") or svc.DEFAULT_BATCH_CONCURRENCY, "batch_size": normalized.get("batch_size") or svc.DEFAULT_BATCH_SIZE}, message="批量服务器巡检已启动")


@router.post("/projects/{project_id}/start")
def start_project(project_id: str, payload: RunProjectPayload, background_tasks: BackgroundTasks, request: Request, db: Session = Depends(get_db)):
    user = require_auth(request, db)
    run = svc.create_project_inspection_run(db, project_id=project_id or payload.project_id or "", categories=payload.categories, created_by=user.get("username") or "")
    background_tasks.add_task(_run_project_background, run.id, project_id or payload.project_id or "", payload.categories, payload.include_server_summary)
    audit("inspection.project.start", "inspection", run.id, f"user={user.get('username')} project={project_id or payload.project_id}")
    return api_response(data=svc.inspection_run_detail(db, run.id), message="项目巡检已启动")


@router.post("/servers/run")
def run_server_legacy(payload: RunServerPayload, request: Request, db: Session = Depends(get_db)):
    if payload.server_ids or payload.groups or payload.group:
        user = require_auth(request, db)
        result = svc.run_servers_batch_inspection(
            db,
            server_ids=payload.server_ids or [],
            categories=payload.categories,
            generate_report=payload.generate_report,
            created_by=user.get("username") or "",
            concurrency=payload.concurrency or svc.DEFAULT_BATCH_CONCURRENCY,
            batch_size=payload.batch_size or svc.DEFAULT_BATCH_SIZE,
            command_timeout_seconds=payload.command_timeout_seconds or svc.DEFAULT_COMMAND_TIMEOUT_SECONDS,
            run_timeout_seconds=payload.run_timeout_seconds or svc.DEFAULT_RUN_TIMEOUT_SECONDS,
            skip_disabled=payload.skip_disabled,
            all_servers=payload.all_servers,
            groups=(payload.groups or ([payload.group] if payload.group else None)),
        )
        audit("inspection.server.batch_run", "inspection", "batch", f"user={user.get('username')} total={result.get('total')} success={result.get('success')} groups={payload.groups or payload.group}")
        return api_response(data=result, message=result.get("summary") or "批量服务器巡检完成")
    return _run_server(payload.server_id or "", payload, request, db)


@router.post("/servers/{server_id}/run")
def run_server(server_id: str, payload: RunServerPayload, request: Request, db: Session = Depends(get_db)):
    return _run_server(server_id, payload, request, db)


@router.post("/servers/batch-run")
def run_servers_batch(request: Request, payload: Any = Body(default_factory=dict), db: Session = Depends(get_db)):
    user = require_auth(request, db)
    normalized = _normalize_run_server_payload(payload)
    server_ids = normalized["server_ids"]
    if not server_ids and not normalized.get("all_servers") and not normalized.get("groups"):
        raise HTTPException(status_code=400, detail="server_ids / groups / all_servers 至少需要传一个")
    result = svc.run_servers_batch_inspection(
        db,
        server_ids=server_ids,
        categories=normalized["categories"],
        generate_report=normalized["generate_report"],
        created_by=user.get("username") or "",
        concurrency=normalized.get("concurrency") or svc.DEFAULT_BATCH_CONCURRENCY,
        batch_size=normalized.get("batch_size") or svc.DEFAULT_BATCH_SIZE,
        command_timeout_seconds=normalized.get("command_timeout_seconds") or svc.DEFAULT_COMMAND_TIMEOUT_SECONDS,
        run_timeout_seconds=normalized.get("run_timeout_seconds") or svc.DEFAULT_RUN_TIMEOUT_SECONDS,
        skip_disabled=normalized.get("skip_disabled", True),
        all_servers=normalized.get("all_servers", False),
        groups=normalized.get("groups") or None,
    )
    audit("inspection.server.batch_run", "inspection", "batch", f"user={user.get('username')} total={result.get('total')} success={result.get('success')} groups={normalized.get('groups')}")
    return api_response(data=result, message=result.get("summary") or "批量服务器巡检完成")


def _run_server(server_id: str, payload: RunServerPayload, request: Request, db: Session):
    user = require_auth(request, db)
    result = svc.run_server_inspection(db, server_id=server_id or payload.server_id or "", categories=payload.categories, created_by=user.get("username") or "", command_timeout_seconds=payload.command_timeout_seconds or svc.DEFAULT_COMMAND_TIMEOUT_SECONDS, run_timeout_seconds=payload.run_timeout_seconds or svc.DEFAULT_RUN_TIMEOUT_SECONDS)
    if payload.generate_report:
        result["report"] = svc.generate_report_for_run(db, result.get("run", {}).get("id", ""), fmt="md", created_by=user.get("username") or "").get("report")
    audit("inspection.server.run", "inspection", result.get("run", {}).get("id", ""), f"user={user.get('username')} server={server_id or payload.server_id}")
    return api_response(data=result, message="服务器巡检完成")


@router.post("/projects/run")
def run_project_legacy(payload: RunProjectPayload, request: Request, db: Session = Depends(get_db)):
    return _run_project(payload.project_id or "", payload, request, db)


@router.post("/projects/{project_id}/run")
def run_project(project_id: str, payload: RunProjectPayload, request: Request, db: Session = Depends(get_db)):
    return _run_project(project_id, payload, request, db)


def _run_project(project_id: str, payload: RunProjectPayload, request: Request, db: Session):
    user = require_auth(request, db)
    result = svc.run_project_inspection(db, project_id=project_id or payload.project_id or "", categories=payload.categories, include_server_summary=payload.include_server_summary, created_by=user.get("username") or "")
    if payload.generate_report:
        result["report"] = svc.generate_report_for_run(db, result.get("run", {}).get("id", ""), fmt="md", created_by=user.get("username") or "").get("report")
    audit("inspection.project.run", "inspection", result.get("run", {}).get("id", ""), f"user={user.get('username')} project={project_id or payload.project_id}")
    return api_response(data=result, message="项目巡检完成")


@router.post("/projects/{project_id}/combined-run")
def run_project_combined(project_id: str, payload: RunProjectPayload, request: Request, db: Session = Depends(get_db)):
    user = require_auth(request, db)
    result = svc.run_project_combined_inspection(db, project_id=project_id or payload.project_id or "", categories=payload.categories, generate_report=payload.generate_report, created_by=user.get("username") or "")
    audit("inspection.project.combined_run", "inspection", result.get("run", {}).get("id", ""), f"user={user.get('username')} project={project_id or payload.project_id}")
    return api_response(data=result, message="项目综合巡检完成")


@router.get("/runs")
def runs(request: Request, scope_type: str = "", server_id: str = "", project_id: str = "", limit: int = 100, offset: int = 0, db: Session = Depends(get_db)):
    require_auth(request, db)
    return api_response(data=svc.list_runs(db, scope_type=scope_type, server_id=server_id, project_id=project_id, limit=limit, offset=offset))


@router.get("/ledger")
def ledger(request: Request, period: str = "daily", date_from: str = "", date_to: str = "", scope_type: str = "", server_id: str = "", project_id: str = "", status: str = "", limit: int = 500, offset: int = 0, db: Session = Depends(get_db)):
    require_auth(request, db)
    return api_response(data=svc.inspection_ledger(db, period=period, date_from=date_from, date_to=date_to, scope_type=scope_type, server_id=server_id, project_id=project_id, status=status, limit=limit, offset=offset))


@router.get("/reports/periodic-preview")
def periodic_report_preview(request: Request, period: str = "daily", date_from: str = "", date_to: str = "", scope_type: str = "", db: Session = Depends(get_db)):
    require_auth(request, db)
    return api_response(data=svc.inspection_periodic_report_payload(db, period=period, date_from=date_from, date_to=date_to, scope_type=scope_type))


@router.post("/reports/periodic")
def periodic_report(payload: PeriodicReportPayload, request: Request, db: Session = Depends(get_db)):
    user = require_auth(request, db)
    result = svc.generate_periodic_report(db, period=payload.period, date_from=payload.date_from, date_to=payload.date_to, scope_type=payload.scope_type, fmt=payload.format or "md", title=payload.title or "", created_by=user.get("username") or "")
    audit("inspection.report.periodic", "inspection", payload.period, f"user={user.get('username')} period={payload.period} report={result.get('report', {}).get('id')}")
    return api_response(data=result, message="巡检台账/报表已生成")


@router.get("/runs/{run_id}")
def run_detail(run_id: str, request: Request, db: Session = Depends(get_db)):
    require_auth(request, db)
    return api_response(data=svc.inspection_run_detail(db, run_id))


@router.delete("/runs/{run_id}")
def run_delete(run_id: str, request: Request, delete_reports: bool = True, force: bool = False, db: Session = Depends(get_db)):
    user = require_auth(request, db)
    result = svc.delete_inspection_runs(db, [run_id], delete_reports=delete_reports, force=force)
    audit("inspection.run.delete", "inspection", run_id, f"user={user.get('username')} deleted={result.get('deleted')} reports={result.get('deleted_reports')}")
    return api_response(data=result, message="巡检记录已删除")


@router.post("/runs/delete")
def runs_delete(payload: DeleteRunsPayload, request: Request, db: Session = Depends(get_db)):
    user = require_auth(request, db)
    result = svc.delete_inspection_runs(db, payload.run_ids, delete_reports=payload.delete_reports, force=payload.force)
    audit("inspection.runs.delete", "inspection", "batch", f"user={user.get('username')} requested={len(payload.run_ids or [])} deleted={result.get('deleted')} reports={result.get('deleted_reports')}")
    return api_response(data=result, message="巡检记录已批量删除")


@router.post("/ledger/delete")
def ledger_delete(payload: DeleteLedgerPayload, request: Request, db: Session = Depends(get_db)):
    user = require_auth(request, db)
    result = svc.delete_inspection_history(db, period=payload.period, date_from=payload.date_from, date_to=payload.date_to, scope_type=payload.scope_type, server_id=payload.server_id, project_id=payload.project_id, status=payload.status, delete_reports=payload.delete_reports, force=payload.force)
    audit("inspection.ledger.delete", "inspection", payload.period, f"user={user.get('username')} deleted={result.get('deleted')} reports={result.get('deleted_reports')}")
    return api_response(data=result, message="巡检台账历史已删除")


@router.post("/runs/report")
def runs_report(payload: GenerateRunsReportPayload, request: Request, db: Session = Depends(get_db)):
    user = require_auth(request, db)
    run_ids = [str(x).strip() for x in (payload.run_ids or []) if str(x).strip()]
    if not run_ids:
        raise HTTPException(status_code=400, detail="run_ids is required")
    result = svc.generate_report_for_runs(db, run_ids, fmt=payload.format or "md", title=payload.title or "", created_by=user.get("username") or "")
    audit("inspection.report.generate_many", "inspection", ",".join(run_ids[:10]), f"user={user.get('username')} count={len(run_ids)} report={result.get('report', {}).get('id')}")
    return api_response(data=result, message="巡检合并报告已生成")


@router.post("/runs/{run_id}/report")
def run_report(run_id: str, payload: GenerateReportPayload, request: Request, db: Session = Depends(get_db)):
    user = require_auth(request, db)
    result = svc.generate_report_for_run(db, run_id, fmt=payload.format or "md", title=payload.title or "", created_by=user.get("username") or "")
    audit("inspection.report.generate", "inspection", run_id, f"user={user.get('username')} report={result.get('report', {}).get('id')}")
    return api_response(data=result, message="巡检报告已生成")


@router.get("/issues")
def issues(request: Request, scope_type: str = "", risk_level: str = "", status: str = "", server_id: str = "", project_id: str = "", limit: int = 200, offset: int = 0, db: Session = Depends(get_db)):
    require_auth(request, db)
    return api_response(data=svc.list_issues(db, scope_type=scope_type, risk_level=risk_level, status=status, server_id=server_id, project_id=project_id, limit=limit, offset=offset))


@router.get("/issues/{issue_id}")
def issue_detail(issue_id: str, request: Request, db: Session = Depends(get_db)):
    require_auth(request, db)
    return api_response(data=svc.get_issue(db, issue_id))


@router.patch("/issues/{issue_id}")
def issue_update(issue_id: str, payload: UpdateIssuePayload, request: Request, db: Session = Depends(get_db)):
    user = require_auth(request, db)
    result = svc.update_issue(db, issue_id, payload.model_dump(exclude_none=True))
    audit("inspection.issue.update", "inspection_issue", issue_id, f"user={user.get('username')} status={result.get('status')}")
    return api_response(data=result, message="巡检问题已更新")


@router.delete("/issues/{issue_id}")
def issue_delete(issue_id: str, request: Request, db: Session = Depends(get_db)):
    user = require_auth(request, db)
    result = svc.delete_issue(db, issue_id)
    audit("inspection.issue.delete", "inspection_issue", issue_id, f"user={user.get('username')} deleted={result.get('deleted')}")
    return api_response(data=result, message="风险问题已删除")


@router.get("/evidence/{evidence_id}")
def evidence_detail(evidence_id: str, request: Request, db: Session = Depends(get_db)):
    require_auth(request, db)
    return api_response(data=svc.get_evidence(db, evidence_id))


@router.get("/rules")
def rules(request: Request, scope_type: str = "", category: str = "", enabled: Optional[bool] = None, include_deleted: bool = False, keyword: str = "", risk_level: str = "", limit: int = 0, offset: int = 0, db: Session = Depends(get_db)):
    require_auth(request, db)
    return api_response(data=svc.list_rules(db, scope_type=scope_type, category=category, enabled=enabled, include_deleted=include_deleted, keyword=keyword, risk_level=risk_level, limit=limit, offset=offset))


@router.get("/rules/{rule_code}")
def rule_detail(rule_code: str, request: Request, db: Session = Depends(get_db)):
    require_auth(request, db)
    return api_response(data=svc.get_rule(db, rule_code))


@router.post("/rules")
def rule_create(payload: UpdateRulePayload, request: Request, db: Session = Depends(get_db)):
    user = require_auth(request, db)
    result = svc.create_rule(db, payload.model_dump(exclude_none=True))
    audit("inspection.rule.create", "inspection_rule", result.get("rule_code", ""), f"user={user.get('username')}")
    return api_response(data=result, message="巡检规则已创建")


@router.patch("/rules/{rule_code}")
def rule_update(rule_code: str, payload: UpdateRulePayload, request: Request, db: Session = Depends(get_db)):
    user = require_auth(request, db)
    data = payload.model_dump(exclude_none=True)
    result = svc.update_rule(db, rule_code, data)
    audit("inspection.rule.update", "inspection_rule", rule_code, f"user={user.get('username')} new={result.get('rule_code')} enabled={result.get('enabled')} risk={result.get('risk_level')}")
    return api_response(data=result, message="巡检规则已更新")


@router.delete("/rules/{rule_code}")
def rule_delete(rule_code: str, request: Request, db: Session = Depends(get_db)):
    user = require_auth(request, db)
    result = svc.delete_rule(db, rule_code)
    audit("inspection.rule.delete", "inspection_rule", rule_code, f"user={user.get('username')} soft={result.get('soft_deleted')}")
    return api_response(data=result, message="巡检规则已删除")


# ============ 巡检项目配置管理（新增） ============

from app.services.inspection_item_config import (
    list_item_configs as svc_list_item_configs,
    get_item_config as svc_get_item_config,
    update_item_config as svc_update_item_config,
    toggle_item_config as svc_toggle_item_config,
    reorder_item_configs as svc_reorder_item_configs,
    update_item_rules as svc_update_item_rules,
    get_run_raw_output as svc_get_run_raw_output,
)


@router.get("/item-configs")
def item_configs_list(request: Request, scope_type: str = "SERVER", db: Session = Depends(get_db)):
    """获取巡检项目配置列表"""
    require_auth(request, db)
    configs = svc_list_item_configs(db, scope_type=scope_type)
    return api_response(data={"items": configs, "total": len(configs)})


@router.get("/item-configs/{item_id}")
def item_configs_get(item_id: str, request: Request, db: Session = Depends(get_db)):
    """获取单个巡检项目配置"""
    require_auth(request, db)
    cfg = svc_get_item_config(db, item_id)
    if not cfg:
        raise HTTPException(status_code=404, detail="巡检项目配置不存在")
    return api_response(data=cfg)


@router.put("/item-configs/{item_id}")
def item_configs_update(item_id: str, payload: dict, request: Request, db: Session = Depends(get_db)):
    """更新巡检项目配置"""
    user = require_auth(request, db)
    cfg = svc_update_item_config(db, item_id, payload)
    if not cfg:
        raise HTTPException(status_code=404, detail="巡检项目配置不存在")
    audit("inspection.item_config.update", "inspection_item_config", item_id, f"user={user.get('username')}")
    return api_response(data=cfg, message="巡检项目配置已更新")


@router.post("/item-configs/{item_id}/toggle")
def item_configs_toggle(item_id: str, request: Request, db: Session = Depends(get_db)):
    """启用/禁用巡检项目"""
    user = require_auth(request, db)
    cfg = svc_toggle_item_config(db, item_id)
    if not cfg:
        raise HTTPException(status_code=404, detail="巡检项目配置不存在")
    audit("inspection.item_config.toggle", "inspection_item_config", item_id, f"user={user.get('username')} enabled={cfg.get('enabled')}")
    return api_response(data=cfg, message=f"已{'启用' if cfg.get('enabled') else '禁用'}")


@router.put("/item-configs/reorder")
def item_configs_reorder(payload: dict, request: Request, db: Session = Depends(get_db)):
    """调整巡检项目顺序"""
    user = require_auth(request, db)
    scope_type = payload.get("scope_type", "SERVER")
    ordered_ids = payload.get("ordered_ids", [])
    svc_reorder_item_configs(db, scope_type, ordered_ids)
    audit("inspection.item_config.reorder", "inspection_item_config", scope_type, f"user={user.get('username')} count={len(ordered_ids)}")
    return api_response(message="顺序已更新")


@router.put("/item-configs/{item_id}/rules")
def item_configs_update_rules(item_id: str, payload: dict, request: Request, db: Session = Depends(get_db)):
    """更新巡检项目关联的规则"""
    user = require_auth(request, db)
    rules = payload.get("rules", [])
    cfg = svc_update_item_rules(db, item_id, rules)
    if not cfg:
        raise HTTPException(status_code=404, detail="巡检项目配置不存在")
    audit("inspection.item_config.update_rules", "inspection_item_config", item_id, f"user={user.get('username')} count={len(rules)}")
    return api_response(data=cfg, message="规则关联已更新")


@router.get("/runs/{run_id}/raw-output")
def runs_get_raw_output(run_id: str, request: Request, db: Session = Depends(get_db)):
    """获取巡检记录的原始输出数据"""
    require_auth(request, db)
    output = svc_get_run_raw_output(db, run_id)
    return api_response(data={"run_id": run_id, "items": output, "total": len(output)})



@router.get("/baselines")
def baselines(request: Request, scope_type: str = "", server_id: str = "", project_id: str = "", baseline_type: str = "", db: Session = Depends(get_db)):
    require_auth(request, db)
    return api_response(data=svc.list_baselines(db, scope_type=scope_type, server_id=server_id, project_id=project_id, baseline_type=baseline_type))


# ============ 巡检阈值（可配置） ============

@router.get("/thresholds")
def thresholds_get(request: Request, db: Session = Depends(get_db)):
    """获取所有巡检分类的阈值（默认值 + 已配置覆盖）。"""
    require_auth(request, db)
    return api_response(data=svc._load_thresholds(db))


@router.post("/thresholds")
def thresholds_save(payload: dict, request: Request, db: Session = Depends(get_db)):
    """保存指定分类的阈值到对应 InspectionItemConfig.config_json.thresholds。"""
    user = require_auth(request, db)
    category = (payload or {}).get("category")
    thresholds = (payload or {}).get("thresholds") or {}
    if not category or category not in svc.DEFAULT_THRESHOLDS:
        raise HTTPException(status_code=400, detail=f"不支持的分类: {category}")
    if not isinstance(thresholds, dict):
        raise HTTPException(status_code=400, detail="thresholds 必须是字典")
    # 找到该 category 的第一个启用的项目配置；如没有则记录日志后跳过
    from app.db.models import InspectionItemConfig
    cfg = db.query(InspectionItemConfig).filter(
        InspectionItemConfig.scope_type == "SERVER",
        InspectionItemConfig.category == category,
    ).order_by(InspectionItemConfig.sort_order).first()
    if not cfg:
        raise HTTPException(status_code=404, detail=f"未找到 {category} 分类的巡检项目配置")
    current = dict(cfg.config_json or {})
    current["thresholds"] = dict(thresholds)
    cfg.config_json = current
    db.commit()
    db.refresh(cfg)
    audit("inspection.threshold.update", "inspection_item_config", cfg.id, f"user={user.get('username')} category={category}")
    return api_response(data={"category": category, "thresholds": current["thresholds"]}, message="阈值已更新")


class SecurityDailyCollectPayload(BaseModel):
    report_date: str = Field(default="", description="采集指定日期 YYYY-MM-DD，缺省=今天")
    persist_risks: bool = Field(default=True)
    servers: List[str] = Field(default_factory=list,
                               description="指定要采集的服务器名称列表；空=所有已启用安全监控的在线服务器")


@router.get("/security-daily/reports")
def security_daily_reports(request: Request, report_date: str = "", server_name: str = "", group_by: str = "", limit: int = 200, offset: int = 0, db: Session = Depends(get_db)):
    """查询已采集的每日安全日报记录（安全日报巡检入口的历史展示）。

    group_by=date 时按执行日期聚合：每日期返回服务器清单、成功/失败数与风险汇总，
    供「按执行日期展示 + 报告查看/下载」使用。
    """
    require_auth(request, db)
    from app.db.models import SecurityDailyReport

    q = db.query(SecurityDailyReport)
    if report_date:
        q = q.filter(SecurityDailyReport.report_date == report_date)
    if server_name:
        q = q.filter(SecurityDailyReport.server_name == server_name)

    if group_by == "date":
        rows = q.order_by(
            SecurityDailyReport.report_date.desc(),
            SecurityDailyReport.created_at.desc(),
        ).all()
        grouped: Dict[str, List[Any]] = {}
        for r in rows:
            grouped.setdefault(r.report_date, []).append(r)
        items = []
        for date, group in sorted(grouped.items(), key=lambda kv: kv[0], reverse=True):
            ok = [r for r in group if r.status == "ok"]
            high = sum(1 for r in group if r.max_risk == "HIGH")
            medium = sum(1 for r in group if r.max_risk == "MEDIUM")
            login_failures_total = sum(int((r.summary or {}).get("login_failures") or 0) for r in ok)
            banned_ips_total = sum(int((r.summary or {}).get("banned_ips") or 0) for r in ok)
            max_risk = "HIGH" if high else "MEDIUM" if medium else ("LOW" if ok else "NONE")
            artifact_id = next((r.artifact_id for r in group if r.artifact_id), None)
            items.append({
                "report_date": date,
                "server_count": len(group),
                "ok_count": len(ok),
                "failed_count": len(group) - len(ok),
                "high_count": high,
                "medium_count": medium,
                "login_failures_total": login_failures_total,
                "banned_ips_total": banned_ips_total,
                "max_risk": max_risk,
                "artifact_id": artifact_id,
                "servers": [r.server_name for r in group],
                "created_at": max((r.created_at.isoformat() if r.created_at else "" for r in group), default=None),
            })
        return api_response(data={"items": items, "total": len(items)})

    total = q.count()
    rows = q.order_by(
        SecurityDailyReport.report_date.desc(),
        SecurityDailyReport.created_at.desc(),
    ).offset(offset).limit(limit).all()
    items = [{
        "id": r.id,
        "server_name": r.server_name,
        "report_date": r.report_date,
        "status": r.status,
        "max_risk": r.max_risk,
        "summary": r.summary or {},
        "artifact_id": r.artifact_id,
        "error": r.error,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    } for r in rows]
    return api_response(data={"items": items, "total": total})


@router.post("/security-daily/collect")
def security_daily_collect(payload: SecurityDailyCollectPayload, request: Request, db: Session = Depends(get_db)):
    """单独触发一次安全日报巡检：提交后台任务采集日报、写入风险台账并归档，立即返回任务 ID。

    采集为多台服务器现场 SSH 执行的耗时操作，改为统一任务中心后台执行，
    避免阻塞请求线程；进度与结果可在任务中心查看。
    """
    user = require_auth(request, db)
    from app.services.job_service import enqueue_tool_job
    from app.services.tool_context import ToolContext
    from app.services.tool_registry import register_builtin_tools, registry

    register_builtin_tools()
    tool = registry.get("ops.inspection.run_security_daily")
    ctx = ToolContext(
        username=user.get("username") or "",
        user_id=user.get("id") or "",
        role="operator" if user.get("is_admin") else "user",
        is_admin=bool(user.get("is_admin")),
        can_deploy=bool(user.get("can_deploy")),
        auth_type="session",
        scopes=["*"],
        allow_write=True,
        allow_prod=bool(user.get("is_admin")),
        client_name="ops-web-session",
        ip_address=request.client.host if request.client else "",
        user_agent=request.headers.get("user-agent", "") if request else "",
    )
    args = {
        "report_date": payload.report_date or None,
        "persist_risks": payload.persist_risks,
        "servers": payload.servers or None,
        "confirm_text": "CONFIRM ops.inspection.run_security_daily",
    }
    job = enqueue_tool_job(db, tool_def=tool, arguments=args, ctx=ctx, policy_result={})
    audit("inspection.security_daily.collect", "inspection", job.get("id", ""),
          f"user={user.get('username')} job={job.get('id')}")
    return api_response(
        data={"job_id": job.get("id"), "job": job, "status": job.get("status"),
              "task_center_url": f"/tasks?kind=tool&job={job.get('id')}"},
        message="安全日报巡检已提交后台任务，可在任务中心查看进度",
    )
