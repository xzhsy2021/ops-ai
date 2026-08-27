"""测试 ops.approval.reject_plan MCP 工具：拒绝执行计划的状态机与审计。

覆盖：待审批计划成功拒绝为 REJECTED、已终态计划幂等、计划不存在返回错误，
以及拒绝人身份与可选拒绝原因的落库。
"""
import pytest

from app.db.base import SessionLocal, Base, engine
from app.db.migrations.runner import run_schema_migrations
from app.services.execution_plan import ExecutionPlanService
from app.services.message_context import normalize_message_context
from app.services.tool_context import ToolContext
from app.services.tool_adapters import approval_tools


def _ctx(*, username="ops-agent", scopes=None, **overrides):
    values = {
        "auth_type": "tool_token",
        "scopes": scopes or ["ops:write"],
        "username": username,
        **overrides,
    }
    return ToolContext(**values)


def _message_context(room_id="!room:example.org", event_id="ev-reject-1", sender="@req:example.org"):
    return normalize_message_context(
        {
            "room_id": room_id,
            "request_event_id": event_id,
            "sender_matrix_id": sender,
            "content_sha256": "b" * 64,
        }
    )


def _prepare_plan(db, *, system_name="payment", environment="test"):
    service = ExecutionPlanService(db)
    plan, _ = service.prepare(
        message_context=_message_context(),
        system_name=system_name,
        service_name="api",
        environment=environment,
        targets=["s1"],
        steps=[
            {"step_key": "one", "action_type": "SERVICE_CONTROL", "parameters": {"service": "api", "action": "restart"}},
        ],
        policy={"continue_on_error": False, "max_retries": 0},
        routing_config_revision="rev-1",
        routing_ticket_digest="ticket-1",
        risk_level="high",
        authorized_matrix_users=["@alice:matrix.org"],
    )
    assert plan.status == "PENDING_APPROVAL"
    return plan, service


@pytest.fixture(scope="module")
def db():
    Base.metadata.create_all(engine)
    run_schema_migrations(engine)
    session = SessionLocal()
    yield session
    session.rollback()
    session.close()


def test_reject_pending_plan(db):
    plan, _ = _prepare_plan(db)
    result = approval_tools.approval_reject_plan(
        {"plan_id": plan.id, "reason": "方案调整，暂不实施"},
        _ctx(),
        db,
    )
    assert result["ok"] is True
    assert result["status"] == "REJECTED"
    assert result["rejected_by"] == "matrix:default:ops-agent"
    assert result["reason"] == "方案调整，暂不实施"

    db.expire_all()
    refreshed = ExecutionPlanService(db).get(plan.id)
    assert refreshed.status == "REJECTED"
    assert refreshed.failure_reason == "方案调整，暂不实施"


def test_reject_idempotent_on_terminal_state(db):
    plan, _ = _prepare_plan(db, system_name="crypto", environment="prod")
    first = approval_tools.approval_reject_plan({"plan_id": plan.id, "reason": "否"}, _ctx(), db)
    assert first["ok"] is True
    assert first["status"] == "REJECTED"

    second = approval_tools.approval_reject_plan({"plan_id": plan.id}, _ctx(), db)
    assert second["ok"] is True
    assert second["status"] == "REJECTED"
    assert "终态" in second["note"]


def test_reject_missing_plan(db):
    result = approval_tools.approval_reject_plan({"plan_id": "nope"}, _ctx(), db)
    assert result["ok"] is False
    assert result["plan_id"] == "nope"


def test_reject_uses_message_context_sender(db):
    plan, _ = _prepare_plan(db, system_name="dovo", environment="staging")
    result = approval_tools.approval_reject_plan(
        {
            "plan_id": plan.id,
            "message_context": {
                "room_id": "!room:example.org",
                "request_event_id": "ev-reject-2",
                "sender_matrix_id": "@balice:example.org",
                "content_sha256": "c" * 64,
            },
        },
        _ctx(),
        db,
    )
    assert result["ok"] is True
    assert result["rejected_by"] == "matrix:default:@balice:example.org"


def test_tool_is_registered(db):
    """工具已注册到全局 registry，并可通过 registry.call 被 agent 调用。"""
    from app.services.tool_registry import registry

    t = registry.get("ops.approval.reject_plan")
    assert t is not None
    assert t.category == "approval_reject"
    assert t.scopes == ["ops:write"]
    assert t.write is True


def test_tool_callable_via_registry(monkeypatch, db):
    """registry.call 可路由到 reject_plan handler（agent 实际调用路径）。"""
    from app.services.tool_registry import registry

    plan, _ = _prepare_plan(db, system_name="shop", environment="test")
    monkeypatch.setattr("app.services.tool_registry.enforce_tool_policy", lambda *args: {})
    monkeypatch.setattr(
        "app.services.tool_registry.record_tool_call_async",
        lambda **kwargs: "audit-1",
    )
    ctx = ToolContext(auth_type="tool_token", scopes=["ops:write"], username="ops-agent")
    result = registry.call(
        db,
        "ops.approval.reject_plan",
        {"plan_id": plan.id, "reason": "策略调整"},
        ctx,
    )["result"]
    assert result["ok"] is True
    assert result["status"] == "REJECTED"