"""Deploy package metadata, references, upload and cleanup policies.

This module keeps local File Center packages safe for retry/rollback by using
metadata + deployment references instead of deleting files only by mtime.
"""
from __future__ import annotations

import base64
import hashlib
import os
import re
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Iterable, List, Tuple

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.config import get_runtime_path
from app.db.models import Deployment, DeployTask, ToolPlan, DeployPackage, DeployPackageRef
from app.db import ConfigRepository


DEFAULT_PACKAGE_RETENTION: Dict[str, Any] = {
    "package_keep_days": 90,
    "package_keep_max": 500,
    "test_success_keep_days": 60,
    "prod_success_keep_days": 180,
    "failed_package_keep_days": 180,
    "rollback_package_keep_days": 365,
    "unused_package_keep_days": 30,
    "keep_latest_success_per_service": 3,
    "keep_latest_prod_success_per_service": 5,
    "min_keep_days": 7,
    "protect_running_deployments": True,
    "protect_failed_deployments": True,
    "protect_rollback_candidates": True,
    "keep_metadata_after_file_delete": True,
    "max_upload_size_mb": 1024,
    "allowed_extensions": [".tar.gz", ".tgz", ".tar", ".zip", ".jar", ".war", ".gz", ".bin"],
    "dry_run": True,
}


def _upload_dir() -> str:
    return get_runtime_path("UPLOAD_DIR", "uploads")

RUNNING_STATUSES = {"pending", "running", "queued"}
SUCCESS_STATUSES = {"success", "succeeded"}
FAILED_STATUSES = {"failed", "error"}
ROLLBACK_STATUSES = {"rollback", "rollbacked", "rolledback", "reverted"}


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _as_int(v: Any, default: int) -> int:
    try:
        return max(0, int(v))
    except Exception:
        return default


def _as_bool(v: Any, default: bool = False) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.lower() in {"1", "true", "yes", "on"}
    if v is None:
        return default
    return bool(v)


def _normalize_policy(policy: Dict[str, Any] | None) -> Dict[str, Any]:
    merged = {**DEFAULT_PACKAGE_RETENTION, **(policy or {})}
    for key in [
        "package_keep_days", "package_keep_max", "test_success_keep_days", "prod_success_keep_days",
        "failed_package_keep_days", "rollback_package_keep_days", "unused_package_keep_days",
        "keep_latest_success_per_service", "keep_latest_prod_success_per_service", "min_keep_days", "max_upload_size_mb",
    ]:
        merged[key] = _as_int(merged.get(key), DEFAULT_PACKAGE_RETENTION[key])
    for key in [
        "protect_running_deployments", "protect_failed_deployments", "protect_rollback_candidates",
        "keep_metadata_after_file_delete", "dry_run",
    ]:
        merged[key] = _as_bool(merged.get(key), DEFAULT_PACKAGE_RETENTION[key])
    exts = merged.get("allowed_extensions") or DEFAULT_PACKAGE_RETENTION["allowed_extensions"]
    merged["allowed_extensions"] = sorted({str(x).lower().strip() for x in exts if str(x).strip()})
    return merged


def get_package_retention_policy(db: Session) -> Dict[str, Any]:
    cfg = ConfigRepository(db).get("package_retention") or {}
    return _normalize_policy(cfg if isinstance(cfg, dict) else {})


def save_package_retention_policy(db: Session, policy: Dict[str, Any]) -> Dict[str, Any]:
    merged = _normalize_policy({**get_package_retention_policy(db), **(policy or {})})
    ConfigRepository(db).set("package_retention", merged)
    db.commit()
    return merged


def safe_package_name(name: str) -> str:
    base = os.path.basename(name or "")
    base = re.sub(r"[^A-Za-z0-9._@+\-=\u4e00-\u9fff]+", "_", base).strip("._")
    if not base:
        raise HTTPException(status_code=400, detail="Invalid package file name")
    return base


def package_path(name: str) -> str:
    return os.path.join(_upload_dir(), safe_package_name(name))


def _deletable_package_path(row: DeployPackage, name: str) -> str:
    stored = os.path.abspath(row.file_path or package_path(name))
    upload_dir = os.path.abspath(_upload_dir())
    try:
        if os.path.commonpath([upload_dir, stored]) == upload_dir:
            return stored
    except ValueError:
        pass
    return package_path(name)


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def infer_service_hint(name: str) -> str:
    base = safe_package_name(name).lower()
    base = re.sub(r"\.(tar\.gz|tgz|tar|zip|jar|war|gz|bin)$", "", base)
    # system-20263108153148.tar.gz -> system, crypto-system_2026 -> crypto-system
    return re.split(r"[-_.][0-9]{6,}", base)[0] or base


