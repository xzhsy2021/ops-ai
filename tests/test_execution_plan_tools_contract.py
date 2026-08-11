"""MCP contract tests for ops.approval.prepare_plan / execute_plan tools."""
import uuid
from unittest.mock import patch

import pytest

from app.db.base import SessionLocal, Base, engine
from app.db.migrations.runner import run_schema_migrations
from app.services.tool_registry import registry, register_builtin_tools
from app.services.tool_context import ToolContext
from app.db.models import ExecutionPlan
from app.services.message_context import MessageContext
from app.services.qclaw_routing import compute_routing_revision, issue_ticket

_RUN_ID = uuid.uuid4().hex[:8]

register_builtin_tools()


@pytest.fixture(autouse=True)
def _strong_signing_key(monkeypatch):
    monkeypatch.setattr(
        "app.services.qclaw_routing.QCLAW_APPROVAL_SIGNING_KEY",
        "execution-plan-test-key-0123456789abcdef",
    )


def _room(suffix: str) -> str:
    return f"!room-{_RUN_ID}-{suffix}:matrix.org"


def _event(suffix: str) -> str:
    return f"$evt-{_RUN_ID}-{suffix}:matrix.org"


def _steps() -> list[dict]:
    return [
        {
            "step_key": "restart",
            "action_type": "SERVICE_CONTROL",
            "parameters": {"control_action": "restart", "targets": ["s1"]},
            "dependencies": [],
        },
        {
            "step_key": "health",
            "action_type": "HEALTH_CHECK",
            "parameters": {"targets": ["s1"]},
            "dependencies": ["restart"],
        },
    ]


def _ctx(bound_room_ids=None, approver_matrix_ids=None):
    from app.services.tool_token import (
        resolve_approver_identities,
        resolve_channel_bindings,
    )

    return ToolContext(
        auth_type="tool_token",
        token_name="test-token",
        token_owner="test",
        allow_write=True,
        channel_bindings=resolve_channel_bindings(legacy=bound_room_ids or []),
        approver_identities=resolve_approver_identities(
            legacy=approver_matrix_ids or []
        ),
    )


@pytest.fixture(scope="module")
def db():
    Base.metadata.create_all(engine)
    run_schema_migrations(engine)
    session = SessionLocal()
    yield session
    session.rollback()
    session.close()


def _prepare_args(suffix, **overrides):
    from app.services.tool_adapters.approval_tools import _routing_systems

    context = MessageContext(
        channel="matrix",
        channel_account_id="default",
        conversation_id=_room(suffix),
        message_id=_event(suffix),
        sender_id="@requester:matrix.org",
        content_sha256="a" * 64,
    )
    system_name = overrides.get("system_name", "payment")
    service_name = overrides.get("service_name", "api")
    ticket = issue_ticket(
        context,
        system_name,
        service_name,
        compute_routing_revision(_routing_systems()),
    )
    defaults = dict(
        message_context=context.to_dict(),
        routing_ticket=ticket.ticket,
        system_name="payment",
        service_name="api",
        environment="test",
        targets=["s1"],
        steps=_steps(),
        policy={"continue_on_error": False, "max_retries": 0},
        risk_level="high",
        ai_reason="test",
    )
    defaults.update(overrides)
    return defaults


# ── 注册契约 ──

def test_prepare_plan_is_registered():
    """prepare_plan 已注册并包含消息/路由/步骤字段。"""
    tool = registry.get("ops.approval.prepare_plan")
    props = tool.input_schema["properties"]
    for field in (
        "message_context", "routing_ticket",
        "system_name", "environment", "targets",
        "steps", "policy",
    ):
        assert field in props, f"prepare_plan schema 缺少 {field}"
    assert "routing_config_revision" not in props
    assert "routing_ticket_digest" not in props
    assert "steps" in tool.input_schema["required"]


def test_execute_plan_is_registered():
    """execute_plan 已注册并包含计划 ID、短码、审批人、房间、事件字段。"""
    tool = registry.get("ops.approval.execute_plan")
    props = tool.input_schema["properties"]
    for field in (
        "plan_id", "short_code", "approver_matrix_id",
        "room_id", "approval_event_id",
    ):
        assert field in props, f"execute_plan schema 缺少 {field}"
    assert "plan_id" in tool.input_schema["required"]
    assert "short_code" in tool.input_schema["required"]


# ── prepare_plan ──

