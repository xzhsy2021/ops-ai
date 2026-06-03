from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.helpers import api_response
from app.db import get_db
from app.domain.runtime.snapshots import build_deployments_aggregate

router = APIRouter(prefix="/api/v2/status", tags=["发布聚合状态"])


@router.get("/deploy-aggregate")
async def deploy_aggregate_status(
    db: Session = Depends(get_db),
    system: str = Query(""),
    environment: str = Query(""),
):
    result = build_deployments_aggregate(db, system=system, environment=environment)
    return api_response(data=result)