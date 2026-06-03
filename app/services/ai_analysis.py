from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.db.models import AiAnalysisFinding, AiAnalysisRun
from app.services.ai_evidence import finding_rows_from_payload, normalize_analysis_payload


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def analysis_to_dict(row: AiAnalysisRun, findings: bool = False, db: Session | None = None) -> Dict[str, Any]:
    data = {
        "id": row.id,
        "analysis_type": row.analysis_type,
        "target_type": row.target_type,
        "target_id": row.target_id,
        "source_type": row.source_type,
        "source_id": row.source_id,
        "prompt_name": row.prompt_name,
        "input_refs": row.input_refs,
        "output": row.output_json or {},
        "summary": row.summary,
        "confidence": row.confidence,
        "created_by": row.created_by,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }
    if findings and db is not None:
        rows = db.query(AiAnalysisFinding).filter(AiAnalysisFinding.analysis_run_id == row.id).order_by(AiAnalysisFinding.created_at.asc()).all()
        data["findings"] = [finding_to_dict(x) for x in rows]
    return data


def finding_to_dict(row: AiAnalysisFinding) -> Dict[str, Any]:
    return {
        "id": row.id,
        "analysis_run_id": row.analysis_run_id,
        "title": row.title,
        "finding_type": row.finding_type,
        "severity": row.severity,
        "claim": row.claim,
        "evidence": row.evidence_json or [],
        "suggestion": row.suggestion,
        "confidence": row.confidence,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def save_analysis(
    db: Session,
    *,
    analysis_type: str,
    target_type: str = "",
    target_id: str = "",
    source_type: str = "",
    source_id: str = "",
    prompt_name: str = "",
    input_refs: str = "",
    output: Dict[str, Any] | None = None,
    created_by: str = "",
) -> Dict[str, Any]:
    normalized = normalize_analysis_payload(output or {})
    row = AiAnalysisRun(
        id=uuid4().hex,
        analysis_type=analysis_type or "general",
        target_type=target_type or None,
        target_id=str(target_id) if target_id not in (None, "") else None,
        source_type=source_type or None,
        source_id=str(source_id) if source_id not in (None, "") else None,
        prompt_name=prompt_name or None,
        input_refs=input_refs or None,
        output_json=normalized,
        summary=normalized.get("summary"),
        confidence=normalized.get("confidence"),
        created_by=created_by or None,
        created_at=_now(),
    )
    db.add(row)
    db.flush()
    for finding in finding_rows_from_payload(normalized):
        db.add(AiAnalysisFinding(
            id=uuid4().hex,
            analysis_run_id=row.id,
            title=finding.get("title"),
            finding_type=finding.get("finding_type"),
            severity=finding.get("severity"),
            claim=finding.get("claim"),
            evidence_json=finding.get("evidence_json") or [],
            suggestion=finding.get("suggestion"),
            confidence=finding.get("confidence"),
            created_at=_now(),
        ))
    db.commit()
    db.refresh(row)
    return analysis_to_dict(row, findings=True, db=db)


def list_analysis(db: Session, *, analysis_type: str = "", target_type: str = "", target_id: str = "", limit: int = 100) -> Dict[str, Any]:
    limit = max(1, min(int(limit or 100), 500))
    q = db.query(AiAnalysisRun)
    if analysis_type:
        q = q.filter(AiAnalysisRun.analysis_type == analysis_type)
    if target_type:
        q = q.filter(AiAnalysisRun.target_type == target_type)
    if target_id not in (None, ""):
        q = q.filter(AiAnalysisRun.target_id == str(target_id))
    rows = q.order_by(AiAnalysisRun.created_at.desc()).limit(limit).all()
    return {"items": [analysis_to_dict(r) for r in rows], "total": len(rows)}


def get_analysis(db: Session, analysis_id: str) -> Dict[str, Any]:
    row = db.query(AiAnalysisRun).filter(AiAnalysisRun.id == analysis_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="AI analysis not found")
    return analysis_to_dict(row, findings=True, db=db)


def generate_report_from_analysis(db: Session, analysis_id: str, *, title: str = "", created_by: str = "") -> Dict[str, Any]:
    data = get_analysis(db, analysis_id)
    from app.services.report_center import generate_report_from_payload
    payload = {
        "schema_version": "ai.analysis.v1",
        "report_type": "ai_analysis",
        "target_id": analysis_id,
        "generated_at": _now().isoformat(),
        "summary": {"analysis_type": data.get("analysis_type"), "confidence": data.get("confidence"), "summary": data.get("summary")},
        "metadata": {"target_type": data.get("target_type"), "target_id": data.get("target_id")},
        "data": data,
    }
    return generate_report_from_payload(db, payload, report_type="ai_analysis", target_id=analysis_id, fmt="md", title=title or f"AI 分析报告 {analysis_id}", created_by=created_by or "ai")
