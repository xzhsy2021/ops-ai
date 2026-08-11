from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, UploadFile, File, Form
from fastapi.responses import JSONResponse, Response as FastAPIResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.helpers import api_response, audit
from app.core.auth_v2 import require_auth, require_admin, normalize_role
from app.db import get_db
from app.db.models import ToolToken, ToolCallLog, ToolPlan, ToolPlanEvent
from app.services.tool_context import ToolContext
from app.services.tool_token import (
    create_tool_token,
    enforce_conversation_binding,
    normalize_approver_identities,
    normalize_channel_bindings,
    recommended_tool_token_templates,
    resolve_approver_identities,
    resolve_channel_bindings,
    token_to_dict,
    validate_tool_token,
)
from app.services.tool_policy import get_capability_settings, save_capability_settings
from app.services.risk_policy import risk_policy_manifest
from app.services.tool_registry import (
    normalize_tool_profile,
    register_builtin_tools,
    registry,
    tool_matches_profile,
    tool_profile_manifest,
    bump_capability_version as _bump_capability_version,
)
from app.services.mcp_capability_service import (
    mcp_call_tool,
    mcp_call_tool_stream,
    mcp_prompt_get as service_mcp_prompt_get,
    mcp_prompt_items as service_mcp_prompt_items,
    mcp_prompts_list as service_mcp_prompts_list,
    mcp_resource_items,
    mcp_resource_read as service_mcp_resource_read,
    mcp_resources_list,
    mcp_tools_list,
)
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


class ToolCallPayload(BaseModel):
    tool: str
    arguments: Dict[str, Any] = {}


class CreateToolTokenPayload(BaseModel):
    name: str
    description: str = ""
    scopes: List[str] = ["ops:read", "ops:write"]
    # `allow_write` is intentionally `None` (not False): the create_tool_token
    # service will derive the right value from `scopes` so that a token scoped
    # to "ops:write" doesn't accidentally end up read-only (which was the
    # most common 403 we hit during the AI/MCP rollout). Callers can still
    # explicitly force True / False if they want to override.
    allow_write: Optional[bool] = None
    allow_prod: bool = False
    expires_in_days: int = 90
    # qclaw Element room binding. Empty list (= default) = no binding,
    # token can be used from any room. Non-empty list = token is restricted
    # to the listed Matrix room IDs (e.g. ["!ops:matrix.org"]).
    bound_room_ids: List[str] = Field(default_factory=list)
    # qclaw Element approver whitelist. Empty list (= default) = no
    # token-level restriction. Non-empty list = only these Matrix user IDs
    # may consume approval short codes created through this token.
    approver_matrix_ids: List[str] = Field(default_factory=list)
    channel_bindings: List[Dict[str, str]] = Field(default_factory=list)
    approver_identities: List[Dict[str, str]] = Field(default_factory=list)




