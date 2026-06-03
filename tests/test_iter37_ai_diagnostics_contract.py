from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def _sqlite_session(tmp_path):
    from app.db.models import Base

    engine = create_engine(
        f"sqlite:///{tmp_path / 'iter37_ai_diag.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    return engine, Session


def test_ai_diagnostic_analysis_is_read_only_and_has_toolchain(tmp_path, monkeypatch):
    from app.services.ai_diagnostics import build_ai_diagnostic_analysis

    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path / "data" / "backups"))
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "data" / "logs"))
    engine, Session = _sqlite_session(tmp_path)
    db = Session()
    try:
        payload = build_ai_diagnostic_analysis(db, mode="summary", focus="deploy")

        assert payload["schema_version"] == "iter37.ai-diagnostics.v1"
        assert payload["guardrails"]["mode"] == "read_only_analysis"
        assert "read" in payload["guardrails"]["ai_auto_allowed_risks"]
        assert "critical" in payload["guardrails"]["must_use_task_center"]
        assert any(step["tool"] == "ops.run_diagnostics" for step in payload["safe_mcp_toolchain"])
        assert any(step["tool"] == "ops.list_deploy_plans" for step in payload["safe_mcp_toolchain"])
        assert payload["findings"]
    finally:
        db.close()
        engine.dispose()


def test_prompt_registry_contains_release_and_diagnostics():
    from app.agent.prompts import prompt_registry

    all_prompts = prompt_registry()

    names = {p["name"] for p in all_prompts}
    assert "release_plan" in names
    assert "diagnostic_triage" in names
    assert "db_workflow" in names

    for p in all_prompts:
        assert "name" in p
        assert "title" in p
        assert "path" in p
        assert "category" in p


def test_iter37_ai_diagnostics_tool_is_registered_low_risk(tmp_path):
    from app.services.tool_context import ToolContext
    from app.services.tool_registry import register_builtin_tools, registry

    engine, Session = _sqlite_session(tmp_path)
    db = Session()
    try:
        register_builtin_tools()
        ctx = ToolContext(username="tester", auth_type="session", is_admin=True, scopes=["*"], allow_write=True)
        listed = registry.list_tools(db, ctx, include_disabled=True, include_schema=False, limit=900)
        tools = {item["name"]: item for item in listed["tools"]}

        assert "ops.analyze_diagnostics" in tools
        assert tools["ops.analyze_diagnostics"]["risk"] == "low"
        assert tools["ops.analyze_diagnostics"]["category"] == "ai_read"
        assert tools["ops.analyze_diagnostics"]["write"] is False
    finally:
        db.close()
        engine.dispose()


def test_ai_diagnostics_tool_call_returns_guardrails(tmp_path, monkeypatch):
    from app.services.tool_context import ToolContext
    from app.services.tool_registry import register_builtin_tools, registry

    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "data"))
    engine, Session = _sqlite_session(tmp_path)
    db = Session()
    try:
        register_builtin_tools()
        ctx = ToolContext(username="tester", auth_type="session", is_admin=True, scopes=["*"], allow_write=True)
        result = registry.call(db, "ops.analyze_diagnostics", {"mode": "summary", "focus": "backup"}, ctx)

        assert result["message"] == "success"
        payload = result["result"]
        assert payload["guardrails"]["mode"] == "read_only_analysis"
        assert any(step["tool"] == "ops.list_backups" for step in payload["safe_mcp_toolchain"])
        assert result["risk"] == "low"
    finally:
        db.close()
        engine.dispose()
