from __future__ import annotations

from unittest.mock import MagicMock, patch


def test_policy_preview_uses_token_template_context_for_tool_decision():
    from app.api.tools import ToolPolicyPreviewPayload, preview_tool_policy

    payload = ToolPolicyPreviewPayload(
        template_key="readonly-ai",
        tool="ops.inspection.run_server",
        arguments={"server_id": "srv-1"},
    )

    with patch("app.api.tools.require_auth", return_value={"is_admin": True, "username": "admin"}), \
         patch("app.api.tools.register_builtin_tools"), \
         patch("app.api.tools.registry.get") as mock_get, \
         patch("app.api.tools.registry.evaluate_policy", return_value={"allowed": False, "blocked_reason": "Tool scope required: ops:write", "risk": "high", "category": "inspection_execute"}):
        tool = MagicMock()
        tool.name = "ops.inspection.run_server"
        tool.scopes = ["ops:read", "ops:write"]
        tool.write = True
        tool.risk = "high"
        tool.category = "inspection_execute"
        mock_get.return_value = tool

        response = preview_tool_policy(payload, MagicMock(), MagicMock())

    data = response["data"]
    assert data["allowed"] is False
    assert data["blocked_reason"] == "Tool scope required: ops:write"
    assert data["subject"]["source"] == "template"
    assert data["subject"]["template_key"] == "readonly-ai"
    assert data["subject"]["allow_write"] is False
    assert data["permission"]["required_scopes"] == ["ops:read", "ops:write"]
    assert "scope" in data["permission"]["gates"]


def test_policy_preview_uses_existing_token_record_context():
    from app.api.tools import ToolPolicyPreviewPayload, preview_tool_policy

    token = MagicMock()
    token.id = "tok-1"
    token.name = "inspection-ai"
    token.owner = "ops"
    token.scopes = ["ops:read", "ops:write"]
    token.allow_write = True
    token.allow_prod = False
    token.channel_bindings = []
    token.approver_identities = []

    payload = ToolPolicyPreviewPayload(token_id="tok-1", tool="ops.inspection.run_server")

    with patch("app.api.tools.require_auth", return_value={"is_admin": True, "username": "admin"}), \
         patch("app.api.tools.register_builtin_tools"), \
         patch("app.api.tools.registry.get") as mock_get, \
         patch("app.api.tools.registry.evaluate_policy", return_value={"allowed": True, "blocked_reason": "", "risk": "high", "category": "inspection_execute"}):
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = token
        tool = MagicMock()
        tool.name = "ops.inspection.run_server"
        tool.scopes = ["ops:read", "ops:write"]
        tool.write = True
        tool.risk = "high"
        tool.category = "inspection_execute"
        mock_get.return_value = tool

        response = preview_tool_policy(payload, MagicMock(), db)

    data = response["data"]
    assert data["allowed"] is True
    assert data["subject"]["source"] == "token"
    assert data["subject"]["token_id"] == "tok-1"
    assert data["required_scopes"] == ["ops:read", "ops:write"]
    assert data["permission"]["write"] is True
