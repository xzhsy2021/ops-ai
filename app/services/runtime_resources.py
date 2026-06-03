"""Lightweight runtime and storage resource helpers for local-team installs."""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Iterable, List, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.platform import platform_info
from app.core.config import (
    get_app_data_dir,
    get_database_path,
    get_runtime_path,
)
from app.db.models import Deployment, DeployTask, DeployLog, AuditRecord, ToolCallLog, ToolPlan
from app.services.package_retention import cleanup_packages, preview_package_cleanup
from app.services.release_retention import cleanup_release_history, preview_release_cleanup

STARTED_AT = time.time()
_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}


def _uploads_dir() -> str:
    return get_runtime_path("UPLOAD_DIR", "uploads")


def _logs_dir() -> str:
    return get_runtime_path("LOG_DIR", "logs")


def _backups_dir() -> str:
    return get_runtime_path("BACKUP_DIR", "backups")


def _keys_dir() -> str:
    return get_runtime_path("KEYS_DIR", "keys")


def _runtime_dir() -> str:
    return get_runtime_path("RUNTIME_DIR", "runtime")


def _reports_dir() -> str:
    return get_runtime_path("REPORT_DIR", "reports")


def _storage_cache_key() -> str:
    return "|".join([
        get_app_data_dir(),
        get_database_path(),
        _uploads_dir(),
        _logs_dir(),
        _backups_dir(),
        _keys_dir(),
        _runtime_dir(),
        _reports_dir(),
    ])


def _runtime_cache_key() -> str:
    return "|".join([
        os.getenv("MAX_CONCURRENT_DEPLOYS", ""),
        os.getenv("TASK_IDLE_POLL_SECONDS", ""),
        os.getenv("TASK_ACTIVE_POLL_SECONDS", ""),
        os.getenv("MAX_LOG_TAIL_LINES", ""),
    ])

def _cache_ttl_seconds() -> int:
    return _as_int(os.getenv("RESOURCE_CACHE_TTL_SECONDS"), 8)

def _cached_value(key: str, builder):
    ttl = _cache_ttl_seconds()
    if ttl <= 0:
        return builder()
    now = time.time()
    cached = _CACHE.get(key)
    if cached and now - cached[0] < ttl:
        value = dict(cached[1])
        value["cache"] = {"hit": True, "ttl_seconds": ttl, "age_seconds": round(now - cached[0], 3)}
        return value
    value = builder()
    if isinstance(value, dict):
        stored = dict(value)
        stored["cache"] = {"hit": False, "ttl_seconds": ttl, "age_seconds": 0}
        _CACHE[key] = (now, stored)
        return dict(stored)
    return value

DEFAULT_RUNTIME_RETENTION = {
    "log_file_keep_days": 30,
    "backup_keep_days": 90,
    "backup_keep_max": 7,
    "runtime_tmp_keep_hours": 24,
    "dry_run": True,
}

CLEANUP_CONFIRM_TEXT = "确认清理运行时资源"


def _now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _as_int(value: Any, default: int) -> int:
    try:
        return max(0, int(value))
    except Exception:
        return default


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in {"1", "true", "yes", "on"}
    if value is None:
        return default
    return bool(value)


def get_runtime_retention_policy(db: Session) -> Dict[str, Any]:
    from app.db.repository import ConfigRepository
    cfg = ConfigRepository(db).get("runtime_retention") or {}
    if not isinstance(cfg, dict):
        cfg = {}
    merged = {**DEFAULT_RUNTIME_RETENTION, **cfg}
    for key in ["log_file_keep_days", "backup_keep_days", "backup_keep_max", "runtime_tmp_keep_hours"]:
        merged[key] = _as_int(merged.get(key), DEFAULT_RUNTIME_RETENTION[key])
    merged["dry_run"] = _as_bool(merged.get("dry_run"), True)
    return merged


def save_runtime_retention_policy(db: Session, policy: Dict[str, Any]) -> Dict[str, Any]:
    from app.db.repository import ConfigRepository
    merged = {**get_runtime_retention_policy(db), **(policy or {})}
    for key in ["log_file_keep_days", "backup_keep_days", "backup_keep_max", "runtime_tmp_keep_hours"]:
        merged[key] = _as_int(merged.get(key), DEFAULT_RUNTIME_RETENTION[key])
    merged["dry_run"] = _as_bool(merged.get("dry_run"), True)
    ConfigRepository(db).set("runtime_retention", merged)
    db.commit()
    return merged