def infer_version_hint(name: str) -> str:
    m = re.search(r"([0-9]{8,18})", safe_package_name(name))
    return m.group(1) if m else ""


def _is_allowed_extension(name: str, policy: Dict[str, Any]) -> bool:
    lower = safe_package_name(name).lower()
    return any(lower.endswith(ext) for ext in policy.get("allowed_extensions") or [])


def _package_meta_payload(name: str, path: str, *, sha256: str | None = None) -> Dict[str, Any]:
    st = os.stat(path)
    return {
        "name": name,
        "package_name": name,
        "file_path": path,
        "size": st.st_size,
        "size_bytes": st.st_size,
        "size_mb": round(st.st_size / 1024 / 1024, 2),
        "sha256": sha256 or sha256_file(path),
        "service_hint": infer_service_hint(name),
        "version_hint": infer_version_hint(name),
        "modified": datetime.fromtimestamp(st.st_mtime).isoformat(),
    }


def upsert_package_metadata(
    db: Session,
    name: str,
    path: str | None = None,
    *,
    system: str = "",
    service: str = "",
    uploaded_by: str = "",
    sha256: str | None = None,
    commit: bool = True,
) -> DeployPackage:
    name = safe_package_name(name)
    path = path or package_path(name)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail=f"Package file not found: {name}")
    meta = _package_meta_payload(name, path, sha256=sha256)
    row = db.query(DeployPackage).filter(DeployPackage.package_name == name).first()
    if not row:
        row = DeployPackage(package_name=name, uploaded_at=_now())
        db.add(row)
    row.file_path = path
    row.size_bytes = int(meta["size_bytes"])
    row.sha256 = meta["sha256"]
    row.system = system or row.system or ""
    row.service = service or row.service or ""
    row.service_hint = meta["service_hint"]
    row.version_hint = meta["version_hint"]
    row.uploaded_by = uploaded_by or row.uploaded_by or ""
    row.deleted = False
    row.deleted_at = None
    row.delete_reason = ""
    if commit:
        db.commit()
        db.refresh(row)
    return row


def _infer_system_from_hint(service_hint: str) -> str:
    if not service_hint:
        return ""
    try:
        from config_manager import load_config_cached
        config = load_config_cached()
        systems = config.get("systems") or {}
        hint_l = service_hint.lower()
        for sys_name, sys_cfg in systems.items():
            services = (sys_cfg or {}).get("services") or []
            for svc in services:
                if not isinstance(svc, dict):
                    continue
                svc_name = (svc.get("name") or "").lower().replace("crypto-", "")
                svc_display = (svc.get("display_name") or "").lower()
                tv = svc.get("template_variables") or {}
                svc_service_name = (tv.get("service_name") or "").lower()
                svc_pm2 = (tv.get("pm2_name") or "").lower()
                if hint_l in [svc_name, svc_display, svc_service_name, svc_pm2] or hint_l == svc_name:
                    return sys_name
    except Exception:
        pass
    return ""


def sync_package_metadata(db: Session) -> Dict[str, Any]:
    upload_dir = _upload_dir()
    os.makedirs(upload_dir, exist_ok=True)
    seen = set()
    updated = 0
    for name in os.listdir(upload_dir):
        path = os.path.join(upload_dir, name)
        if not os.path.isfile(path):
            continue
        try:
            row = upsert_package_metadata(db, name, path, commit=False)
            if not row.system:
                inferred = _infer_system_from_hint(row.service_hint or infer_service_hint(name))
                if inferred:
                    row.system = inferred
            seen.add(safe_package_name(name))
            updated += 1
        except Exception:
            continue
    for row in db.query(DeployPackage).filter(DeployPackage.deleted == False).all():  # noqa: E712
        if row.package_name not in seen and not os.path.isfile(row.file_path or package_path(row.package_name)):
            row.deleted = True
            row.deleted_at = row.deleted_at or _now()
            row.delete_reason = row.delete_reason or "file_missing_on_sync"
        elif not row.system:
            inferred = _infer_system_from_hint(row.service_hint or infer_service_hint(row.package_name))
            if inferred:
                row.system = inferred
    db.commit()
    return {"updated": updated, "seen": len(seen)}


