from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, UploadFile, File, Form
from fastapi.responses import JSONResponse, Response as FastAPIResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.helpers import api_response, audit
from app.core.auth_v2 import require_auth, require_admin, normalize_role
from app.db import get_db
from app.db.models import ToolToken, ToolCallLog, ToolPlan, ToolPlanEvent
from app.services.tool_context import ToolContext
from app.services.tool_policy import get_capability_settings, save_capability_settings
from app.services.risk_policy import risk_policy_manifest
from app.services.tool_registry import register_builtin_tools, registry, bump_capability_version as _bump_capability_version
from app.services.tool_token import create_tool_token, validate_tool_token, token_to_dict
from app.mcp.server import _mcp_tool_payload, _from_mcp_tool_name
from app.domain.tooling.manifest import build_tool_manifest
from app.agent.prompts import prompt_registry


tools_router = APIRouter(prefix="/api/v2/tools", tags=["AI 工具接入"])
mcp_router = APIRouter(prefix="/api/v2/mcp", tags=["MCP-like 工具接入"])
capabilities_router = APIRouter(prefix="/api/v2", tags=["AI 能力发现"])

MCP_SERVER_NAME = "ops-capability-server"
MCP_SERVER_VERSION = "1.3.0"
MCP_PROTOCOL_VERSIONS = ["2025-06-18", "2025-03-26"]
MCP_CANONICAL_ENDPOINTS = {
    "streamable_http": "POST /api/v2/mcp",
    "capabilities": "GET /api/v2/capabilities",
    "tool_catalog": "GET /api/v2/tools?format=mcp",
    "tool_call": "POST /api/v2/tools/call",
    "resources": "GET /api/v2/mcp/resources",
    "prompts": "GET /api/v2/mcp/prompts",
    "legacy_gateway": "GET /api/v2/mcp/legacy/capabilities",
}


def _mcp_resource_items() -> List[Dict[str, str]]:
    return [
        {"uri": "ops://capabilities", "name": "Capability Manifest", "description": "OPS capability manifest", "mimeType": "application/json"},
        {"uri": "ops://systems", "name": "OPS Systems", "description": "OPS system list", "mimeType": "application/json"},
        {"uri": "ops://deployments/recent", "name": "Recent Deployments", "description": "Recent deployment history", "mimeType": "application/json"},
        {"uri": "ops://tools", "name": "Tool Catalog", "description": "OPS tool catalog", "mimeType": "application/json"},
        {"uri": "ops://ai-diagnostics", "name": "AI Diagnostics Analysis", "description": "Read-only AI diagnostic analysis and safe MCP toolchain", "mimeType": "application/json"},
        {"uri": "ops://operation-chains", "name": "Recent Operation Chains", "description": "Read-only audit replay index for OPS/MCP/AI actions", "mimeType": "application/json"},
        {"uri": "ops://reports", "name": "Report Center", "description": "Generated OPS diagnostic/release/audit reports", "mimeType": "application/json"},
        {"uri": "ops://db/exports", "name": "Database Exports", "description": "Read-only database query export artifacts", "mimeType": "application/json"},
        {"uri": "ops://servers", "name": "Servers", "description": "服务器资产摘要", "mimeType": "application/json"},
        {"uri": "ops://projects", "name": "Projects", "description": "项目/系统资产摘要", "mimeType": "application/json"},
        {"uri": "ops://status/overview", "name": "Status Overview", "description": "状态中心总览", "mimeType": "application/json"},
        {"uri": "ops://risks/open", "name": "Open Risks", "description": "未闭环风险", "mimeType": "application/json"},
        {"uri": "ops://inspection/recent", "name": "Recent Inspection Runs", "description": "最近巡检记录", "mimeType": "application/json"},
        {"uri": "ops://diagnosis/recent", "name": "Recent Diagnosis Runs", "description": "最近诊断记录", "mimeType": "application/json"},
        {"uri": "ops://reports/recent", "name": "Recent Reports", "description": "最近报告", "mimeType": "application/json"},
        {"uri": "ops://backups/status", "name": "Backup Status", "description": "备份状态摘要", "mimeType": "application/json"},
        {"uri": "ops://deployments/failed", "name": "Failed Deployments", "description": "最近失败发布", "mimeType": "application/json"},
        {"uri": "ops://tool-risk-policy", "name": "Tool Risk Policy", "description": "工具风险策略", "mimeType": "application/json"},
        {"uri": "ops://ai-workflows", "name": "AI Workflows", "description": "AI 工作流目录", "mimeType": "application/json"},
    ]


def _mcp_prompt_items() -> List[Dict[str, Any]]:
    return [
        {"name": "ops_release_plan", "description": "Create a safe OPS release plan. Trigger: user asks for release/deployment/上线/发布.", "arguments": [{"name": "request", "description": "Natural language release request", "required": True}]},
        {"name": "ops_failure_analysis", "description": "Analyze a failed OPS deployment. Trigger: user asks why deployment failed/发布失败/报错.", "arguments": [{"name": "deployment_id", "description": "Deployment id", "required": True}]},
        {"name": "ops_diagnostic_triage", "description": "Triage OPS runtime issues with read-only MCP tools. Trigger: user asks for system health check/诊断/排查问题.", "arguments": [{"name": "focus", "description": "Optional focus such as frontend, mcp, backup, deploy", "required": False}]},
        {"name": "ops_operation_replay", "description": "Replay an OPS/MCP/AI operation chain from audit evidence. Trigger: user asks to replay/review past operations/操作回放.", "arguments": [{"name": "chain_id", "description": "tool:/job:/plan:/deployment:/audit: id", "required": True}]},
        {"name": "ops_report_brief", "description": "Summarize a generated OPS report artifact. Trigger: user asks for report summary/报告摘要/查看报告.", "arguments": [{"name": "report_id", "description": "Report artifact id", "required": True}]},
        {"name": "ops_db_export_request", "description": "Plan a safe database workflow: query, export, or maintain tables. Trigger: user says 导出CSV/查数据/查询表/列出表/查看表结构/导出Excel. IMPORTANT: use this prompt BEFORE writing any Python scripts for database access.", "arguments": [{"name": "request", "description": "Natural language query/export request", "required": True}]},
        {"name": "ops_server_management", "description": "Manage OPS server assets. Trigger: user asks to add/edit/configure/list servers/服务器管理/配置服务器.", "arguments": [{"name": "request", "description": "Natural language server management request", "required": True}]},
        {"name": "ops_backup_workflow", "description": "Safe OPS database backup workflow (list, create, verify, restore). Trigger: user asks for backup/restore/备份/恢复.", "arguments": [{"name": "request", "description": "Natural language backup request", "required": True}]},
        {"name": "ops_project_health_brief", "description": "Generate a lightweight single-project health brief with facts/inferences/recommendations/evidence. Trigger: 项目健康/项目状态/最近是否正常. 不依赖服务器侧 Agent。", "arguments": [{"name": "project_id", "description": "Project/system identifier", "required": True}]},
        {"name": "ops_risk_triage", "description": "Triage open risks without executing remediation. Trigger: 风险分流/优先处理/整改计划.", "arguments": [{"name": "request", "description": "Natural language risk triage request", "required": False}]},
        {"name": "ops_monthly_ops_report", "description": "Generate a monthly OPS report using status, diagnosis, inspection, backup, risk and report context.", "arguments": [{"name": "month", "description": "YYYY-MM", "required": False}]},
    ]


