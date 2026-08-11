"""Controlled package/file transfer tools for AI-assisted operations."""
from __future__ import annotations

import hashlib
import os
import posixpath
import secrets
import shlex
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from app.core.config import APPROVAL_STAGING_DIR, get_runtime_path
from app.services.package_retention import (
    get_package_retention_policy,
    inspect_package_file,
    package_path,
    safe_package_name,
)
from app.services.tool_adapters.remote_exec_tools import _connect
from app.services.tool_registry import registry


_MAX_REMOTE_PATH_LENGTH = 4096


def _controlled_roots() -> list[Path]:
    return [
        Path(get_runtime_path("UPLOAD_DIR", "uploads")).resolve(),
        Path(APPROVAL_STAGING_DIR).resolve(),
    ]


def _is_under(path: Path, root: Path) -> bool:
    try:
        return os.path.commonpath([str(path), str(root)]) == str(root)
    except ValueError:
        return False


def _resolve_source(args: dict[str, Any]) -> tuple[Path, str]:
    package_name = str(args.get("package_name") or "").strip()
    local_path = str(args.get("local_path") or "").strip()
    if package_name and local_path:
        raise HTTPException(status_code=400, detail="Provide either package_name or local_path, not both")
    if package_name:
        name = safe_package_name(package_name)
        path = Path(package_path(name)).resolve()
    elif local_path:
        path = Path(os.path.abspath(os.path.expanduser(local_path))).resolve()
        name = safe_package_name(args.get("filename") or path.name)
    else:
        raise HTTPException(status_code=400, detail="package_name or local_path is required")

    if not any(_is_under(path, root) for root in _controlled_roots()):
        raise HTTPException(
            status_code=400,
            detail="Source file must be in OPS uploads or approval staging directory",
        )
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"Local package not found: {path.name}")
    return path, name


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _remote_file_path(remote_path: str) -> str:
    value = str(remote_path or "").replace("\\", "/").strip()
    if not value or len(value) > _MAX_REMOTE_PATH_LENGTH or not value.startswith("/"):
        raise HTTPException(status_code=400, detail="remote_path must be an absolute remote file path")
    if value.endswith("/"):
        raise HTTPException(status_code=400, detail="remote_path must identify a file")
    normalized = posixpath.normpath(value)
    if normalized in {"", "/", "."} or normalized.endswith("/"):
        raise HTTPException(status_code=400, detail="remote_path must identify a file")
    return normalized


def _remote_sha256(ssh, path: str) -> str:
    code, out, _ = ssh.exec(
        f"if command -v sha256sum >/dev/null 2>&1; then sha256sum {shlex.quote(path)} | awk '{{print $1}}'; fi",
        timeout=60,
    )
    if code != 0:
        return ""
    return (out or "").strip().split()[0] if (out or "").strip() else ""


def _remote_realpath(ssh, path: str) -> str:
    code, out, _ = ssh.exec(f"readlink -f -- {shlex.quote(path)}", timeout=10)
    resolved = (out or "").strip().splitlines()
    if code != 0 or not resolved:
        raise HTTPException(status_code=502, detail="Cannot resolve the remote upload directory")
    return posixpath.normpath(resolved[-1])


