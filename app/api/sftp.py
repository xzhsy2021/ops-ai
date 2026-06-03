"""服务器工作台 SFTP 文件管理 API - 管理员专用"""
import base64
import os
import posixpath
import stat
import logging
import tempfile
from datetime import datetime
from fastapi import APIRouter, HTTPException, Request, UploadFile, File, Form, Depends
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from sqlalchemy.orm import Session
from app.db import get_db
from app.core.auth_v2 import require_admin
from app.api.helpers import api_response, audit
from app.services.remote_access import build_audit_context, format_audit_detail

logger = logging.getLogger(__name__)

sftp_router = APIRouter(prefix="/api/v2/servers", tags=["SFTP文件"])

_MAX_FILE_SIZE = 200 * 1024 * 1024
_MAX_TEXT_PREVIEW = 100 * 1024
_MAX_TAIL_BYTES = 512 * 1024
_MAX_TAIL_LINES = 1000
_DEFAULT_ALLOWED_ROOTS = ("/data", "/opt", "/var/log", "/tmp")


def _get_sftp(name: str):
    from config_manager import get_server_by_name
    srv = get_server_by_name(name)
    if not srv:
        raise HTTPException(status_code=404, detail=f"Server '{name}' not found")
    from app.services.remote_access import create_exec_client
    ssh = create_exec_client(srv)
    if not ssh:
        raise HTTPException(status_code=503, detail=f"Cannot connect to {name}")
    try:
        sftp = ssh._client.open_sftp()
        return sftp, ssh, srv
    except Exception as e:
        # The pooled transport may have died silently; reconnect once on
        # transport-level errors (EOFError / OSError / paramiko.SSHException).
        import paramiko
        retryable = isinstance(e, (EOFError, OSError, paramiko.SSHException))
        try:
            ssh.close()
        except Exception:
            logger.debug("close after sftp open fail", exc_info=True)
        if not retryable:
            raise HTTPException(status_code=503, detail=f"SFTP open failed: {e}")
        ssh = create_exec_client(srv)
        if not ssh:
            raise HTTPException(status_code=503, detail=f"Cannot connect to {name}")
        try:
            sftp = ssh._client.open_sftp()
            return sftp, ssh, srv
        except Exception as e2:
            try:
                ssh.close()
            except Exception:
                pass
            raise HTTPException(status_code=503, detail=f"SFTP open failed after retry: {e2}")


def _stat_to_dict(st, name: str, parent_path: str):
    full = (parent_path.rstrip("/") + "/" + name).replace("//", "/")
    mode = st.st_mode if st else 0
    is_dir = stat.S_ISDIR(mode)
    perms = ""
    for who in "USR", "GRP", "OTH":
        for what in "R", "W", "X":
            if mode & getattr(stat, f"S_I{what}{who}"):
                perms += what.lower()
            else:
                perms += "-"
    return {
        "name": name,
        "path": full,
        "size": st.st_size if st and not is_dir else 0,
        "is_dir": is_dir,
        "is_link": stat.S_ISLNK(mode) if st else False,
        "permissions": perms,
        "mode": oct(mode)[-3:] if st else "???",
        "mtime": datetime.fromtimestamp(st.st_mtime).isoformat() if st and st.st_mtime else None,
    }


def _safe_path(p: str) -> str:
    p = str(p or "/").replace("\\", "/")
    p = posixpath.normpath(p)
    if not p.startswith("/"):
        p = "/" + p
    return p


def _server_allowed_roots(server_config: dict) -> list[str]:
    roots = (
        server_config.get("sftp_allowed_roots")
        or server_config.get("allowed_roots")
        or server_config.get("file_roots")
        or os.getenv("SFTP_ALLOWED_ROOTS", "")
    )
    if isinstance(roots, str):
        roots = [r.strip() for r in roots.split(",") if r.strip()]
    if not isinstance(roots, list) or not roots:
        roots = list(_DEFAULT_ALLOWED_ROOTS)
    normalized = []
    for root in roots:
        safe = _safe_path(str(root))
        if safe not in normalized:
            normalized.append(safe)
    return normalized or list(_DEFAULT_ALLOWED_ROOTS)




