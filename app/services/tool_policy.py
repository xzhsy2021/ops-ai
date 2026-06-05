from __future__ import annotations

from typing import Any, Dict
from fastapi import HTTPException

from app.db.repository import ConfigRepository
from app.services.tool_context import ToolContext

DEFAULT_CAPABILITY_SETTINGS = {
    "enabled": True,
    "ops_mode": "lightweight_single_project",
    "agent_runtime_enabled": False,
    "http_tools_enabled": True,
    "mcp_enabled": False,
    "read_only": True,
    "allow_deploy_plan": True,
    "allow_deploy_execute": False,
    "allow_prod_deploy": False,
    "allow_rollback": False,
    "allow_config_write": False,
    "allow_backup_write": False,
    "allow_backup_restore": False,
    "allow_server_read": False,
    "allow_server_write": False,
    "allow_package_write": False,
    "allow_package_cleanup": False,
    "allow_runtime_cleanup": False,
    "allow_db_read_tools": True,
    "allow_db_export_tools": True,
    "allow_db_write_tools": True,
    "allow_high_risk_tools": True,
    "allow_critical_risk_tools": False,
    "require_confirmation": True,
    "taskize_high_risk_tools": True,
    "strict_prod_confirmation": True,
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


def ai_tool_policy_metadata(tool_def) -> Dict[str, Any]:
    level = ai_tool_level(tool_def)
    return {
        "ai_level": level,
        "ai_callable": bool(getattr(tool_def, "ai_callable", True)),
        "ai_auto_callable": bool(getattr(tool_def, "ai_auto_callable", False)) and level in {"L1", "L2"},
        "requires_human_approval": bool(getattr(tool_def, "requires_human_approval", False) or getattr(tool_def, "requires_confirmation", False) or level == "L4"),
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
    if getattr(ctx, "auth_type", "") == "tool_token" and (
        getattr(tool_def, "requires_human_approval", False)
        or (getattr(tool_def, "write", False) and str(getattr(tool_def, "risk", "low")).lower() in {"high", "critical"})
    ):
        raise HTTPException(status_code=403, detail="This tool requires human approval and cannot be executed directly by AI/MCP token")

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
