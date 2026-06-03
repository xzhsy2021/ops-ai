"""Deprecated lightweight MCP task gateway.

The canonical AI Agent integration surface is implemented in app.api.tools:
- POST /api/v2/mcp for MCP Streamable HTTP JSON-RPC
- GET  /api/v2/capabilities for capability discovery
- GET  /api/v2/tools and POST /api/v2/tools/call for HTTP tool clients

This module only keeps the small in-memory task gateway for old internal scripts.
It is intentionally marked as legacy so new clients do not treat it as the MCP
server implementation.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field

from app.api.helpers import api_response

router = APIRouter(tags=["mcp-gateway-legacy"])
legacy_router = APIRouter(prefix="/api/v2/mcp/legacy", tags=["mcp-gateway-legacy"])
compat_router = APIRouter(prefix="/api/v2/mcp", tags=["mcp-gateway-legacy"])

TASKS: Dict[str, Dict[str, Any]] = {}
CANONICAL_ENDPOINTS = {
    "mcp_streamable_http": "POST /api/v2/mcp",
    "capabilities": "GET /api/v2/capabilities",
    "tools": "GET /api/v2/tools",
    "tool_call": "POST /api/v2/tools/call",
    "resources": "GET /api/v2/mcp/resources",
    "prompts": "GET /api/v2/mcp/prompts",
}
LEGACY_NOTICE = (
    "This endpoint is kept only for old internal scripts. "
    "Use /api/v2/mcp for MCP JSON-RPC or /api/v2/tools for HTTP tool calls."
)


class MCPTaskRequest(BaseModel):
    capability: str = Field(..., description="Legacy capability name, e.g. deploy/terminal/maintenance")
    payload: Dict[str, Any] = Field(default_factory=dict)
    priority: int = 5


def _deprecated_response(response: Response) -> None:
    response.headers["Deprecation"] = "true"
    response.headers["X-OPS-Canonical-MCP"] = "/api/v2/mcp"
    response.headers["X-OPS-Canonical-Tools"] = "/api/v2/tools"


def _legacy_payload(data: Dict[str, Any]) -> Dict[str, Any]:
    return {
        **data,
        "legacy": True,
        "deprecated": True,
        "notice": LEGACY_NOTICE,
        "canonical_endpoints": CANONICAL_ENDPOINTS,
    }


@legacy_router.post('/submit', deprecated=True)
@compat_router.post('/submit', deprecated=True)
async def submit_task(request: MCPTaskRequest, response: Response):
    _deprecated_response(response)
    task_id = str(uuid.uuid4())
    TASKS[task_id] = {
        'task_id': task_id,
        'status': 'queued',
        'capability': request.capability,
        'payload': request.payload,
        'priority': request.priority,
        'created_at': datetime.now(timezone.utc).isoformat(),
        'storage': 'memory',
    }
    return api_response(data=_legacy_payload(TASKS[task_id]), message='Legacy task accepted')


@legacy_router.get('/status/{task_id}', deprecated=True)
@compat_router.get('/status/{task_id}', deprecated=True)
async def query_status(task_id: str, response: Response):
    _deprecated_response(response)
    task = TASKS.get(task_id)
    if not task:
        return api_response(success=False, message='legacy task not found', data=_legacy_payload({'task_id': task_id, 'status': 'not_found'}))
    return api_response(data=_legacy_payload(task))


@legacy_router.get('/capabilities', deprecated=True)
@compat_router.get('/capabilities', deprecated=True)
async def capabilities(response: Response):
    _deprecated_response(response)
    return api_response(data=_legacy_payload({
        'capabilities': [
            {'name': 'deploy', 'description': 'legacy deploy task placeholder'},
            {'name': 'terminal', 'description': 'legacy terminal task placeholder'},
            {'name': 'maintenance', 'description': 'legacy maintenance task placeholder'},
        ],
        'migration': [
            'Use GET /api/v2/capabilities to discover real Tool Registry capabilities.',
            'Use POST /api/v2/mcp for MCP Streamable HTTP JSON-RPC clients.',
            'Use POST /api/v2/tools/call for generic HTTP Agent clients.',
        ],
    }))


router.include_router(legacy_router)
router.include_router(compat_router)