def inspect_package_file(
    local_path: str,
    *,
    filename: str = "",
    policy: Dict[str, Any] | None = None,
    calculate_sha256: bool = True,
) -> Dict[str, Any]:
    """Inspect a package file without importing it into the File Center.

    This is used by the stdio MCP bridge before uploading local files and by
    the HTTP tool when the backend can read the given path. It intentionally
    returns validation warnings instead of raising for policy issues so clients
    can show a useful preflight result before a high-risk upload.
    """
    path = os.path.abspath(os.path.expanduser(str(local_path or "")))
    if not path or not os.path.isfile(path):
        raise HTTPException(status_code=400, detail="Local package path is not readable")
    pol = _normalize_policy(policy or DEFAULT_PACKAGE_RETENTION)
    name = safe_package_name(filename or os.path.basename(path))
    size = os.path.getsize(path)
    max_bytes = int(pol.get("max_upload_size_mb", 1024)) * 1024 * 1024
    allowed_extension = _is_allowed_extension(name, pol)
    within_size_limit = size <= max_bytes
    blockers: List[str] = []
    warnings: List[str] = []
    if not allowed_extension:
        blockers.append(f"Unsupported package extension: {name}")
    if not within_size_limit:
        blockers.append(f"Package too large, max={pol.get('max_upload_size_mb')}MB")
    if size == 0:
        warnings.append("Package file is empty")
    sha = sha256_file(path) if calculate_sha256 else ""
    return {
        "ok": not blockers,
        "local_path": path,
        "name": name,
        "package_name": name,
        "size": size,
        "size_bytes": size,
        "size_mb": round(size / 1024 / 1024, 2),
        "sha256": sha,
        "service_hint": infer_service_hint(name),
        "version_hint": infer_version_hint(name),
        "allowed_extension": allowed_extension,
        "allowed_extensions": pol.get("allowed_extensions") or [],
        "max_upload_size_mb": int(pol.get("max_upload_size_mb", 1024)),
        "within_size_limit": within_size_limit,
        "blockers": blockers,
        "warnings": warnings,
        "summary": "local package ready for upload" if not blockers else "local package cannot be uploaded until blockers are fixed",
    }


def save_package_fileobj(
    db: Session,
    *,
    filename: str,
    fileobj,
    system: str = "",
    service: str = "",
    uploaded_by: str = "",
    overwrite: bool = False,
) -> Dict[str, Any]:
    """Stream a file-like object into the File Center with size/hash checks."""
    policy = get_package_retention_policy(db)
    name = safe_package_name(filename)
    if not _is_allowed_extension(name, policy):
        raise HTTPException(status_code=400, detail=f"Unsupported package extension: {name}")
    max_bytes = int(policy.get("max_upload_size_mb", 1024)) * 1024 * 1024
    os.makedirs(_upload_dir(), exist_ok=True)
    path = package_path(name)
    if os.path.exists(path) and not overwrite:
        raise HTTPException(status_code=409, detail="Package already exists; set overwrite=true to replace")
    tmp_path = f"{path}.upload-{uuid.uuid4().hex}.tmp"
    h = hashlib.sha256()
    total = 0
    try:
        try:
            fileobj.seek(0)
        except Exception:
            pass
        with open(tmp_path, "wb") as out:
            while True:
                chunk = fileobj.read(1024 * 1024)
                if not chunk:
                    break
                if isinstance(chunk, str):
                    chunk = chunk.encode("utf-8")
                total += len(chunk)
                if total > max_bytes:
                    raise HTTPException(status_code=413, detail=f"Package too large, max={policy.get('max_upload_size_mb')}MB")
                h.update(chunk)
                out.write(chunk)
        os.replace(tmp_path, path)
    except Exception:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        finally:
            pass
        raise
    sha = h.hexdigest()
    row = upsert_package_metadata(db, name, path, system=system, service=service, uploaded_by=uploaded_by, sha256=sha)
    return package_to_dict(row, include_retention=False)


def save_package_bytes(
    db: Session,
    *,
    filename: str,
    content: bytes,
    system: str = "",
    service: str = "",
    uploaded_by: str = "",
    overwrite: bool = False,
) -> Dict[str, Any]:
    import io
    return save_package_fileobj(
        db,
        filename=filename,
        fileobj=io.BytesIO(content),
        system=system,
        service=service,
        uploaded_by=uploaded_by,
        overwrite=overwrite,
    )


