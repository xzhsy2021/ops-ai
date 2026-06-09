from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.helpers import api_response, audit
from app.core.auth_v2 import require_admin, require_auth
from app.db import get_db
from app.db.models import ReportArtifact
from app.services.db_query_export import export_media_type
from app.services.report_center import (
    delete_reports,
    generate_report,
    get_report,
    list_report_types,
    list_reports,
    report_download_path,
    report_summary,
    report_to_dict,
)

router = APIRouter(prefix="/api/v2/reports", tags=["报告中心"])


class GenerateReportPayload(BaseModel):
    report_type: str
    target_id: str = ""
    format: str = "json"
    title: str = ""
    include_raw: bool = False
    focus: str = ""


class UpdateReportPayload(BaseModel):
    title: str | None = Field(default=None, max_length=255)
    status: str | None = Field(default=None, max_length=24)
    summary: str | None = Field(default=None, max_length=4000)


class DeleteReportsPayload(BaseModel):
    report_ids: list[str] = Field(default_factory=list, max_length=200)


@router.get("/types")
def report_types(request: Request, db: Session = Depends(get_db)):
    require_auth(request, db)
    return api_response(data=list_report_types())


@router.get("/summary")
def reports_summary(request: Request, db: Session = Depends(get_db)):
    require_auth(request, db)
    return api_response(data=report_summary(db))


@router.get("/")
@router.get("")
def reports_list(request: Request, response: Response, report_type: str = "", limit: int = 100, since_id: str = "", offset: int = 0, db: Session = Depends(get_db)):
    require_auth(request, db)
    items = list_reports(db, report_type=report_type, limit=limit, since_id=since_id, offset=offset)
    from app.api.helpers import compute_list_etag, check_etag_not_modified
    etag = compute_list_etag(items if isinstance(items, list) else items.get("items", []) if isinstance(items, dict) else [], "reports")
    not_modified = check_etag_not_modified(request, etag)
    if not_modified:
        return not_modified
    response.headers["ETag"] = etag
    return api_response(data=items)


@router.post("/generate")
def reports_generate(payload: GenerateReportPayload, request: Request, db: Session = Depends(get_db)):
    user = require_auth(request, db)
    result = generate_report(
        db,
        report_type=payload.report_type,
        target_id=payload.target_id,
        fmt=payload.format,
        title=payload.title,
        created_by=user.get("username") or "",
        include_raw=payload.include_raw,
        focus=payload.focus,
    )
    report = result.get("report") or {}
    audit("report.generate", "report", report.get("id") or payload.report_type, f"user={user.get('username')} type={payload.report_type} target={payload.target_id} format={payload.format}")
    return api_response(data=result, message="Report generated")


@router.get("/operation-chain/export")
def export_operation_chain_report(
    request: Request,
    chain_id: str,
    format: str = "json",
    include_raw: bool = False,
    db: Session = Depends(get_db),
):
    user = require_auth(request, db)
    result = generate_report(
        db,
        report_type="operation_chain",
        target_id=chain_id,
        fmt=format,
        created_by=user.get("username") or "",
        include_raw=include_raw,
    )
    report = result.get("report") or {}
    audit("report.operation_chain.export", "operation_chain", chain_id, f"user={user.get('username')} report={report.get('id')} format={format}")
    row = get_report(db, report.get("id") or "")
    path = report_download_path(row)
    media_type = export_media_type(row.format)
    return FileResponse(str(path), media_type=media_type, filename=path.name)


@router.get("/{report_id}")
def reports_get(report_id: str, request: Request, db: Session = Depends(get_db)):
    require_auth(request, db)
    return api_response(data=report_to_dict(get_report(db, report_id)))


@router.post("/delete")
def reports_delete_many(payload: DeleteReportsPayload, request: Request, db: Session = Depends(get_db)):
    user = require_admin(request, db)
    result = delete_reports(db, payload.report_ids, actor=user.get("username") or "")
    audit("report.delete_many", "report", ",".join(result.get("report_ids") or []), f"user={user.get('username')} deleted={result.get('deleted')}")
    return api_response(data=result, message="Reports deleted")


