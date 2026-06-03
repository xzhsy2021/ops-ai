from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import queue


def _ctx() -> SimpleNamespace:
    return SimpleNamespace(
        username="alice",
        token_owner="",
        token_name="",
        token_id="",
        client_name="test-client",
        ip_address="127.0.0.1",
        user_agent="pytest",
        role="operator",
        is_admin=False,
        can_deploy=True,
        allow_write=True,
        allow_prod=False,
        auth_type="session",
        scopes=["*"],
    )


def test_record_tool_call_async_returns_preallocated_audit_id():
    from app.services.audit_writer import record_tool_call_async

    with patch("app.services.audit_writer.submit_audit_write") as submit:
        audit_id = record_tool_call_async(
            tool_name="ops.test",
            ctx=_ctx(),
            input_args={},
            result={"ok": True},
            status="success",
        )

    assert isinstance(audit_id, str)
    assert len(audit_id) == 32
    submit.assert_called_once()


def test_record_plan_event_async_returns_event_id():
    from app.services.audit_writer import record_plan_event_async

    with patch("app.services.audit_writer.submit_audit_write") as submit:
        event_id = record_plan_event_async(
            plan_id="plan-1",
            event_type="created",
            actor="alice",
            message="created",
            payload={"x": 1},
        )

    assert isinstance(event_id, str)
    assert len(event_id) == 32
    submit.assert_called_once()


def test_submit_audit_write_falls_back_to_sync_when_queue_is_full():
    from app.services.audit_writer import submit_audit_write

    called = {"value": 0}

    def _write():
        called["value"] += 1

    with patch("app.services.audit_writer._audit_queue.put_nowait", side_effect=queue.Full):
        submit_audit_write(_write)

    assert called["value"] == 1


def test_successful_tool_call_uses_async_audit_writer():
    from app.services.tool_registry import ToolRegistry

    registry = ToolRegistry()

    @registry.register(
        name="ops.test_success",
        description="test",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
    )
    def _handler(args, ctx, db):
        return {"summary": "ok", "value": 1}

    db = MagicMock()
    ctx = _ctx()

    with patch("app.services.tool_registry.enforce_tool_policy", return_value={}), \
         patch("app.services.tool_registry.record_tool_call_async", return_value="a" * 32) as async_write:
        result = registry.call(db, "ops.test_success", {}, ctx)

    assert result["audit_id"] == "a" * 32
    assert result["message"] == "success"
    async_write.assert_called_once()
    assert async_write.call_args.kwargs["status"] == "success"


def test_queued_tool_call_uses_async_audit_writer_and_preserves_audit_id():
    from app.services.tool_registry import ToolRegistry

    registry = ToolRegistry()

    @registry.register(
        name="ops.test_queue",
        description="test",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        risk="high",
        write=True,
    )
    def _handler(args, ctx, db):
        return {"unexpected": True}

    db = MagicMock()
    ctx = _ctx()

    with patch("app.services.tool_registry.enforce_tool_policy", return_value={"risk_policy": {"must_create_job": True}}), \
         patch("app.services.job_service.enqueue_tool_job", return_value={"id": "job-1", "status": "queued"}), \
         patch("app.services.job_service.mark_job_audit_id") as mark_job_audit_id, \
         patch("app.services.tool_registry.record_tool_call_async", return_value="b" * 32) as async_write:
        result = registry.call(db, "ops.test_queue", {}, ctx)

    assert result["audit_id"] == "b" * 32
    assert result["job_id"] == "job-1"
    assert result["message"] == "queued"
    async_write.assert_called_once()
    assert async_write.call_args.kwargs["status"] == "queued"
    mark_job_audit_id.assert_called_once_with(db, "job-1", "b" * 32)
