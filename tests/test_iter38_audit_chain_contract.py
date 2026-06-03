from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def _sqlite_session(tmp_path):
    from app.db.models import Base

    engine = create_engine(
        f"sqlite:///{tmp_path / 'iter38_audit_chain.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    return engine, Session


def test_operation_chain_links_tool_job_plan_deployment(tmp_path, monkeypatch):
    from app.db.models import Deployment, OperationJob, ToolCallLog, ToolPlan, ToolPlanEvent
    from app.services.audit_chain import build_operation_chain, list_operation_chains

    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "data"))
    engine, Session = _sqlite_session(tmp_path)
    db = Session()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    try:
        dep = Deployment(id="dep1", system="ops", service="web", environment="prod", status="success", servers="s1", started_at=now, finished_at=now, created_by="alice")
        plan = ToolPlan(id="plan1", plan_type="deploy", status="executed", created_by="alice", source_tool="ops.create_deploy_plan", system="ops", service="web", environment="prod", risk_level="high", related_deployment_id="dep1", created_at=now, updated_at=now)
        job = OperationJob(id="job1", job_type="mcp_tool", source="mcp", source_tool="ops.execute_deploy_plan", title="工具执行 ops.execute_deploy_plan", status="success", risk_level="high", operator="alice", target="plan1", request_json={"tool": "ops.execute_deploy_plan", "arguments": {"plan_id": "plan1"}}, result_json={"ok": True}, audit_id="call1", created_at=now, started_at=now, finished_at=now, updated_at=now)
        call = ToolCallLog(id="call1", tool_name="ops.execute_deploy_plan", username="alice", input_args='{"plan_id":"plan1"}', result_preview='{"job_id":"job1"}', status="queued", risk_level="high", related_plan_id="plan1", related_job_id="job1", created_at=now)
        event = ToolPlanEvent(plan_id="plan1", event_type="executed", actor="alice", message="计划已执行", payload={"deployment_id": "dep1"}, created_at=now)
        db.add_all([dep, plan, job, call, event])
        db.commit()

        chain = build_operation_chain(db, chain_id="tool:call1")
        assert chain["schema_version"] == "iter38.audit-chain.v1"
        assert chain["summary"]["risk_level"] == "high"
        assert chain["summary"]["tool_call_count"] >= 1
        assert chain["summary"]["job_count"] >= 1
        assert chain["summary"]["plan_count"] >= 1
        assert chain["summary"]["deployment_count"] >= 1
        assert any(edge["relation"] == "queued_job" for edge in chain["edges"])
        assert any(item["type"] == "job_created" for item in chain["timeline"])
        assert chain["guardrails"]["does_not_execute_tools"] is True

        listed = list_operation_chains(db, limit=20)
        ids = {x["chain_id"] for x in listed["items"]}
        assert "tool:call1" in ids
        assert "job:job1" in ids
        assert "plan:plan1" in ids
        assert "deployment:dep1" in ids
    finally:
        db.close()
        engine.dispose()


def test_iter38_audit_tools_are_registered_read_only(tmp_path):
    from app.services.tool_context import ToolContext
    from app.services.tool_registry import register_builtin_tools, registry

    engine, Session = _sqlite_session(tmp_path)
    db = Session()
    try:
        register_builtin_tools()
        ctx = ToolContext(username="tester", auth_type="session", is_admin=True, scopes=["*"], allow_write=True)
        listed = registry.list_tools(db, ctx, include_disabled=True, include_schema=False, limit=900)
        tools = {item["name"]: item for item in listed["tools"]}
        for name in ["ops.list_operation_chains", "ops.get_operation_chain"]:
            assert name in tools
            assert tools[name]["risk"] == "low"
            assert tools[name]["category"] == "audit_read"
            assert tools[name]["write"] is False
    finally:
        db.close()
        engine.dispose()
