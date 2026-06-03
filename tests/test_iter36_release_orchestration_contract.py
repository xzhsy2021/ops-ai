from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def _sqlite_session(tmp_path):
    from app.db.models import Base

    engine = create_engine(
        f"sqlite:///{tmp_path / 'iter36_release.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    return engine, Session


def _make_plan(db, *, environment="test"):
    from app.db.models import ToolPlan

    plan = ToolPlan(
        plan_type="deploy",
        status="ready",
        created_by="tester",
        source_tool="ops.create_deploy_plan",
        system="ops",
        service="api",
        environment=environment,
        servers=["s1", "s2"],
        package_name="api-v2.tar.gz",
        pipeline_id="pipe-1",
        payload={
            "steps": [{"name": "restart", "type": "command", "config": {"command": "systemctl restart api"}}],
            "reason": "contract test",
        },
        confirmation={
            "blockers": [],
            "warnings": [],
            "rollback_plan": {"safe": True, "mode": "script", "description": "run rollback_script"},
        },
        precheck={"ok": True, "blocking_checks": [], "warning_checks": []},
        risk_level="high",
        confirm_text="确认发布 ops/api 到 test",
    )
    db.add(plan)
    db.commit()
    db.refresh(plan)
    return plan


def test_release_runbook_exposes_quality_gates_and_mcp_flow(tmp_path):
    from app.services.release_plan import release_runbook

    engine, Session = _sqlite_session(tmp_path)
    db = Session()
    try:
        plan = _make_plan(db)
        payload = release_runbook(plan, include_events=True, db=db)

        assert payload["summary"]["ready"] is True
        assert payload["summary"]["execution_is_taskized"] is True
        assert any(gate["key"] == "precheck" and gate["status"] == "passed" for gate in payload["quality_gates"])
        assert any(step["tool"] == "ops.execute_deploy_plan" and step["taskized"] is True for step in payload["mcp_flow"])
        assert any(section["key"] == "rollback" for section in payload["runbook_sections"])
    finally:
        db.close()
        engine.dispose()


def test_iter36_mcp_release_tools_are_registered(tmp_path):
    from app.services.tool_context import ToolContext
    from app.services.tool_registry import register_builtin_tools, registry

    engine, Session = _sqlite_session(tmp_path)
    db = Session()
    try:
        register_builtin_tools()
        ctx = ToolContext(username="tester", auth_type="session", is_admin=True, scopes=["*"], allow_write=True)
        listed = registry.list_tools(db, ctx, include_disabled=True, include_schema=False, limit=800)
        tools = {item["name"]: item for item in listed["tools"]}

        for name in ["ops.list_deploy_plans", "ops.get_deploy_plan", "ops.generate_release_runbook", "ops.get_rollback_readiness"]:
            assert name in tools
            assert tools[name]["risk"] == "low"
            assert tools[name]["category"] == "deploy_read"
    finally:
        db.close()
        engine.dispose()


def test_deploy_execute_tool_is_queued_as_operation_job(tmp_path, monkeypatch):
    from app.services.tool_context import ToolContext
    from app.services.tool_policy import save_capability_settings
    from app.services.tool_registry import register_builtin_tools, registry
    import app.services.job_service as job_service
    from app.services.job_service import get_operation_job

    engine, Session = _sqlite_session(tmp_path)
    db = Session()
    try:
        plan = _make_plan(db)
        monkeypatch.setattr(job_service, "SessionLocal", Session)
        monkeypatch.setattr(job_service, "start_job_worker", lambda job_id: None)
        save_capability_settings(db, {
            "enabled": True,
            "http_tools_enabled": True,
            "read_only": False,
            "allow_deploy_execute": True,
            "allow_high_risk_tools": True,
            "allow_prod_deploy": False,
            "require_confirmation": True,
            "taskize_high_risk_tools": True,
        })
        register_builtin_tools()
        ctx = ToolContext(username="tester", auth_type="session", is_admin=True, scopes=["*"], allow_write=True)

        result = registry.call(db, "ops.execute_deploy_plan", {"plan_id": plan.id, "confirm_text": plan.confirm_text}, ctx)

        assert result["message"] == "queued"
        assert result["job_id"]
        job = get_operation_job(db, result["job_id"])
        assert job is not None
        assert job["source_tool"] == "ops.execute_deploy_plan"
        assert job["risk_level"] == "high"
        assert job["request"]["arguments"]["plan_id"] == plan.id
    finally:
        db.close()
        engine.dispose()