def save_package_base64(db: Session, *, filename: str, content_base64: str, **kwargs) -> Dict[str, Any]:
    try:
        content = base64.b64decode(content_base64, validate=True)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid base64 package content: {exc}")
    return save_package_bytes(db, filename=filename, content=content, **kwargs)


def record_package_reference(
    db: Session,
    *,
    package_name: str,
    deployment_id: str = "",
    system: str = "",
    service: str = "",
    environment: str = "",
    server_name: str = "",
    usage_type: str = "deploy",
    commit: bool = True,
) -> None:
    if not package_name:
        return
    name = safe_package_name(package_name)
    row = db.query(DeployPackage).filter(DeployPackage.package_name == name).first()
    if not row and os.path.isfile(package_path(name)):
        row = upsert_package_metadata(db, name, commit=False)
    ref = DeployPackageRef(
        package_id=row.id if row else None,
        package_name=name,
        deployment_id=deployment_id or "",
        system=system or "",
        service=service or "",
        environment=environment or "",
        server_name=server_name or "",
        usage_type=usage_type or "deploy",
        created_at=_now(),
    )
    db.add(ref)
    if row:
        row.used_count = int(row.used_count or 0) + 1
        row.last_used_at = _now()
        if system and not row.system:
            row.system = system
        if service and not row.service:
            row.service = service
    if commit:
        db.commit()


def _is_prod_env(env: str | None) -> bool:
    return (env or "").lower() in {"prod", "production", "online", "release", "live", "线上", "生产"}


def _is_rollback_deployment(row: Deployment) -> bool:
    text = f"{row.status or ''} {row.message or ''} {row.strategy or ''}".lower()
    return any(x in text for x in ["rollback", "rollbacked", "rolledback", "reverted", "回滚"])


def _deployment_keep_days(row: Deployment, policy: Dict[str, Any]) -> int:
    status = (row.status or "").lower()
    if _is_rollback_deployment(row):
        return int(policy["rollback_package_keep_days"])
    if status in FAILED_STATUSES:
        return int(policy["failed_package_keep_days"])
    if status in SUCCESS_STATUSES:
        return int(policy["prod_success_keep_days"] if _is_prod_env(row.environment) else policy["test_success_keep_days"])
    return int(policy["package_keep_days"])


def _protected_names_from_deployments(db: Session, policy: Dict[str, Any]) -> Dict[str, List[str]]:
    protected: Dict[str, List[str]] = defaultdict(list)
    now = _now()
    rows = db.query(Deployment).order_by(Deployment.started_at.desc()).all()

    # Running packages are always protected.
    if policy.get("protect_running_deployments", True):
        for row in rows:
            if (row.status or "").lower() in RUNNING_STATUSES and row.version:
                protected[safe_package_name(row.version)].append(f"running deployment {row.id}")

    # Failed packages remain protected for retry window.
    if policy.get("protect_failed_deployments", True):
        cutoff = now - timedelta(days=int(policy["failed_package_keep_days"]))
        for row in rows:
            if (row.status or "").lower() in FAILED_STATUSES and row.started_at and row.started_at >= cutoff and row.version:
                protected[safe_package_name(row.version)].append(f"failed deployment retry window {row.id}")

    # Rollback related packages stay longer.
    if policy.get("protect_rollback_candidates", True):
        cutoff = now - timedelta(days=int(policy["rollback_package_keep_days"]))
        for row in rows:
            if _is_rollback_deployment(row) and row.started_at and row.started_at >= cutoff and row.version:
                protected[safe_package_name(row.version)].append(f"rollback candidate {row.id}")

    # Keep latest N successful packages per system/service/environment.
    buckets: Dict[Tuple[str, str, str], List[Deployment]] = defaultdict(list)
    for row in rows:
        if (row.status or "").lower() in SUCCESS_STATUSES and row.version:
            buckets[(row.system or "", row.service or "", row.environment or "")].append(row)
    for (system, service, env), bucket in buckets.items():
        keep = int(policy["keep_latest_prod_success_per_service"] if _is_prod_env(env) else policy["keep_latest_success_per_service"])
        for row in bucket[:keep]:
            protected[safe_package_name(row.version)].append(f"latest success {system}/{service}/{env} deployment {row.id}")

    # Tool plans that have not been executed/canceled yet should keep their package.
    for plan in db.query(ToolPlan).filter(ToolPlan.package_name != None).all():  # noqa: E711
        if plan.status in {"ready", "prechecked", "draft", "blocked"} and plan.package_name:
            protected[safe_package_name(plan.package_name)].append(f"tool plan {plan.id} status={plan.status}")

    return protected


