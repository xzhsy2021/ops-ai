from __future__ import annotations

"""Central risk and confirmation policy for OPS web actions and MCP tools.

The policy intentionally sits above individual tool adapters.  Adapters still
perform their own domain-specific checks (for example backup restore phrases),
but this module gives the UI/MCP layer one consistent contract for risk level,
confirmation requirements, and whether a tool should be taskized later.
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

RISK_LEVELS = ["read", "low", "medium", "high", "critical"]
RISK_RANK = {name: index for index, name in enumerate(RISK_LEVELS)}
RISK_LABELS = {
    "read": "只读",
    "low": "低风险",
    "medium": "中风险",
    "high": "高风险",
    "critical": "严重风险",
}

DEFAULT_CONFIRMATION_REQUIRED_FROM = "medium"
JOB_REQUIRED_FROM = "high"
PROFILE_CONFIRMATION_TOOLS = {"ops.inspection.profile.run", "ops.inspection.profile.retry_issues"}
BATCH_INSPECTION_CONFIRMATION_TOOLS = {"ops.inspection.run_servers_batch"}


@dataclass(frozen=True)
class RiskDecision:
    allowed: bool
    risk_level: str
    risk_label: str
    category: str
    write: bool
    requires_confirmation: bool
    confirmation_required: bool
    expected_confirm_text: str
    confirm_text_matched: bool
    must_create_job: bool
    can_auto_execute: bool
    reasons: List[str]
    next_actions: List[Dict[str, Any]]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "allowed": self.allowed,
            "risk_level": self.risk_level,
            "risk": self.risk_level,
            "risk_label": self.risk_label,
            "category": self.category,
            "write": self.write,
            "requires_confirmation": self.requires_confirmation,
            "confirmation_required": self.confirmation_required,
            "expected_confirm_text": self.expected_confirm_text,
            "confirm_text_matched": self.confirm_text_matched,
            "must_create_job": self.must_create_job,
            "can_auto_execute": self.can_auto_execute,
            "reasons": self.reasons,
            "next_actions": self.next_actions,
        }


def normalize_risk(level: str | None, *, write: bool = False) -> str:
    value = (level or "").strip().lower()
    if value in RISK_RANK:
        return value
    return "medium" if write else "low"


def risk_rank(level: str | None) -> int:
    return RISK_RANK.get(normalize_risk(level), RISK_RANK["low"])


def is_at_least(level: str | None, minimum: str) -> bool:
    return risk_rank(level) >= risk_rank(minimum)


def _plan_confirm_text(db, plan_id: str) -> str:
    if not db or not plan_id:
        return ""
    try:
        from app.db.models import ToolPlan
        plan = db.query(ToolPlan).filter(ToolPlan.id == plan_id).first()
        return str(getattr(plan, "confirm_text", "") or "")
    except Exception:
        return ""


def expected_confirmation_text(tool_def, args: Dict[str, Any], db=None) -> str:
    """Return a deterministic confirmation phrase for a tool invocation.

    Tool adapters with domain-specific phrases still validate them in their own
    handler.  This function is used by discovery, policy previews and the first
    generic MCP guard before the handler is reached.
    """
    name = getattr(tool_def, "name", "") or ""
    args = args or {}
    file_name = str(args.get("file") or args.get("file_name") or args.get("package_name") or "").strip()
    plan_id = str(args.get("plan_id") or "").strip()

    if name == "ops.create_backup":
        return "CREATE BACKUP"
    if name == "ops.restore_backup":
        return f"RESTORE {file_name}" if file_name else "RESTORE <file>"
    if name == "ops.delete_backup":
        return f"DELETE {file_name}" if file_name else "DELETE <file>"
    if name in {"ops.execute_deploy_plan", "ops.execute_rollback_plan"}:
        return _plan_confirm_text(db, plan_id) or f"CONFIRM {name}"
    if name == "ops.cleanup_packages":
        return "CLEANUP PACKAGES"
    if name == "ops.protect_package":
        return f"PROTECT PACKAGE {file_name}" if bool(args.get("protected", True)) else f"UNPROTECT PACKAGE {file_name}"
    if name in PROFILE_CONFIRMATION_TOOLS:
        profile_id = str(args.get("profile_id") or "").strip()
        expected_count = args.get("expected_count")
        fingerprint = str(args.get("fingerprint") or "").strip()
        try:
            if name == "ops.inspection.profile.retry_issues" and db and profile_id and not (expected_count is not None and fingerprint):
                from app.services.inspection_profiles import preview_issue_retry

                confirmation = preview_issue_retry(
                    db,
                    profile_id=profile_id,
                    risk_level=str(args.get("risk_level") or ""),
                    status=str(args.get("status") or ""),
                ).get("confirmation") or {}
                return str(confirmation.get("confirm_text") or "").strip()
            from app.services.inspection_profiles import profile_expected_confirm_text

            return profile_expected_confirm_text(db, profile_id, expected_count=expected_count, fingerprint=fingerprint)
        except Exception:
            if profile_id and expected_count is not None and fingerprint:
                return f"RUN {profile_id} {expected_count} {fingerprint}"
            return f"RUN {profile_id or '<profile>'} <count> <fingerprint>"
    if name in BATCH_INSPECTION_CONFIRMATION_TOOLS:
        try:
            from app.services.tool_adapters.inspection_tools import _batch_preview

            confirmation = _batch_preview(args).get("confirmation") or {}
            return str(confirmation.get("confirm_text") or "").strip() or "确认巡检 <fingerprint>"
        except Exception:
            return "确认巡检 <fingerprint>"
    return f"CONFIRM {name}"


def accepted_confirmation_texts(tool_def, args: Dict[str, Any], db=None) -> List[str]:
    """Return acceptable confirmation phrases, ordered by preferred display text."""
    args = args or {}
    expected = expected_confirmation_text(tool_def, args, db)
    values = [expected]
    name = getattr(tool_def, "name", "") or ""
    if name in PROFILE_CONFIRMATION_TOOLS:
        profile_id = str(args.get("profile_id") or "").strip()
        expected_count = args.get("expected_count")
        fingerprint = str(args.get("fingerprint") or "").strip()
        if profile_id and expected_count is not None and fingerprint:
            try:
                count = int(expected_count)
                values.append(f"RUN INSPECTION {profile_id} {count} {fingerprint}")
            except Exception:
                pass
        elif db and profile_id:
            try:
                if name == "ops.inspection.profile.retry_issues":
                    from app.services.inspection_profiles import preview_issue_retry

                    confirmation = preview_issue_retry(
                        db,
                        profile_id=profile_id,
                        risk_level=str(args.get("risk_level") or ""),
                        status=str(args.get("status") or ""),
                    ).get("confirmation") or {}
                else:
                    from app.services.inspection_profiles import preview_profile

                    confirmation = preview_profile(db, profile_id).get("confirmation") or {}
                values.extend(confirmation.get("accepted_confirm_texts") or [])
            except Exception:
                pass
    if name in BATCH_INSPECTION_CONFIRMATION_TOOLS:
        try:
            from app.services.tool_adapters.inspection_tools import _batch_preview

            confirmation = _batch_preview(args).get("confirmation") or {}
            values.extend(confirmation.get("accepted_confirm_texts") or [])
        except Exception:
            pass
    result: List[str] = []
    seen = set()
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _is_non_destructive_plan_creation(tool_def) -> bool:
    name = getattr(tool_def, "name", "") or ""
    category = getattr(tool_def, "category", "") or ""
    if category == "deploy_plan":
        return True
    return name in {
        "ops.create_config_change_plan",
        "ops.create_rollback_plan",
        "ops.create_deploy_plan",
    }


def _is_dry_run(args: Dict[str, Any]) -> bool:
    # Treat omitted dry_run as true only for tools that are explicitly designed
    # as preview-first cleanup APIs. Runtime cleanup already defaults to dry_run.
    if "dry_run" not in (args or {}):
        return False
    return bool((args or {}).get("dry_run"))


def evaluate_risk_policy(tool_def, args: Optional[Dict[str, Any]] = None, *, ctx=None, db=None, settings: Optional[Dict[str, Any]] = None) -> RiskDecision:
    args = args or {}
    settings = settings or {}
    write = bool(getattr(tool_def, "write", False))
    risk = normalize_risk(getattr(tool_def, "risk", "low"), write=write)
    category = str(getattr(tool_def, "category", "read") or "read")
    requires_confirmation = bool(getattr(tool_def, "requires_confirmation", False))
    force_taskize = bool(getattr(tool_def, "force_taskize", False))
    reasons: List[str] = []

    require_confirmation_globally = bool(settings.get("require_confirmation", True))
    plan_creation = _is_non_destructive_plan_creation(tool_def)
    dry_run = _is_dry_run(args)

    confirmation_required = bool(
        require_confirmation_globally
        and write
        and requires_confirmation
        and not plan_creation
        and not dry_run
        and is_at_least(risk, DEFAULT_CONFIRMATION_REQUIRED_FROM)
    )

    accepted = accepted_confirmation_texts(tool_def, args, db) if confirmation_required else []
    expected = accepted[0] if accepted else ""
    supplied = str(args.get("confirm_text") or "").strip()
    matched = bool(expected and supplied in accepted) if confirmation_required else True

    if confirmation_required:
        reasons.append(f"{RISK_LABELS.get(risk, risk)}写操作需要人工确认")
    if plan_creation:
        reasons.append("该工具只创建计划，不直接执行变更")
    if dry_run:
        reasons.append("dry_run=true，仅预览不执行破坏性操作")

    must_create_job = bool(
        force_taskize
        or (settings.get("taskize_high_risk_tools", True) and write and not dry_run and is_at_least(risk, JOB_REQUIRED_FROM))
    )
    can_auto_execute = bool(not confirmation_required and (not write or risk in {"read", "low"} or dry_run))

    next_actions: List[Dict[str, Any]] = []
    if confirmation_required and not matched:
        next_actions.append({
            "type": "confirmation_required",
            "description": "要求用户明确输入确认短语后再执行。",
            "confirm_text": expected,
            "accepted_confirm_texts": accepted,
        })
    if must_create_job:
        next_actions.append({
            "type": "job_required",
            "description": "后续应通过统一任务中心承载执行进度、日志和失败恢复。",
        })

    return RiskDecision(
        allowed=True,
        risk_level=risk,
        risk_label=RISK_LABELS.get(risk, risk),
        category=category,
        write=write,
        requires_confirmation=requires_confirmation,
        confirmation_required=confirmation_required,
        expected_confirm_text=expected,
        confirm_text_matched=matched,
        must_create_job=must_create_job,
        can_auto_execute=can_auto_execute,
        reasons=reasons,
        next_actions=next_actions,
    )


def enforce_risk_policy(tool_def, args: Optional[Dict[str, Any]], *, ctx=None, db=None, settings: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    decision = evaluate_risk_policy(tool_def, args, ctx=ctx, db=db, settings=settings).to_dict()
    if decision.get("confirmation_required") and not decision.get("confirm_text_matched"):
        expected = decision.get("expected_confirm_text") or ""
        raise HTTPException(
            status_code=428,
            detail={
                "code": "CONFIRMATION_REQUIRED",
                "message": "Tool execution requires explicit confirmation text",
                "tool": getattr(tool_def, "name", ""),
                "risk": decision.get("risk_level"),
                "expected_confirm_text": expected,
                "accepted_confirm_texts": (decision.get("next_actions") or [{}])[0].get("accepted_confirm_texts", []),
                "next_actions": decision.get("next_actions") or [],
            },
        )
    return decision


def risk_policy_manifest() -> Dict[str, Any]:
    return {
        "version": "iter34-risk-policy-v1",
        "levels": [
            {"level": level, "rank": rank, "label": RISK_LABELS[level]}
            for level, rank in sorted(RISK_RANK.items(), key=lambda item: item[1])
        ],
        "rules": {
            "confirmation_required_from": DEFAULT_CONFIRMATION_REQUIRED_FROM,
            "job_required_from": JOB_REQUIRED_FROM,
            "read_low_auto_execute": True,
            "medium_write_requires_confirmation": True,
            "high_critical_should_be_taskized": True,
            "taskize_high_risk_tools_setting": "capability_server.taskize_high_risk_tools",
            "plan_creation_is_non_destructive": True,
        },
        "principles": [
            "MCP tools call service-layer functions only; they must not bypass domain checks.",
            "Medium or higher write tools require explicit confirmation unless they are preview-only or plan-only.",
            "High and critical write tools are queued as unified OperationJob records when taskize_high_risk_tools is enabled.",
            "Every MCP/tool invocation is audited through tool_call_logs.",
        ],
    }
