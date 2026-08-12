"""Plan-policy tests for temporary self-approval integration."""
import uuid
from unittest.mock import patch

import pytest

from app.db.base import SessionLocal, Base, engine
from app.db.migrations.runner import run_schema_migrations
from app.services.tool_registry import registry, register_builtin_tools
from app.services.tool_context import ToolContext
from app.db.models import System, SystemEnvironment, ExecutionPlan, TemporaryApprovalGrant
from app.services.message_context import MessageContext
from app.services.qclaw_routing import compute_routing_revision, issue_ticket
from app.services.tool_token import (
    resolve_approver_identities,
    resolve_channel_bindings,
)
from app.services.temporary_approval import TemporaryApprovalService

_RUN_ID = uuid.uuid4().hex[:8]

register_builtin_tools()


@pytest.fixture(autouse=True)
def _strong_signing_key(monkeypatch):
    monkeypatch.setattr(
        "app.services.qclaw_routing.QCLAW_APPROVAL_SIGNING_KEY",
        "temp-self-approval-plan-test-key-0123456789",
    )


@pytest.fixture(scope="module")
def db():
    Base.metadata.create_all(engine)
    run_schema_migrations(engine)
    session = SessionLocal()
    existing = session.query(System).filter(System.name == "crypto-trader").first()
    if existing is None:
        session.add(System(
            name="crypto-trader",
            display_name="Crypto Trader",
            message_routing={
                "approvers": [{
                    "channel": "matrix",
                    "channel_account_id": "default",
                    "sender_id": "@owner:matrix.org",
                }],
            },
        ))
        session.add_all([
            SystemEnvironment(system_name="crypto-trader", name="test", category="test"),
            SystemEnvironment(system_name="crypto-trader", name="prod", category="prod"),
        ])
        session.commit()
    yield session
    session.rollback()
    session.close()


def _room(suffix: str) -> str:
    return f"!room-{_RUN_ID}-{suffix}:matrix.org"


def _event(suffix: str) -> str:
    return f"$evt-{_RUN_ID}-{suffix}:matrix.org"


def _context(*, sender="@requester:matrix.org", conversation=None, message=None):
    return MessageContext(
        channel="matrix",
        channel_account_id="default",
        conversation_id=conversation or _room(f"ctx-{message or 'default'}"),
        message_id=message or _event("ctx"),
        sender_id=sender,
        content_sha256="a" * 64,
    )


def _ctx(approver_matrix_ids=None, bound_room_ids=None):
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


def _activate_grant(db, context) -> str:
    """通过服务层创建并确认一个 ACTIVE 临时审批授权，返回 grant id。"""
    from app.services.tool_adapters.approval_tools import _lookup_approvers

    approvers = _lookup_approvers(
        _ctx(approver_matrix_ids=["@owner:matrix.org"]),
        "crypto-trader",
        "api",
        channel=context.channel,
        channel_account_id=context.channel_account_id,
    )
    service = TemporaryApprovalService(db)
    grant, code = service.request(
        message_context=context,
        beneficiary_actor_key=context.actor_key,
        system_name="crypto-trader",
        environment_name="test",
        allowed_actions=["FILE_UPLOAD", "RELEASE", "SERVICE_CONTROL", "HEALTH_CHECK"],
        reason="urgent test integration",
        authorized_identities=[
            {"channel": context.channel, "channel_account_id": context.channel_account_id, "sender_id": approver}
            for approver in approvers
        ],
    )
    confirmation = MessageContext(
        channel=context.channel,
        channel_account_id=context.channel_account_id,
        conversation_id=context.conversation_id,
        message_id=_event("grant-confirm"),
        sender_id=approvers[0],
        content_sha256="a" * 64,
    )
    active = service.confirm(
        grant.id,
        code,
        actor_key=confirmation.actor_key,
        message_context=confirmation,
    )
    assert active is not None and active.status == "ACTIVE"
    return grant.id


def _prepare_args(suffix, context, *, with_grant: bool = False, **overrides):
    from app.services.tool_adapters.approval_tools import _routing_systems

    system_name = overrides.get("system_name", "crypto-trader")
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
        system_name=system_name,
        service_name=service_name,
        environment="test",
        targets=["s1"],
        steps=_steps(),
        policy={"continue_on_error": False, "max_retries": 0},
        risk_level="high",
        ai_reason="self-approval test",
    )
    defaults.update(overrides)
    return defaults


# ── 计划集成 ──

def test_active_grant_adds_self_approval_path_and_records_grant_id(db):
    """有效授权为受益人添加自审批路径并记录 temporary_grant_id。"""
    from app.services.tool_adapters.approval_tools import approval_prepare_plan

    context = _context(message="plan-self-approved")
    grant_id = _activate_grant(db, context)
    ctx = _ctx(approver_matrix_ids=["@owner:matrix.org"])

    result = approval_prepare_plan(
        _prepare_args("plan-self-approved", context, with_grant=True),
        ctx=ctx,
        db=db,
    )
    plan = db.query(ExecutionPlan).filter(ExecutionPlan.id == result["plan_id"]).one()
    assert plan.temporary_grant_id == grant_id
    assert plan.requested_by == context.actor_key
    # 原始审批人仍在授权身份中
    assert any(
        item.get("sender_id") == "@owner:matrix.org"
        for item in plan.authorized_identities or []
    )
    # 受益人（请求方）也在自审批路径中
    assert context.actor_key in {
        f"{item.get('channel')}:{item.get('channel_account_id')}:{item.get('sender_id')}"
        for item in plan.authorized_identities or []
    }


