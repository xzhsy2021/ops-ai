from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.db.repository import ConfigRepository

PROFILE_CONFIG_KEY = "inspection_profiles"

LIGHTWEIGHT_CATEGORIES = ["DISK", "MEMORY", "SERVICE_STATUS", "BACKUP"]
SECURITY_CATEGORIES = ["LOGIN_SECURITY", "ACCOUNT_SECURITY", "COMMAND_HISTORY", "PROCESS_PORT", "FIREWALL"]


def _all_server_categories() -> List[str]:
    from app.services.inspection_center import SERVER_CATEGORIES

    return [str(item.get("code")) for item in SERVER_CATEGORIES if item.get("code")]


def _default_profiles() -> List[Dict[str, Any]]:
    all_categories = _all_server_categories()
    return [
        {
            "id": "daily-lite",
            "name": "日常轻量巡检",
            "description": "面向全部在线服务器的日常资源、服务和备份巡检。",
            "tier": "DAILY",
            "enabled": True,
            "target": {"all_servers": True, "groups": [], "include_keywords": [], "exclude_keywords": []},
            "categories": LIGHTWEIGHT_CATEGORIES,
            "concurrency": 4,
            "batch_size": 8,
            "command_timeout_seconds": 25,
            "run_timeout_seconds": 240,
            "skip_disabled": True,
            "generate_report": True,
            "report_format": "md",
            "requires_confirmation": True,
        },
        {
            "id": "weekly-security",
            "name": "每周安全巡检",
            "description": "面向全部在线服务器的登录、账号、命令、端口和防火墙巡检。",
            "tier": "WEEKLY",
            "enabled": True,
            "target": {"all_servers": True, "groups": [], "include_keywords": [], "exclude_keywords": []},
            "categories": SECURITY_CATEGORIES,
            "concurrency": 3,
            "batch_size": 6,
            "command_timeout_seconds": 45,
            "run_timeout_seconds": 480,
            "skip_disabled": True,
            "generate_report": True,
            "report_format": "md",
            "requires_confirmation": True,
        },
        {
            "id": "monthly-full",
            "name": "月度全量巡检",
            "description": "面向全部在线服务器的全量巡检，默认降低并发。",
            "tier": "MONTHLY",
            "enabled": True,
            "target": {"all_servers": True, "groups": [], "include_keywords": [], "exclude_keywords": []},
            "categories": all_categories,
            "concurrency": 2,
            "batch_size": 5,
            "command_timeout_seconds": 60,
            "run_timeout_seconds": 900,
            "skip_disabled": True,
            "generate_report": True,
            "report_format": "md",
            "requires_confirmation": True,
        },
        {
            "id": "crypto-test-daily",
            "name": "Crypto 测试服务器日巡",
            "description": "crypto 分组内测试服务器的常用轻量巡检方案。",
            "tier": "DAILY",
            "enabled": True,
            "target": {"all_servers": False, "groups": ["crypto"], "include_keywords": ["测试", "test"], "exclude_keywords": ["prod", "生产"]},
            "categories": LIGHTWEIGHT_CATEGORIES,
            "concurrency": 2,
            "batch_size": 5,
            "command_timeout_seconds": 30,
            "run_timeout_seconds": 300,
            "skip_disabled": True,
            "generate_report": True,
            "report_format": "md",
            "requires_confirmation": True,
        },
    ]


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple) or isinstance(value, set):
        return list(value)
    return [value]


def _strings(value: Any) -> List[str]:
    result: List[str] = []
    seen = set()
    for item in _as_list(value):
        text = str(item or "").strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _int_in_range(value: Any, default: int, low: int, high: int) -> int:
    try:
        number = int(value)
    except Exception:
        number = default
    return max(low, min(number, high))