class ToolCallPayload(BaseModel):
    tool: str
    arguments: Dict[str, Any] = {}


class CreateToolTokenPayload(BaseModel):
    name: str
    scopes: List[str] = ["ops:read"]
    allow_write: bool = False
    allow_prod: bool = False
    expires_in_days: int = 90




class UpdateToolTokenPayload(BaseModel):
    name: Optional[str] = None
    scopes: Optional[List[str]] = None
    allow_write: Optional[bool] = None
    allow_prod: Optional[bool] = None
    expires_in_days: Optional[int] = None
    revoke: Optional[bool] = None


class UpdateSettingsPayload(BaseModel):
    settings: Dict[str, Any]


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _ctx_from_user(request: Request, user: Dict[str, Any]) -> ToolContext:
    role = normalize_role(user.get("role"), bool(user.get("is_admin")))
    return ToolContext(
        username=user.get("username") or "",
        user_id=user.get("id") or "",
        role=role,
        is_admin=bool(user.get("is_admin")),
        can_deploy=bool(user.get("can_deploy")),
        auth_type="session",
        scopes=["*"],
        allow_write=True,
        allow_prod=bool(user.get("is_admin")),
        client_name="ops-web-session",
        ip_address=request.client.host if request.client else "",
        user_agent=request.headers.get("user-agent", ""),
    )


def _ctx_from_token(request: Request, db: Session, raw_token: str) -> ToolContext:
    token = validate_tool_token(db, raw_token)
    return ToolContext(
        username=token.owner,
        role="operator" if token.allow_write else "readonly",
        is_admin=False,
        can_deploy=token.allow_write,
        auth_type="tool_token",
        token_id=token.id,
        token_name=token.name,
        token_owner=token.owner,
        scopes=token.scopes or [],
        allow_write=bool(token.allow_write),
        allow_prod=bool(token.allow_prod),
        client_name=token.name,
        ip_address=request.client.host if request.client else "",
        user_agent=request.headers.get("user-agent", ""),
    )


def get_tool_context(request: Request, db: Session) -> ToolContext:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return _ctx_from_token(request, db, auth.split(" ", 1)[1].strip())
    user = require_auth(request, db)
    return _ctx_from_user(request, user)


def _plan_to_dict(p: ToolPlan) -> Dict[str, Any]:
    return {
        "id": p.id,
        "plan_type": p.plan_type,
        "status": p.status,
        "created_by": p.created_by,
        "source_tool": p.source_tool,
        "system": p.system,
        "service": p.service,
        "environment": p.environment,
        "servers": p.servers or [],
        "package_name": p.package_name,
        "risk_level": p.risk_level,
        "confirm_text": p.confirm_text,
        "related_deployment_id": p.related_deployment_id,
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
    }


@tools_router.get("")
def list_tools(
    request: Request,
    response: Response,
    category: str = "",
    risk: str = "",
    include_disabled: bool = False,
    include_schema: bool = True,
    limit: int = 100,
    cursor: int = 0,
    output_format: str = Query("native", alias="format"),
    db: Session = Depends(get_db),
):
    ctx = get_tool_context(request, db)
    register_builtin_tools()
    all_tools = [registry._tools[name] for name in sorted(registry._tools.keys())]
    listed = build_tool_manifest(
        all_tools,
        output_format=output_format,
        include_schema=include_schema,
        category=category,
        risk=risk,
        limit=limit,
        cursor=cursor,
    )
    version = registry.capability_version(db, ctx)
    response.headers["ETag"] = f'"capability-{version}"'
    response.headers["X-Capability-Version"] = version
    data = {
        **listed,
        "settings": get_capability_settings(db),
        "capability_version": version,
        "categories": registry.list_categories(),
    }
    return api_response(data=data)


@tools_router.get("/detail/{tool_name:path}")
def get_tool_detail(tool_name: str, request: Request, db: Session = Depends(get_db)):
    ctx = get_tool_context(request, db)
    register_builtin_tools()
    tool = registry.get(tool_name)
    policy = registry.evaluate_policy(tool, ctx, db)
    return api_response(data=tool.to_public_dict(include_schema=True, policy=policy))


@capabilities_router.get("/capabilities")
def get_capabilities(
    request: Request,
    response: Response,
    category: str = "",
    include_disabled: bool = False,
    include_schema: bool = True,
    limit: int = 200,
    cursor: int = 0,
    output_format: str = Query("native", alias="format"),
    db: Session = Depends(get_db),
):
    ctx = get_tool_context(request, db)
    register_builtin_tools()
    data = registry.describe_capabilities(
        db,
        ctx,
        category=category,
        include_schema=include_schema,
        include_disabled=include_disabled,
        output_format=output_format,
        limit=limit,
        cursor=cursor,
    )
    version = data.get("server", {}).get("capability_version") or registry.capability_version(db, ctx)
    response.headers["ETag"] = f'"capability-{version}"'
    response.headers["X-Capability-Version"] = version
    return api_response(data=data)


@capabilities_router.get("/prompts")
def get_prompts(request: Request, db: Session = Depends(get_db)):
    require_auth(request, db)
    return api_response(data=prompt_registry())


@tools_router.post("/call")
def call_tool(payload: ToolCallPayload, request: Request, db: Session = Depends(get_db)):
    ctx = get_tool_context(request, db)
    register_builtin_tools()
    result = registry.call(db, payload.tool, payload.arguments or {}, ctx)
    return api_response(data=result)


