"""Report Center for diagnostics, release, backup and MCP/AI audit evidence.

Iter39 keeps report generation read-only with respect to production resources:
it packages data that already exists in OPS into immutable JSON/Markdown
artifacts, records metadata, and emits a local notification event for traceability.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.config import get_runtime_path
from app.db.models import NotificationEvent, ReportArtifact

SCHEMA_VERSION = "iter39.report-center.v1"
REPORT_TYPES = {
    "diagnostics": {
        "title": "系统诊断报告",
        "target_type": "system",
        "description": "系统健康、构建状态、运行目录、最近错误和建议。",
        "formats": ["json", "md"],
    },
    "ai_diagnostics": {
        "title": "AI 诊断分析报告",
        "target_type": "system",
        "description": "只读 AI 诊断分析、MCP 工具链与安全边界。",
        "formats": ["json", "md"],
    },
    "operation_chain": {
        "title": "MCP/AI 操作链路报告",
        "target_type": "operation_chain",
        "description": "工具调用、风险策略、统一任务、发布计划与审计证据链。",
        "formats": ["json", "md"],
    },
    "operation_chains_index": {
        "title": "操作链路索引报告",
        "target_type": "audit",
        "description": "最近 OPS/MCP/AI 操作链路摘要索引。",
        "formats": ["json", "md"],
    },
    "deployment": {
        "title": "发布报告",
        "target_type": "deployment",
        "description": "发布结果、失败分析、分发校验、步骤任务和日志摘要。",
        "formats": ["json", "md"],
    },
    "db_query_export": {
        "title": "数据库查询导出",
        "target_type": "database",
        "description": "只读数据库查询结果导出制品，支持 CSV、JSON、XLSX、Markdown、SQL Query 和 SQL Insert。",
        "formats": ["csv", "json", "xlsx", "md", "sql_query"],
        "can_generate": False,
    },
}


def _now_dt() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _reports_dir() -> Path:
    path = Path(get_runtime_path("REPORT_DIR", "reports"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_filename(value: str, fallback: str = "report") -> str:
    name = re.sub(r"[^A-Za-z0-9._@+\-=\u4e00-\u9fff]+", "_", str(value or fallback)).strip("._")
    return name[:120] or fallback


def _json_default(value: Any):
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default), encoding="utf-8")


def _table_value(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False, default=_json_default) if isinstance(value, (dict, list)) else str(value if value is not None else "-")
    return text.replace("|", "/").replace("\n", " ")[:500]


def _generic_markdown(title: str, payload: Dict[str, Any]) -> str:
    summary = payload.get("summary") or {}
    metadata = payload.get("metadata") or {}
    lines = [f"# {title}", "", f"- 生成时间：{payload.get('generated_at') or '-'}", f"- 报告类型：{payload.get('report_type') or '-'}", f"- 目标：{payload.get('target_id') or '-'}", f"- Schema：{payload.get('schema_version') or SCHEMA_VERSION}", ""]
    if isinstance(summary, dict) and summary:
        lines.extend(["## 摘要", "", "| 字段 | 值 |", "|---|---|"])
        for key, value in summary.items():
            lines.append(f"| {_table_value(key)} | {_table_value(value)} |")
        lines.append("")
    if isinstance(metadata, dict) and metadata:
        lines.extend(["## 元数据", "", "| 字段 | 值 |", "|---|---|"])
        for key, value in metadata.items():
            lines.append(f"| {_table_value(key)} | {_table_value(value)} |")
        lines.append("")
    data = payload.get("data")
    if isinstance(data, dict):
        if payload.get("report_type") == "operation_chain":
            timeline = data.get("timeline") or []
            lines.extend(["## 操作链路时间线", ""])
            for event in timeline[:300]:
                lines.append(f"- `{_table_value(event.get('time'))}` **{_table_value(event.get('title'))}** · { _table_value(event.get('status')) } · { _table_value(event.get('risk_level')) }")
                if event.get("detail"):
                    lines.append(f"  - { _table_value(event.get('detail')) }")
            lines.append("")
        elif payload.get("report_type") == "diagnostics":
            rec_raw = data.get("recommendations")
            if isinstance(rec_raw, dict):
                recommendations = rec_raw.get("items") or []
            elif isinstance(rec_raw, list):
                recommendations = rec_raw
            else:
                recommendations = []
            if isinstance(recommendations, list):
                lines.extend(["## 问题建议", ""])
                for item in recommendations[:50]:
                    if isinstance(item, dict):
                        lines.append(f"- **{_table_value(item.get('title') or item.get('message'))}**：{_table_value(item.get('description') or item.get('reason') or '')}")
                    else:
                        lines.append(f"- {_table_value(item)}")
                lines.append("")
        elif payload.get("report_type") == "deployment":
            lines.append("## 发布概览")
            lines.append("")
            for key in ["system", "service", "environment", "status", "version", "summary_text"]:
                lines.append(f"- {key}: {_table_value(data.get(key))}")
            lines.append("")
    lines.extend(["## 原始 JSON 摘要", "", "```json", json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default)[:20000], "```", ""])
    return "\n".join(lines)


def _payload_for_report(db: Session, report_type: str, target_id: str = "", include_raw: bool = False, focus: str = "") -> Dict[str, Any]:
    if report_type == "diagnostics":
        from app.services.diagnostics import build_diagnostics_report
        data = build_diagnostics_report(db)
        summary = data.get("summary") or data.get("health") or {}
        return {"data": data, "summary": summary, "metadata": {"focus": focus or "general"}}
    if report_type == "ai_diagnostics":
        from app.services.ai_diagnostics import build_ai_diagnostic_analysis
        data = build_ai_diagnostic_analysis(db, mode="full" if include_raw else "summary", focus=focus or "general", include_report=include_raw)
        summary = data.get("summary") or {"status": data.get("status"), "highest_risk": data.get("highest_risk"), "finding_count": len(data.get("findings") or [])}
        return {"data": data, "summary": summary, "metadata": {"focus": focus or "general"}}
    if report_type == "operation_chain":
        if not target_id:
            raise HTTPException(status_code=400, detail="target_id is required for operation_chain reports")
        from app.services.audit_chain import build_operation_chain
        data = build_operation_chain(db, chain_id=target_id, include_raw=include_raw)
        summary = data.get("summary") or {}
        if not data.get("chain_id") and not summary:
            raise HTTPException(status_code=404, detail="Operation chain not found")
        return {"data": data, "summary": summary, "metadata": {"chain_id": data.get("chain_id") or target_id}}
    if report_type == "operation_chains_index":
        from app.services.audit_chain import list_operation_chains
        data = list_operation_chains(db, limit=200)
        items = data.get("items") or []
        summary = {"total": len(items), "generated_from": "operation_chains"}
        return {"data": data, "summary": summary, "metadata": {}}
    if report_type == "deployment":
        if not target_id:
            raise HTTPException(status_code=400, detail="target_id is required for deployment reports")
        from app.deploy.report import deployment_report_payload
        data = deployment_report_payload(target_id, db)
        summary = data.get("summary") or {"deployment_id": target_id, "status": data.get("status")}
        return {"data": data, "summary": summary, "metadata": {"deployment_id": target_id}}
    raise HTTPException(status_code=400, detail=f"Unsupported report_type: {report_type}")


def _format_summary(report_type: str, summary: Dict[str, Any]) -> str:
    if report_type == "operation_chain":
        return f"链路 {summary.get('chain_id') or ''} 状态 {summary.get('status') or '-'}，最高风险 {summary.get('risk_level') or '-'}。".strip()
    if report_type == "diagnostics":
        return f"系统诊断报告，状态 {summary.get('status') or summary.get('overall_status') or '-'}。"
    if report_type == "deployment":
        return f"发布 {summary.get('system') or '-'} / {summary.get('service') or '-'} 状态 {summary.get('status') or '-'}。"
    if report_type == "db_query_export":
        return f"数据库查询导出，格式 {summary.get('format') or '-'}，行数 {summary.get('row_count') or 0}。"
    if report_type == "ai_diagnostics":
        return f"AI 诊断分析，最高风险 {summary.get('highest_risk') or '-'}，发现 {summary.get('finding_count') or 0} 项。"
    return "报告已生成。"


def report_to_dict(row: ReportArtifact) -> Dict[str, Any]:
    return {
        "id": row.id,
        "report_type": row.report_type,
        "title": row.title,
        "target_type": row.target_type,
        "target_id": row.target_id,
        "status": row.status,
        "format": row.format,
        "file_path": row.file_path,
        "size_bytes": row.size_bytes or 0,
        "sha256": row.sha256,
        "summary": row.summary,
        "metadata": row.metadata_json or {},
        "created_by": row.created_by,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
        "download_url": f"/api/v2/reports/{row.id}/download",
    }


def list_report_types() -> Dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "types": REPORT_TYPES}


def list_reports(db: Session, *, report_type: str = "", limit: int = 100, since_id: str = "") -> Dict[str, Any]:
    limit = max(1, min(int(limit or 100), 500))
    q = db.query(ReportArtifact)
    if report_type:
        q = q.filter(ReportArtifact.report_type == report_type)
    if since_id:
        q = q.filter(ReportArtifact.id < since_id)
    rows = q.order_by(ReportArtifact.created_at.desc()).limit(limit).all()
    return {"items": [report_to_dict(r) for r in rows], "total": len(rows), "types": REPORT_TYPES}


def report_summary(db: Session) -> Dict[str, Any]:
    rows = db.query(ReportArtifact).order_by(ReportArtifact.created_at.desc()).limit(200).all()
    by_type: Dict[str, int] = {}
    total_size = 0
    for row in rows:
        by_type[row.report_type] = by_type.get(row.report_type, 0) + 1
        total_size += int(row.size_bytes or 0)
    return {
        "total_recent": len(rows),
        "by_type": by_type,
        "total_size_bytes": total_size,
        "latest": report_to_dict(rows[0]) if rows else None,
        "supported_types": REPORT_TYPES,
    }


def get_report(db: Session, report_id: str) -> ReportArtifact:
    row = db.query(ReportArtifact).filter(ReportArtifact.id == report_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Report not found")
    return row


def generate_report(
    db: Session,
    *,
    report_type: str,
    target_id: str = "",
    fmt: str = "json",
    title: str = "",
    created_by: str = "",
    include_raw: bool = False,
    focus: str = "",
) -> Dict[str, Any]:
    report_type = (report_type or "").strip()
    if report_type not in REPORT_TYPES:
        raise HTTPException(status_code=400, detail=f"Unsupported report_type: {report_type}")
    if REPORT_TYPES.get(report_type, {}).get("can_generate") is False:
        raise HTTPException(status_code=400, detail=f"Report type {report_type} is generated by its dedicated workflow")
    fmt = (fmt or "json").lower().strip()
    if fmt not in REPORT_TYPES[report_type]["formats"]:
        raise HTTPException(status_code=400, detail=f"Unsupported format for {report_type}: {fmt}")
    generated_at = _now_dt()
    content = _payload_for_report(db, report_type, target_id=target_id, include_raw=include_raw, focus=focus)
    default = REPORT_TYPES[report_type]
    title = title or default["title"]
    payload = {
        "schema_version": SCHEMA_VERSION,
        "report_type": report_type,
        "title": title,
        "target_type": default["target_type"],
        "target_id": target_id or "local",
        "generated_at": generated_at.isoformat(),
        "generated_by": created_by or "system",
        "summary": content.get("summary") or {},
        "metadata": content.get("metadata") or {},
        "data": content.get("data"),
    }
    report_id = uuid4().hex
    basename = _safe_filename(f"{report_type}_{target_id or 'local'}_{generated_at.strftime('%Y%m%d_%H%M%S')}_{report_id[:8]}")
    path = _reports_dir() / f"{basename}.{fmt}"
    if fmt == "json":
        _write_json(path, payload)
    else:
        path.write_text(_generic_markdown(title, payload), encoding="utf-8")
    size = path.stat().st_size
    sha = _sha256_file(path)
    row = ReportArtifact(
        id=report_id,
        report_type=report_type,
        title=title,
        target_type=default["target_type"],
        target_id=target_id or "local",
        status="ready",
        format=fmt,
        file_path=str(path),
        size_bytes=size,
        sha256=sha,
        summary=_format_summary(report_type, content.get("summary") or {}),
        metadata_json={"schema_version": SCHEMA_VERSION, "focus": focus or "", **(content.get("metadata") or {})},
        created_by=created_by or "system",
        created_at=generated_at,
        updated_at=generated_at,
    )
    db.add(row)
    db.add(NotificationEvent(
        event_type="report.created",
        target=report_id,
        status="success",
        message=f"{title} 已生成",
        payload={"report_type": report_type, "target_id": target_id or "local", "format": fmt, "sha256": sha},
        created_at=generated_at,
    ))
    db.commit()
    db.refresh(row)
    return {"report": report_to_dict(row), "payload_preview": {"summary": payload.get("summary"), "metadata": payload.get("metadata")}}


def report_download_path(row: ReportArtifact) -> Path:
    if not row.file_path:
        raise HTTPException(status_code=404, detail="Report file path is empty")
    path = Path(row.file_path).resolve()
    root = _reports_dir().resolve()
    try:
        path.relative_to(root)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid report path")
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Report file not found")
    return path
