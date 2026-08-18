"""Stage 4: HTTP MCP tools/list TTL cache contract."""
import time

import pytest

from app.services import mcp_capability_service as svc


@pytest.fixture(autouse=True)
def _clean_cache():
    svc._tools_list_cache.clear()
    yield
    svc._tools_list_cache.clear()


def _spy_list_tools(monkeypatch):
    from app.services.tool_registry import registry

    calls = []
    orig = registry.list_tools

    def spy(*args, **kwargs):
        calls.append(1)
        return orig(*args, **kwargs)

    monkeypatch.setattr(registry, "list_tools", spy)
    return calls


def test_tools_list_cache_hit_returns_equal_copy_with_stable_etag(monkeypatch):
    from app.services.tool_registry import register_builtin_tools

    register_builtin_tools()
    monkeypatch.setattr(svc, "TOOLS_LIST_TTL", 600.0)
    params = {"limit": 200, "profile": "ai_full"}

    r1 = svc.mcp_tools_list(None, None, params)
    r2 = svc.mcp_tools_list(None, None, params)

    assert r1["tools"] == r2["tools"]
    assert r1["tools"] is not r2["tools"]  # caller gets an independent copy
    assert svc._tools_payload_etag(r1) == svc._tools_payload_etag(r2)
    assert len(r1["tools"]) > 0


def test_tools_list_cache_short_circuits_and_evicts_after_ttl(monkeypatch):
    from app.services.tool_registry import register_builtin_tools

    register_builtin_tools()
    monkeypatch.setattr(svc, "TOOLS_LIST_TTL", 5.0)
    calls = _spy_list_tools(monkeypatch)
    params = {"limit": 200, "profile": "ai_full"}

    svc.mcp_tools_list(None, None, params)  # miss -> 1 list_tools
    assert len(calls) == 1

    svc.mcp_tools_list(None, None, params)  # hit  -> still 1
    assert len(calls) == 1

    # Age the entry past TTL -> the next call recomputes.
    key = svc._tools_list_profile(params)
    svc._tools_list_cache[key]["ts"] = time.monotonic() - 10.0
    svc.mcp_tools_list(None, None, params)  # stale -> 2 list_tools
    assert len(calls) == 2


def test_tools_list_cache_disabled_when_ttl_zero(monkeypatch):
    from app.services.tool_registry import register_builtin_tools

    register_builtin_tools()
    monkeypatch.setattr(svc, "TOOLS_LIST_TTL", 0.0)
    calls = _spy_list_tools(monkeypatch)
    params = {"limit": 200, "profile": "ai_full"}

    svc.mcp_tools_list(None, None, params)
    svc.mcp_tools_list(None, None, params)
    assert len(calls) == 2  # TTL=0 always recomputes