@tools_router.post("/call/stream")
async def call_tool_stream(payload: ToolCallPayload, request: Request, db: Session = Depends(get_db)):
    import asyncio
    import json as _json
    from fastapi.responses import StreamingResponse

    ctx = get_tool_context(request, db)
    register_builtin_tools()
    tool = registry.get(payload.tool)
    streamable = getattr(tool, "streamable", False) if tool else False

    if not streamable:
        result = registry.call(db, payload.tool, payload.arguments or {}, ctx)
        return api_response(data=result)

    async def _stream():
        queue: asyncio.Queue = asyncio.Queue(maxsize=256)

        def _on_chunk(chunk: Any):
            try:
                queue.put_nowait(chunk)
            except asyncio.QueueFull:
                pass

        def _run_sync():
            try:
                result = registry.call(db, payload.tool, payload.arguments or {}, ctx, stream_callback=_on_chunk)
                queue.put_nowait({"event": "done", "data": result})
            except Exception as e:
                queue.put_nowait({"event": "error", "data": {"error": str(e)}})

        import threading
        t = threading.Thread(target=_run_sync, daemon=True)
        t.start()

        while True:
            try:
                chunk = await asyncio.wait_for(queue.get(), timeout=60.0)
            except asyncio.TimeoutError:
                yield f"event: error\ndata: {_json.dumps({'error': 'timeout'})}\n\n"
                break
            event_type = chunk.get("event", "chunk") if isinstance(chunk, dict) else "chunk"
            data = chunk.get("data", chunk) if isinstance(chunk, dict) else chunk
            yield f"event: {event_type}\ndata: {_json.dumps(data, ensure_ascii=False, default=str)}\n\n"
            if event_type in ("done", "error"):
                break

    return StreamingResponse(_stream(), media_type="text/event-stream")


@tools_router.post("/packages/upload")
async def upload_package_by_tool_token(
    request: Request,
    file: UploadFile = File(...),
    system: str = Form(""),
    service: str = Form(""),
    overwrite: bool = Form(False),
    db: Session = Depends(get_db),
):
    ctx = get_tool_context(request, db)
    register_builtin_tools()
    # Reuse policy enforcement through the registered tool, but save via multipart
    # to avoid base64 overhead for local stdio MCP clients.
    from app.services.tool_policy import enforce_tool_policy
    from app.services.package_retention import save_package_fileobj
    tool = registry.get("ops.upload_package")
    enforce_tool_policy(tool, {"system": system, "service": service}, ctx, db)
    try:
        await file.seek(0)
    except Exception:
        pass
    meta = save_package_fileobj(
        db,
        filename=file.filename or "uploaded_package",
        fileobj=file.file,
        system=system or "",
        service=service or "",
        uploaded_by=ctx.username or ctx.token_owner or "tool",
        overwrite=bool(overwrite),
    )
    audit("tool.package.upload", "file", meta.get("package_name") or meta.get("name"), f"client={ctx.client_name} owner={ctx.token_owner or ctx.username} size={meta.get('size_bytes')} sha256={meta.get('sha256')}")
    return api_response(data={
        "ok": True,
        "tool": "ops.upload_package",
        "result": {**meta, "uploaded_via": "multipart"},
        "summary": "Package uploaded to OPS File Center",
        "risk": "high",
        "blocked": False,
    }, message="Package uploaded")




@tools_router.get("/risk-policy")
def get_risk_policy(request: Request, db: Session = Depends(get_db)):
    ctx = get_tool_context(request, db)
    register_builtin_tools()
    tools = registry.list_tools(db, ctx, include_disabled=True, include_schema=False, limit=500).get("tools", [])
    return api_response(data={
        **risk_policy_manifest(),
        "settings": get_capability_settings(db),
        "tools": [
            {
                "name": item.get("name"),
                "title": item.get("title"),
                "risk": item.get("risk"),
                "category": item.get("category"),
                "write": item.get("write"),
                "requires_confirmation": item.get("requires_confirmation"),
                "available": item.get("available"),
                "blocked_reason": item.get("blocked_reason"),
                "risk_policy": (item.get("policy") or {}).get("risk_policy"),
            }
            for item in tools
        ],
    })


@tools_router.get("/settings")
def get_tool_settings(request: Request, db: Session = Depends(get_db)):
    require_admin(request, db)
    return api_response(data=get_capability_settings(db))


@tools_router.put("/settings")
def update_tool_settings(payload: UpdateSettingsPayload, request: Request, db: Session = Depends(get_db)):
    user = require_admin(request, db)
    settings = save_capability_settings(db, payload.settings or {})
    audit("tool.settings.update", "capability_server", "settings", f"user={user.get('username')}")
    _bump_capability_version(db)
    return api_response(data=settings)


@tools_router.get("/tokens")
def list_tokens(request: Request, response: Response, since_id: str = "", limit: int = 50, db: Session = Depends(get_db)):
    user = require_auth(request, db)
    q = db.query(ToolToken)
    if not user.get("is_admin"):
        q = q.filter(ToolToken.owner == user.get("username"))
    if since_id:
        q = q.filter(ToolToken.id < since_id)
    q = q.order_by(ToolToken.created_at.desc()).limit(min(max(limit, 1), 500))
    rows = q.all()
    from app.api.helpers import compute_list_etag, check_etag_not_modified
    etag = compute_list_etag(rows, "tokens")
    not_modified = check_etag_not_modified(request, etag)
    if not_modified:
        return not_modified
    response.headers["ETag"] = etag
    return api_response(data=[token_to_dict(x) for x in rows])


@tools_router.post("/tokens")
def create_token(payload: CreateToolTokenPayload, request: Request, db: Session = Depends(get_db)):
    user = require_auth(request, db)
    # Non-admin users can create read/plan/precheck tokens only.
    scopes = payload.scopes or ["ops:read"]
    allow_write = bool(payload.allow_write)
    allow_prod = bool(payload.allow_prod)
    if not user.get("is_admin"):
        dangerous = {"deploy:execute", "config:write", "server:write", "package:write", "package:cleanup", "db:write", "*"}
        if allow_write or allow_prod or any(s in dangerous or s.endswith(":*") for s in scopes):
            raise HTTPException(status_code=403, detail="Only admin can create write/prod tool tokens")
    created = create_tool_token(
        db,
        name=payload.name,
        owner=user.get("username") or "",
        scopes=scopes,
        allow_write=allow_write,
        allow_prod=allow_prod,
        expires_in_days=payload.expires_in_days,
    )
    audit("tool.token.create", "tool_token", payload.name, f"user={user.get('username')} scopes={','.join(scopes)}")
    _bump_capability_version(db)
    return api_response(data={"token": created["token"], "record": token_to_dict(created["record"])}, message="Token created; copy it now, it will not be shown again")