def test_requester_can_consume_with_active_grant(db):
    """有活跃授权时，请求方可以消费计划（自审批）。"""
    from app.services.tool_adapters.approval_tools import approval_prepare_plan
    from app.services.execution_plan import ExecutionPlanService

    context = _context(message="plan-consume-self")
    _activate_grant(db, context)
    ctx = _ctx(approver_matrix_ids=["@owner:matrix.org"])

    prepared = approval_prepare_plan(
        _prepare_args("plan-consume-self", context),
        ctx=ctx,
        db=db,
    )
    service = ExecutionPlanService(db)
    consumed = service.consume(
        plan_id=prepared["plan_id"],
        short_code=prepared["short_code"],
        approver_matrix_id=context.sender_id,
        room_id=context.conversation_id,
        approval_event_id=_event("consume-self"),
        approval_context=context,
    )
    assert consumed is not None
    assert consumed.status == "APPROVED"
    assert consumed.approved_by == context.actor_key


def test_grant_expiry_prevents_beneficiary_consumption(db):
    """授权过期后，受益人不能再消费计划。"""
    from app.services.tool_adapters.approval_tools import approval_prepare_plan
    from app.services.execution_plan import ExecutionPlanService

    context = _context(message="plan-expired-grant")
    grant_id = _activate_grant(db, context)
    ctx = _ctx(approver_matrix_ids=["@owner:matrix.org"])

    prepared = approval_prepare_plan(
        _prepare_args("plan-expired-grant", context),
        ctx=ctx,
        db=db,
    )

    # 手动使授权过期
    grant = db.query(TemporaryApprovalGrant).filter(TemporaryApprovalGrant.id == grant_id).one()
    from datetime import datetime, timedelta, timezone
    grant.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()

    service = ExecutionPlanService(db)
    consumed = service.consume(
        plan_id=prepared["plan_id"],
        short_code=prepared["short_code"],
        approver_matrix_id=context.sender_id,
        room_id=context.conversation_id,
        approval_event_id=_event("consume-expired"),
        approval_context=context,
    )
    assert consumed is None
    plan = db.query(ExecutionPlan).filter(ExecutionPlan.id == prepared["plan_id"]).one()
    assert plan.status == "PENDING_APPROVAL"


def test_original_approver_can_still_consume_after_revocation(db):
    """授权撤销后，原始审批人仍可消费已有计划。"""
    from app.services.tool_adapters.approval_tools import approval_prepare_plan
    from app.services.execution_plan import ExecutionPlanService

    context = _context(message="plan-owner-consume")
    grant_id = _activate_grant(db, context)
    ctx = _ctx(approver_matrix_ids=["@owner:matrix.org"])

    prepared = approval_prepare_plan(
        _prepare_args("plan-owner-consume", context),
        ctx=ctx,
        db=db,
    )

    # 撤销授权
    grant = db.query(TemporaryApprovalGrant).filter(TemporaryApprovalGrant.id == grant_id).one()
    grant.status = "REVOKED"
    grant.active_scope_key = None
    db.commit()

    # 原始审批人仍然可以消费
    owner_context = MessageContext(
        channel=context.channel,
        channel_account_id=context.channel_account_id,
        conversation_id=context.conversation_id,
        message_id=_event("owner-consume"),
        sender_id="@owner:matrix.org",
        content_sha256="a" * 64,
    )
    service = ExecutionPlanService(db)
    consumed = service.consume(
        plan_id=prepared["plan_id"],
        short_code=prepared["short_code"],
        approver_matrix_id="@owner:matrix.org",
        room_id=context.conversation_id,
        approval_event_id=_event("owner-consume"),
        approval_context=owner_context,
    )
    assert consumed is not None
    assert consumed.status == "APPROVED"
    assert consumed.approved_by == owner_context.actor_key


def test_changing_step_or_package_hash_invalidates_digest(db):
    """改变步骤或包哈希使计划摘要失效，受益人无法消费。"""
    from app.services.tool_adapters.approval_tools import approval_prepare_plan
    from app.services.execution_plan import ExecutionPlanService

    context = _context(message="plan-tamper")
    _activate_grant(db, context)
    ctx = _ctx(approver_matrix_ids=["@owner:matrix.org"])

    prepared = approval_prepare_plan(
        _prepare_args("plan-tamper", context),
        ctx=ctx,
        db=db,
    )
    plan = db.query(ExecutionPlan).filter(ExecutionPlan.id == prepared["plan_id"]).one()
    import copy
    manifest = copy.deepcopy(plan.manifest or {})
    steps = list(manifest.get("steps", []))
    steps[0]["parameters"] = dict(steps[0]["parameters"], control_action="stop")
    manifest["steps"] = steps
    plan.manifest = manifest
    db.commit()

    service = ExecutionPlanService(db)
    consumed = service.consume(
        plan_id=prepared["plan_id"],
        short_code=prepared["short_code"],
        approver_matrix_id=context.sender_id,
        room_id=context.conversation_id,
        approval_event_id=_event("consume-tamper"),
        approval_context=context,
    )
    assert consumed is None
    plan = db.query(ExecutionPlan).filter(ExecutionPlan.id == prepared["plan_id"]).one()
    assert plan.status == "PENDING_APPROVAL"
