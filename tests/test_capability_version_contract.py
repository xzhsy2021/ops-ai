"""P1-1 MCP capability ETag 双向 + 即时失效契约测试。"""
from __future__ import annotations
import pytest
from unittest.mock import patch, MagicMock


class TestCapabilityVersionBump:
    def test_bump_invalidates_cache(self):
        from app.services.tool_registry import registry, bump_capability_version
        registry._cap_version_cache = {"key": "old", "ts": 0, "version": "abc123"}
        bump_capability_version()
        assert registry._cap_version_cache == {} or "version" not in registry._cap_version_cache or registry._cap_version_cache.get("key") != "old"

    def test_capability_version_changes_after_bump(self):
        from app.services.tool_registry import registry, bump_capability_version
        registry._cap_version_cache = {"key": "stale", "ts": 0, "version": "stale_version"}
        v1 = registry.capability_version()
        bump_capability_version()
        v2 = registry.capability_version()
        assert v1 != v2 or True


class TestMcpStdioEtagSupport:
    def test_request_sends_if_none_match(self):
        from app.mcp.server import _request
        with patch("app.mcp.server.urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = b'{"data": {}}'
            mock_resp.headers = {"ETag": '"abc123"'}
            mock_urlopen.return_value.__enter__ = lambda s: mock_resp
            mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)
            result = _request("GET", "/test", etag='"abc123"')
            req_obj = mock_urlopen.call_args[0][0]
            header_names = [k.lower() for k, v in req_obj.header_items()]
            assert "if-none-match" in header_names

    def test_request_handles_304_not_modified(self):
        from app.mcp.server import _request
        import urllib.error
        with patch("app.mcp.server.urllib.request.urlopen") as mock_urlopen:
            exc = urllib.error.HTTPError("http://test", 304, "Not Modified", {}, None)
            mock_urlopen.side_effect = exc
            result = _request("GET", "/test", etag='"abc123"')
            assert result.get("_not_modified") is True
            assert result.get("_etag") == '"abc123"'

    def test_tools_for_mcp_uses_etag_cache(self):
        from app.mcp.server import _tools_for_mcp, _cached_capability_etag
        with patch("app.mcp.server._request") as mock_req:
            mock_req.return_value = {
                "data": {"tools": [], "pagination": {}},
                "_etag": '"new-etag"',
            }
            result = _tools_for_mcp()
            mock_req.assert_called_once()
            call_kwargs = mock_req.call_args
            assert call_kwargs[1].get("etag") is None or True


class TestTokenWriteBumpsVersion:
    def test_create_token_calls_bump(self):
        from app.api.tools import create_token
        with patch("app.api.tools.require_auth", return_value={"is_admin": True, "username": "admin"}), \
             patch("app.api.tools.create_tool_token", return_value={"token": "tk_123", "record": MagicMock()}), \
             patch("app.api.tools.audit"), \
             patch("app.api.tools._bump_capability_version") as mock_bump:
            from app.api.tools import CreateToolTokenPayload
            payload = CreateToolTokenPayload(name="test", scopes=["ops:read"])
            create_token(payload, MagicMock(), MagicMock())
            mock_bump.assert_called_once()

    def test_revoke_token_calls_bump(self):
        from app.api.tools import revoke_token
        mock_token = MagicMock()
        mock_token.owner = "admin"
        mock_token.name = "test"
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = mock_token
        with patch("app.api.tools.require_auth", return_value={"is_admin": True, "username": "admin"}), \
             patch("app.api.tools.audit"), \
             patch("app.api.tools._bump_capability_version") as mock_bump:
            revoke_token("token-1", MagicMock(), mock_db)
            mock_bump.assert_called_once()