class UpdateToolTokenPayload(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    scopes: Optional[List[str]] = None
    allow_write: Optional[bool] = None
    allow_prod: Optional[bool] = None
    expires_in_days: Optional[int] = None
    revoke: Optional[bool] = None
    # Pass an empty list to clear the room binding; pass None to leave it
    # unchanged. Frontend always sends the current list on edit.
    bound_room_ids: Optional[List[str]] = None
    # Pass an empty list to clear the approver whitelist; pass None to leave
    # it unchanged. Frontend always sends the current list on edit.
    approver_matrix_ids: Optional[List[str]] = None
    channel_bindings: Optional[List[Dict[str, str]]] = None
    approver_identities: Optional[List[Dict[str, str]]] = None


class ToolPolicyPreviewPayload(BaseModel):
    tool: str
    arguments: Dict[str, Any] = {}
    template_key: str = ""
    token_id: str = ""
    scopes: Optional[List[str]] = None
    allow_write: Optional[bool] = None
    allow_prod: Optional[bool] = None


class UpdateSettingsPayload(BaseModel):
    settings: Dict[str, Any]


class DeleteToolRecordsPayload(BaseModel):
    call_ids: List[str] = Field(default_factory=list, max_length=200)
    plan_ids: List[str] = Field(default_factory=list, max_length=200)
    force: bool = False


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _capability_etag(version: str) -> str:
    return f'"capability-{version}"'


def _request_etags(request: Request) -> set[str]:
    raw = request.headers.get("if-none-match", "") if request else ""
    return {part.strip() for part in raw.split(",") if part.strip()}


def _maybe_return_not_modified(request: Request, response: Response, version: str) -> None:
    etag = _capability_etag(version)
    response.headers["ETag"] = etag
    response.headers["X-Capability-Version"] = version
    if etag in _request_etags(request) or "*" in _request_etags(request):
        raise HTTPException(status_code=304, detail="Not Modified", headers={"ETag": etag, "X-Capability-Version": version})


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
    channel_bindings = normalize_channel_bindings(
        getattr(token, "channel_bindings", None)
    )
    approver_identities = normalize_approver_identities(
        getattr(token, "approver_identities", None)
    )
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
        channel_bindings=channel_bindings,
        approver_identities=approver_identities,
        client_name=token.name,
        ip_address=request.client.host if request.client else "",
        user_agent=request.headers.get("user-agent", ""),
    )