def _normalize_profile(raw: Dict[str, Any]) -> Dict[str, Any]:
    from app.services.inspection_center import (
        DEFAULT_BATCH_CONCURRENCY,
        DEFAULT_BATCH_SIZE,
        DEFAULT_COMMAND_TIMEOUT_SECONDS,
        DEFAULT_RUN_TIMEOUT_SECONDS,
        MAX_BATCH_CONCURRENCY,
        MAX_BATCH_SIZE,
        MAX_COMMAND_TIMEOUT_SECONDS,
        MAX_RUN_TIMEOUT_SECONDS,
    )

    profile_id = str(raw.get("id") or "").strip()
    target = raw.get("target") if isinstance(raw.get("target"), dict) else {}
    categories = _strings(raw.get("categories")) or LIGHTWEIGHT_CATEGORIES
    return {
        "id": profile_id,
        "name": str(raw.get("name") or profile_id).strip() or profile_id,
        "description": str(raw.get("description") or "").strip(),
        "tier": str(raw.get("tier") or "MANUAL").strip().upper() or "MANUAL",
        "enabled": bool(raw.get("enabled", True)),
        "target": {
            "all_servers": bool(target.get("all_servers", False)),
            "server_ids": _strings(target.get("server_ids")),
            "groups": _strings(target.get("groups")),
            "include_keywords": _strings(target.get("include_keywords")),
            "exclude_keywords": _strings(target.get("exclude_keywords")),
        },
        "categories": categories,
        "concurrency": _int_in_range(raw.get("concurrency"), DEFAULT_BATCH_CONCURRENCY, 1, MAX_BATCH_CONCURRENCY),
        "batch_size": _int_in_range(raw.get("batch_size"), DEFAULT_BATCH_SIZE, 1, MAX_BATCH_SIZE),
        "command_timeout_seconds": _int_in_range(raw.get("command_timeout_seconds"), DEFAULT_COMMAND_TIMEOUT_SECONDS, 5, MAX_COMMAND_TIMEOUT_SECONDS),
        "run_timeout_seconds": _int_in_range(raw.get("run_timeout_seconds"), DEFAULT_RUN_TIMEOUT_SECONDS, 30, MAX_RUN_TIMEOUT_SECONDS),
        "skip_disabled": bool(raw.get("skip_disabled", True)),
        "generate_report": bool(raw.get("generate_report", True)),
        "report_format": str(raw.get("report_format") or "md").strip().lower() or "md",
        "requires_confirmation": bool(raw.get("requires_confirmation", True)),
    }


def _configured_profiles(db: Session) -> List[Dict[str, Any]]:
    stored = ConfigRepository(db).get(PROFILE_CONFIG_KEY)
    if isinstance(stored, dict):
        stored = stored.get("items")
    if not isinstance(stored, list):
        stored = []
    defaults = {item["id"]: item for item in _default_profiles()}
    merged = {key: deepcopy(value) for key, value in defaults.items()}
    for item in stored:
        if not isinstance(item, dict):
            continue
        profile_id = str(item.get("id") or "").strip()
        if not profile_id:
            continue
        base = merged.get(profile_id, {})
        merged[profile_id] = {**base, **item}
    return [_normalize_profile(item) for item in merged.values()]


def list_profiles(db: Session, *, include_disabled: bool = False) -> Dict[str, Any]:
    items = [item for item in _configured_profiles(db) if include_disabled or item.get("enabled", True)]
    return {"items": items, "total": len(items)}


def get_profile(db: Session, profile_id: str) -> Dict[str, Any]:
    wanted = str(profile_id or "").strip()
    for profile in _configured_profiles(db):
        if profile["id"] == wanted:
            return profile
    raise HTTPException(status_code=404, detail=f"Inspection profile not found: {wanted}")


def _server_text(server: Dict[str, Any]) -> str:
    fields = [
        server.get("id"),
        server.get("asset_id"),
        server.get("name"),
        server.get("host"),
        server.get("ip"),
        server.get("group"),
    ]
    return " ".join(str(value or "").lower() for value in fields)


