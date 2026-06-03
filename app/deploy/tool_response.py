from __future__ import annotations

from typing import Any, Dict, List


def tool_result(
    *,
    data: Any = None,
    summary: str = "",
    message: str = "success",
    error_code: str = "",
    suggestions: List[str] | None = None,
    ok: bool = True,
    **extra: Any,
) -> Dict[str, Any]:
    """Stable result shape for MCP-facing OPS tools.

    The registry still wraps this value in {ok, tool, result, ...}. Keeping a
    consistent inner shape helps MCP clients rely on fields without knowing
    every tool-specific payload.
    """
    payload: Dict[str, Any] = {
        "ok": bool(ok),
        "error_code": error_code,
        "message": message,
        "summary": summary or message,
        "data": data,
        "suggestions": suggestions or [],
    }
    payload.update(extra)
    return payload