@tools_router.patch("/tokens/{token_id}")
def update_token(token_id: str, payload: UpdateToolTokenPayload, request: Request, db: Session = Depends(get_db)):
    user = require_auth(request, db)
    token = db.query(ToolToken).filter(ToolToken.id == token_id).first()
    if not token:
        raise HTTPException(status_code=404, detail="Token not found")
    is_admin = bool(user.get("is_admin"))
    if not is_admin and token.owner != user.get("username"):
        raise HTTPException(status_code=403, detail="Cannot edit another user's token")

    old_scopes = list(token.scopes or [])
    new_scopes = payload.scopes if payload.scopes is not None else old_scopes
    new_allow_write = bool(payload.allow_write) if payload.allow_write is not None else bool(token.allow_write)
    new_allow_prod = bool(payload.allow_prod) if payload.allow_prod is not None else bool(token.allow_prod)

    if not is_admin:
        dangerous = {"deploy:execute", "config:write", "server:write", "package:write", "package:cleanup", "db:write", "*"}
        if new_allow_write or new_allow_prod or any(s in dangerous or str(s).endswith(":*") for s in new_scopes):
            raise HTTPException(status_code=403, detail="Only admin can grant write/prod or dangerous tool token scopes")

    if payload.name is not None:
        name = payload.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="Token name cannot be empty")
        token.name = name[:128]
    if payload.scopes is not None:
        token.scopes = [str(x).strip() for x in (payload.scopes or []) if str(x).strip()] or ["ops:read"]
    if payload.allow_write is not None:
        token.allow_write = new_allow_write
    if payload.allow_prod is not None:
        token.allow_prod = new_allow_prod
    if payload.expires_in_days is not None:
        days = int(payload.expires_in_days or 0)
        if days <= 0:
            token.expires_at = None
        else:
            token.expires_at = _utcnow() + timedelta(days=min(max(days, 1), 3650))
    if payload.revoke is not None:
        token.revoked_at = _utcnow() if payload.revoke else None

    db.commit()
    db.refresh(token)
    audit("tool.token.update", "tool_token", token.name, f"user={user.get('username')} scopes={','.join(token.scopes or [])} allow_write={token.allow_write} allow_prod={token.allow_prod}")
    _bump_capability_version(db)
    return api_response(data=token_to_dict(token), message="Token updated")

@tools_router.delete("/tokens/{token_id}")
def revoke_token(token_id: str, request: Request, db: Session = Depends(get_db)):
    user = require_auth(request, db)
    token = db.query(ToolToken).filter(ToolToken.id == token_id).first()
    if not token:
        raise HTTPException(status_code=404, detail="Token not found")
    if not user.get("is_admin") and token.owner != user.get("username"):
        raise HTTPException(status_code=403, detail="Cannot revoke another user's token")
    token.revoked_at = _utcnow()
    db.commit()
    audit("tool.token.revoke", "tool_token", token.name, f"user={user.get('username')} owner={token.owner}")
    _bump_capability_version(db)
    return api_response(message="Token revoked")