def _filter_eligible(eligible: List[Dict[str, Any]], eligible_ids: List[str], target: Dict[str, Any]) -> Dict[str, Any]:
    includes = [item.lower() for item in _strings(target.get("include_keywords"))]
    excludes = [item.lower() for item in _strings(target.get("exclude_keywords"))]
    if not includes and not excludes:
        return {"eligible": eligible, "eligible_ids": eligible_ids, "filtered_out": []}

    kept: List[Dict[str, Any]] = []
    kept_ids: List[str] = []
    filtered_out: List[Dict[str, Any]] = []
    for index, server in enumerate(eligible):
        text = _server_text(server)
        include_ok = not includes or any(keyword in text for keyword in includes)
        exclude_hit = bool(excludes and any(keyword in text for keyword in excludes))
        if include_ok and not exclude_hit:
            kept.append(server)
            kept_ids.append(eligible_ids[index] if index < len(eligible_ids) else str(server.get("name") or server.get("host") or server.get("id") or ""))
        else:
            filtered_out.append({
                "server_id": server.get("id") or server.get("asset_id") or server.get("name") or server.get("host"),
                "name": server.get("name"),
                "host": server.get("host"),
                "group": server.get("group"),
                "status": server.get("status"),
                "reason": "filtered_by_profile_keywords",
            })
    return {"eligible": kept, "eligible_ids": [item for item in kept_ids if item], "filtered_out": filtered_out}


def resolve_profile_targets(db: Session, profile: Dict[str, Any]) -> Dict[str, Any]:
    from app.services.inspection_center import resolve_servers_for_inspection

    target = profile.get("target") or {}
    resolved = resolve_servers_for_inspection(
        target.get("server_ids") or [],
        all_servers=bool(target.get("all_servers")),
        skip_disabled=bool(profile.get("skip_disabled", True)),
        groups=target.get("groups") or None,
    )
    filtered = _filter_eligible(resolved.get("eligible") or [], resolved.get("eligible_ids") or [], target)
    skipped = list(resolved.get("skipped") or [])
    return {
        **resolved,
        "eligible": filtered["eligible"],
        "eligible_ids": filtered["eligible_ids"],
        "eligible_count": len(filtered["eligible_ids"]),
        "skipped": skipped,
        "skipped_count": len(skipped),
        "filtered_out": filtered["filtered_out"],
        "filtered_count": len(filtered["filtered_out"]),
    }


def confirmation_fingerprint(profile: Dict[str, Any], resolved: Dict[str, Any]) -> str:
    payload = {
        "profile_id": profile.get("id"),
        "target_ids": resolved.get("eligible_ids") or [],
        "categories": profile.get("categories") or [],
        "concurrency": profile.get("concurrency"),
        "batch_size": profile.get("batch_size"),
        "command_timeout_seconds": profile.get("command_timeout_seconds"),
        "run_timeout_seconds": profile.get("run_timeout_seconds"),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]


def build_confirmation(profile: Dict[str, Any], resolved: Dict[str, Any]) -> Dict[str, Any]:
    count = int(resolved.get("eligible_count") or 0)
    fingerprint = confirmation_fingerprint(profile, resolved)
    text = f"RUN {profile.get('id')} {count} {fingerprint}"
    legacy_text = f"RUN INSPECTION {profile.get('id')} {count} {fingerprint}"
    return {
        "confirm_text": text,
        "expected_confirm_text": text,
        "legacy_confirm_text": legacy_text,
        "accepted_confirm_texts": [text, legacy_text],
        "fingerprint": fingerprint,
        "target_count": count,
        "mode": "copy",
        "description": "复制该短语确认本次巡检目标、巡检项和执行参数。",
    }


