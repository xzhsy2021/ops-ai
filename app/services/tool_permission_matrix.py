from __future__ import annotations

from typing import Any, Dict, List

from app.services.tool_policy import ai_tool_level


CATEGORY_SETTING_MAP = {
    "deploy_plan": "allow_deploy_plan",
    "deploy_execute": "allow_deploy_execute",
    "config_write": "allow_config_write",
    "backup_write": "allow_backup_write",
    "backup_restore": "allow_backup_restore",
    "server_read": "allow_server_read",
    "server_write": "allow_server_write",
    "package_write": "allow_package_write",
    "package_cleanup": "allow_package_cleanup",
    "runtime_cleanup": "allow_runtime_cleanup",
    "db_read": "allow_db_read_tools",
    "db_export": "allow_db_export_tools",
    "db_export_read": "allow_db_export_tools",
    "db_write": "allow_db_write_tools",
}


def _capability_settings_for(tool_def) -> List[str]:
    settings: List[str] = []
    category = str(getattr(tool_def, "category", "") or "")
    mapped = CATEGORY_SETTING_MAP.get(category)
    if mapped:
        settings.append(mapped)
    risk = str(getattr(tool_def, "risk", "low") or "low").lower()
    if bool(getattr(tool_def, "write", False)):
        if risk == "high":
            settings.append("allow_high_risk_tools")
        elif risk == "critical":
            settings.append("allow_critical_risk_tools")
    return settings


def summarize_tool_permission(tool_def) -> Dict[str, Any]:
    risk = str(getattr(tool_def, "risk", "low") or "low").lower()
    write = bool(getattr(tool_def, "write", False))
    requires_confirmation = bool(getattr(tool_def, "requires_confirmation", False))
    requires_human_approval = bool(getattr(tool_def, "requires_human_approval", False))
    gates = ["scope"]
    capability_settings = _capability_settings_for(tool_def)
    if capability_settings:
        gates.append("capability_setting")
    if write:
        gates.append("write_token")
    if requires_confirmation or (write and risk in {"high", "critical"}):
        gates.append("risk_confirmation")
    if requires_human_approval:
        gates.append("human_approval")
    if str(getattr(tool_def, "category", "") or "") in {"deploy_execute", "deploy_plan"}:
        gates.append("production")

    level = ai_tool_level(tool_def)
    return {
        "tool": getattr(tool_def, "name", ""),
        "required_scopes": list(getattr(tool_def, "scopes", []) or []),
        "category": getattr(tool_def, "category", ""),
        "risk": risk,
        "write": write,
        "requires_confirmation": requires_confirmation,
        "requires_human_approval": requires_human_approval,
        "data_sensitivity": getattr(tool_def, "data_sensitivity", "internal"),
        "ai_level": level,
        "auto_callable_candidate": level in {"L1", "L2"} and not write and not requires_confirmation,
        "capability_settings": capability_settings,
        "gates": gates,
    }
