from datetime import datetime
import os
from fastapi import APIRouter
from app.api.helpers import api_response

router = APIRouter(prefix="/api/v2/dashboard", tags=["dashboard"])

@router.get("/overview")
async def overview():
    load=os.getloadavg() if hasattr(os, "getloadavg") else (0,0,0)
    return api_response(data={
        "timestamp": datetime.utcnow().isoformat(),
        "platform": "ops-platform",
        "capabilities": ["deploy", "terminal", "maintenance", "mcp"],
        "runtime": {
            "load_avg": list(load),
            "cpu_count": os.cpu_count()
        },
        "architecture": {
            "mode": "mcp-gateway",
            "llm_embedded": False
        }
    })