def _default_browse_path(server_config: dict) -> str:
    roots = _server_allowed_roots(server_config)
    return roots[0] if roots else "/"


def _ensure_path_allowed(path: str, server_config: dict) -> str:
    safe = _safe_path(path)
    roots = _server_allowed_roots(server_config)
    for root in roots:
        if root == "/" or safe == root or safe.startswith(root.rstrip("/") + "/"):
            return safe
    raise HTTPException(
        status_code=403,
        detail=f"Path '{safe}' is outside allowed SFTP roots: {', '.join(roots)}",
    )


def _safe_filename(filename: str) -> str:
    name = posixpath.basename(str(filename or "").replace("\\", "/"))
    if not name or name in {".", ".."} or "/" in name or "\\" in name:
        raise HTTPException(status_code=400, detail="Invalid filename")
    return name


def _audit_sftp(request: Request, action: str, srv: dict, server_name: str, **extra):
    ac = build_audit_context(srv, server_name)
    audit(action, "server", server_name,
          format_audit_detail(ac, user=getattr(request.state, "username", ""), **{k: str(v) for k, v in extra.items()}))


@sftp_router.get("/{name}/files")
def file_list(request: Request, name: str, path: str = "/", limit: int = 500, offset: int = 0, db: Session = Depends(get_db)):
    require_admin(request, db)
    path = _safe_path(path)
    limit = max(1, min(int(limit or 500), 2000))
    offset = max(0, int(offset or 0))
    sftp, ssh, srv = _get_sftp(name)
    try:
        if path == "/" and "/" not in _server_allowed_roots(srv):
            path = _default_browse_path(srv)
        path = _ensure_path_allowed(path, srv)
        items = sftp.listdir_attr(path)
        result = [_stat_to_dict(attr, attr.filename if hasattr(attr, "filename") else str(attr), path) for attr in items]
        result.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))
        total = len(result)
        page = result[offset:offset + limit]
        _audit_sftp(request, "server.file.list", srv, name, path=path, items=len(page), total=total, offset=offset, limit=limit)
        return api_response(data={
            "path": path,
            "items": page,
            "count": len(page),
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": offset + len(page) < total,
            "allowed_roots": _server_allowed_roots(srv),
            "safety": {
                "large_directory_truncated": total > len(page),
                "delete_requires_confirm_path": True,
                "upload_overwrite_requires_confirmation": True,
            },
        })
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Path not found: {path}")
    except PermissionError:
        raise HTTPException(status_code=403, detail=f"Permission denied: {path}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        sftp.close()


@sftp_router.post("/{name}/files/upload")
async def file_upload(request: Request, name: str,
                       remote_path: str = Form(...),
                       file: UploadFile = File(...),
                       overwrite: bool = Form(False),
                       confirm_path: str = Form(""),
                       db: Session = Depends(get_db)):
    require_admin(request, db)
    remote_path = _safe_path(remote_path)
    safe_name = _safe_filename(file.filename or "upload.bin")
    sftp, ssh, srv = _get_sftp(name)
    tmp_name = None
    total_size = 0
    try:
        remote_path = _ensure_path_allowed(remote_path, srv)
        remote_full = _ensure_path_allowed(posixpath.join(remote_path, safe_name), srv)
        try:
            sftp.stat(remote_full)
            exists = True
        except FileNotFoundError:
            exists = False
        if exists and not overwrite:
            raise HTTPException(status_code=409, detail=f"File already exists: {remote_full}. Set overwrite=true and confirm_path to overwrite.")
        if exists and _safe_path(confirm_path or "") != remote_full:
            raise HTTPException(status_code=400, detail="Overwriting an existing file requires matching confirm_path")
        with tempfile.NamedTemporaryFile(delete=False, suffix=".upload") as tf:
            tmp_name = tf.name
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                total_size += len(chunk)
                if total_size > _MAX_FILE_SIZE:
                    raise HTTPException(status_code=400, detail=f"File too large, max {_MAX_FILE_SIZE} bytes")
                tf.write(chunk)
        sftp.put(tmp_name, remote_full)
        st = sftp.stat(remote_full)
        item = _stat_to_dict(st, safe_name, remote_path)
        _audit_sftp(request, "server.file.upload", srv, name, path=remote_full, size=total_size, overwrite=exists)
        return api_response(data=item)
    except PermissionError:
        raise HTTPException(status_code=403, detail=f"Permission denied: {remote_path}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if tmp_name:
            try:
                os.unlink(tmp_name)
            except FileNotFoundError:
                pass
        sftp.close()


@sftp_router.get("/{name}/files/download")
def file_download(request: Request, name: str, path: str = "", db: Session = Depends(get_db)):
    require_admin(request, db)
    if not path:
        raise HTTPException(status_code=400, detail="path is required")
    path = _safe_path(path)
    sftp, ssh, srv = _get_sftp(name)
    try:
        path = _ensure_path_allowed(path, srv)
        st = sftp.stat(path)
        if stat.S_ISDIR(st.st_mode):
            raise HTTPException(status_code=400, detail="Cannot download a directory")
        if st.st_size > _MAX_FILE_SIZE:
            raise HTTPException(status_code=400, detail=f"File too large to download, max {_MAX_FILE_SIZE}")
        with tempfile.NamedTemporaryFile(delete=False, suffix=".tmp") as tf:
            tmp_name = tf.name
        try:
            sftp.get(path, tmp_name)
            with open(tmp_name, "rb") as f:
                content = f.read()
        finally:
            try:
                os.unlink(tmp_name)
            except FileNotFoundError:
                pass
        _audit_sftp(request, "server.file.download", srv, name, path=path, size=len(content))
        return api_response(data={
            "path": path,
            "size": len(content),
            "content_base64": base64.b64encode(content).decode("ascii"),
        })
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"File not found: {path}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        sftp.close()


@sftp_router.post("/{name}/files/download")
async def file_download_post(request: Request, name: str, db: Session = Depends(get_db)):
    data = await request.json()
    return file_download(request, name, data.get("path", ""), db)


@sftp_router.get("/{name}/files/download-stream")
def file_download_stream(request: Request, name: str, path: str = "", db: Session = Depends(get_db)):
    require_admin(request, db)
    if not path:
        raise HTTPException(status_code=400, detail="path is required")
    path = _safe_path(path)
    sftp, ssh, srv = _get_sftp(name)
    tmp_name = None
    try:
        path = _ensure_path_allowed(path, srv)
        st = sftp.stat(path)
        if stat.S_ISDIR(st.st_mode):
            raise HTTPException(status_code=400, detail="Cannot download a directory")
        with tempfile.NamedTemporaryFile(delete=False, suffix=".download") as tf:
            tmp_name = tf.name
        sftp.get(path, tmp_name)
        _audit_sftp(request, "server.file.download_stream", srv, name, path=path, size=getattr(st, "st_size", 0))
        filename = posixpath.basename(path) or "download.bin"
        return FileResponse(
            tmp_name,
            media_type="application/octet-stream",
            filename=filename,
            background=BackgroundTask(lambda p: os.path.exists(p) and os.unlink(p), tmp_name),
        )
    except FileNotFoundError:
        if tmp_name:
            try: os.unlink(tmp_name)
            except Exception: pass
        raise HTTPException(status_code=404, detail=f"File not found: {path}")
    except HTTPException:
        if tmp_name:
            try: os.unlink(tmp_name)
            except Exception: pass
        raise
    except Exception as e:
        if tmp_name:
            try: os.unlink(tmp_name)
            except Exception: pass
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        sftp.close()


@sftp_router.get("/{name}/files/tail")
def file_tail(request: Request, name: str, path: str = "", lines: int = 200, bytes_limit: int = _MAX_TAIL_BYTES, db: Session = Depends(get_db)):
    require_admin(request, db)
    if not path:
        raise HTTPException(status_code=400, detail="path is required")
    path = _safe_path(path)
    lines = max(1, min(int(lines or 200), _MAX_TAIL_LINES))
    bytes_limit = max(4096, min(int(bytes_limit or _MAX_TAIL_BYTES), _MAX_TAIL_BYTES))
    sftp, ssh, srv = _get_sftp(name)
    try:
        path = _ensure_path_allowed(path, srv)
        st = sftp.stat(path)
        if stat.S_ISDIR(st.st_mode):
            raise HTTPException(status_code=400, detail="Cannot tail a directory")
        size = int(getattr(st, "st_size", 0) or 0)
        start = max(0, size - bytes_limit)
        with sftp.file(path, "rb") as f:
            if start:
                f.seek(start)
            content = f.read(bytes_limit)
        try:
            text = content.decode("utf-8", errors="replace")
        except Exception:
            text = ""
        tail_lines = text.splitlines()[-lines:]
        _audit_sftp(request, "server.file.tail", srv, name, path=path, size=size, lines=lines)
        return api_response(data={
            "path": path,
            "size": size,
            "start_offset": start,
            "lines": tail_lines,
            "line_count": len(tail_lines),
            "truncated": start > 0 or len(text.splitlines()) > lines,
            "max_tail_bytes": bytes_limit,
        })
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"File not found: {path}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        sftp.close()


@sftp_router.delete("/{name}/files")
async def file_delete(request: Request, name: str, path: str = "", db: Session = Depends(get_db)):
    require_admin(request, db)
    data = {}
    if not path:
        try:
            data = await request.json()
        except Exception:
            pass
    path = path or data.get("path", "")
    confirm_path = str(data.get("confirm_path", ""))
    if not path:
        raise HTTPException(status_code=400, detail="path is required")
    path = _safe_path(path)
    if _safe_path(confirm_path or "") != path:
        raise HTTPException(status_code=400, detail="Deleting files requires matching confirm_path")
    sftp, ssh, srv = _get_sftp(name)
    try:
        path = _ensure_path_allowed(path, srv)
        if path in _server_allowed_roots(srv):
            raise HTTPException(status_code=400, detail="Deleting an allowed root is not allowed")
        try:
            st = sftp.stat(path)
            is_dir = stat.S_ISDIR(st.st_mode)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail=f"Path not found: {path}")
        if is_dir:
            sftp.rmdir(path)
        else:
            sftp.remove(path)
        _audit_sftp(request, "server.file.delete", srv, name, path=path, is_dir=is_dir)
        return api_response(data={"deleted": path, "is_dir": is_dir})
    except PermissionError:
        raise HTTPException(status_code=403, detail=f"Permission denied: {path}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        sftp.close()


@sftp_router.post("/{name}/files/directories")
async def file_mkdir(request: Request, name: str, db: Session = Depends(get_db)):
    require_admin(request, db)
    data = await request.json()
    remote_path = data.get("path", "")
    if not remote_path:
        raise HTTPException(status_code=400, detail="path is required")
    remote_path = _safe_path(remote_path)
    sftp, ssh, srv = _get_sftp(name)
    try:
        remote_path = _ensure_path_allowed(remote_path, srv)
        sftp.mkdir(remote_path)
        _audit_sftp(request, "server.file.mkdir", srv, name, path=remote_path)
        return api_response(data={"created": remote_path})
    except FileExistsError:
        raise HTTPException(status_code=409, detail=f"Directory already exists: {remote_path}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        sftp.close()


@sftp_router.post("/{name}/files/rename")
async def file_rename(request: Request, name: str, db: Session = Depends(get_db)):
    require_admin(request, db)
    data = await request.json()
    old_path = data.get("old_path", "")
    new_path = data.get("new_path", "")
    if not old_path or not new_path:
        raise HTTPException(status_code=400, detail="old_path and new_path are required")
    old_path = _safe_path(old_path)
    new_path = _safe_path(new_path)
    sftp, ssh, srv = _get_sftp(name)
    try:
        old_path = _ensure_path_allowed(old_path, srv)
        new_path = _ensure_path_allowed(new_path, srv)
        sftp.rename(old_path, new_path)
        st = sftp.stat(new_path)
        basename = posixpath.basename(new_path)
        parent = posixpath.dirname(new_path) or "/"
        item = _stat_to_dict(st, basename, parent)
        _audit_sftp(request, "server.file.rename", srv, name, from_path=old_path, to=new_path)
        return api_response(data=item)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Path not found: {old_path}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        sftp.close()


@sftp_router.post("/{name}/files/chmod")
async def file_chmod(request: Request, name: str, db: Session = Depends(get_db)):
    require_admin(request, db)
    data = await request.json()
    remote_path = data.get("path", "")
    mode_str = data.get("mode", "")
    if not remote_path or not mode_str:
        raise HTTPException(status_code=400, detail="path and mode are required")
    remote_path = _safe_path(remote_path)
    try:
        mode = int(mode_str, 8)
    except ValueError:
        raise HTTPException(status_code=400, detail="mode must be an octal string like '755'")
    sftp, ssh, srv = _get_sftp(name)
    try:
        remote_path = _ensure_path_allowed(remote_path, srv)
        sftp.chmod(remote_path, mode)
        st = sftp.stat(remote_path)
        basename = posixpath.basename(remote_path) or remote_path.strip("/") or "/"
        parent = posixpath.dirname(remote_path) or "/"
        item = _stat_to_dict(st, basename, parent)
        _audit_sftp(request, "server.file.chmod", srv, name, path=remote_path, mode=mode_str)
        return api_response(data=item)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Path not found: {remote_path}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        sftp.close()


@sftp_router.get("/{name}/files/content")
def file_content(request: Request, name: str, path: str = "/", db: Session = Depends(get_db)):
    require_admin(request, db)
    path = _safe_path(path)
    sftp, ssh, srv = _get_sftp(name)
    try:
        path = _ensure_path_allowed(path, srv)
        st = sftp.stat(path)
        if stat.S_ISDIR(st.st_mode):
            raise HTTPException(status_code=400, detail="Cannot read directory content as text")
        if st.st_size > _MAX_TEXT_PREVIEW:
            raise HTTPException(status_code=400,
                detail=f"File too large for text preview, max {_MAX_TEXT_PREVIEW // 1024}KB")
        with sftp.file(path, "rb") as f:
            content = f.read()
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            raise HTTPException(status_code=400, detail="File is not valid UTF-8 text")
        _audit_sftp(request, "server.file.read", srv, name, path=path, size=len(content))
        return api_response(data={"path": path, "content": text, "size": len(content)})
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Path not found: {path}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        sftp.close()


@sftp_router.put("/{name}/files/content")
async def file_content_save(request: Request, name: str, db: Session = Depends(get_db)):
    require_admin(request, db)
    data = await request.json()
    remote_path = data.get("path", "")
    content = data.get("content", "")
    if not remote_path:
        raise HTTPException(status_code=400, detail="path is required")
    remote_path = _safe_path(remote_path)
    if len(content.encode("utf-8")) > _MAX_TEXT_PREVIEW:
        raise HTTPException(status_code=400,
            detail=f"Content too large, max {_MAX_TEXT_PREVIEW // 1024}KB")
    sftp, ssh, srv = _get_sftp(name)
    try:
        remote_path = _ensure_path_allowed(remote_path, srv)
        with sftp.file(remote_path, "wb") as f:
            f.write(content.encode("utf-8"))
        _audit_sftp(request, "server.file.edit", srv, name, path=remote_path, size=len(content))
        return api_response(data={"saved": remote_path, "size": len(content)})
    except PermissionError:
        raise HTTPException(status_code=403, detail=f"Permission denied: {remote_path}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        sftp.close()


@sftp_router.get("/{name}/files/stat")
def file_stat(request: Request, name: str, path: str = "/", db: Session = Depends(get_db)):
    require_admin(request, db)
    path = _safe_path(path)
    sftp, ssh, srv = _get_sftp(name)
    try:
        path = _ensure_path_allowed(path, srv)
        st = sftp.stat(path)
        basename = posixpath.basename(path) or path.strip("/") or "/"
        parent = posixpath.dirname(path) or "/"
        _audit_sftp(request, "server.file.stat", srv, name, path=path)
        return api_response(data=_stat_to_dict(st, basename, parent))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Path not found: {path}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        sftp.close()
