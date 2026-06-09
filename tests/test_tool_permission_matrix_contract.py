from __future__ import annotations

from types import SimpleNamespace


def test_tool_permission_matrix_summarizes_gate_requirements():
    from app.services.tool_permission_matrix import summarize_tool_permission

    tool = SimpleNamespace(
        name="ops.deploy.execute_plan",
        scopes=["deploy:execute"],
        category="deploy_execute",
        risk="critical",
        write=True,
        requires_confirmation=True,
        requires_human_approval=True,
        data_sensitivity="internal",
    )

    summary = summarize_tool_permission(tool)

    assert summary["tool"] == "ops.deploy.execute_plan"
    assert summary["required_scopes"] == ["deploy:execute"]
    assert summary["write"] is True
    assert summary["risk"] == "critical"
    assert "scope" in summary["gates"]
    assert "capability_setting" in summary["gates"]
    assert "risk_confirmation" in summary["gates"]
    assert "human_approval" in summary["gates"]
    assert summary["capability_settings"] == ["allow_deploy_execute", "allow_critical_risk_tools"]


def test_tool_permission_matrix_marks_low_risk_read_tools_as_auto_callable_candidates():
    from app.services.tool_permission_matrix import summarize_tool_permission

    tool = SimpleNamespace(
        name="ops.list_servers",
        scopes=["ops:read", "server:read"],
        category="server_read",
        risk="low",
        write=False,
        requires_confirmation=False,
        requires_human_approval=False,
        data_sensitivity="internal",
    )

    summary = summarize_tool_permission(tool)

    assert summary["write"] is False
    assert summary["ai_level"] == "L1"
    assert summary["auto_callable_candidate"] is True
    assert summary["capability_settings"] == ["allow_server_read"]
