from types import SimpleNamespace

import pytest
from fastapi import HTTPException


class DummyCtx:
    username = "tester"
    token_owner = "tester"
    client_name = "contract"


def tool(**kwargs):
    base = {
        "name": "ops.test",
        "risk": "low",
        "category": "read",
        "write": False,
        "requires_confirmation": False,
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_risk_policy_manifest_defines_unified_rules():
    from app.services.risk_policy import risk_policy_manifest

    manifest = risk_policy_manifest()
    assert manifest["version"].startswith("iter34")
    assert manifest["rules"]["confirmation_required_from"] == "medium"
    assert manifest["rules"]["job_required_from"] == "high"
    assert any(item["level"] == "critical" for item in manifest["levels"])


def test_medium_write_tool_requires_confirmation_phrase():
    from app.services.risk_policy import evaluate_risk_policy, enforce_risk_policy

    t = tool(name="ops.create_backup", risk="medium", category="backup_write", write=True, requires_confirmation=True)
    decision = evaluate_risk_policy(t, {}, settings={"require_confirmation": True})
    assert decision.confirmation_required is True
    assert decision.expected_confirm_text == "CREATE BACKUP"
    assert decision.confirm_text_matched is False

    with pytest.raises(HTTPException) as exc:
        enforce_risk_policy(t, {}, settings={"require_confirmation": True})
    assert exc.value.status_code == 428
    assert exc.value.detail["code"] == "CONFIRMATION_REQUIRED"

    allowed = enforce_risk_policy(t, {"confirm_text": "CREATE BACKUP"}, settings={"require_confirmation": True})
    assert allowed["confirm_text_matched"] is True


def test_high_risk_plan_creation_is_not_blocked_by_confirmation():
    from app.services.risk_policy import evaluate_risk_policy, enforce_risk_policy

    t = tool(name="ops.create_config_change_plan", risk="high", category="config_write", write=True, requires_confirmation=True)
    decision = evaluate_risk_policy(t, {"system": "demo"}, settings={"require_confirmation": True})
    assert decision.confirmation_required is False
    assert any("只创建计划" in reason for reason in decision.reasons)
    assert enforce_risk_policy(t, {"system": "demo"}, settings={"require_confirmation": True})["can_auto_execute"] is False


def test_backup_restore_expected_phrase_is_file_specific():
    from app.services.risk_policy import expected_confirmation_text

    t = tool(name="ops.restore_backup", risk="critical", category="backup_restore", write=True, requires_confirmation=True)
    assert expected_confirmation_text(t, {"file": "ops_backup_x.db"}) == "RESTORE ops_backup_x.db"

    d = tool(name="ops.delete_backup", risk="high", category="backup_write", write=True, requires_confirmation=True)
    assert expected_confirmation_text(d, {"file": "ops_backup_x.db"}) == "DELETE ops_backup_x.db"


def test_manifest_builder_produces_consistent_tool_metadata():
    from app.services.tool_registry import registry, ToolDefinition

    tool_def = ToolDefinition(
        name="ops.test_builder",
        title="测试工具",
        description="Test tool for manifest builder",
        input_schema={"type": "object", "properties": {"message": {"type": "string"}}},
        output_schema=None,
        handler=lambda *_: {"ok": True},
        scopes=["ops:read"],
        risk="low",
        category="read",
        write=False,
        requires_confirmation=False,
        enabled=True,
    )

    from app.domain.tooling.manifest import build_tool_manifest, _format_tool

    native = _format_tool(tool_def, "native", include_schema=True)
    mcp = _format_tool(tool_def, "mcp")
    openai_fmt = _format_tool(tool_def, "openai")
    anthropic = _format_tool(tool_def, "anthropic")

    assert native["name"] == mcp["name"] == "ops.test_builder"
    assert openai_fmt["function"]["name"] == "ops__test_builder"
    assert anthropic["name"] == "ops.test_builder"

    assert mcp["inputSchema"]["type"] == "object"
    assert mcp["annotations"]["readOnlyHint"] is True
    assert mcp["annotations"]["destructiveHint"] is False

    assert openai_fmt["type"] == "function"
    assert openai_fmt["x_ops_tool_name"] == "ops.test_builder"

    assert anthropic["metadata"]["risk"] == "low"

    manifest = build_tool_manifest([tool_def], output_format="native", include_schema=True)
    assert len(manifest["tools"]) == 1
    assert manifest["tools"][0]["name"] == "ops.test_builder"
