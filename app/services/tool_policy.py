from __future__ import annotations

from typing import Any, Dict
from fastapi import HTTPException

from app.db.repository import ConfigRepository
from app.services.tool_context import ToolContext

# Default capability settings.
#
# These defaults are tuned for an MCP/AI client that talks to OPS for routine
# inspection / health-check / read-only diagnostic work. The settings favor
# "just works" for the common Path-A inspection flow (ops.inspection.run_*) and
# the Path-B single-shot probes (ops.check_disk, ops.check_process, ...).
#
# Anything that can mutate production state (deploy execute, prod deploy,
# server write, config write, backup write/restore, package write/cleanup,
# runtime cleanup, db write) still defaults to False and must be explicitly
# enabled by an admin. Token-level allow_write / allow_prod remains the second
# gate. This is the same belt-and-suspenders pattern we had before, but with
# the read-side defaults relaxed to match what AI/MCP clients actually need.
DEFAULT_CAPABILITY_SETTINGS = {
    "enabled": True,
    "ops_mode": "lightweight_single_project",
    "agent_runtime_enabled": False,
    "http_tools_enabled": True,
    "mcp_enabled": True,                       # MCP is the standard transport for AI clients
    "read_only": False,                        # Path A inspection.run_* is write=True; flip default
    "allow_deploy_plan": True,                 # safe: plan/preview only, never executes
    "allow_deploy_execute": False,             # production-grade gate, keep closed
    "allow_prod_deploy": False,                # production-grade gate, keep closed
    "allow_rollback": False,                   # destructive, requires admin + confirmation
    "allow_config_write": False,               # destructive, keep closed
    "allow_backup_write": False,               # high risk, keep closed by default
    "allow_backup_restore": False,             # critical risk, keep closed
    "allow_server_read": True,                 # Path B probes (check_disk / check_process / ...) need this
    "allow_server_write": False,               # destructive, keep closed
    "allow_package_write": False,              # destructive (uploads, retention), keep closed
    "allow_package_cleanup": False,            # destructive, keep closed
    "allow_runtime_cleanup": False,            # destructive, keep closed
    "allow_db_read_tools": True,               # safe SELECT for diagnostics
    "allow_db_export_tools": True,             # safe export to report center
    "allow_db_write_tools": False,             # DML/UPDATE/INSERT, must be admin-enabled
    "allow_high_risk_tools": True,             # Path A inspection.run_* is high risk
    "allow_critical_risk_tools": True,         # some inspection ops + connection delete are critical
    "allow_ai_token_to_run_inspection_execute": True,
    # When True, an AI/MCP tool-token is allowed to call inspection_execute tools
    # (ops.inspection.run_server / run_servers_batch / run_project / run_combined)
    # provided the second-layer confirm_text gate (enforce_risk_policy) passes.
    # When False, the old behavior is restored: tool_token gets 403 on these tools
    # and inspection must be triggered from a web session / admin. Other write
    # tools (deploy, rollback, config_write, db_write, ...) are NOT affected by
    # this switch and stay blocked for tool_token regardless.
    "require_confirmation": True,              # 2nd-layer: human confirm_text phrase
    "taskize_high_risk_tools": True,           # 3rd-layer: queue as OperationJob
    "strict_prod_confirmation": True,          # 4th-layer: prod env requires extra phrase
    "token_expire_days": 90,
}


def ai_tool_level(tool_def) -> str:
    """Classify a tool for AI/MCP usage governance.

    L1: read-only, low sensitivity; AI may call automatically.
    L2: read-only but sensitive/secret output; AI may call with masking/limits.
    L3: planning or non-critical write; AI may prepare plan, not execute automatically.
    L4: destructive/high-risk/critical write; human approval is required.
    """
    risk = str(getattr(tool_def, "risk", "low") or "low").lower()
    sensitivity = str(getattr(tool_def, "data_sensitivity", "internal") or "internal").lower()
    write = bool(getattr(tool_def, "write", False))
    requires_approval = bool(getattr(tool_def, "requires_human_approval", False) or getattr(tool_def, "requires_confirmation", False))
    if write and (risk in {"high", "critical"} or requires_approval):
        return "L4"
    if write or requires_approval:
        return "L3"
    if sensitivity in {"sensitive", "secret"}:
        return "L2"
    return "L1"


