from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.helpers import api_response
from app.core.auth_v2 import require_auth
from app.db import get_db
from app.services.ai_analysis import generate_report_from_analysis, get_analysis, list_analysis, save_analysis

router = APIRouter(prefix="/api/v2/ai/analysis", tags=["AI 分析"])


class SaveAnalysisPayload(BaseModel):
    analysis_type: str = "general"
    target_type: str = ""
    target_id: str = ""
    source_type: str = ""
    source_id: str = ""
    prompt_name: str = ""
    input_refs: str = ""
    output: Dict[str, Any] = {}


@router.get("")
def list_ai_analysis(
    request: Request,
    analysis_type: str = "",
    target_type: str = "",
    target_id: str = "",
    limit: int = 100,
    db: Session = Depends(get_db),
):
    require_auth(request, db)
    return api_response(data=list_analysis(db, analysis_type=analysis_type, target_type=target_type, target_id=target_id, limit=limit))


@router.get("/{analysis_id}")
def get_ai_analysis(analysis_id: str, request: Request, db: Session = Depends(get_db)):
    require_auth(request, db)
    return api_response(data=get_analysis(db, analysis_id))


@router.post("")
def create_ai_analysis(payload: SaveAnalysisPayload, request: Request, db: Session = Depends(get_db)):
    user = require_auth(request, db)
    actor = user.get("username") or user.get("id") or "web"
    return api_response(data=save_analysis(
        db,
        analysis_type=payload.analysis_type,
        target_type=payload.target_type,
        target_id=payload.target_id,
        source_type=payload.source_type,
        source_id=payload.source_id,
        prompt_name=payload.prompt_name,
        input_refs=payload.input_refs,
        output=payload.output,
        created_by=actor,
    ))


@router.post("/{analysis_id}/generate-report")
def generate_ai_analysis_report(analysis_id: str, request: Request, db: Session = Depends(get_db)):
    user = require_auth(request, db)
    actor = user.get("username") or user.get("id") or "web"
    return api_response(data=generate_report_from_analysis(db, analysis_id, created_by=actor))