@tools_router.get("/calls")
def list_tool_call_logs(request: Request, response: Response, limit: int = 100, tool: str = "", status: str = "", since_id: str = "", db: Session = Depends(get_db)):
    user = require_auth(request, db)
    q = db.query(ToolCallLog)
    if not user.get("is_admin"):
        q = q.filter((ToolCallLog.username == user.get("username")) | (ToolCallLog.token_owner == user.get("username")))
    if tool:
        q = q.filter(ToolCallLog.tool_name == tool)
    if status:
        q = q.filter(ToolCallLog.status == status)
    if since_id:
        q = q.filter(ToolCallLog.id < since_id)
    rows = q.order_by(ToolCallLog.created_at.desc()).limit(min(max(limit, 1), 500)).all()
    from app.api.helpers import compute_list_etag, check_etag_not_modified
    etag = compute_list_etag(rows, "call_logs")
    not_modified = check_etag_not_modified(request, etag)
    if not_modified:
        return not_modified
    response.headers["ETag"] = etag
    return api_response(data=[
        {
            "id": r.id,
            "tool_name": r.tool_name,
            "client_name": r.client_name,
            "token_owner": r.token_owner,
            "username": r.username,
            "input_args": r.input_args,
            "result_preview": r.result_preview,
            "status": r.status,
            "risk_level": r.risk_level,
            "blocked_reason": r.blocked_reason,
            "related_plan_id": r.related_plan_id,
            "related_deployment_id": r.related_deployment_id,
            "related_job_id": r.related_job_id,
            "duration_ms": r.duration_ms,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ])


@tools_router.get("/plans")
def list_tool_plans(request: Request, limit: int = 100, plan_type: str = "", status: str = "", db: Session = Depends(get_db)):
    user = require_auth(request, db)
    q = db.query(ToolPlan)
    if not user.get("is_admin"):
        q = q.filter(ToolPlan.created_by == user.get("username"))
    if plan_type:
        q = q.filter(ToolPlan.plan_type == plan_type)
    if status:
        q = q.filter(ToolPlan.status == status)
    rows = q.order_by(ToolPlan.created_at.desc()).limit(min(max(limit, 1), 500)).all()
    return api_response(data=[_plan_to_dict(x) for x in rows])


@tools_router.get("/plans/{plan_id}")
def get_tool_plan(plan_id: str, request: Request, db: Session = Depends(get_db)):
    user = require_auth(request, db)
    plan = db.query(ToolPlan).filter(ToolPlan.id == plan_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    if not user.get("is_admin") and plan.created_by != user.get("username"):
        raise HTTPException(status_code=403, detail="Cannot view another user's plan")
    events = db.query(ToolPlanEvent).filter(ToolPlanEvent.plan_id == plan_id).order_by(ToolPlanEvent.created_at).all()
    data = _plan_to_dict(plan)
    data.update({
        "payload": plan.payload or {},
        "confirmation": plan.confirmation or {},
        "precheck": plan.precheck or {},
        "diff": plan.diff or {},
        "events": [
            {"id": e.id, "event_type": e.event_type, "actor": e.actor, "message": e.message, "payload": e.payload or {}, "created_at": e.created_at.isoformat() if e.created_at else None}
            for e in events
        ],
    })
    return api_response(data=data)



@tools_router.get("/plans/{plan_id}/events")
def get_tool_plan_events(plan_id: str, request: Request, db: Session = Depends(get_db)):
    user = require_auth(request, db)
    plan = db.query(ToolPlan).filter(ToolPlan.id == plan_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    if not user.get("is_admin") and plan.created_by != user.get("username"):
        raise HTTPException(status_code=403, detail="Cannot view another user's plan")
    events = db.query(ToolPlanEvent).filter(ToolPlanEvent.plan_id == plan_id).order_by(ToolPlanEvent.created_at).all()
    return api_response(data=[
        {"id": e.id, "event_type": e.event_type, "actor": e.actor, "message": e.message, "payload": e.payload or {}, "created_at": e.created_at.isoformat() if e.created_at else None}
        for e in events
    ])


@tools_router.post("/plans/{plan_id}/cancel")
def cancel_tool_plan(plan_id: str, request: Request, db: Session = Depends(get_db)):
    user = require_auth(request, db)
    plan = db.query(ToolPlan).filter(ToolPlan.id == plan_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    if not user.get("is_admin") and plan.created_by != user.get("username"):
        raise HTTPException(status_code=403, detail="Cannot cancel another user's plan")
    if plan.status in {"executed", "applied"}:
        raise HTTPException(status_code=400, detail=f"Plan already {plan.status}")
    plan.status = "canceled"
    plan.updated_at = _utcnow()
    db.commit()
    from app.services.audit_writer import record_plan_event_async
    record_plan_event_async(plan.id, "canceled", user.get("username"), "工具计划已取消", {})
    audit("tool.plan.cancel", "tool_plan", plan_id, f"user={user.get('username')}")
    return api_response(data=_plan_to_dict(plan), message="Plan canceled")


# ---------------------------------------------------------------------------
# MCP Streamable HTTP compatibility endpoint
# ---------------------------------------------------------------------------
# Some clients (Trace Solo / Cursor remote MCP / Claude compatible clients)
# expect the configured MCP URL itself to accept JSON-RPC POST messages, e.g.
# POST /api/v2/mcp with method=initialize, tools/list, tools/call, etc.  The
# older OPS implementation exposed helper REST endpoints under /api/v2/mcp/*
# only, which works for our stdio bridge but not for direct Remote HTTP MCP.


def _mcp_jsonrpc_result(mid: Any, result: Dict[str, Any]) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def _mcp_jsonrpc_error(mid: Any, code: int, message: str) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def _mcp_initialize_result(params: Dict[str, Any]) -> Dict[str, Any]:
    requested_version = str((params or {}).get("protocolVersion") or "2025-06-18")
    return {
        "protocolVersion": requested_version,
        "serverInfo": {"name": MCP_SERVER_NAME, "version": MCP_SERVER_VERSION},
        "capabilities": {
            "tools": {"listChanged": True},
            "resources": {"subscribe": False, "listChanged": True},
            "prompts": {"listChanged": True},
        },
    }


def _mcp_http_tools_list(request: Request, db: Session, params: Dict[str, Any]) -> Dict[str, Any]:
    ctx = get_tool_context(request, db)
    register_builtin_tools()
    limit = int((params or {}).get("limit") or 100)
    cursor_raw = (params or {}).get("cursor")
    try:
        cursor = int(cursor_raw or 0)
    except Exception:
        cursor = 0
    listed = registry.list_tools(
        db,
        ctx,
        category=str((params or {}).get("category") or ""),
        include_disabled=False,
        include_schema=True,
        output_format="mcp",
        limit=max(1, min(limit, 200)),
        cursor=max(0, cursor),
    )
    tools = [_mcp_tool_payload(t) for t in (listed.get("tools") or [])]
    result: Dict[str, Any] = {"tools": tools}
    next_cursor = (listed.get("pagination") or {}).get("next_cursor")
    if next_cursor is not None:
        result["nextCursor"] = str(next_cursor)
    return result


def _mcp_http_call_tool(request: Request, db: Session, params: Dict[str, Any]) -> Dict[str, Any]:
    ctx = get_tool_context(request, db)
    register_builtin_tools()
    tool_name = _from_mcp_tool_name(str((params or {}).get("name") or (params or {}).get("tool") or ""))
    args = (params or {}).get("arguments") or {}
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


def _mcp_http_resources_list(request: Request, db: Session) -> Dict[str, Any]:
    get_tool_context(request, db)
    return {"resources": _mcp_resource_items()}


def _mcp_http_resource_read(request: Request, db: Session, params: Dict[str, Any]) -> Dict[str, Any]:
    ctx = get_tool_context(request, db)
    register_builtin_tools()
    uri = (params or {}).get("uri") or ""
    generated_at = _utcnow().isoformat()
    if uri == "ops://capabilities":
        data = registry.describe_capabilities(db, ctx, include_schema=True, include_disabled=False)
    elif uri == "ops://systems":
        data = registry.call(db, "ops.list_systems", {}, ctx)["result"]
    elif uri == "ops://deployments/recent":
        data = registry.call(db, "ops.list_deployments", {"limit": 20}, ctx)["result"]
    elif uri == "ops://tools":
        data = registry.list_tools(db, ctx, include_schema=True).get("tools", [])
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
            data = {"summary": f"诊断摘要暂不可用：{exc}", "items": []}
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
                {"tool": "ops.workflow.generate_project_health_brief", "description": "项目健康分析"},
                {"tool": "ops.workflow.analyze_failed_deploy", "description": "发布失败分析"},
                {"tool": "ops.workflow.inspect_project_security", "description": "项目安全巡检分析"},
                {"tool": "ops.workflow.triage_open_risks", "description": "未闭环风险分流"},
                {"tool": "ops.workflow.generate_monthly_ops_report", "description": "月度运维报告"},
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


def _mcp_http_prompts_list(request: Request, db: Session) -> Dict[str, Any]:
    get_tool_context(request, db)
    return {"prompts": _mcp_prompt_items()}


def _mcp_http_prompt_get(request: Request, db: Session, params: Dict[str, Any]) -> Dict[str, Any]:
    get_tool_context(request, db)
    name = (params or {}).get("name")
    args = (params or {}).get("arguments") or {}
    if name == "ops_release_plan":
        text = (
            "CRITICAL RULE: Always use OPS tools for OPS-related tasks. Never write standalone scripts instead. "
            "Use OPS tools to query real systems (ops.list_systems), services (ops.list_services), "
            "environments (ops.list_environments), servers (ops.list_servers), and packages (ops.list_packages). "
            "Create a deploy plan with ops.create_deploy_plan and run precheck with ops.run_precheck first. "
            "Do not execute deployment unless explicitly confirmed. "
            "User request: " + str(args.get("request") or "")
        )
    elif name == "ops_failure_analysis":
        text = (
            "Use ops.get_deployment_report, ops.get_deployment_tasks, and ops.get_deployment_logs "
            "to analyze failure. Provide recommendations only, do not execute rollback. "
            "deployment_id=" + str(args.get("deployment_id") or "")
        )
    elif name == "ops_diagnostic_triage":
        text = (
            "Use only read-only OPS tools to triage runtime issues: ops.analyze_diagnostics, "
            "ops.run_diagnostics, ops.get_recent_errors, ops.get_build_info, ops.list_jobs, "
            "and deploy read tools when relevant. Do not execute deploy, rollback, restore, delete, SQL write, "
            "or terminal actions. Provide evidence, likely cause, and guarded next steps. focus="
            + str(args.get("focus") or "general")
        )
    elif name == "ops_operation_replay":
        text = (
            "Use only read-only audit tools to replay this OPS/MCP/AI operation chain: "
            "ops.get_operation_chain and ops.list_operation_chains. Summarize what happened, who initiated it, "
            "which risk gates applied, whether a job was queued, and the final outcome. Do not execute or retry anything. chain_id="
            + str(args.get("chain_id") or "")
        )
    elif name == "ops_report_brief":
        text = (
            "Use only read-only report tools: ops.get_report, ops.list_reports, ops.get_report_summary. "
            "Summarize the report metadata, explain what evidence it contains, and suggest safe next steps. "
            "Do not execute deploy, rollback, restore, delete, SQL write, or terminal actions. report_id="
            + str(args.get("report_id") or "")
        )
    elif name == "ops_db_export_request":
        text = (
            "CRITICAL RULE: Always use OPS database tools for database tasks. NEVER write standalone Python scripts "
            "for database queries, exports, or table listings. ALL database access MUST go through OPS tools.\n\n"
            "Natural language to tool mapping:\n"
            "- 'list tables / 有哪些表 / 列出表' -> ops.db.list_tables\n"
            "- 'show table structure / 查看表结构 / 字段' -> ops.db.describe_table\n"
            "- 'query data / 查询数据 / SELECT' -> ops.db.query_readonly\n"
            "- 'export as CSV/Excel / 导出CSV/导出数据' -> ops.db.export_query_result\n"
            "- 'modify data / 修改数据 / UPDATE/DELETE' -> ops.db.preview_dml then ops.db.execute_dml\n\n"
            "Workflow: tables -> describe -> query -> export. Never skip steps when table/field names are uncertain.\n"
            "User request=" + str(args.get("request") or "")
        )
    elif name == "ops_server_management":
        text = (
            "Help user manage OPS server assets using OPS tools. Available server tools:\n"
            "- ops.list_servers: list configured servers\n"
            "- ops.check_disk: check server disk usage\n"
            "- ops.check_process: check if a service process is running\n"
            "- ops.list_service_directory: list service directory files\n"
            "- ops.tail_service_log: read recent service log lines\n"
            "- ops.run_health_check: run service health checks\n"
            "Do not write scripts or SSH directly - use OPS server tools. "
            "User request=" + str(args.get("request") or "")
        )
    elif name == "ops_backup_workflow":
        text = (
            "Help user manage OPS database backups safely. Available backup tools:\n"
            "- ops.list_backups: list existing backups\n"
            "- ops.verify_backup: verify backup integrity (read-only)\n"
            "- ops.create_backup: create a new backup (requires confirmation)\n"
            "- ops.restore_backup: restore from backup (CRITICAL - requires confirm_text)\n"
            "- ops.delete_backup: delete a backup (HIGH risk - requires confirm_text)\n"
            "Always verify before restore. Always create a safety backup before restore.\n"
            "User request=" + str(args.get("request") or "")
        )
    elif name == "ops_project_health_brief":
        text = (
            "Use MCP resources first: ops://projects, ops://status/overview, ops://risks/open, ops://inspection/recent. 当前为单项目轻量模式，不依赖 ops://agents。 "
            "Then call ops.workflow.generate_project_health_brief. Output facts, inferences, recommendations and evidence separately. "
            "Do not execute deploy, rollback, DML, restore, delete, shell, or remediation actions. project_id="
            + str(args.get("project_id") or "")
        )
    elif name == "ops_risk_triage":
        text = (
            "Use ops.risk.list and ops.workflow.triage_open_risks to prioritize open risks. "
            "Only generate a plan; do not update risk status, verify, ignore, deploy, rollback, or run shell. request="
            + str(args.get("request") or "")
        )
    elif name == "ops_monthly_ops_report":
        text = (
            "Use ops.workflow.generate_monthly_ops_report. The output must separate facts, inferences, recommendations and evidence. "
            "Generate reports only from saved evidence and do not execute high-risk actions. month="
            + str(args.get("month") or "")
        )
    else:
        raise ValueError("Prompt not found")
    return {"description": name, "messages": [{"role": "user", "content": {"type": "text", "text": text}}]}


def _handle_mcp_http_message(msg: Dict[str, Any], request: Request, db: Session) -> Dict[str, Any] | None:
    mid = msg.get("id")
    method = msg.get("method")
    params = msg.get("params") or {}
    if method in {"notifications/initialized", "notifications/cancelled"}:
        return None
    try:
        if method == "initialize":
            result = _mcp_initialize_result(params)
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = _mcp_http_tools_list(request, db, params)
        elif method == "tools/call":
            result = _mcp_http_call_tool(request, db, params)
        elif method == "resources/list":
            result = _mcp_http_resources_list(request, db)
        elif method == "resources/read":
            result = _mcp_http_resource_read(request, db, params)
        elif method == "prompts/list":
            result = _mcp_http_prompts_list(request, db)
        elif method == "prompts/get":
            result = _mcp_http_prompt_get(request, db, params)
        else:
            return _mcp_jsonrpc_error(mid, -32601, f"Unsupported method: {method}")
        return _mcp_jsonrpc_result(mid, result)
    except HTTPException as exc:
        return _mcp_jsonrpc_error(mid, -32000, str(exc.detail))
    except Exception as exc:
        return _mcp_jsonrpc_error(mid, -32000, str(exc))


@mcp_router.get("")
def mcp_http_root(request: Request, db: Session = Depends(get_db)):
    # Helpful for browser/manual checks. Real MCP Streamable HTTP clients use
    # JSON-RPC POST on this same endpoint.
    ctx = get_tool_context(request, db)
    register_builtin_tools()
    return JSONResponse(content={
        "name": MCP_SERVER_NAME,
        "version": MCP_SERVER_VERSION,
        "transport": "streamable-http-jsonrpc",
        "protocolVersions": MCP_PROTOCOL_VERSIONS,
        "canonical_endpoints": MCP_CANONICAL_ENDPOINTS,
        "capability_version": registry.capability_version(db, ctx),
        "resources_count": len(_mcp_resource_items()),
        "prompts_count": len(_mcp_prompt_items()),
        "usage": "POST JSON-RPC messages to this endpoint, e.g. initialize, tools/list, tools/call.",
    })


@mcp_router.get("/", include_in_schema=False)
def mcp_http_root_slash(request: Request, db: Session = Depends(get_db)):
    return mcp_http_root(request, db)


@mcp_router.post("")
async def mcp_streamable_http_endpoint(request: Request, db: Session = Depends(get_db)):
    try:
        payload = await request.json()
    except Exception as exc:
        return JSONResponse(content=_mcp_jsonrpc_error(None, -32700, f"Invalid JSON: {exc}"), status_code=400)

    if isinstance(payload, list):
        responses = []
        for item in payload:
            if not isinstance(item, dict):
                responses.append(_mcp_jsonrpc_error(None, -32600, "Invalid request"))
                continue
            response = _handle_mcp_http_message(item, request, db)
            if response is not None:
                responses.append(response)
        if not responses:
            return FastAPIResponse(status_code=202)
        return JSONResponse(content=responses, media_type="application/json")

    if not isinstance(payload, dict):
        return JSONResponse(content=_mcp_jsonrpc_error(None, -32600, "Invalid request"), status_code=400)
    response = _handle_mcp_http_message(payload, request, db)
    if response is None:
        return FastAPIResponse(status_code=202)
    return JSONResponse(content=response, media_type="application/json")


@mcp_router.post("/", include_in_schema=False)
async def mcp_streamable_http_endpoint_slash(request: Request, db: Session = Depends(get_db)):
    return await mcp_streamable_http_endpoint(request, db)


@mcp_router.get("/manifest")
def mcp_manifest(request: Request, db: Session = Depends(get_db)):
    ctx = get_tool_context(request, db)
    register_builtin_tools()
    return api_response(data={
        "name": MCP_SERVER_NAME,
        "version": MCP_SERVER_VERSION,
        "description": "OPS MCP Capability Server. Models run in external clients; OPS exposes discoverable, audited tools/resources/prompts.",
        "canonical_endpoints": MCP_CANONICAL_ENDPOINTS,
        "tools_endpoint": "/api/v2/tools",
        "call_endpoint": "/api/v2/tools/call",
        "capabilities_endpoint": "/api/v2/capabilities",
        "mcp_endpoint": "/api/v2/mcp",
        "legacy_gateway_endpoint": "/api/v2/mcp/legacy",
        "capability_version": registry.capability_version(db, ctx),
        "resources": _mcp_resource_items(),
        "prompts": _mcp_prompt_items(),
        "tools": registry.list_tools(db, ctx).get("tools", []),
    })


@mcp_router.get("/tools")
def mcp_tools(
    request: Request,
    response: Response,
    category: str = "",
    limit: int = 100,
    cursor: int = 0,
    db: Session = Depends(get_db),
):
    return list_tools(request, response, category=category, limit=limit, cursor=cursor, output_format="mcp", db=db)




@mcp_router.get("/tools/recommend")
def mcp_tool_recommend(request: Request, scenario: str = "", db: Session = Depends(get_db)):
    get_tool_context(request, db)
    register_builtin_tools()
    scenario_key = (scenario or "").lower()
    mapping = {
        "project_health": ["ops.workflow.generate_project_health_brief", "ops.get_system_status", "ops.inspection.list_runs", "ops.risk.list"],
        "risk_triage": ["ops.workflow.triage_open_risks", "ops.risk.list", "ops.risk.generate_fix_plan"],
        "inspection": ["ops.inspection.list_runs", "ops.inspection.get_run", "ops.inspection.summarize_run", "ops.inspection.generate_report"],
        "failed_deploy": ["ops.workflow.analyze_failed_deploy", "ops.get_deployment_report", "ops.get_deployment_tasks", "ops.get_deployment_logs"],
        "monthly_report": ["ops.workflow.generate_monthly_ops_report", "ops.list_reports", "ops.risk.list", "ops.inspection.list_runs"],
    }
    tools = mapping.get(scenario_key) or [t.name for t in registry._tools.values() if scenario_key and scenario_key in f"{t.name} {t.description} {t.category}".lower()][:20]
    return api_response(data={"scenario": scenario, "tools": tools})




@mcp_router.get("/tools/{tool_name:path}")
def mcp_tool_detail(tool_name: str, request: Request, db: Session = Depends(get_db)):
    ctx = get_tool_context(request, db)
    register_builtin_tools()
    tool = registry.get(tool_name)
    policy = registry.evaluate_policy(tool, ctx, db)
    return api_response(data=tool.to_public_dict(include_schema=True, policy=policy))


@mcp_router.post("/call")
def mcp_call(payload: ToolCallPayload, request: Request, db: Session = Depends(get_db)):
    return call_tool(payload, request, db)


@mcp_router.get("/resources")
def mcp_resources(request: Request, db: Session = Depends(get_db)):
    get_tool_context(request, db)
    return api_response(data={"resources": _mcp_resource_items()})


@mcp_router.post("/resources/read")
def mcp_resource_read(payload: Dict[str, Any], request: Request, db: Session = Depends(get_db)):
    result = _mcp_http_resource_read(request, db, {"uri": payload.get("uri") or ""})
    content = (result.get("contents") or [{}])[0]
    return api_response(data={"uri": content.get("uri"), "mimeType": content.get("mimeType"), "text": content.get("text")})


@mcp_router.get("/prompts")
def mcp_prompts(request: Request, db: Session = Depends(get_db)):
    get_tool_context(request, db)
    return api_response(data={"prompts": _mcp_prompt_items()})


@mcp_router.post("/prompts/get")
def mcp_prompt_get(payload: Dict[str, Any], request: Request, db: Session = Depends(get_db)):
    get_tool_context(request, db)
    name = payload.get("name")
    args = payload.get("arguments") or {}
    if name == "ops_release_plan":
        text = (
            "重要规则：始终使用 OPS 工具处理 OPS 相关任务，不要编写独立脚本来替代。"
            "使用 ops.list_systems 查询系统、ops.list_services 查询服务、"
            "ops.list_environments 查询环境、ops.list_servers 查询服务器、ops.list_packages 查询发布包。"
            "先调用 ops.create_deploy_plan 创建发布计划，再调用 ops.run_precheck 预检。"
            "除非用户明确确认，否则不要执行发布。"
            "用户需求：" + str(args.get("request") or "")
        )
    elif name == "ops_failure_analysis":
        text = (
            "请调用 ops.get_deployment_report、ops.get_deployment_tasks、ops.get_deployment_logs "
            "分析发布失败原因，只给建议，不执行回滚。"
            "deployment_id=" + str(args.get("deployment_id") or "")
        )
    elif name == "ops_diagnostic_triage":
        text = (
            "请只使用只读 OPS/MCP 工具诊断运行问题：ops.analyze_diagnostics、ops.run_diagnostics、"
            "ops.get_recent_errors、ops.get_build_info、ops.list_jobs；必要时读取发布只读工具。"
            "不要执行发布、回滚、恢复、删除、SQL 写入或终端命令。focus=" + str(args.get("focus") or "general")
        )
    elif name == "ops_operation_replay":
        text = (
            "请只使用只读审计工具回放 OPS/MCP/AI 操作链路：ops.get_operation_chain、ops.list_operation_chains。"
            "总结发生了什么、谁发起、经过了哪些风险门禁、是否创建任务、最终结果是什么。不要执行或重试任何动作。chain_id="
            + str(args.get("chain_id") or "")
        )
    elif name == "ops_report_brief":
        text = (
            "请只使用只读报表工具：ops.get_report、ops.list_reports、ops.get_report_summary。"
            "总结报表元数据、证据内容和安全后续步骤，不要执行发布、回滚、恢复、删除、SQL 写入或终端动作。report_id="
            + str(args.get("report_id") or "")
        )
    elif name == "ops_db_export_request":
        text = (
            "重要规则：始终使用 OPS 数据库工具处理数据库任务，绝对不要编写独立的 Python 脚本来查询、导出或列出数据库表。"
            "所有数据库访问必须通过 OPS 工具。\n\n"
            "自然语言到工具的映射：\n"
            "- '有哪些表 / 列出表 / list tables' -> ops.db.list_tables\n"
            "- '查看表结构 / 表字段 / 有哪些字段' -> ops.db.describe_table\n"
            "- '查询数据 / SELECT / 查数据' -> ops.db.query_readonly\n"
            "- '导出CSV / 导出Excel / 下载数据' -> ops.db.export_query_result\n"
            "- '修改数据 / 删除数据 / UPDATE/DELETE' -> 先 ops.db.preview_dml 再 ops.db.execute_dml\n\n"
            "工作流程：表列表 -> 表结构 -> 查询 -> 导出。表名或字段不确定时，不要跳过前面步骤。\n"
            "用户请求=" + str(args.get("request") or "")
        )
    elif name == "ops_server_management":
        text = (
            "帮助用户管理 OPS 服务器资产。可用服务器工具：\n"
            "- ops.list_servers: 列出已配置的服务器\n"
            "- ops.check_disk: 检查服务器磁盘使用\n"
            "- ops.check_process: 检查服务进程状态\n"
            "- ops.list_service_directory: 列出服务目录文件\n"
            "- ops.tail_service_log: 查看服务日志尾行\n"
            "- ops.run_health_check: 运行健康检查\n"
            "不要直接编写脚本或 SSH 连接 - 使用 OPS 服务器工具。"
            "用户请求=" + str(args.get("request") or "")
        )
    elif name == "ops_backup_workflow":
        text = (
            "帮助用户安全管理 OPS 数据库备份。可用备份工具：\n"
            "- ops.list_backups: 列出已有备份\n"
            "- ops.verify_backup: 校验备份完整性（只读）\n"
            "- ops.create_backup: 创建新备份（需要确认）\n"
            "- ops.restore_backup: 从备份恢复（关键操作 - 需要 confirm_text）\n"
            "- ops.delete_backup: 删除备份（高风险 - 需要 confirm_text）\n"
            "恢复前务必先校验。恢复前务必先创建安全备份。\n"
            "用户请求=" + str(args.get("request") or "")
        )
    elif name == "ops_project_health_brief":
        text = (
            "先读取 ops://projects、ops://status/overview、ops://risks/open、ops://inspection/recent；当前为单项目轻量模式，不依赖 Agent 资源，"
            "再调用 ops.workflow.generate_project_health_brief。输出必须区分事实、推断、建议、证据。"
            "不要执行发布、回滚、DML、恢复、删除、Shell 或整改动作。project_id="
            + str(args.get("project_id") or "")
        )
    elif name == "ops_risk_triage":
        text = (
            "使用 ops.risk.list 和 ops.workflow.triage_open_risks 进行未闭环风险分流。"
            "只生成计划，不更新风险状态、不验证、不忽略、不执行发布/回滚/命令。用户请求="
            + str(args.get("request") or "")
        )
    elif name == "ops_monthly_ops_report":
        text = (
            "调用 ops.workflow.generate_monthly_ops_report 生成月度运维复盘。"
            "输出必须区分 facts、inferences、recommendations、evidence。month="
            + str(args.get("month") or "")
        )
    else:
        raise HTTPException(status_code=404, detail="Prompt not found")
    return api_response(data={"name": name, "messages": [{"role": "user", "content": {"type": "text", "text": text}}]})