@registry.register(
    name="ops.upload_file",
    title="Upload package to server",
    description=(
        "Upload a validated package from the controlled OPS upload/staging directory "
        "to an allowed path on a configured server through SFTP. The operation is a "
        "high-risk server write and requires human approval."
    ),
    scopes=["ops:read", "server:read"],
    risk="high",
    category="server_write",
    write=True,
    requires_confirmation=True,
    requires_human_approval=True,
    data_sensitivity="sensitive",
    keywords=["upload file", "upload package", "scp", "sftp"],
    input_schema={
        "type": "object",
        "properties": {
            "server": {"type": "string", "description": "Configured server name, host, or UUID"},
            "package_name": {"type": "string", "description": "Existing package name in OPS File Center"},
            "local_path": {"type": "string", "description": "Path under OPS uploads or approval staging"},
            "filename": {"type": "string", "description": "Optional package filename when local_path is used"},
            "remote_path": {"type": "string", "description": "Absolute remote target file path"},
            "overwrite": {"type": "boolean", "default": False},
            "confirm_path": {"type": "string", "description": "Must equal remote_path when overwriting"},
            "confirm_text": {"type": "string", "description": "Confirmation phrase: CONFIRM ops.upload_file"},
            "expected_sha256": {"type": "string", "description": "Approval-bound local SHA-256"},
            "expected_size_bytes": {"type": "integer", "description": "Approval-bound local size"},
        },
        "required": ["server", "remote_path", "confirm_text"],
        "additionalProperties": False,
    },
)
def upload_file(args, ctx, db):
    source, filename = _resolve_source(args)
    remote_path = _remote_file_path(args.get("remote_path"))
    if not args.get("package_name") and not args.get("filename"):
        filename = safe_package_name(source.name)

    policy = get_package_retention_policy(db) if db is not None else None
    inspection = inspect_package_file(source, filename=filename, policy=policy, calculate_sha256=True)
    if inspection.get("blockers"):
        raise HTTPException(status_code=400, detail="; ".join(inspection["blockers"]))
    local_sha256 = inspection.get("sha256") or _sha256(source)
    expected_sha256 = str(args.get("expected_sha256") or "").strip().lower()
    if expected_sha256 and expected_sha256 != local_sha256.lower():
        raise HTTPException(status_code=409, detail="Local package checksum no longer matches the approved checksum")
    if args.get("expected_size_bytes") is not None:
        try:
            expected_size = int(args.get("expected_size_bytes"))
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="expected_size_bytes must be an integer")
        actual_size = int(inspection.get("size_bytes") or source.stat().st_size)
        if expected_size != actual_size:
            raise HTTPException(status_code=409, detail="Local package size no longer matches the approved size")

    ssh, srv = _connect(args.get("server", ""))
    temp_path = ""
    try:
        from app.api.sftp import _ensure_path_allowed
        remote_path = _ensure_path_allowed(remote_path, srv)
        remote_parent = posixpath.dirname(remote_path) or "/"
        resolved_parent = _remote_realpath(ssh, remote_parent)
        _ensure_path_allowed(posixpath.join(resolved_parent, posixpath.basename(remote_path)), srv)
        if resolved_parent != remote_parent:
            raise HTTPException(status_code=403, detail="Remote upload directory must not contain symbolic links")

        exists_code, _, _ = ssh.exec(
            f"test -e {shlex.quote(remote_path)} || test -L {shlex.quote(remote_path)}",
            timeout=10,
        )
        exists = exists_code == 0
        overwrite = bool(args.get("overwrite", False))
        if exists and not overwrite:
            raise HTTPException(status_code=409, detail="Remote file already exists; set overwrite=true")
        if exists and str(args.get("confirm_path") or "") != remote_path:
            raise HTTPException(status_code=400, detail="Overwriting requires confirm_path equal to remote_path")

        temp_path = posixpath.join(
            remote_parent,
            f".{posixpath.basename(remote_path)}.ops-upload-{secrets.token_hex(8)}.tmp",
        )
        _ensure_path_allowed(temp_path, srv)
        ssh.upload(str(source), temp_path)
        temp_sha256 = _remote_sha256(ssh, temp_path)
        if not temp_sha256:
            raise HTTPException(status_code=502, detail="Remote SHA-256 verification is unavailable")
        if temp_sha256 != local_sha256:
            raise HTTPException(status_code=502, detail="Remote checksum does not match local package")

        if overwrite:
            publish_command = f"mv -f -- {shlex.quote(temp_path)} {shlex.quote(remote_path)}"
        else:
            publish_command = (
                f"ln -- {shlex.quote(temp_path)} {shlex.quote(remote_path)} "
                f"&& rm -f -- {shlex.quote(temp_path)}"
            )
        publish_code, _, publish_err = ssh.exec(publish_command, timeout=30)
        if publish_code != 0:
            if not overwrite:
                raise HTTPException(status_code=409, detail="Remote file appeared before publish; upload was not applied")
            raise HTTPException(status_code=502, detail=f"Remote atomic publish failed: {publish_err or publish_code}")
        temp_path = ""

        remote_sha256 = _remote_sha256(ssh, remote_path)
        if not remote_sha256 or remote_sha256 != local_sha256:
            raise HTTPException(status_code=502, detail="Published remote checksum does not match local package")
        return {
            "ok": True,
            "server": args.get("server", ""),
            "source": filename,
            "remote_path": remote_path,
            "size_bytes": inspection.get("size_bytes", source.stat().st_size),
            "sha256": local_sha256,
            "remote_sha256": remote_sha256,
            "overwritten": exists,
            "transport": "sftp",
        }
    finally:
        if temp_path:
            try:
                ssh.exec(f"rm -f -- {shlex.quote(temp_path)}", timeout=10)
            except Exception:
                pass
        ssh.close()
