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
        assert v1 != v2

    def test_capability_version_is_stable_without_registry_or_policy_changes(self):
        from app.services.tool_registry import registry
        from datetime import datetime, timezone

        registry.clear_capability_cache()
        with patch("app.services.tool_registry.datetime") as mock_datetime:
            mock_datetime.now.return_value = datetime(2026, 6, 8, 10, 0, tzinfo=timezone.utc)
            v1 = registry.capability_version()
        registry.clear_capability_cache()
        with patch("app.services.tool_registry.datetime") as mock_datetime:
            mock_datetime.now.return_value = datetime(2026, 6, 8, 10, 5, tzinfo=timezone.utc)
            v2 = registry.capability_version()

        assert v1 == v2


class TestCapabilityConditionalGet:
    def test_http_exception_handler_preserves_empty_304_response(self):
        from fastapi import FastAPI, HTTPException
        from fastapi.testclient import TestClient

        from app.api.helpers import register_exception_handlers

        app = FastAPI()
        register_exception_handlers(app)

        @app.get("/etag")
        def etag_route():
            raise HTTPException(status_code=304, detail="Not Modified", headers={"ETag": '"capability-stable123"'})

        response = TestClient(app).get("/etag")

        assert response.status_code == 304
        assert response.content == b""
        assert response.headers.get("etag") == '"capability-stable123"'

    def test_capabilities_returns_304_when_if_none_match_matches(self):
        from fastapi import HTTPException
        from app.api.tools import get_capabilities

        request = MagicMock()
        request.headers = {"if-none-match": '"capability-stable123"'}
        response = MagicMock()
        with patch("app.api.tools.get_tool_context"), \
             patch("app.api.tools.register_builtin_tools"), \
             patch("app.api.tools.registry.describe_capabilities", return_value={"server": {"capability_version": "stable123"}}):
            try:
                get_capabilities(request, response, db=MagicMock())
            except HTTPException as exc:
                assert exc.status_code == 304
                assert exc.headers == {"ETag": '"capability-stable123"', "X-Capability-Version": "stable123"}
            else:
                raise AssertionError("Expected HTTPException 304")

    def test_tools_returns_304_when_if_none_match_matches(self):
        from fastapi import HTTPException
        from app.api.tools import list_tools

        request = MagicMock()
        request.headers = {"if-none-match": '"capability-stable123"'}
        response = MagicMock()
        with patch("app.api.tools.get_tool_context"), \
             patch("app.api.tools.register_builtin_tools"), \
             patch("app.api.tools.build_tool_manifest", return_value={"tools": [], "pagination": {}}), \
             patch("app.api.tools.registry.capability_version", return_value="stable123"), \
             patch("app.api.tools.get_capability_settings", return_value={}):
            try:
                list_tools(request, response, db=MagicMock())
            except HTTPException as exc:
                assert exc.status_code == 304
                assert exc.headers == {"ETag": '"capability-stable123"', "X-Capability-Version": "stable123"}
            else:
                raise AssertionError("Expected HTTPException 304")


class TestTokenWriteBumpsVersion:
    def test_create_token_calls_bump(self):
        from app.api.tools import create_token
        with patch("app.api.tools.require_auth", return_value={"is_admin": True, "username": "admin"}), \
             patch("app.api.tools.create_tool_token", return_value={"token": "tk_123", "record": MagicMock()}), \
             patch("app.api.tools.token_to_dict", return_value={"allow_write": False, "allow_prod": False}), \
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