@router.patch("/{report_id}")
def reports_update(report_id: str, payload: UpdateReportPayload, request: Request, db: Session = Depends(get_db)):
    user = require_admin(request, db)
    row = get_report(db, report_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"Report '{report_id}' not found")
    changed = []
    if payload.title is not None:
        row.title = payload.title.strip() or row.title
        changed.append("title")
    if payload.status is not None:
        row.status = payload.status.strip() or row.status
        changed.append("status")
    if payload.summary is not None:
        row.summary = payload.summary.strip()
        changed.append("summary")
    db.commit()
    db.refresh(row)
    audit("report.update", "report", report_id, f"user={user.get('username')} fields={','.join(changed) or 'none'}")
    return api_response(data=report_to_dict(row), message="Report updated")


@router.get("/{report_id}/download")
def reports_download(
    report_id: str,
    request: Request,
    as_format: str = Query(
        "md",
        alias="as",
        description="目标格式：md/json/csv/xlsx；找不到则降级到该报告的原始 format",
    ),
    db: Session = Depends(get_db),
):
    """以 attachment 方式下载报告文件（Content-Disposition: attachment）。"""
    require_auth(request, db)
    row = _resolve_report(db, report_id, as_format)
    path = report_download_path(row)
    media_type = export_media_type(row.format)
    return FileResponse(
        str(path),
        media_type=media_type,
        filename=path.name,
        content_disposition_type="attachment",
    )


@router.get("/{report_id}/view")
def reports_view(
    report_id: str,
    request: Request,
    as_format: str = Query(
        "html",
        alias="as",
        description="目标格式：html/md/json/sql；找不到则降级到该报告的原始 format",
    ),
    db: Session = Depends(get_db),
):
    """在线查看报告（Content-Disposition: inline；浏览器内嵌显示而非下载）。

    与 /download 的区别：
    - HTML / MD  / JSON  / SQL  → 浏览器直接渲染（HTML/MD 友好，JSON 树形）
    - CSV / XLSX              → 浏览器尝试内嵌；不支持时自动降级为下载
    """
    require_auth(request, db)
    row = _resolve_report(db, report_id, as_format)
    path = report_download_path(row)
    media_type = export_media_type(row.format)
    # 非浏览器友好格式显式标注 inline，让前端能拦截并提示用户改用 /download
    fallback = row.format in {"csv", "xlsx"}
    return FileResponse(
        str(path),
        media_type=media_type,
        filename=path.name,
        content_disposition_type="inline" if not fallback else "attachment",
    )


def _resolve_report(db, report_id: str, desired_format: str):
    """按 (report_type, target_id, format) 找同源兄弟报告，找不到则降级到原 report_id。"""
    row = get_report(db, report_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"Report '{report_id}' not found")
    desired = (desired_format or "").lower().strip()
    if not desired or desired == row.format:
        return row
    # 在同 (report_type, target_id) 下找兄弟
    sibling = db.query(ReportArtifact).filter(
        ReportArtifact.report_type == row.report_type,
        ReportArtifact.target_id == row.target_id,
        ReportArtifact.format == desired,
        ReportArtifact.status == "ready",
    ).order_by(ReportArtifact.created_at.desc()).first()
    if sibling:
        return sibling
    # 降级：返回原 row，前端用 ?as=xxx 找不到时自动 fallback
    return row


@router.delete("/{report_id}")
def reports_delete(report_id: str, request: Request, db: Session = Depends(get_db)):
    user = require_admin(request, db)
    result = delete_reports(db, [report_id], actor=user.get("username") or "")
    audit("report.delete", "report", report_id, f"user={user.get('username')}")
    return api_response(data={"id": report_id, **result}, message="Report deleted")
