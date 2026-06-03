from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List


def _env_alias(environment: str) -> str:
    env = (environment or "").strip().lower()
    if env in {"prod", "production", "online", "release", "live", "线上", "生产"}:
        return "prod"
    if env in {"test", "testing", "staging", "qa", "uat", "测试", "预发"}:
        return "test"
    if env in {"dev", "development", "local", "开发"}:
        return "dev"
    return env


def _normalize_status(status: str) -> str:
    value = (status or "").strip().lower()
    if value in {"ok", "passed", "pass", "success", "ready"}:
        return "passed"
    if value in {"warn", "warning", "caution"}:
        return "warning"
    if value in {"error", "blocked", "block", "failed", "fail", "danger"}:
        return "blocked"
    return "info"


def _legacy_status(status: str) -> str:
    normalized = _normalize_status(status)
    if normalized == "passed":
        return "ok"
    if normalized == "warning":
        return "warn"
    if normalized == "blocked":
        return "error"
    return "info"


def normalize_checks(checks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    for item in checks:
        next_item = dict(item or {})
        raw_status = str(next_item.get("status") or "")
        canonical = _normalize_status(raw_status)
        next_item["status"] = _legacy_status(raw_status)
        next_item["check_status"] = canonical
        if "key" not in next_item:
            name = str(next_item.get("name") or "check").strip().lower().replace(" ", "_")
            next_item["key"] = name or "check"
        normalized.append(next_item)
    return normalized


def summarize_checks(checks: List[Dict[str, Any]]) -> Dict[str, Any]:
    normalized_checks = normalize_checks(checks)
    passed = [c for c in normalized_checks if c.get("check_status") == "passed"]
    warnings = [c for c in normalized_checks if c.get("check_status") == "warning"]
    errors = [c for c in normalized_checks if c.get("check_status") == "blocked"]
    return {
        "ready": len(errors) == 0,
        "ok": len(errors) == 0,
        "can_continue": len(errors) == 0,
        "status": "blocked" if errors else "warning" if warnings else "passed",
        "passed": len(passed),
        "warnings": len(warnings),
        "errors": len(errors),
        "blocking_checks": errors,
        "warning_checks": warnings,
        "checks": normalized_checks,
    }


def preflight_risk_level(checks: List[Dict[str, Any]], environment: str = "") -> str:
    summary = summarize_checks(checks)
    if summary["errors"]:
        return "high"
    if summary["warnings"] or _env_alias(environment) == "prod":
        return "medium"
    return "low"


def preflight_recommendations(
    *,
    checks: List[Dict[str, Any]],
    environment: str = "",
    file_name: str = "",
    ssh_fail: int = 0,
) -> List[str]:
    recommendations: List[str] = []
    if not file_name:
        recommendations.append("选择发布包后再次运行预检")
    if ssh_fail:
        recommendations.append("先修复 SSH 连接失败的服务器，再执行发布")
    if _env_alias(environment) == "prod":
        recommendations.append("生产环境发布前请确认回滚方案和最近一次备份")
    if any(_normalize_status(str(c.get("status") or "")) == "blocked" for c in checks):
        recommendations.append("存在阻断项，请修复后重新运行预检")
    if not recommendations:
        recommendations.append("预检未发现阻断项，可以进入发布确认")
    # Preserve order while removing duplicates.
    seen = set()
    result = []
    for item in recommendations:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def build_preflight_payload(
    *,
    checks: List[Dict[str, Any]],
    environment: str = "",
    file_name: str = "",
    ssh_fail: int = 0,
    servers: List[str] | None = None,
    remote_disks: List[Dict[str, Any]] | None = None,
    topology: Dict[str, Any] | None = None,
    package: Dict[str, Any] | None = None,
    recommendations: List[str] | None = None,
    required_confirmation: str = "",
    requires_confirmation: bool | None = None,
) -> Dict[str, Any]:
    summary = summarize_checks(checks)
    recs = recommendations or preflight_recommendations(
        checks=checks,
        environment=environment,
        file_name=file_name,
        ssh_fail=ssh_fail,
    )
    return {
        **summary,
        "risk_level": preflight_risk_level(checks, environment),
        "recommendations": recs,
        "requires_confirmation": bool(_env_alias(environment) == "prod") if requires_confirmation is None else bool(requires_confirmation),
        "required_confirmation": required_confirmation,
        "confirm_text": required_confirmation,
        "servers": servers or [],
        "remote_disks": remote_disks or [],
        "topology": topology or {},
        "package": package or {},
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def plan_precheck_payload(plan: Any, confirmation: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Build the lightweight MCP precheck payload from a saved ToolPlan.

    This deliberately stays side-effect free so Web preflight, MCP precheck, and
    future release-workflow tools can share the same status/risk conventions.
    """
    confirmation = confirmation or getattr(plan, "confirmation", None) or {}
    checks: List[Dict[str, Any]] = []
    for msg in confirmation.get("blockers") or []:
        checks.append({"name": "阻断项", "status": "error", "detail": msg})
    for msg in confirmation.get("warnings") or []:
        checks.append({"name": "警告项", "status": "warn", "detail": msg})
    package_match = confirmation.get("package_match") or {}
    if package_match:
        checks.append({"name": "发布包匹配", "status": package_match.get("status", "info"), "detail": package_match.get("message", "")})
    servers = list(getattr(plan, "servers", None) or [])
    checks.append({"name": "服务器数量", "status": "ok" if servers else "error", "detail": f"{len(servers)} 台"})
    payload = build_preflight_payload(
        checks=checks,
        environment=getattr(plan, "environment", "") or "",
        file_name=getattr(plan, "package_name", "") or "",
        servers=servers,
        package=(package_match.get("package") or {}),
        topology=(confirmation.get("topology") or {}),
        required_confirmation=getattr(plan, "confirm_text", "") or confirmation.get("confirm_text") or "",
        requires_confirmation=bool(confirmation.get("requires_confirmation")),
    )
    payload["ok"] = bool(payload.get("ready"))
    return payload
