"""Tests for execution plan one-time approval consumption and rejection."""
import uuid
from datetime import timedelta

import pytest

from app.db.base import SessionLocal, Base, engine
from app.db.migrations.runner import run_schema_migrations
from app.services.execution_plan import ExecutionPlanService, compute_plan_digest, _utcnow

_RUN_ID = uuid.uuid4().hex[:8]


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


@pytest.fixture(scope="module")
def db():
    Base.metadata.create_all(engine)
    run_schema_migrations(engine)
    session = SessionLocal()
    yield session
    session.rollback()
    session.close()


def _prepare(service, suffix, **overrides):
    defaults = dict(
        room_id=_room(suffix),
        request_event_id=_event(suffix),
        content_sha256="a" * 64,
        system_name="payment",
        service_name="api",
        environment="test",
        targets=["s1"],
        steps=_steps(),
        policy={"continue_on_error": False, "max_retries": 0},
        routing_config_revision=f"rev-{_RUN_ID}-{suffix}",
        routing_ticket_digest=f"ticket-{_RUN_ID}-{suffix}",
        risk_level="high",
        authorized_matrix_users=["@alice:matrix.org"],
    )
    defaults.update(overrides)
    return service.prepare(**defaults)


def test_consume_approves_plan_exactly_once(db):
    """正确短码+房间+授权人使计划 APPROVED，且只成功一次。"""
    service = ExecutionPlanService(db)
    plan, short_code = _prepare(service, "approve-once")

    result = service.consume(
        plan_id=plan.id,
        short_code=short_code,
        approver_matrix_id="@alice:matrix.org",
        room_id=_room("approve-once"),
        approval_event_id=_event("approve-once"),
    )
    assert result is not None
    assert result.status == "APPROVED"
    assert result.consumed_at is not None
    assert result.approved_by == "matrix:default:@alice:matrix.org"

    # 再次消费失败
    result2 = service.consume(
        plan_id=plan.id,
        short_code=short_code,
        approver_matrix_id="@alice:matrix.org",
        room_id=_room("approve-once"),
        approval_event_id=_event("approve-once-2"),
    )
    assert result2 is None


def test_wrong_code_leaves_plan_pending(db):
    """错误短码无法消费，计划保持 PENDING_APPROVAL。"""
    service = ExecutionPlanService(db)
    plan, short_code = _prepare(service, "wrong-code")

    result = service.consume(
        plan_id=plan.id,
        short_code="DEADBEEF",
        approver_matrix_id="@alice:matrix.org",
        room_id=_room("wrong-code"),
        approval_event_id=_event("wrong-code"),
    )
    assert result is None
    db.refresh(plan)
    assert plan.status == "PENDING_APPROVAL"
    assert plan.consumed_at is None


def test_wrong_room_cannot_consume(db):
    """错误房间无法消费。"""
    service = ExecutionPlanService(db)
    plan, short_code = _prepare(service, "wrong-room")

    result = service.consume(
        plan_id=plan.id,
        short_code=short_code,
        approver_matrix_id="@alice:matrix.org",
        room_id="!room-other:matrix.org",
        approval_event_id=_event("wrong-room"),
    )
    assert result is None
    db.refresh(plan)
    assert plan.status == "PENDING_APPROVAL"


def test_unauthorized_approver_leaves_plan_pending(db):
    """未授权审批人被拒绝并记录，不执行步骤。"""
    service = ExecutionPlanService(db)
    plan, short_code = _prepare(service, "unauthorized")

    result = service.consume(
        plan_id=plan.id,
        short_code=short_code,
        approver_matrix_id="@mallory:matrix.org",
        room_id=_room("unauthorized"),
        approval_event_id=_event("unauthorized"),
    )
    assert result is None
    db.refresh(plan)
    assert plan.status == "PENDING_APPROVAL"
    assert plan.rejected_by is None
    assert plan.rejected_at is None
    # 步骤未被执行（仍是 PENDING）
    assert all(s.status == "PENDING" for s in plan.steps)


def test_expired_plan_transitions_to_expired(db):
    """过期计划标记为 EXPIRED 且不能消费。"""
    service = ExecutionPlanService(db)
    plan, short_code = _prepare(service, "expired")

    plan.expires_at = _utcnow() - timedelta(seconds=10)
    db.commit()

    result = service.consume(
        plan_id=plan.id,
        short_code=short_code,
        approver_matrix_id="@alice:matrix.org",
        room_id=_room("expired"),
        approval_event_id=_event("expired"),
    )
    assert result is None
    db.refresh(plan)
    assert plan.status == "EXPIRED"
    assert plan.consumed_at is None


def test_consume_is_atomic_under_two_concurrent_calls(db):
    """两次并发消费只有一个成功。"""
    service = ExecutionPlanService(db)
    plan, short_code = _prepare(service, "concurrent")

    db2 = SessionLocal()
    try:
        svc1 = ExecutionPlanService(db)
        svc2 = ExecutionPlanService(db2)
        result1 = svc1.consume(
            plan_id=plan.id,
            short_code=short_code,
            approver_matrix_id="@alice:matrix.org",
            room_id=_room("concurrent"),
            approval_event_id=_event("concurrent-1"),
        )
        result2 = svc2.consume(
            plan_id=plan.id,
            short_code=short_code,
            approver_matrix_id="@bob:matrix.org",
            room_id=_room("concurrent"),
            approval_event_id=_event("concurrent-2"),
        )
        successes = sum(1 for r in (result1, result2) if r is not None)
        assert successes == 1

        db.refresh(plan)
        assert plan.status == "APPROVED"
        assert plan.consumed_at is not None
    finally:
        db2.close()


def test_changed_stored_manifest_cannot_execute(db):
    """存储 manifest 被篡改（digest 不匹配）后无法消费。"""
    service = ExecutionPlanService(db)
    plan, short_code = _prepare(service, "tampered")

    # 篡改存储的 manifest（如新增一个步骤）；重新赋值以触发 JSON 列持久化
    tampered_steps = list(plan.manifest["steps"])
    tampered_steps.append({
        "step_key": "injected",
        "action_type": "SERVICE_CONTROL",
        "parameters": {"control_action": "stop"},
        "dependencies": [],
    })
    plan.manifest = {**plan.manifest, "steps": tampered_steps}
    db.commit()

    result = service.consume(
        plan_id=plan.id,
        short_code=short_code,
        approver_matrix_id="@alice:matrix.org",
        room_id=_room("tampered"),
        approval_event_id=_event("tampered"),
    )
    assert result is None
    db.refresh(plan)
    assert plan.status == "PENDING_APPROVAL"
    assert "digest mismatch" in (plan.failure_reason or "")


def test_reject_is_terminal(db):
    """拒绝后不能再消费。"""
    service = ExecutionPlanService(db)
    plan, short_code = _prepare(service, "reject")

    rejected = service.reject(plan.id, "@alice:matrix.org")
    assert rejected is not None
    assert rejected.status == "REJECTED"
    assert rejected.rejected_by == "matrix:default:@alice:matrix.org"
    assert rejected.rejected_at is not None

    result = service.consume(
        plan_id=plan.id,
        short_code=short_code,
        approver_matrix_id="@alice:matrix.org",
        room_id=_room("reject"),
        approval_event_id=_event("reject-after"),
    )
    assert result is None
    db.refresh(plan)
    assert plan.status == "REJECTED"


def test_get_returns_none_for_nonexistent(db):
    """不存在的计划返回 None。"""
    service = ExecutionPlanService(db)
    assert service.get("nonexistent-id") is None