# Map tool categories/names to the approval tool that unlocks them.
APPROVAL_TOOL_MAP: Dict[str, str] = {
    "server_write": "ops.approval.prepare_service_control",
    "deploy_execute": "ops.approval.prepare_release",
    "package_cleanup": "ops.approval.prepare_package_cleanup",
    "db_write": "ops.approval.prepare_dml",
}

# Specific tool name overrides (for tools that don't fit category mapping).
APPROVAL_TOOL_NAME_MAP: Dict[str, str] = {
    "ops.execute_rollback_plan": "ops.approval.prepare_rollback",
    "ops.execute_deploy_plan": "ops.approval.prepare_release",
}


def _find_approval_tool_for(tool_def) -> str:
    """Return the approval tool name that unlocks this tool, or empty string."""
    name = getattr(tool_def, "name", "")
    if name in APPROVAL_TOOL_NAME_MAP:
        return APPROVAL_TOOL_NAME_MAP[name]
    category = getattr(tool_def, "category", "")
    return APPROVAL_TOOL_MAP.get(category, "")


def _approval_hint_for_tool(tool_def, level: str) -> str:
    """Generate a human-readable hint about how to get authorization for this tool."""
    if level == "L4":
        approval_tool = _find_approval_tool_for(tool_def)
        if approval_tool:
            return f"需人工审批。调用 {approval_tool} 创建审批工单，人工批准后系统自动执行。"
        return "需人工审批。联系管理员或通过 Web UI 执行。"
    if level == "L3":
        return "需 confirm_text 确认后执行。"
    return ""


def ai_tool_policy_metadata(tool_def) -> Dict[str, Any]:
    level = ai_tool_level(tool_def)
    return {
        "ai_level": level,
        "ai_callable": bool(getattr(tool_def, "ai_callable", True)),
        "ai_auto_callable": bool(getattr(tool_def, "ai_auto_callable", False)) and level in {"L1", "L2"},
        "requires_human_approval": bool(getattr(tool_def, "requires_human_approval", False) or getattr(tool_def, "requires_confirmation", False) or level == "L4"),
        "approval_hint": _approval_hint_for_tool(tool_def, level),
        "data_sensitivity": getattr(tool_def, "data_sensitivity", "internal"),
        "output_masking": bool(getattr(tool_def, "output_masking", True)),
    }


def get_capability_settings(db) -> Dict[str, Any]:
    cfg = ConfigRepository(db).get("capability_server")
    merged = dict(DEFAULT_CAPABILITY_SETTINGS)
    if isinstance(cfg, dict):
        merged.update(cfg)
    return merged