def format_bytes(value: int) -> str:
    size = float(value or 0)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{int(value or 0)} B"


def _file_stat(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        return {"path": path, "exists": False, "size_bytes": 0, "size_human": "0 B"}
    size = os.path.getsize(path) if os.path.isfile(path) else 0
    return {
        "path": path,
        "exists": True,
        "size_bytes": size,
        "size_human": format_bytes(size),
        "modified_at": datetime.fromtimestamp(os.path.getmtime(path)).isoformat(),
    }


def _dir_size(path: str, *, max_files: int = 20000) -> Dict[str, Any]:
    total = 0
    count = 0
    skipped = 0
    errors: List[str] = []
    if not os.path.exists(path):
        return {"path": path, "exists": False, "size_bytes": 0, "size_human": "0 B", "file_count": 0, "skipped": 0, "errors": []}
    if os.path.isfile(path):
        return {**_file_stat(path), "file_count": 1, "skipped": 0, "errors": []}

    def _onerror(exc: OSError) -> None:
        nonlocal skipped
        skipped += 1
        if len(errors) < 20:
            errors.append(str(exc))

    for root, dirs, files in os.walk(path, onerror=_onerror):
        kept_dirs = []
        for d in dirs:
            dp = os.path.join(root, d)
            try:
                if os.path.islink(dp):
                    skipped += 1
                    continue
            except OSError:
                skipped += 1
                continue
            kept_dirs.append(d)
        dirs[:] = kept_dirs
        for name in files:
            if count >= max_files:
                skipped += 1
                continue
            fp = os.path.join(root, name)
            try:
                if os.path.islink(fp):
                    skipped += 1
                    continue
                total += os.path.getsize(fp)
                count += 1
            except OSError as exc:
                skipped += 1
                if len(errors) < 20:
                    errors.append(str(exc))
    return {"path": path, "exists": True, "size_bytes": total, "size_human": format_bytes(total), "file_count": count, "skipped": skipped, "errors": errors}


def _build_storage_usage(db: Session) -> Dict[str, Any]:
    app_data_dir = get_app_data_dir()
    database_path = get_database_path()
    buckets = {
        "app_data": _dir_size(app_data_dir),
        "database": _file_stat(database_path),
        "uploads": _dir_size(_uploads_dir()),
        "logs": _dir_size(_logs_dir()),
        "backups": _dir_size(_backups_dir()),
        "keys": _dir_size(_keys_dir()),
        "runtime": _dir_size(_runtime_dir()),
        "reports": _dir_size(_reports_dir()),
    }
    total = sum(int(v.get("size_bytes") or 0) for k, v in buckets.items() if k != "app_data")
    table_counts = {}
    for name, model in [
        ("deployments", Deployment),
        ("deploy_tasks", DeployTask),
        ("deploy_logs", DeployLog),
        ("tool_call_logs", ToolCallLog),
        ("tool_plans", ToolPlan),
        ("audit_records", AuditRecord),
    ]:
        try:
            table_counts[name] = int(db.query(func.count(model.id)).scalar() or 0)
        except Exception:
            table_counts[name] = None
    return {
        "generated_at": _now_naive().isoformat(),
        "app_data_dir": app_data_dir,
        "total_managed_size_bytes": total,
        "total_managed_size_human": format_bytes(total),
        "buckets": buckets,
        "table_counts": table_counts,
        "retention": {
            "runtime": get_runtime_retention_policy(db),
        },
    }


def _counts_by_status(db: Session, model, column) -> Dict[str, int]:
    try:
        rows = db.query(column, func.count(model.id)).group_by(column).all()
        return {str(key or "unknown"): int(value or 0) for key, value in rows}
    except Exception:
        return {}


def _ssh_pool_stats() -> Dict[str, Any]:
    try:
        from ssh_client import SSHConnectionPool
        pool = SSHConnectionPool.get_instance()
        if hasattr(pool, "stats"):
            return pool.stats()
        return {"active_connections": len(getattr(pool, "_pool", {}) or {})}
    except Exception as exc:
        return {"active_connections": None, "error": str(exc)}


def _build_runtime_usage(db: Session) -> Dict[str, Any]:
    terminal_sessions = []
    try:
        from app.services.terminal_sessions import list_sessions, get_terminal_limits
        terminal_sessions = list_sessions()
        terminal_limits = get_terminal_limits()
    except Exception:
        terminal_limits = {}

    try:
        import resource  # type: ignore
        usage = resource.getrusage(resource.RUSAGE_SELF)
        max_rss_kb = int(getattr(usage, "ru_maxrss", 0) or 0)
    except Exception:
        max_rss_kb = None

    return {
        "generated_at": _now_naive().isoformat(),
        "process": {
            "pid": os.getpid(),
            "uptime_seconds": int(time.time() - STARTED_AT),
            "max_rss_kb": max_rss_kb,
        },
        "platform": platform_info(),
        "runtime_limits": {
            "max_concurrent_deploys": _as_int(os.getenv("MAX_CONCURRENT_DEPLOYS"), 1),
            "task_idle_poll_seconds": _as_int(os.getenv("TASK_IDLE_POLL_SECONDS"), 15),
            "task_active_poll_seconds": _as_int(os.getenv("TASK_ACTIVE_POLL_SECONDS"), 2),
            "max_log_tail_lines": _as_int(os.getenv("MAX_LOG_TAIL_LINES"), 500),
            **terminal_limits,
        },
        "terminal": {
            "active_sessions": len(terminal_sessions),
            "sessions": terminal_sessions[:20],
        },
        "ssh_pool": _ssh_pool_stats(),
        "deploy_tasks_by_status": _counts_by_status(db, DeployTask, DeployTask.status),
        "deployments_by_status": _counts_by_status(db, Deployment, Deployment.status),
        "recent_tool_calls_24h": _recent_tool_calls(db),
    }


def _recent_tool_calls(db: Session) -> int | None:
    try:
        cutoff = _now_naive() - timedelta(hours=24)
        return int(db.query(func.count(ToolCallLog.id)).filter(ToolCallLog.created_at >= cutoff).scalar() or 0)
    except Exception:
        return None




def get_storage_usage(db: Session) -> Dict[str, Any]:
    return _cached_value(f"storage_usage:{_storage_cache_key()}", lambda: _build_storage_usage(db))


def get_runtime_usage(db: Session) -> Dict[str, Any]:
    return _cached_value(f"runtime_usage:{_runtime_cache_key()}", lambda: _build_runtime_usage(db))


def clear_runtime_resource_cache() -> None:
    _CACHE.clear()


def build_runtime_snapshot(db: Session, force: bool = False) -> Dict[str, Any]:
    if force:
        storage_key = f"storage_usage:{_storage_cache_key()}"
        runtime_key = f"runtime_usage:{_runtime_cache_key()}"
        _CACHE.pop(storage_key, None)
        _CACHE.pop(runtime_key, None)
    storage = get_storage_usage(db)
    runtime = get_runtime_usage(db)
    return {
        "generated_at": _now_naive().isoformat(),
        "storage": storage,
        "runtime": runtime,
        "cache": storage.get("cache", {"hit": False}),
    }

def _iter_files(path: str) -> Iterable[Tuple[str, os.stat_result]]:
    if not os.path.isdir(path):
        return []
    rows: List[Tuple[str, os.stat_result]] = []
    for name in os.listdir(path):
        fp = os.path.join(path, name)
        if not os.path.isfile(fp) or os.path.islink(fp):
            continue
        try:
            rows.append((fp, os.stat(fp)))
        except OSError:
            continue
    return rows


def _file_candidates_for_age(path: str, *, days: int = 0, hours: int = 0, exclude: set[str] | None = None) -> List[Dict[str, Any]]:
    exclude = exclude or set()
    seconds = days * 86400 + hours * 3600
    cutoff = time.time() - seconds
    items = []
    for fp, st in _iter_files(path):
        if os.path.basename(fp) in exclude:
            continue
        if st.st_mtime < cutoff:
            items.append({
                "path": fp,
                "name": os.path.basename(fp),
                "size_bytes": int(st.st_size),
                "modified_at": datetime.fromtimestamp(st.st_mtime).isoformat(),
                "reason": f"age>{days}d" if days else f"age>{hours}h",
            })
    return items


def _backup_candidates(path: str, *, keep_days: int, keep_max: int) -> List[Dict[str, Any]]:
    rows = sorted(_iter_files(path), key=lambda item: item[1].st_mtime, reverse=True)
    cutoff = time.time() - keep_days * 86400
    candidates: Dict[str, Dict[str, Any]] = {}
    for idx, (fp, st) in enumerate(rows):
        if keep_max and idx >= keep_max:
            candidates[fp] = {
                "path": fp,
                "name": os.path.basename(fp),
                "size_bytes": int(st.st_size),
                "modified_at": datetime.fromtimestamp(st.st_mtime).isoformat(),
                "reason": f"exceed_max>{keep_max}",
            }
        elif st.st_mtime < cutoff:
            candidates[fp] = {
                "path": fp,
                "name": os.path.basename(fp),
                "size_bytes": int(st.st_size),
                "modified_at": datetime.fromtimestamp(st.st_mtime).isoformat(),
                "reason": f"age>{keep_days}d",
            }
    return list(candidates.values())


def _delete_files(candidates: List[Dict[str, Any]], dry_run: bool) -> Dict[str, Any]:
    size = sum(int(x.get("size_bytes") or 0) for x in candidates)
    result = {"count": len(candidates), "size_bytes": size, "size_human": format_bytes(size), "sample": candidates[:50], "candidates": candidates, "deleted": [], "errors": []}
    if dry_run:
        return result
    for item in candidates:
        fp = item.get("path")
        try:
            if fp and os.path.isfile(fp) and not os.path.islink(fp):
                os.remove(fp)
                result["deleted"].append({"path": fp, "size_bytes": item.get("size_bytes"), "reason": item.get("reason")})
        except Exception as exc:
            result["errors"].append({"path": fp, "error": str(exc)})
    return result


def preview_runtime_cleanup(db: Session, policy: Dict[str, Any] | None = None) -> Dict[str, Any]:
    policy_n = {**get_runtime_retention_policy(db), **(policy or {})}
    for key in ["log_file_keep_days", "backup_keep_days", "backup_keep_max", "runtime_tmp_keep_hours"]:
        policy_n[key] = _as_int(policy_n.get(key), DEFAULT_RUNTIME_RETENTION[key])
    logs = _file_candidates_for_age(_logs_dir(), days=policy_n["log_file_keep_days"], exclude={"ops.log"})
    backups = _backup_candidates(_backups_dir(), keep_days=policy_n["backup_keep_days"], keep_max=policy_n["backup_keep_max"])
    runtime_tmp = _file_candidates_for_age(_runtime_dir(), hours=policy_n["runtime_tmp_keep_hours"])
    file_size = sum(int(x.get("size_bytes") or 0) for x in [*logs, *backups, *runtime_tmp])
    release_preview = preview_release_cleanup(db)
    package_preview = preview_package_cleanup(db)
    return {
        "policy": policy_n,
        "confirm_text": CLEANUP_CONFIRM_TEXT,
        "files": {
            "logs": _delete_files(logs, True),
            "backups": _delete_files(backups, True),
            "runtime_tmp": _delete_files(runtime_tmp, True),
        },
        "database": release_preview,
        "packages": package_preview,
        "summary": {
            "file_cleanup_size_bytes": file_size,
            "file_cleanup_size_human": format_bytes(file_size),
            "package_cleanup_size_bytes": int(package_preview.get("summary", {}).get("cleanup_size_bytes") or 0),
            "database_candidate_counts": release_preview.get("candidate_counts", {}),
        },
    }


def cleanup_runtime_artifacts(
    db: Session,
    *,
    dry_run: bool = True,
    confirm_text: str = "",
    actor: str = "",
    policy: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    if not dry_run and (confirm_text or "").strip() != CLEANUP_CONFIRM_TEXT:
        raise ValueError(f"cleanup confirmation required: {CLEANUP_CONFIRM_TEXT}")
    preview = preview_runtime_cleanup(db, policy)
    policy_n = preview["policy"]
    result = {**preview, "dry_run": dry_run, "removed": {}}
    if dry_run:
        return result

    log_candidates = preview["files"]["logs"].get("candidates") or []
    backup_candidates = preview["files"]["backups"].get("candidates") or []
    runtime_candidates = preview["files"]["runtime_tmp"].get("candidates") or []
    result["removed"]["logs"] = _delete_files(log_candidates, False)
    result["removed"]["backups"] = _delete_files(backup_candidates, False)
    result["removed"]["runtime_tmp"] = _delete_files(runtime_candidates, False)
    result["removed"]["database"] = cleanup_release_history(db, dry_run=False)
    result["removed"]["packages"] = cleanup_packages(db, policy=None, dry_run=False, actor=actor)
    try:
        from config_manager import save_audit_log
        save_audit_log(
            "runtime.cleanup.execute",
            "runtime",
            "local",
            f"actor={actor or '-'} files={result['removed'].get('logs', {}).get('count', 0)} dry_run={dry_run}",
        )
    except Exception:
        pass
    clear_runtime_resource_cache()
    return result