def test_prepare_plan_creates_plan_and_enforces_room_binding(db):
    """prepare_plan 创建计划、强制房间绑定、捕获授权审批人。"""
    from app.services.tool_adapters.approval_tools import approval_prepare_plan

    suffix = "prepare-basic"
    room_id = _room(suffix)
    ctx = _ctx(bound_room_ids=[room_id], approver_matrix_ids=["@alice:matrix.org"])

    result = approval_prepare_plan(_prepare_args(suffix), ctx=ctx, db=db)

    assert result["plan_id"]
    assert len(result["short_code"]) == 8
    assert result["status"] == "PENDING_APPROVAL"
    assert result["step_count"] == 2
    # 授权审批人来自 token 白名单
    assert result["authorized_approvers"] == ["@alice:matrix.org"]

    plan = db.query(ExecutionPlan).filter(ExecutionPlan.id == result["plan_id"]).first()
    assert plan is not None
    assert plan.authorized_matrix_users == ["@alice:matrix.org"]


def test_prepare_plan_rejects_unbound_room(db):
    """未绑定房间调用 prepare_plan 被拒绝。"""
    from app.services.tool_adapters.approval_tools import approval_prepare_plan

    suffix = "prepare-room-block"
    ctx = _ctx(bound_room_ids=["!allowed:matrix.org"])

    with pytest.raises(Exception):
        approval_prepare_plan(_prepare_args(suffix), ctx=ctx, db=db)


def test_prepare_plan_idempotent_for_duplicate_manifest(db):
    """重复 manifest 的 prepare_plan 返回已有计划且无新短码。"""
    from app.services.tool_adapters.approval_tools import approval_prepare_plan

    suffix = "prepare-idem"
    ctx = _ctx(approver_matrix_ids=["@alice:matrix.org"])
    args = _prepare_args(suffix)

    r1 = approval_prepare_plan(dict(args), ctx=ctx, db=db)
    r2 = approval_prepare_plan(dict(args), ctx=ctx, db=db)

    assert r1["plan_id"] == r2["plan_id"]
    assert r1["short_code"] != ""
    assert r2["short_code"] == ""
    assert r2["status"] == "PENDING_APPROVAL"


# ── execute_plan ──

def test_execute_plan_consumes_once_and_invokes_executor(db):
    """execute_plan 消费一次并调用 PlanExecutor。"""
    from app.services.tool_adapters.approval_tools import (
        approval_prepare_plan,
        approval_execute_plan,
    )
    from app.services.plan_executor import PlanExecutor

    suffix = "execute-basic"
    ctx = _ctx(approver_matrix_ids=["@alice:matrix.org"])
    prepared = approval_prepare_plan(_prepare_args(suffix), ctx=ctx, db=db)

    with patch.object(
        PlanExecutor, "execute",
        return_value=db.query(ExecutionPlan).filter(
            ExecutionPlan.id == prepared["plan_id"]
        ).first(),
    ) as mock_execute:
        result = approval_execute_plan(
            args={
                "plan_id": prepared["plan_id"],
                "short_code": prepared["short_code"],
                "approver_matrix_id": "@alice:matrix.org",
                "room_id": _room(suffix),
                "approval_event_id": _event(f"approve-{suffix}"),
            },
            ctx=ctx,
            db=db,
        )
        mock_execute.assert_called_once()

    assert result["plan_id"] == prepared["plan_id"]
    assert result["status"] in ("APPROVED", "RUNNING", "SUCCEEDED", "FAILED")


def test_execute_plan_wrong_code_fails(db):
    """错误短码的 execute_plan 返回 ok=False。"""
    from app.services.tool_adapters.approval_tools import (
        approval_prepare_plan,
        approval_execute_plan,
    )

    suffix = "execute-wrong"
    ctx = _ctx(approver_matrix_ids=["@alice:matrix.org"])
    prepared = approval_prepare_plan(_prepare_args(suffix), ctx=ctx, db=db)

    result = approval_execute_plan(
        args={
            "plan_id": prepared["plan_id"],
            "short_code": "00000000",
            "approver_matrix_id": "@alice:matrix.org",
            "room_id": _room(suffix),
            "approval_event_id": _event(f"approve-{suffix}"),
        },
        ctx=ctx,
        db=db,
    )
    assert result["ok"] is False
    assert "无效" in result["error"] or "过期" in result["error"]


def test_prepare_plan_returns_step_summary(db):
    """prepare_plan 响应暴露步骤摘要。"""
    from app.services.tool_adapters.approval_tools import approval_prepare_plan

    suffix = "prepare-summary"
    ctx = _ctx(approver_matrix_ids=["@alice:matrix.org"])
    result = approval_prepare_plan(_prepare_args(suffix), ctx=ctx, db=db)

    assert "steps" in result
    assert len(result["steps"]) == 2
    assert result["steps"][0]["step_key"] == "restart"
    assert result["steps"][0]["action_type"] == "SERVICE_CONTROL"
    assert result["steps"][0]["status"] == "PENDING"
