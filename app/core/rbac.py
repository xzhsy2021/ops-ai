"""轻量权限与风险策略工具。

本模块避免把权限判断散落在各 API 中，先提供稳定化 V1 需要的
生产环境识别、高风险操作解释、脱敏和二次确认校验。
"""
from __future__ import annotations

from typing import Any, Dict, Iterable
from fastapi import HTTPException

PROD_ALIASES = {"prod", "production", "prd", "live", "线上", "生产", "生产环境"}
HIGH_RISK_ACTIONS = {"deploy", "rollback", "sql", "cleanup", "ssh_key", "server_exec"}


def normalize_env(environment: str | None) -> str:
    return (environment or "").strip().lower()


def is_production(environment: str | None) -> bool:
    return normalize_env(environment) in PROD_ALIASES


def user_role(user: Dict[str, Any]) -> str:
    if user.get("is_admin"):
        return "admin"
    return str(user.get("role") or "viewer").lower()


def mask_secret(value: Any, keep: int = 3) -> str:
    text = "" if value is None else str(value)
    if not text:
        return ""
    if len(text) <= keep * 2:
        return "*" * len(text)
    return f"{text[:keep]}{'*' * 8}{text[-keep:]}"


def require_confirmed_high_risk(user: Dict[str, Any], *, action: str, environment: str = "", payload: Dict[str, Any] | None = None) -> None:
    """生产环境/高风险操作二次确认。

    兼容现有前端：非生产不阻塞；生产环境要求 admin 且需要明确确认字段。
    payload 支持 confirm_production=true 或 confirm_text="CONFIRM"。
    """
    payload = payload or {}
    if not is_production(environment):
        return
    if not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="生产环境操作仅管理员可执行")
    confirm_text = str(payload.get("confirm_text") or payload.get("confirmation") or "").strip()
    confirmed = (
        bool(payload.get("confirm_production"))
        or confirm_text.upper() == "CONFIRM"
        or confirm_text.startswith("确认发布 ")
        or confirm_text.startswith("确认回滚 ")
    )
    if not confirmed:
        raise HTTPException(status_code=400, detail=f"生产环境 {action} 需要二次确认：confirm_production=true 或输入确认短语")


def explain_operation_risk(action: str, environment: str = "", target: str = "", extra: Dict[str, Any] | None = None) -> Dict[str, Any]:
    extra = extra or {}
    prod = is_production(environment)
    level = "high" if prod or action in {"rollback", "cleanup", "server_exec"} else "medium" if action in HIGH_RISK_ACTIONS else "low"
    reasons = []
    if prod:
        reasons.append("目标为生产环境")
    if action in HIGH_RISK_ACTIONS:
        reasons.append("操作会影响远程资源或真实数据")
    if extra.get("uses_ssh_tunnel"):
        reasons.append("通过 SSH 跳板机访问内网资源")
    if extra.get("row_count", 0):
        reasons.append(f"涉及数据行数: {extra.get('row_count')}")
    return {
        "action": action,
        "environment": environment,
        "target": target,
        "risk_level": level,
        "requires_audit": level in {"medium", "high"},
        "requires_confirmation": prod or level == "high",
        "reasons": reasons or ["常规操作"],
    }