def _preview_context_from_payload(payload: ToolPolicyPreviewPayload, request: Request, db: Session, user: Dict[str, Any]) -> tuple[ToolContext, Dict[str, Any]]:
    if payload.token_id:
        token = db.query(ToolToken).filter(ToolToken.id == payload.token_id).first()
        if not token:
            raise HTTPException(status_code=404, detail="Token not found")
        if not user.get("is_admin") and token.owner != user.get("username"):
            raise HTTPException(status_code=403, detail="Cannot preview another user's token")
        serialized = token_to_dict(token)
        ctx = ToolContext(
            username=token.owner,
            role="operator" if token.allow_write else "readonly",
            auth_type="tool_token",
            token_id=token.id,
            token_name=token.name,
            token_owner=token.owner,
            scopes=token.scopes or [],
            allow_write=bool(token.allow_write),
            allow_prod=bool(token.allow_prod),
            channel_bindings=serialized["channel_bindings"],
            approver_identities=serialized["approver_identities"],
            client_name=token.name,
            ip_address=request.client.host if request.client else "",
            user_agent=request.headers.get("user-agent", "") if request else "",
        )
        return ctx, {
            "source": "token",
            "token_id": token.id,
            "name": token.name,
            "owner": token.owner,
            "scopes": token.scopes or [],
            "allow_write": bool(token.allow_write),
            "allow_prod": bool(token.allow_prod),
            "channel_bindings": serialized["channel_bindings"],
            "approver_identities": serialized["approver_identities"],
            "bound_room_ids": serialized["bound_room_ids"],
            "approver_matrix_ids": serialized["approver_matrix_ids"],
        }

    templates = {item["key"]: item for item in recommended_tool_token_templates()}
    template = templates.get(payload.template_key or "") if payload.template_key else None
    scopes = payload.scopes if payload.scopes is not None else ((template or {}).get("scopes") or ["ops:read"])
    allow_write = payload.allow_write if payload.allow_write is not None else bool((template or {}).get("allow_write", False))
    allow_prod = payload.allow_prod if payload.allow_prod is not None else bool((template or {}).get("allow_prod", False))
    name = (template or {}).get("name") or payload.template_key or "custom-preview"
    ctx = ToolContext(
        username=user.get("username") or "",
        role="operator" if allow_write else "readonly",
        auth_type="tool_token",
        token_name=name,
        token_owner=user.get("username") or "",
        scopes=list(scopes or []),
        allow_write=bool(allow_write),
        allow_prod=bool(allow_prod),
        client_name=name,
        ip_address=request.client.host if request.client else "",
        user_agent=request.headers.get("user-agent", "") if request else "",
    )
    return ctx, {
        "source": "template" if template else "custom",
        "template_key": (template or {}).get("key") or payload.template_key,
        "name": name,
        "owner": user.get("username") or "",
        "scopes": list(scopes or []),
        "allow_write": bool(allow_write),
        "allow_prod": bool(allow_prod),
    }


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
    profile: str = "daily_ops",
    include_disabled: bool = False,
    include_schema: bool = True,
    limit: int = 100,
    cursor: int = 0,
    output_format: str = Query("native", alias="format"),
    db: Session = Depends(get_db),
):
    ctx = get_tool_context(request, db)
    register_builtin_tools()
    profile_name = normalize_tool_profile(profile, default="daily_ops")
    all_tools = [
        registry._tools[name]
        for name in sorted(registry._tools.keys())
        if tool_matches_profile(registry._tools[name], profile_name)
    ]
    listed = build_tool_manifest(
        all_tools,
        output_format=output_format,
        include_schema=include_schema,
        category=category,
        risk=risk,
        limit=limit,
        cursor=cursor,
    )
    listed["filters"] = {**(listed.get("filters") or {}), "profile": profile_name}
    version = registry.capability_version(db, ctx, profile=profile_name)
    _maybe_return_not_modified(request, response, version)
    data = {
        **listed,
        "settings": get_capability_settings(db),
        "capability_version": version,
        "tool_profile": profile_name,
        "tool_profiles": tool_profile_manifest(),
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
    profile: str = "daily_ops",
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
        profile=profile,
        include_schema=include_schema,
        include_disabled=include_disabled,
        output_format=output_format,
        limit=limit,
        cursor=cursor,
    )
    version = data.get("server", {}).get("capability_version") or registry.capability_version(db, ctx, profile=profile)
    _maybe_return_not_modified(request, response, version)
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

    registry.normalize_and_enforce_call(tool, payload.arguments or {}, ctx)

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
    approval_intake: bool = Form(False),
    room_id: str = Form(""),
    request_event_id: str = Form(""),
    content_sha256: str = Form(""),
    package_sha256: str = Form(""),
    db: Session = Depends(get_db),
):
    ctx = get_tool_context(request, db)
    register_builtin_tools()
    from app.services.package_retention import (
        package_path,
        package_to_dict,
        safe_package_name,
        save_package_fileobj,
        sha256_file,
        upsert_package_metadata,
    )

    filename = file.filename or "uploaded_package"
    effective_overwrite = bool(overwrite)
    meta = None
    reused = False
    if approval_intake:
        channel_bindings = normalize_channel_bindings(
            getattr(ctx, "channel_bindings", None)
        )
        if getattr(ctx, "auth_type", "") != "tool_token" or not ctx.has_scope("ops:read"):
            raise HTTPException(status_code=403, detail="Approval package intake requires an ops:read Tool Token")
        if not channel_bindings:
            raise HTTPException(status_code=403, detail="Approval package intake requires a room-bound Tool Token")
        enforce_conversation_binding(
            channel_bindings,
            channel="matrix",
            channel_account_id="default",
            conversation_id=room_id,
        )
        expected_package_sha256 = str(package_sha256 or "").strip().lower()
        if (
            not str(request_event_id or "").strip()
            or not re.fullmatch(r"[0-9a-fA-F]{64}", content_sha256 or "")
            or not re.fullmatch(r"[0-9a-f]{64}", expected_package_sha256)
        ):
            raise HTTPException(
                status_code=400,
                detail="Approval package intake requires request_event_id, content_sha256, and package_sha256",
            )

        digest = hashlib.sha256()
        try:
            file.file.seek(0)
        except Exception:
            pass
        for chunk in iter(lambda: file.file.read(1024 * 1024), b""):
            digest.update(chunk.encode("utf-8") if isinstance(chunk, str) else chunk)
        if digest.hexdigest() != expected_package_sha256:
            raise HTTPException(status_code=409, detail="Uploaded package checksum does not match package_sha256")

        safe_name = safe_package_name(filename)
        filename = f"approval-{content_sha256.lower()[:12]}-{expected_package_sha256[:16]}-{safe_name[-190:]}"
        effective_overwrite = False
        existing_path = package_path(filename)
        if os.path.isfile(existing_path):
            if sha256_file(existing_path).lower() != expected_package_sha256:
                raise HTTPException(status_code=409, detail="Approval package name already exists with different content")
            row = upsert_package_metadata(
                db,
                filename,
                existing_path,
                system=system or "",
                service=service or "",
                uploaded_by=ctx.username or ctx.token_owner or "tool",
                sha256=expected_package_sha256,
            )
            meta = package_to_dict(row, include_retention=False)
            reused = True
    else:
        # Generic File Center writes retain the normal write/scope/capability gates.
        from app.services.tool_policy import enforce_tool_policy
        tool = registry.get("ops.upload_package")
        enforce_tool_policy(tool, {"system": system, "service": service}, ctx, db)
    try:
        await file.seek(0)
    except Exception:
        pass
    if meta is None:
        try:
            meta = save_package_fileobj(
                db,
                filename=filename,
                fileobj=file.file,
                system=system or "",
                service=service or "",
                uploaded_by=ctx.username or ctx.token_owner or "tool",
                overwrite=effective_overwrite,
            )
        except HTTPException as exc:
            existing_path = package_path(filename)
            expected_package_sha256 = str(package_sha256 or "").strip().lower()
            if not approval_intake or exc.status_code != 409 or not os.path.isfile(existing_path):
                raise
            if sha256_file(existing_path).lower() != expected_package_sha256:
                raise
            row = upsert_package_metadata(
                db,
                filename,
                existing_path,
                system=system or "",
                service=service or "",
                uploaded_by=ctx.username or ctx.token_owner or "tool",
                sha256=expected_package_sha256,
            )
            meta = package_to_dict(row, include_retention=False)
            reused = True
    audit(
        "tool.package.approval_intake" if approval_intake else "tool.package.upload",
        "file",
        meta.get("package_name") or meta.get("name"),
        f"client={ctx.client_name} owner={ctx.token_owner or ctx.username} room={room_id if approval_intake else ''} "
        f"event={request_event_id if approval_intake else ''} size={meta.get('size_bytes')} sha256={meta.get('sha256')}",
    )
    return api_response(data={
        "ok": True,
        "tool": "ops.upload_package",
        "result": {
            **meta,
            "uploaded_via": "multipart",
            "approval_intake": approval_intake,
            "reused": reused,
        },
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


@tools_router.get("/token-templates")
def list_token_templates(request: Request, db: Session = Depends(get_db)):
    require_auth(request, db)
    return api_response(data={"templates": recommended_tool_token_templates()})


@tools_router.post("/policy-preview")
def preview_tool_policy(payload: ToolPolicyPreviewPayload, request: Request, db: Session = Depends(get_db)):
    from app.services.tool_permission_matrix import summarize_tool_permission

    user = require_auth(request, db)
    register_builtin_tools()
    tool = registry.get(payload.tool)
    preview_ctx, subject = _preview_context_from_payload(payload, request, db, user)
    policy = registry.evaluate_policy(tool, preview_ctx, db)
    return api_response(data={
        "tool": tool.name,
        "title": tool.title or tool.name,
        "allowed": bool(policy.get("allowed")),
        "blocked_reason": policy.get("blocked_reason", ""),
        "risk": policy.get("risk", tool.risk),
        "category": policy.get("category", tool.category),
        "required_scopes": tool.scopes or [],
        "write": bool(tool.write),
        "requires_confirmation": bool(tool.requires_confirmation),
        "subject": subject,
        "permission": summarize_tool_permission(tool),
        "policy": policy,
    })


@tools_router.post("/tokens")
def create_token(payload: CreateToolTokenPayload, request: Request, db: Session = Depends(get_db)):
    user = require_auth(request, db)
    # Non-admin users can create read/plan/precheck tokens only.
    scopes = payload.scopes or ["ops:read", "ops:write"]
    # Pass `None` through so `create_tool_token` can derive allow_write from
    # scopes (see CreateToolTokenPayload.allow_write). The admin gate below
    # still blocks non-admins from holding a write-capable token.
    allow_write: Optional[bool] = payload.allow_write
    allow_prod = bool(payload.allow_prod)
    if not user.get("is_admin"):
        dangerous = {"deploy:execute", "config:write", "server:write", "package:write", "package:cleanup", "db:write", "*"}
        # Re-derive what the service will resolve, so we can guard early.
        effective_write = bool(allow_write) or any(s in dangerous or s.endswith(":*") for s in scopes) or any(s == "ops:write" for s in scopes)
        if effective_write or allow_prod or any(s in dangerous or s.endswith(":*") for s in scopes):
            raise HTTPException(status_code=403, detail="Only admin can create write/prod tool tokens")
    fields_set = (
        payload.model_fields_set
        if hasattr(payload, "model_fields_set")
        else payload.__fields_set__
    )
    binding_kwargs: Dict[str, Any] = {}
    if "channel_bindings" in fields_set:
        binding_kwargs["channel_bindings"] = payload.channel_bindings
    if "bound_room_ids" in fields_set:
        binding_kwargs["bound_room_ids"] = payload.bound_room_ids
    if "approver_identities" in fields_set:
        binding_kwargs["approver_identities"] = payload.approver_identities
    if "approver_matrix_ids" in fields_set:
        binding_kwargs["approver_matrix_ids"] = payload.approver_matrix_ids
    try:
        created = create_tool_token(
            db,
            name=payload.name,
            owner=user.get("username") or "",
            description=payload.description,
            scopes=scopes,
            allow_write=allow_write,
            allow_prod=allow_prod,
            expires_in_days=payload.expires_in_days,
            **binding_kwargs,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    serialized = token_to_dict(created["record"])
    audit(
        "tool.token.create",
        "tool_token",
        payload.name,
        (
            f"user={user.get('username')} scopes={','.join(scopes)} "
            f"bound_rooms={','.join(serialized['bound_room_ids'])} "
            f"approvers={','.join(serialized['approver_matrix_ids'])} "
            "channel_bindings="
            f"{json.dumps(serialized['channel_bindings'], ensure_ascii=False, sort_keys=True, separators=(',', ':'))} "
            "approver_identities="
            f"{json.dumps(serialized['approver_identities'], ensure_ascii=False, sort_keys=True, separators=(',', ':'))}"
        ),
    )
    _bump_capability_version(db)
    return api_response(data={"token": created["token"], "record": token_to_dict(created["record"])}, message="Token created; copy it now, it will be shown only once")




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
    if payload.description is not None:
        token.description = payload.description.strip()[:500]
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
    fields_set = (
        payload.model_fields_set
        if hasattr(payload, "model_fields_set")
        else payload.__fields_set__
    )
    if {"channel_bindings", "bound_room_ids"} & fields_set:
        kwargs: Dict[str, Any] = {}
        if "channel_bindings" in fields_set:
            kwargs["generic"] = payload.channel_bindings
        if "bound_room_ids" in fields_set:
            kwargs["legacy"] = payload.bound_room_ids
        try:
            token.channel_bindings = resolve_channel_bindings(**kwargs)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    if {"approver_identities", "approver_matrix_ids"} & fields_set:
        kwargs = {}
        if "approver_identities" in fields_set:
            kwargs["generic"] = payload.approver_identities
        if "approver_matrix_ids" in fields_set:
            kwargs["legacy"] = payload.approver_matrix_ids
        try:
            token.approver_identities = resolve_approver_identities(**kwargs)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    db.commit()
    db.refresh(token)
    serialized = token_to_dict(token)
    audit(
        "tool.token.update",
        "tool_token",
        token.name,
        (
            f"user={user.get('username')} scopes={','.join(token.scopes or [])} "
            f"allow_write={token.allow_write} allow_prod={token.allow_prod} "
            f"bound_rooms={','.join(serialized['bound_room_ids'])} "
            f"approvers={','.join(serialized['approver_matrix_ids'])} "
            "channel_bindings="
            f"{json.dumps(serialized['channel_bindings'], ensure_ascii=False, sort_keys=True, separators=(',', ':'))} "
            "approver_identities="
            f"{json.dumps(serialized['approver_identities'], ensure_ascii=False, sort_keys=True, separators=(',', ':'))}"
        ),
    )
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


@tools_router.delete("/tokens/{token_id}/purge")
def purge_token(token_id: str, request: Request, db: Session = Depends(get_db)):
    """Hard-delete a tool token (remove the row from DB). Admin only.

    与 revoke_token 的区别：revoke 只是置 revoked_at 软删除，purge 直接从数据库删除。
    一旦 purge，无法恢复；调用记录审计会保留 token 名称 / 所有者 / scopes 用于追溯。
    """
    user = require_admin(request, db)
    token = db.query(ToolToken).filter(ToolToken.id == token_id).first()
    if not token:
        raise HTTPException(status_code=404, detail="Token not found")
    # 记录关键信息后再删除，方便审计
    name = token.name
    owner = token.owner
    scopes = list(token.scopes or [])
    allow_write = bool(token.allow_write)
    allow_prod = bool(token.allow_prod)
    db.delete(token)
    db.commit()
    audit(
        "tool.token.purge", "tool_token", name,
        f"user={user.get('username')} owner={owner} scopes={','.join(scopes)} "
        f"allow_write={allow_write} allow_prod={allow_prod} HARD_DELETE"
    )
    _bump_capability_version(db)
    return api_response(message="Token purged", data={"id": token_id, "name": name})


@tools_router.get("/calls")
def list_tool_call_logs(request: Request, response: Response, limit: int = 100, offset: int = 0, tool: str = "", status: str = "", since_id: str = "", db: Session = Depends(get_db)):
    user = require_auth(request, db)
    from app.services.tool_records import list_tool_call_logs as list_records

    data = list_records(db, limit=limit, offset=offset, tool=tool, status=status, since_id=since_id, user=user)
    from app.api.helpers import compute_list_etag, check_etag_not_modified
    etag = compute_list_etag(data.get("items") or [], "call_logs")
    not_modified = check_etag_not_modified(request, etag)
    if not_modified:
        return not_modified
    response.headers["ETag"] = etag
    return api_response(data=data)


@tools_router.get("/plans")
def list_tool_plans(request: Request, limit: int = 100, offset: int = 0, plan_type: str = "", status: str = "", db: Session = Depends(get_db)):
    user = require_auth(request, db)
    from app.services.tool_records import list_tool_plans as list_records

    return api_response(data=list_records(db, limit=limit, offset=offset, plan_type=plan_type, status=status, user=user))


@tools_router.post("/records/delete")
def delete_tool_records(payload: DeleteToolRecordsPayload, request: Request, db: Session = Depends(get_db)):
    user = require_admin(request, db)
    from app.services.tool_records import delete_tool_records as delete_records

    result = delete_records(
        db,
        call_ids=payload.call_ids,
        plan_ids=payload.plan_ids,
        actor=user.get("username") or "",
        force=payload.force,
    )
    audit(
        "tool.records.delete_many",
        "tool_records",
        f"calls={len(result.get('call_ids') or [])},plans={len(result.get('plan_ids') or [])}",
        f"user={user.get('username')} force={payload.force} deleted={result.get('deleted')}",
    )
    return api_response(data=result, message="Tool records deleted")


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
    return mcp_tools_list(db, ctx, params)


def _mcp_http_call_tool(request: Request, db: Session, params: Dict[str, Any]) -> Dict[str, Any]:
    ctx = get_tool_context(request, db)
    return mcp_call_tool(db, ctx, params)


def _mcp_http_call_tool_stream(request: Request, db: Session, params: Dict[str, Any]) -> Dict[str, Any]:
    ctx = get_tool_context(request, db)
    return mcp_call_tool_stream(db, ctx, params)


def _mcp_http_resources_list(request: Request, db: Session) -> Dict[str, Any]:
    get_tool_context(request, db)
    return mcp_resources_list()


def _mcp_http_resource_read(request: Request, db: Session, params: Dict[str, Any]) -> Dict[str, Any]:
    ctx = get_tool_context(request, db)
    return service_mcp_resource_read(db, ctx, params)


def _mcp_http_prompts_list(request: Request, db: Session) -> Dict[str, Any]:
    get_tool_context(request, db)
    return service_mcp_prompts_list()


def _mcp_http_prompt_get(request: Request, db: Session, params: Dict[str, Any]) -> Dict[str, Any]:
    get_tool_context(request, db)
    return service_mcp_prompt_get(params)


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
        elif method == "tools/call.stream":
            result = _mcp_http_call_tool_stream(request, db, params)
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
        "capability_version": registry.capability_version(db, ctx, profile="daily_ops"),
        "resources_count": len(mcp_resource_items()),
        "prompts_count": len(service_mcp_prompt_items()),
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
        "capability_version": registry.capability_version(db, ctx, profile="daily_ops"),
        "resources": mcp_resource_items(),
        "prompts": service_mcp_prompt_items(),
        "tools": registry.list_tools(db, ctx, profile="daily_ops").get("tools", []),
    })


@mcp_router.get("/tools")
def mcp_tools(
    request: Request,
    response: Response,
    category: str = "",
    profile: str = "daily_ops",
    limit: int = 100,
    cursor: int = 0,
    db: Session = Depends(get_db),
):
    return list_tools(request, response, category=category, profile=profile, limit=limit, cursor=cursor, output_format="mcp", db=db)




@mcp_router.get("/tools/recommend")
def mcp_tool_recommend(request: Request, scenario: str = "", db: Session = Depends(get_db)):
    get_tool_context(request, db)
    register_builtin_tools()
    scenario_key = (scenario or "").lower()
    mapping = {
        "project_health": ["ops.get_system_status", "ops.inspection.list_runs", "ops.risk.list"],
        "risk_triage": ["ops.risk.list", "ops.risk.generate_fix_plan"],
        "inspection": [
            "ops.list_server_groups",
            "ops.inspection.preview_servers_batch",
            "ops.inspection.run_servers_batch",
            "ops.inspection.list_runs",
            "ops.inspection.get_run",
            "ops.inspection.summarize_run",
            "ops.inspection.generate_report",
            "ops.inspection.generate_report_for_runs",
        ],
        "failed_deploy": ["ops.get_deployment_report", "ops.get_deployment_tasks", "ops.get_deployment_logs"],
        "monthly_report": ["ops.list_reports", "ops.risk.list", "ops.inspection.list_runs"],
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
    return api_response(data=mcp_resources_list())


@mcp_router.post("/resources/read")
def mcp_resource_read(payload: Dict[str, Any], request: Request, db: Session = Depends(get_db)):
    result = _mcp_http_resource_read(request, db, {"uri": payload.get("uri") or ""})
    content = (result.get("contents") or [{}])[0]
    return api_response(data={"uri": content.get("uri"), "mimeType": content.get("mimeType"), "text": content.get("text")})


@mcp_router.get("/prompts")
def mcp_prompts(request: Request, db: Session = Depends(get_db)):
    get_tool_context(request, db)
    return api_response(data=service_mcp_prompts_list())


@mcp_router.post("/prompts/get")
def mcp_prompt_get(payload: Dict[str, Any], request: Request, db: Session = Depends(get_db)):
    get_tool_context(request, db)
    try:
        data = service_mcp_prompt_get(payload)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return api_response(data=data)