def package_to_dict(row: DeployPackage, *, include_retention: bool = True, protected: Dict[str, List[str]] | None = None) -> Dict[str, Any]:
    path = row.file_path or package_path(row.package_name)
    exists = os.path.isfile(path)
    data = {
        "id": row.id,
        "name": row.package_name,
        "package_name": row.package_name,
        "file_path": path,
        "exists": exists,
        "size": int(row.size_bytes or 0),
        "size_bytes": int(row.size_bytes or 0),
        "size_mb": round((row.size_bytes or 0) / 1024 / 1024, 2),
        "sha256": row.sha256 or "",
        "system": row.system or "",
        "service": row.service or "",
        "service_hint": row.service_hint or infer_service_hint(row.package_name),
        "version_hint": row.version_hint or infer_version_hint(row.package_name),
        "uploaded_by": row.uploaded_by or "",
        "uploaded_at": row.uploaded_at.isoformat() if row.uploaded_at else None,
        "last_used_at": row.last_used_at.isoformat() if row.last_used_at else None,
        "used_count": int(row.used_count or 0),
        "protected": bool(row.protected),
        "deleted": bool(row.deleted),
        "deleted_at": row.deleted_at.isoformat() if row.deleted_at else None,
        "delete_reason": row.delete_reason or "",
        "modified": datetime.fromtimestamp(os.path.getmtime(path)).isoformat() if exists else None,
    }
    if include_retention:
        reasons = []
        if row.protected:
            reasons.append("manual protected")
        if protected and row.package_name in protected:
            reasons.extend(protected[row.package_name])
        data["retention"] = {"protected": bool(reasons), "reasons": reasons[:8], "can_cleanup": exists and not reasons}
    return data


def list_packages(db: Session, *, service: str = "", system: str = "", limit: int = 100, with_retention: bool = True) -> List[Dict[str, Any]]:
    sync_package_metadata(db)
    protected = _protected_names_from_deployments(db, get_package_retention_policy(db)) if with_retention else {}
    q = db.query(DeployPackage).filter(DeployPackage.deleted == False).order_by(DeployPackage.uploaded_at.desc())  # noqa: E712
    items = []
    service_l = (service or "").lower().replace("crypto-", "")
    system_l = (system or "").lower().replace("crypto-", "")
    for row in q.all():
        hay = " ".join([row.package_name or "", row.service or "", row.service_hint or "", row.system or ""]).lower()
        if service_l and service_l not in hay:
            continue
        if system_l and system_l not in hay:
            continue
        items.append(package_to_dict(row, include_retention=with_retention, protected=protected))
        if len(items) >= max(1, int(limit or 100)):
            break
    return items


def _candidate_reason(row: DeployPackage, policy: Dict[str, Any], protected: Dict[str, List[str]]) -> Tuple[bool, str, List[str]]:
    path = row.file_path or package_path(row.package_name)
    if not os.path.isfile(path):
        return False, "file already missing", ["metadata only"]
    reasons = []
    if row.protected:
        reasons.append("manual protected")
    reasons.extend(protected.get(row.package_name, []))
    if reasons:
        return False, "protected", reasons[:8]

    base_time = row.last_used_at or row.uploaded_at or _now()
    min_cutoff = _now() - timedelta(days=int(policy["min_keep_days"]))
    if base_time > min_cutoff:
        return False, "min keep window", [f"younger than {policy['min_keep_days']}d"]

    # If never used, use unused keep window.
    if not row.last_used_at and int(row.used_count or 0) <= 0:
        cutoff = _now() - timedelta(days=int(policy["unused_package_keep_days"]))
        if (row.uploaded_at or base_time) < cutoff:
            return True, f"unused>{policy['unused_package_keep_days']}d", []
        return False, "unused keep window", [f"unused but younger than {policy['unused_package_keep_days']}d"]

    cutoff = _now() - timedelta(days=int(policy["package_keep_days"]))
    if base_time < cutoff:
        return True, f"last_used>{policy['package_keep_days']}d", []
    return False, "within keep window", [f"last used within {policy['package_keep_days']}d"]