def save_capability_settings(db, data: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(DEFAULT_CAPABILITY_SETTINGS)
    merged.update(data or {})
    ConfigRepository(db).set("capability_server", merged)
    db.commit()
    return merged


def _is_prod_env(environment: str) -> bool:
    env = (environment or "").lower()
    return env in {"prod", "production", "online", "release", "live", "线上", "生产"}


def enforce_tool_policy(tool_def, args: Dict[str, Any], ctx: ToolContext, db) -> Dict[str, Any]:
    settings = get_capability_settings(db)
    if not settings.get("enabled", True):
        raise HTTPException(status_code=403, detail="Capability Server is disabled")
    if not settings.get("http_tools_enabled", True):
        raise HTTPException(status_code=403, detail="HTTP tools are disabled")
    if tool_def.category == "agent" and not settings.get("agent_runtime_enabled", False):
        raise HTTPException(status_code=403, detail="Agent runtime tools are disabled in lightweight mode")

    for scope in tool_def.scopes:
        if not ctx.has_scope(scope):
            raise HTTPException(status_code=403, detail=f"Tool scope required: {scope}")

    if tool_def.write:
        if settings.get("read_only", True):
            raise HTTPException(status_code=403, detail="Capability Server is in read-only mode")
        if not ctx.allow_write and not ctx.is_admin:
            raise HTTPException(status_code=403, detail="Tool token does not allow write operations")

    # AI/MCP tool-token calls must not directly execute tools that require human approval.
    # They may call dedicated plan/preview/approval-request tools instead.
    #
    # EXCEPTION: inspection_execute tools are an explicit, scoped carve-out. The
    # intent of the inspection flow is "AI proposes a run, user types
    # CONFIRM ops.inspection.run_server, AI then calls the tool with that
    # confirm_text". The second-layer `enforce_risk_policy` (below) is the
    # real gate that verifies the confirm_text — so the first-layer hard
    # block here is redundant and blocks the entire flow. We still keep the
    # hard block for every other requires_human_approval=True / high-risk
    # write tool, so deploy / rollback / config_write / db_write / etc. stay
    # admin-only.
    #
    # EXCEPTION: qclaw Element approval categories (approval_prepare /
    # approval_execute / approval_reject / approval_read / approval_maintenance /
    # routing) are an explicit carve-out for the qclaw integration. The human
    # approval short_code (one-time, 15min, room+event bound) is the real
    # security gate, not the tool-token scope. qclaw's MCP token only needs
    # ops:read to call these. Actual destructive operations (deploy/rollback/
    # DML/cleanup) run via the internal ApprovalExecutor using OPS internal
    # credentials, NOT the caller's token. This preserves the design boundary:
    # qclaw never holds deploy:execute / package:write / db:write scopes.
    QCLAW_APPROVAL_CATEGORIES = {
        "routing",
        "approval_prepare",
        "approval_execute",
        "approval_reject",
        "approval_read",
        "approval_maintenance",
    }
    if getattr(ctx, "auth_type", "") == "tool_token" and (
        getattr(tool_def, "requires_human_approval", False)
        or (getattr(tool_def, "write", False) and str(getattr(tool_def, "risk", "low")).lower() in {"high", "critical"})
    ):
        is_inspection_execute = (
            getattr(tool_def, "category", "") == "inspection_execute"
            and settings.get("allow_ai_token_to_run_inspection_execute", True)
        )
        is_qclaw_approval = getattr(tool_def, "category", "") in QCLAW_APPROVAL_CATEGORIES
        if not is_inspection_execute and not is_qclaw_approval:
            approval_tool = _find_approval_tool_for(tool_def)
            guidance = _approval_hint_for_tool(tool_def, "L4")
            raise HTTPException(
                status_code=403,
                detail={
                    "blocked": True,
                    "reason": "requires_human_approval",
                    "approval_tool": approval_tool,
                    "guidance": guidance,
                    "tool_name": tool_def.name,
                    "ai_level": "L4",
                },
            )

    if tool_def.category == "deploy_plan" and not settings.get("allow_deploy_plan", True):
        raise HTTPException(status_code=403, detail="Deploy plan tools are disabled")
    if tool_def.category == "deploy_execute" and not settings.get("allow_deploy_execute", False):
        raise HTTPException(status_code=403, detail="Deploy execution tools are disabled")
    if tool_def.name == "ops.execute_rollback_plan" and not settings.get("allow_rollback", False):
        raise HTTPException(status_code=403, detail="Rollback tools are disabled")
    if tool_def.category == "config_write" and not settings.get("allow_config_write", False):
        raise HTTPException(status_code=403, detail="Config write tools are disabled")
    if tool_def.category == "backup_write" and not settings.get("allow_backup_write", False):
        raise HTTPException(status_code=403, detail="Backup write tools are disabled")
    if tool_def.category == "backup_restore" and not settings.get("allow_backup_restore", False):
        raise HTTPException(status_code=403, detail="Backup restore tools are disabled")
    if tool_def.category == "server_read" and not settings.get("allow_server_read", False):
        raise HTTPException(status_code=403, detail="Server read tools are disabled")
    if tool_def.category == "server_write" and not settings.get("allow_server_write", False):
        raise HTTPException(status_code=403, detail="Server write tools are disabled")
    if tool_def.category == "package_write" and not settings.get("allow_package_write", False):
        raise HTTPException(status_code=403, detail="Package upload/write tools are disabled")
    if tool_def.category == "package_cleanup" and not settings.get("allow_package_cleanup", False):
        raise HTTPException(status_code=403, detail="Package cleanup tools are disabled")
    if tool_def.category == "runtime_cleanup" and not settings.get("allow_runtime_cleanup", False):
        raise HTTPException(status_code=403, detail="Runtime cleanup tools are disabled")
    if tool_def.category == "db_read" and not settings.get("allow_db_read_tools", True):
        raise HTTPException(status_code=403, detail="Database read tools are disabled")
    if tool_def.category in {"db_export", "db_export_read"} and not settings.get("allow_db_export_tools", True):
        raise HTTPException(status_code=403, detail="Database export tools are disabled")
    if tool_def.category == "db_write" and not settings.get("allow_db_write_tools", True):
        raise HTTPException(status_code=403, detail="Database write tools are disabled")

    risk = str(getattr(tool_def, "risk", "low") or "low").lower()
    if risk == "high" and tool_def.write and not settings.get("allow_high_risk_tools", True):
        raise HTTPException(status_code=403, detail="High-risk tools are disabled")
    if risk == "critical" and tool_def.write and not settings.get("allow_critical_risk_tools", False):
        # Tool-specific switches below still remain stricter; this generic guard keeps
        # critical tools closed by default unless explicitly enabled by an admin.
        raise HTTPException(status_code=403, detail="Critical-risk tools are disabled")

    environment = str(args.get("environment") or "")
    if _is_prod_env(environment):
        if not settings.get("allow_prod_deploy", False) and tool_def.category in {"deploy_execute", "deploy_plan"}:
            raise HTTPException(status_code=403, detail="Production deploy tools are disabled")
        if not ctx.allow_prod and not ctx.is_admin:
            raise HTTPException(status_code=403, detail="Tool token does not allow production operations")

    from app.services.risk_policy import enforce_risk_policy
    risk_policy = enforce_risk_policy(tool_def, args or {}, ctx=ctx, db=db, settings=settings)
    return {"allowed": True, "risk": tool_def.risk, "category": tool_def.category, "risk_policy": risk_policy}


def evaluate_tool_policy(tool_def, args: Dict[str, Any], ctx: ToolContext, db) -> Dict[str, Any]:
    """Return policy decision without raising. Used by capability discovery.

    This deliberately mirrors enforce_tool_policy so clients only discover tools
    that the current token/session can really call.
    """
    try:
        result = enforce_tool_policy(tool_def, args or {}, ctx, db)
        return {**result, "allowed": True, "blocked_reason": ""}
    except HTTPException as exc:
        if exc.status_code == 428:
            try:
                from app.services.risk_policy import evaluate_risk_policy
                settings = get_capability_settings(db)
                risk_policy = evaluate_risk_policy(tool_def, args or {}, ctx=ctx, db=db, settings=settings).to_dict()
            except Exception:
                risk_policy = {}
            return {
                "allowed": True,
                "blocked_reason": "",
                "risk": getattr(tool_def, "risk", "low"),
                "category": getattr(tool_def, "category", "read"),
                "risk_policy": risk_policy,
            }
        return {
            "allowed": False,
            "blocked_reason": str(exc.detail),
            "risk": getattr(tool_def, "risk", "low"),
            "category": getattr(tool_def, "category", "read"),
        }