def preview_profile(db: Session, profile_id: str, overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    profile = get_profile(db, profile_id)
    if overrides:
        profile = _normalize_profile({**profile, **overrides})
    resolved = resolve_profile_targets(db, profile)
    confirmation = build_confirmation(profile, resolved)
    return {
        "profile": profile,
        "profile_id": profile["id"],
        "eligible": resolved.get("eligible") or [],
        "eligible_ids": resolved.get("eligible_ids") or [],
        "eligible_count": resolved.get("eligible_count") or 0,
        "skipped": resolved.get("skipped") or [],
        "skipped_count": resolved.get("skipped_count") or 0,
        "filtered_out": resolved.get("filtered_out") or [],
        "filtered_count": resolved.get("filtered_count") or 0,
        "total_requested": resolved.get("total_requested") or 0,
        "categories": profile.get("categories") or [],
        "concurrency": profile.get("concurrency"),
        "batch_size": profile.get("batch_size"),
        "command_timeout_seconds": profile.get("command_timeout_seconds"),
        "run_timeout_seconds": profile.get("run_timeout_seconds"),
        "generate_report": profile.get("generate_report"),
        "report_format": profile.get("report_format"),
        "confirmation": confirmation,
        "summary": f"{profile.get('name')} 将巡检 {resolved.get('eligible_count') or 0} 台服务器，跳过 {resolved.get('skipped_count') or 0} 台。",
    }


def profile_expected_confirm_text(db: Session, profile_id: str, *, expected_count: Any = None, fingerprint: str = "") -> str:
    profile_id = str(profile_id or "").strip()
    fingerprint = str(fingerprint or "").strip()
    if profile_id and expected_count is not None and fingerprint:
        try:
            count = int(expected_count)
            return f"RUN {profile_id} {count} {fingerprint}"
        except Exception:
            pass
    if profile_id:
        try:
            return preview_profile(db, profile_id)["confirmation"]["confirm_text"]
        except Exception:
            return f"RUN {profile_id} <count> <fingerprint>"
    return "RUN <profile> <count> <fingerprint>"


def _validate_confirmation(preview: Dict[str, Any], confirm_text: str) -> None:
    confirmation = preview.get("confirmation") or {}
    expected = confirmation.get("confirm_text") or ""
    accepted = [str(item or "").strip() for item in (confirmation.get("accepted_confirm_texts") or [expected]) if str(item or "").strip()]
    supplied = str(confirm_text or "").strip()
    if supplied not in accepted:
        raise HTTPException(
            status_code=428,
            detail={
                "code": "CONFIRMATION_REQUIRED",
                "message": "Inspection profile execution requires current confirmation text",
                "expected_confirm_text": expected,
                "accepted_confirm_texts": accepted,
                "confirmation": confirmation,
                "preview": {
                    "profile_id": preview.get("profile_id"),
                    "eligible_count": preview.get("eligible_count"),
                    "skipped_count": preview.get("skipped_count"),
                    "categories": preview.get("categories"),
                },
            },
        )


def run_profile(db: Session, profile_id: str, *, confirm_text: str, created_by: str = "", overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    preview = preview_profile(db, profile_id, overrides=overrides)
    if not int(preview.get("eligible_count") or 0):
        raise HTTPException(status_code=400, detail={"message": "No inspectable servers resolved for profile", "preview": preview})
    _validate_confirmation(preview, confirm_text)

    profile = preview["profile"]
    from app.services.inspection_center import generate_report_for_runs, run_servers_batch_inspection

    target = profile.get("target") or {}
    batch_result = run_servers_batch_inspection(
        db,
        server_ids=preview.get("eligible_ids") or [],
        categories=profile.get("categories") or [],
        trigger_type=str(profile.get("tier") or "PROFILE").upper(),
        created_by=created_by or "inspection-profile",
        generate_report=False,
        concurrency=profile.get("concurrency"),
        batch_size=profile.get("batch_size"),
        command_timeout_seconds=profile.get("command_timeout_seconds"),
        run_timeout_seconds=profile.get("run_timeout_seconds"),
        skip_disabled=bool(profile.get("skip_disabled", True)),
        all_servers=False,
        groups=target.get("groups") or None,
    )

    run_ids = [str(item.get("id")) for item in (batch_result.get("runs") or []) if isinstance(item, dict) and item.get("id")]
    report = None
    report_error = ""
    if profile.get("generate_report") and run_ids:
        try:
            report_result = generate_report_for_runs(
                db,
                run_ids,
                fmt=profile.get("report_format") or "md",
                title=f"{profile.get('name') or profile.get('id')} 批量巡检报告",
                created_by=created_by or "inspection-profile",
            )
            report = report_result.get("report") or report_result
        except Exception as exc:  # pragma: no cover - defensive reporting path
            report_error = str(exc)

    return {
        **batch_result,
        "profile": profile,
        "profile_id": profile.get("id"),
        "preview": preview,
        "confirmation": preview.get("confirmation"),
        "run_ids": run_ids,
        "report": report,
        "report_error": report_error,
        "summary": batch_result.get("summary") or f"{profile.get('name')} 巡检完成。",
    }