def preview_package_cleanup(db: Session, policy: Dict[str, Any] | None = None) -> Dict[str, Any]:
    sync_package_metadata(db)
    policy = _normalize_policy({**get_package_retention_policy(db), **(policy or {})})
    protected = _protected_names_from_deployments(db, policy)
    candidates: List[Dict[str, Any]] = []
    protected_items: List[Dict[str, Any]] = []
    total_size = 0
    rows = db.query(DeployPackage).filter(DeployPackage.deleted == False).order_by(DeployPackage.uploaded_at.desc()).all()  # noqa: E712
    for row in rows:
        can, reason, reasons = _candidate_reason(row, policy, protected)
        item = package_to_dict(row, include_retention=False)
        item["reason"] = reason
        item["protection_reasons"] = reasons
        if can:
            candidates.append(item)
            total_size += int(row.size_bytes or 0)
        else:
            protected_items.append(item)

    keep_max = int(policy.get("package_keep_max") or 0)
    if keep_max > 0 and len(rows) - len(candidates) > keep_max:
        already = {x["package_name"] for x in candidates}
        for row in rows[keep_max:]:
            if row.package_name in already:
                continue
            can, _, reasons = _candidate_reason(row, policy, protected)
            if not reasons and not row.protected:
                item = package_to_dict(row, include_retention=False)
                item["reason"] = f"exceed_max>{keep_max}"
                item["protection_reasons"] = []
                candidates.append(item)
                total_size += int(row.size_bytes or 0)
                already.add(row.package_name)

    return {
        "policy": policy,
        "summary": {
            "total_packages": len(rows),
            "cleanup_count": len(candidates),
            "protected_count": len(protected_items),
            "cleanup_size_bytes": total_size,
            "cleanup_size_mb": round(total_size / 1024 / 1024, 2),
            "by_reason": dict(Counter(x.get("reason") or "unknown" for x in candidates)),
        },
        "candidates": candidates[:200],
        "protected_sample": protected_items[:100],
    }


def cleanup_packages(db: Session, policy: Dict[str, Any] | None = None, *, dry_run: bool | None = None, actor: str = "") -> Dict[str, Any]:
    preview = preview_package_cleanup(db, policy)
    policy_n = preview["policy"]
    if dry_run is None:
        dry_run = bool(policy_n.get("dry_run", True))
    removed: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    if not dry_run:
        for item in preview["candidates"]:
            name = item["package_name"]
            row = db.query(DeployPackage).filter(DeployPackage.package_name == name).first()
            if not row:
                continue
            path = _deletable_package_path(row, name)
            try:
                if os.path.isfile(path):
                    os.remove(path)
                row.deleted = True
                row.deleted_at = _now()
                row.delete_reason = item.get("reason") or "retention_cleanup"
                removed.append({"package_name": name, "reason": row.delete_reason, "size_bytes": row.size_bytes or 0})
            except Exception as exc:
                errors.append({"package_name": name, "error": str(exc)})
        db.commit()
    return {**preview, "dry_run": dry_run, "removed": removed, "errors": errors}


def delete_package(db: Session, package_name: str, *, actor: str = "") -> Dict[str, Any]:
    """Delete one File Center package and mark its metadata as removed."""
    name = safe_package_name(package_name)
    row = db.query(DeployPackage).filter(
        DeployPackage.package_name == name,
        DeployPackage.deleted == False,  # noqa: E712
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="Package not found")

    protected = _protected_names_from_deployments(db, get_package_retention_policy(db))
    reasons: List[str] = []
    if row.protected:
        reasons.append("manual protected")
    reasons.extend(protected.get(name, []))
    if reasons:
        raise HTTPException(status_code=409, detail={"message": "Package is protected; unprotect or clear references before deleting", "reasons": reasons[:8]})

    path = _deletable_package_path(row, name)
    existed = os.path.isfile(path)
    size_bytes = int(row.size_bytes or 0)
    try:
        if existed:
            os.remove(path)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Failed to delete package file: {exc}") from exc

    row.deleted = True
    row.deleted_at = _now()
    row.delete_reason = f"manual_delete by {actor or 'unknown'}"
    db.commit()
    return {
        "package_name": name,
        "deleted": True,
        "file_deleted": existed,
        "size_bytes": size_bytes,
        "delete_reason": row.delete_reason,
    }
