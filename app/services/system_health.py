"""Small-team local runtime health and maintenance diagnostics."""
from __future__ import annotations

import os
import shutil
import time
from pathlib import Path
from typing import Any, Dict

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import get_app_data_dir, get_database_path, get_runtime_path
from app.services.recommendations import build_recommendations


def _status_from_bool(ok: bool, message: str = "") -> Dict[str, Any]:
    return {"status": "ok" if ok else "error", "message": message}


def _dir_check(label: str, path: str) -> Dict[str, Any]:
    p = Path(path)
    result: Dict[str, Any] = {
        "status": "ok" if p.exists() and p.is_dir() else "error",
        "path": str(p),
        "message": "目录可用" if p.exists() and p.is_dir() else "目录不存在或不是目录",
    }
    try:
        if p.exists():
            test_file = p / ".ops_write_test"
            test_file.write_text("ok", encoding="utf-8")
            test_file.unlink(missing_ok=True)
            result["writable"] = True
        else:
            result["writable"] = False
    except Exception as exc:
        result["status"] = "error"
        result["writable"] = False
        result["message"] = f"目录不可写: {exc}"
    result["label"] = label
    return result


def _backup_summary() -> Dict[str, Any]:
    backup_dir = Path(get_runtime_path("BACKUP_DIR", "backups"))
    backup_dir.mkdir(parents=True, exist_ok=True)
    backups = sorted(backup_dir.glob("ops_backup_*"), key=lambda p: p.stat().st_mtime, reverse=True)
    latest = backups[0] if backups else None
    now = time.time()
    data: Dict[str, Any] = {
        "status": "ok" if latest else "warn",
        "path": str(backup_dir),
        "count": len(backups),
        "message": "已有备份" if latest else "暂无备份，建议立即创建一次备份",
    }
    if latest:
        age_hours = round((now - latest.stat().st_mtime) / 3600, 1)
        data.update({
            "latest_file": latest.name,
            "latest_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(latest.stat().st_mtime)),
            "latest_age_hours": age_hours,
            "latest_size_mb": round(latest.stat().st_size / (1024 * 1024), 2),
            "stale": age_hours > 24 * 7,
        })
        if age_hours > 24 * 7:
            data["status"] = "warn"
            data["message"] = "最近一次备份已超过 7 天，建议创建新备份"
    return data


def _deploy_worker_summary() -> Dict[str, Any]:
    try:
        from app.api import deploy_v2

        # iter19 moved deploy worker lifecycle state behind AsyncWorkerHandle.
        # Keep this health check compatible with both the new handle and older
        # module-level task attributes so /system/health never reports a false
        # error after upgrades.
        handle = getattr(deploy_v2, "_deploy_worker", None)
        if handle is not None and hasattr(handle, "status"):
            status_data = dict(handle.status() or {})
            running = bool(status_data.get("running"))
            return {
                "status": "ok" if running else "warn",
                "running": running,
                "name": status_data.get("name") or "deploy-worker",
                "started_at": status_data.get("started_at"),
                "message": "发布 Worker 运行中" if running else "发布 Worker 未运行或尚未启动",
            }

        legacy_task = getattr(deploy_v2, "_deploy_worker_task", None)
        legacy_started_at = getattr(deploy_v2, "_deploy_worker_started_at", None)
        running = bool(legacy_task and not legacy_task.done())
        return {
            "status": "ok" if running else "warn",
            "running": running,
            "name": "deploy-worker",
            "started_at": legacy_started_at.isoformat() if legacy_started_at else None,
            "message": "发布 Worker 运行中" if running else "发布 Worker 未运行或尚未启动",
        }
    except Exception as exc:
        return {"status": "warn", "running": False, "message": f"无法读取 Worker 状态: {exc}"}


def build_system_health(db: Session) -> Dict[str, Any]:
    checks: Dict[str, Dict[str, Any]] = {}

    try:
        db.execute(text("SELECT 1")).scalar_one_or_none()
        checks["database"] = {"status": "ok", "message": "SELECT 1 ok"}
    except Exception as exc:
        checks["database"] = {"status": "error", "message": str(exc)}

    try:
        db_path = Path(get_database_path())
        checks["database"].update({
            "path": str(db_path),
            "exists": db_path.exists(),
            "size_mb": round(db_path.stat().st_size / (1024 * 1024), 2) if db_path.exists() else 0,
        })
        if db_path.exists() and db_path.stat().st_size > 100 * 1024 * 1024:
            checks["database"]["warning"] = True
            checks["database"]["message"] = "数据库文件超过 100MB，建议检查历史数据和备份策略"
    except Exception as exc:
        checks["database"]["size_check_error"] = str(exc)

    secret_key = os.getenv("OPS_SECRET_KEY") or os.getenv("SESSION_SECRET")
    checks["secret_key"] = {
        "status": "ok" if secret_key and len(secret_key) >= 24 else "warn",
        "configured": bool(secret_key),
        "message": "密钥已配置" if secret_key else "未配置 OPS_SECRET_KEY，凭据加密会退回开发兼容模式",
    }
    if secret_key and len(secret_key) < 24:
        checks["secret_key"]["message"] = "密钥长度偏短，建议使用至少 32 字符随机字符串"

    runtime_dirs = {
        "app_data": get_app_data_dir(),
        "uploads": get_runtime_path("UPLOAD_DIR", "uploads"),
        "keys": get_runtime_path("KEYS_DIR", "keys"),
        "backups": get_runtime_path("BACKUP_DIR", "backups"),
        "logs": get_runtime_path("LOG_DIR", "logs"),
    }
    dir_results = {name: _dir_check(name, path) for name, path in runtime_dirs.items()}
    checks["runtime_dirs"] = {
        "status": "ok" if all(x["status"] == "ok" for x in dir_results.values()) else "error",
        "items": dir_results,
        "message": "运行目录正常" if all(x["status"] == "ok" for x in dir_results.values()) else "部分运行目录不可用",
    }

    try:
        disk = shutil.disk_usage(get_app_data_dir())
        percent = round(disk.used / disk.total * 100, 1)
        checks["disk"] = {
            "status": "ok" if percent < 85 else "warn" if percent < 95 else "error",
            "total_gb": round(disk.total / (1024 ** 3), 1),
            "used_gb": round(disk.used / (1024 ** 3), 1),
            "free_gb": round(disk.free / (1024 ** 3), 1),
            "percent": percent,
            "message": "磁盘空间正常" if percent < 85 else "磁盘空间偏高，请清理日志、发布包或旧备份",
        }
    except Exception as exc:
        checks["disk"] = {"status": "error", "message": str(exc)}

    checks["backups"] = _backup_summary()
    checks["deploy_worker"] = _deploy_worker_summary()

    errors = sum(1 for c in checks.values() if c.get("status") == "error")
    warnings = sum(1 for c in checks.values() if c.get("status") == "warn")
    status = "healthy" if errors == 0 and warnings == 0 else "degraded" if errors == 0 else "unhealthy"
    data = {
        "status": status,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "summary": {"errors": errors, "warnings": warnings, "checks": len(checks)},
        "checks": checks,
    }
    data["recommendations"] = build_recommendations(data)
    try:
        from app.services.build_info import get_frontend_build_check
        check = get_frontend_build_check()
        data["frontend_dist_stale"] = check.get("dist_stale", False)
    except Exception:
        data["frontend_dist_stale"] = False
    return data
