"""API 通用工具 - V2"""
import hashlib
import logging
from typing import Any, Dict, List, Optional
from fastapi import HTTPException, Request, Response
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


def apply_cursor_pagination(
    query,
    since_id: Optional[str] = None,
    limit: int = 50,
    order_by_id_desc: bool = True,
    id_column: str = "id",
):
    if since_id:
        from sqlalchemy import or_
        if order_by_id_desc:
            query = query.filter(getattr(query.column_descriptions[0]["entity"], id_column) < since_id)
        else:
            query = query.filter(getattr(query.column_descriptions[0]["entity"], id_column) > since_id)
    if order_by_id_desc:
        query = query.order_by(getattr(query.column_descriptions[0]["entity"], id_column).desc())
    else:
        query = query.order_by(getattr(query.column_descriptions[0]["entity"], id_column).asc())
    query = query.limit(limit)
    return query


def _etag_identity(item: Any) -> str:
    """取条目身份+时间戳用于 ETag。

    历史缺陷：dict 条目没有 ``id`` 属性，``getattr(item, "id", id(item))`` 退化成
    CPython 内存地址，同一份数据每次请求算出的 ETag 都不同 → 客户端带
    If-None-Match 永远命中不了，304 短路实际失效（审计/工具调用/报表/令牌列表
    传的都是 dict）。
    """
    if isinstance(item, dict):
        ident = item.get("id") or item.get("chain_id") or item.get("key") or item.get("name") or ""
        stamp = item.get("updated_at") or item.get("created_at") or item.get("finished_at") or item.get("started_at") or ""
        return f"{ident}:{stamp}"
    ident = getattr(item, "id", None)
    if ident is None:
        ident = getattr(item, "created_at", "")
    stamp = getattr(item, "updated_at", "") or getattr(item, "created_at", "") or ""
    return f"{ident}:{stamp}"


def compute_list_etag(items: List[Any], prefix: str = "list") -> str:
    raw = f"{prefix}:{len(items)}"
    for item in items:
        raw += f":{_etag_identity(item)}"
    return f'W/"{hashlib.md5(raw.encode()).hexdigest()[:12]}"'


def check_etag_not_modified(request: Request, etag: str) -> Optional[Response]:
    if request.headers.get("If-None-Match") == etag:
        return Response(status_code=304, headers={"ETag": etag})
    return None


def api_response(data=None, success=True, message="", **kwargs):
    resp = {"success": success, "message": message}
    if data is not None:
        resp["data"] = data
    resp.update(kwargs)
    return resp


def api_error_response(status_code: int, message: str, detail: str = "") -> JSONResponse:
    content = {"success": False, "message": message}
    if detail:
        content["detail"] = detail
    return JSONResponse(status_code=status_code, content=content)


def register_exception_handlers(app):
    from fastapi import Request

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        if exc.status_code == 304:
            return Response(status_code=304, headers=exc.headers or {})
        detail = ""
        if isinstance(exc.detail, str):
            detail = exc.detail
        elif isinstance(exc.detail, dict):
            detail = exc.detail.get("detail", str(exc.detail))
        return JSONResponse(
            status_code=exc.status_code,
            content={"success": False, "message": detail or f"HTTP Error {exc.status_code}", "detail": detail},
        )

    @app.exception_handler(Exception)
    async def generic_exception_handler(request: Request, exc: Exception):
        import traceback as _tb
        tb_str = _tb.format_exc()
        logger.error(f"Unhandled exception on {request.url.path}: {exc}\n{tb_str}")
        return JSONResponse(
            status_code=500,
            content={"success": False, "message": "Internal server error", "detail": "An unexpected error occurred"},
        )


def validate_file(file_name: str, upload_dir: str) -> str:
    import os
    if not file_name:
        raise HTTPException(status_code=400, detail="file_name is required")
    if ".." in file_name or file_name.startswith("/") or file_name.startswith("\\"):
        raise HTTPException(status_code=400, detail="Invalid file name")
    file_path = os.path.join(upload_dir, file_name)
    real_path = os.path.realpath(file_path)
    real_upload = os.path.realpath(upload_dir)
    if not real_path.startswith(real_upload + os.sep) and real_path != real_upload:
        raise HTTPException(status_code=400, detail="Invalid file path")
    if not os.path.exists(real_path):
        raise HTTPException(status_code=404, detail=f"File not found: {file_name}")
    return real_path


def audit(action: str, target_type: str = None, target_name: str = None, details: str = None):
    try:
        from config_manager import save_audit_log
        from app.core.log_context import get_request_id, get_operation_id
        ctx = {}
        rid = get_request_id()
        if rid:
            ctx["request_id"] = rid
        oid = get_operation_id()
        if oid:
            ctx["operation_id"] = oid
        details = f"{details or ''}"
        if ctx:
            details = f"{details} | ctx={ctx}"
        save_audit_log(action, target_type, target_name, details)
    except Exception:
        logger.warning(f"Failed to save audit log: action={action}", exc_info=True)


def list_upload_files(upload_dir: str):
    import os
    if not os.path.exists(upload_dir):
        return []
    files = []
    for f in sorted(os.listdir(upload_dir), key=lambda x: os.path.getmtime(os.path.join(upload_dir, x)), reverse=True):
        fp = os.path.join(upload_dir, f)
        if os.path.isfile(fp):
            files.append({
                "name": f,
                "size": os.path.getsize(fp),
                "modified": os.path.getmtime(fp),
            })
    return files
