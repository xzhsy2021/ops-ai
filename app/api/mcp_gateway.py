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

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field

from app.api.helpers import api_response

router = APIRouter(tags=["mcp-gateway-legacy"])
legacy_router = APIRouter(prefix="/api/v2/mcp/legacy", tags=["mcp-gateway-legacy"])
compat_router = APIRouter(prefix="/api/v2/mcp", tags=["mcp-gateway-legacy"])

TASKS: Dict[str, Dict[str, Any]] = {}
# 第 12 轮：/api/v2/mcp 在全局会话中间件的放行前缀里（见 app/core/security.py 的
# PUBLIC_PREFIXES，为了让 tool token / MCP 客户端自己完成鉴权），而本模块的
# /submit **没有任何自身鉴权**，TASKS 又是只增不减的进程内字典 —— 匿名调用即可
# 持续占用内存（小内存自托管机器上足以拖垮进程）。这里给条目数与单条 payload 加上限。
MAX_LEGACY_TASKS = max(1, int(os.getenv("MCP_LEGACY_MAX_TASKS", "200") or "200"))
MAX_LEGACY_PAYLOAD_BYTES = max(1024, int(os.getenv("MCP_LEGACY_MAX_PAYLOAD_BYTES", "65536") or "65536"))
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


def _evict_legacy_tasks() -> None:
    """保留的遗留任务数达到上限时，按创建顺序淘汰最旧的条目（第 12 轮）。

    上限保证匿名（未鉴权）调用无法让进程内存无界增长；被淘汰的任务查询时
    仍会得到结构一致的 ``status=not_found``，旧脚本的兼容性不受影响。
    """
    while len(TASKS) >= MAX_LEGACY_TASKS:
        oldest = min(TASKS, key=lambda key: (str(TASKS[key].get("created_at") or ""), key))
        TASKS.pop(oldest, None)


def _payload_size_bytes(payload: Dict[str, Any]) -> int:
    try:
        return len(json.dumps(payload or {}, ensure_ascii=False, default=str).encode("utf-8"))
    except Exception:
        return MAX_LEGACY_PAYLOAD_BYTES + 1


@legacy_router.post('/submit', deprecated=True)
@compat_router.post('/submit', deprecated=True)
async def submit_task(request: MCPTaskRequest, response: Response):
    _deprecated_response(response)
    if _payload_size_bytes(request.payload) > MAX_LEGACY_PAYLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"legacy payload too large (limit {MAX_LEGACY_PAYLOAD_BYTES} bytes)",
        )
    _evict_legacy_tasks()
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
