from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

from app.api.helpers import audit
from app.core.config import get_runtime_path

RESTORE_CONFIRM_PREFIX = "RESTORE "
DELETE_CONFIRM_PREFIX = "DELETE "
DEFAULT_BACKUP_KEEP_MAX = 10


@dataclass
class BackupServiceError(Exception):
    message: str
    status_code: int = 400

    def __str__(self) -> str:  # pragma: no cover - dataclass repr is noisy in API errors
        return self.message


def _database_url() -> str:
    from app.db.base import DATABASE_URL
    return DATABASE_URL


def get_sqlite_db_path() -> Path:
    database_url = _database_url()
    if not database_url.startswith("sqlite:///"):
        raise BackupServiceError("Database backup is only supported for sqlite", 400)
    db_path = Path(database_url.replace("sqlite:///", "", 1)).expanduser()
    if not db_path.is_absolute():
        db_path = db_path.resolve()
    return db_path


def get_backup_dir() -> Path:
    path = Path(get_runtime_path("BACKUP_DIR", "backups")).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _timestamp() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


def _format_time(ts: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


def _backup_kind(file_name: str) -> str:
    if file_name.startswith("ops_backup_before_restore_"):
        return "before_restore"
    if file_name.startswith("ops_backup_before_config_import_"):
        return "before_config_import"
    if file_name.startswith("ops_backup_mcp_"):
        return "mcp"
    return "manual"


def _human_size(size: int) -> str:
    units = ["B", "KB", "MB", "GB"]
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.2f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{size} B"


def expected_restore_confirm_text(file_name: str) -> str:
    return f"{RESTORE_CONFIRM_PREFIX}{file_name}"


def expected_delete_confirm_text(file_name: str) -> str:
    return f"{DELETE_CONFIRM_PREFIX}{file_name}"


def resolve_backup_file(file_name: str, *, must_exist: bool = True) -> Path:
    if not file_name or Path(file_name).name != file_name:
        raise BackupServiceError("Invalid backup file name", 400)
    if not file_name.startswith("ops_backup_") or not file_name.endswith(".db"):
        raise BackupServiceError("Invalid backup file name", 400)
    backup_dir = get_backup_dir().resolve()
    backup_path = (backup_dir / file_name).resolve()
    if backup_path.parent != backup_dir:
        raise BackupServiceError("Invalid backup file name", 400)
    if must_exist and not backup_path.exists():
        raise BackupServiceError(f"Backup file not found: {file_name}", 404)
    return backup_path


def _sha256_file(path: Path, *, limit_bytes: int | None = None) -> str:
    h = hashlib.sha256()
    read_total = 0
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            if limit_bytes is not None and read_total + len(chunk) > limit_bytes:
                chunk = chunk[: max(0, limit_bytes - read_total)]
            if not chunk:
                break
            h.update(chunk)
            read_total += len(chunk)
            if limit_bytes is not None and read_total >= limit_bytes:
                break
    return h.hexdigest()


def verify_backup_file(file_name: str, *, include_checksum: bool = True) -> Dict[str, Any]:
    path = resolve_backup_file(file_name)
    stat = path.stat()
    result: Dict[str, Any] = {
        "file": path.name,
        "size": stat.st_size,
        "size_human": _human_size(stat.st_size),
        "created_at": _format_time(stat.st_mtime),
        "modified_at": _format_time(stat.st_mtime),
        "kind": _backup_kind(path.name),
        "valid": False,
        "status": "failed",
        "checks": [],
        "errors": [],
    }
    if stat.st_size <= 0:
        result["errors"].append("backup file is empty")
        return result
    result["checks"].append("file_exists")
    try:
        uri = f"file:{path.as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=5)
        try:
            quick_check = conn.execute("PRAGMA quick_check").fetchone()
            quick_value = quick_check[0] if quick_check else "no_result"
            result["sqlite_quick_check"] = quick_value
            if str(quick_value).lower() == "ok":
                result["checks"].append("sqlite_quick_check")
            else:
                result["errors"].append(f"sqlite quick_check failed: {quick_value}")
            table_count = conn.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
            result["table_count"] = int(table_count or 0)
            if result["table_count"] > 0:
                result["checks"].append("sqlite_schema")
        finally:
            conn.close()
    except Exception as exc:
        result["errors"].append(f"sqlite open/check failed: {exc}")
    if include_checksum:
        try:
            result["sha256"] = _sha256_file(path)
            result["checks"].append("sha256")
        except Exception as exc:
            result["errors"].append(f"sha256 failed: {exc}")
    result["valid"] = not result["errors"] and "sqlite_quick_check" in result["checks"]
    result["status"] = "ok" if result["valid"] else "failed"
    result["message"] = "备份校验通过" if result["valid"] else "备份校验失败"
    return result


def _backup_meta(path: Path) -> Dict[str, Any]:
    stat = path.stat()
    return {
        "file": path.name,
        "size": stat.st_size,
        "size_human": _human_size(stat.st_size),
        "created_at": _format_time(stat.st_mtime),
        "modified_at": _format_time(stat.st_mtime),
        "age_seconds": max(0, int(time.time() - stat.st_mtime)),
        "kind": _backup_kind(path.name),
        "restore_confirm_text": expected_restore_confirm_text(path.name),
        "delete_confirm_text": expected_delete_confirm_text(path.name),
        "verification": {"status": "unchecked", "valid": None},
    }


def list_database_backups(*, verify_latest: bool = False) -> List[Dict[str, Any]]:
    backup_dir = get_backup_dir()
    backups = sorted(backup_dir.glob("ops_backup_*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    rows = [_backup_meta(path) for path in backups if path.is_file()]
    if verify_latest and rows:
        try:
            rows[0]["verification"] = verify_backup_file(rows[0]["file"], include_checksum=False)
        except BackupServiceError as exc:
            rows[0]["verification"] = {"status": "failed", "valid": False, "message": exc.message}
    return rows


def _copy_sqlite_consistent(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        src_uri = f"file:{src.as_posix()}?mode=ro"
        source = sqlite3.connect(src_uri, uri=True, timeout=10)
        target = sqlite3.connect(str(dst), timeout=10)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()
    except Exception:
        # Some SQLite files may be copied before the app has initialized them.
        # Fall back to byte copy, matching the legacy behavior.
        shutil.copy2(src, dst)


def _apply_retention(keep_max: int | None = None) -> List[str]:
    if keep_max is None:
        try:
            keep_max = int(os.getenv("DB_BACKUP_KEEP_MAX", str(DEFAULT_BACKUP_KEEP_MAX)))
        except Exception:
            keep_max = DEFAULT_BACKUP_KEEP_MAX
    keep_max = max(1, int(keep_max or DEFAULT_BACKUP_KEEP_MAX))
    backups = sorted(get_backup_dir().glob("ops_backup_*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    removed: List[str] = []
    for old in backups[keep_max:]:
        try:
            old.unlink()
            removed.append(old.name)
        except Exception:
            pass
    return removed


def create_database_backup(*, actor: str = "", reason: str = "manual", prefix: str = "ops_backup", keep_max: int | None = None) -> Dict[str, Any]:
    db_path = get_sqlite_db_path()
    if not db_path.exists():
        raise BackupServiceError(f"Database file not found: {db_path}", 404)
    safe_prefix = "".join(ch for ch in prefix if ch.isalnum() or ch in "_-").strip("_-") or "ops_backup"
    if not safe_prefix.startswith("ops_backup"):
        safe_prefix = "ops_backup_" + safe_prefix
    base_name = f"{safe_prefix}_{_timestamp()}"
    backup_name = f"{base_name}.db"
    backup_path = resolve_backup_file(backup_name, must_exist=False)
    suffix = 1
    while backup_path.exists():
        backup_name = f"{base_name}_{suffix}.db"
        backup_path = resolve_backup_file(backup_name, must_exist=False)
        suffix += 1
    _copy_sqlite_consistent(db_path, backup_path)
    verification = verify_backup_file(backup_name, include_checksum=True)
    if not verification.get("valid"):
        try:
            backup_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise BackupServiceError("Backup was created but failed verification", 500)
    removed = _apply_retention(keep_max)
    audit("db.backup", "system", backup_name, f"user={actor or '-'} reason={reason or '-'} removed={','.join(removed) or '-'} sha256={verification.get('sha256', '-')}")
    return {
        **_backup_meta(backup_path),
        "verification": verification,
        "removed_by_retention": removed,
        "message": "Database backup created and verified",
    }


def restore_database_backup(
    file_name: str,
    *,
    confirm_text: str,
    actor: str = "",
    create_safety_backup: bool = True,
) -> Dict[str, Any]:
    expected = expected_restore_confirm_text(file_name)
    if (confirm_text or "").strip() != expected:
        raise BackupServiceError(f"恢复确认短语不匹配，请输入：{expected}", 400)
    backup_path = resolve_backup_file(file_name)
    verification = verify_backup_file(file_name, include_checksum=True)
    if not verification.get("valid"):
        raise BackupServiceError("备份校验未通过，已拒绝恢复", 400)
    safety = None
    if create_safety_backup:
        safety = create_database_backup(actor=actor, reason=f"before_restore:{file_name}", prefix="ops_backup_before_restore", keep_max=None)
    db_path = get_sqlite_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(backup_path, db_path)
    for suffix in ("-wal", "-shm"):
        try:
            Path(str(db_path) + suffix).unlink(missing_ok=True)
        except Exception:
            pass
    audit("db.restore", "system", file_name, f"user={actor or '-'} safety_backup={(safety or {}).get('file') or '-'} sha256={verification.get('sha256', '-')}")
    return {
        "file": file_name,
        "restored": True,
        "safety_backup": safety,
        "verification": verification,
        "message": f"Database restored from {file_name}. Restart application to apply.",
        "next_actions": [
            {"label": "重启应用", "description": "SQLite 主库文件已替换，建议立即重启后端进程以释放旧连接。"},
            {"label": "重新检查系统状态", "description": "重启后打开 /system/diagnostics 查看数据库与备份状态。"},
        ],
    }


def delete_database_backup(file_name: str, *, confirm_text: str, actor: str = "") -> Dict[str, Any]:
    expected = expected_delete_confirm_text(file_name)
    if (confirm_text or "").strip() != expected:
        raise BackupServiceError(f"删除确认短语不匹配，请输入：{expected}", 400)
    path = resolve_backup_file(file_name)
    meta = _backup_meta(path)
    path.unlink()
    audit("db.backup.delete", "system", file_name, f"user={actor or '-'} size={meta.get('size')}")
    return {"file": file_name, "deleted": True, "message": "Backup deleted"}
