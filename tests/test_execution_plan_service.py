"""Tests for immutable execution plan manifest and idempotent prepare()."""
import uuid

import pytest

from app.db.base import SessionLocal, Base, engine
from app.db.migrations.runner import run_schema_migrations
from app.services.execution_plan import (
    ExecutionPlanService,
    compute_plan_digest,
    _canonical_json,
)

_RUN_ID = uuid.uuid4().hex[:8]


def _room(suffix: str) -> str:
    return f"!room-{_RUN_ID}-{suffix}:matrix.org"


def _event(suffix: str) -> str:
    return f"$evt-{_RUN_ID}-{suffix}:matrix.org"


def _steps(suffix: str = "") -> list[dict]:
    return [
        {
            "step_key": "restart",
            "action_type": "SERVICE_CONTROL",
            "parameters": {
                "control_action": "restart",
                "system_name": "payment",
                "service_name": "api",
                "targets": ["s1", "s2"],
            },
            "dependencies": [],
        },
        {
            "step_key": "health",
            "action_type": "HEALTH_CHECK",
            "parameters": {"system_name": "payment", "service_name": "api", "targets": ["s1", "s2"]},
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
    """辅助函数：创建执行计划，避免跨测试运行冲突。"""
    defaults = dict(
        room_id=_room(suffix),
        request_event_id=_event(suffix),
        content_sha256="a" * 64,
        system_name="payment",
        service_name="api",
        environment="test",
        targets=["s1", "s2"],
        steps=_steps(suffix),
        policy={"max_retries": 0, "continue_on_error": False},
        routing_config_revision=f"rev-{_RUN_ID}-{suffix}",
        routing_ticket_digest=f"ticket-{_RUN_ID}-{suffix}",
        risk_level="high",
        ai_reason="test",
        authorized_matrix_users=["@alice:matrix.org"],
    )
    defaults.update(overrides)
    return service.prepare(**defaults)


# ── digest 规范化 ──

def test_canonical_ordering_produces_same_digest():
    """等价目标/步骤顺序产生相同 digest（排序 + 紧凑 JSON）。"""
    base = dict(
        room_id=_room("digest"),
        request_event_id=_event("digest"),
        content_sha256="c" * 64,
        system_name="payment",
        service_name="api",
        environment="test",
        targets=["s1", "s2"],
        steps=_steps(),
        policy={"continue_on_error": False, "max_retries": 0},
        routing_config_revision="rev-digest-1",
        routing_ticket_digest="ticket-digest-1",
    )
    d1 = compute_plan_digest(**base)

    # targets 顺序不影响 digest
    reordered = dict(base)
    reordered["targets"] = ["s2", "s1"]
    assert compute_plan_digest(**reordered) == d1

    # policy 键顺序不影响 digest
    reordered["policy"] = {"max_retries": 0, "continue_on_error": False}
    assert compute_plan_digest(**reordered) == d1


def test_changed_manifest_changes_digest():
    """环境/目标/步骤参数/步骤顺序/路由 revision 变化都改变 digest。"""
    base = dict(
        room_id=_room("digest2"),
        request_event_id=_event("digest2"),
        content_sha256="d" * 64,
        system_name="payment",
        service_name="api",
        environment="test",
        targets=["s1", "s2"],
        steps=_steps(),
        policy={"continue_on_error": False, "max_retries": 0},
        routing_config_revision="rev-digest-2",
        routing_ticket_digest="ticket-digest-2",
    )
    d1 = compute_plan_digest(**base)

    cases = {
        "environment": dict(environment="prod"),
        "targets": dict(targets=["s1", "s2", "s3"]),
        "routing_revision": dict(routing_config_revision="rev-other"),
        "content": dict(content_sha256="e" * 64),
    }
    for name, change in cases.items():
        modified = dict(base)
        modified.update(change)
        assert compute_plan_digest(**modified) != d1, f"{name} 变化未改变 digest"

    # 步骤参数变化
    steps2 = _steps()
    steps2[0]["parameters"]["control_action"] = "stop"
    modified = dict(base)
    modified["steps"] = steps2
    assert compute_plan_digest(**modified) != d1

    # 步骤顺序变化
    steps3 = list(reversed(_steps()))
    modified = dict(base)
    modified["steps"] = steps3
    assert compute_plan_digest(**modified) != d1


def test_targets_are_sorted_in_canonical_json():
    """_canonical_json 对嵌套 targets 排序（规范化仅顶层排序，嵌套保持不变）。"""
    manifest = {"targets": ["b", "a"], "system_name": "x"}
    canonical = _canonical_json(manifest)
    assert canonical == '{"system_name":"x","targets":["b","a"]}'


# ── prepare 生命周期 ──

def test_prepare_creates_pending_plan_with_short_code(db):
    """prepare 创建 PENDING_APPROVAL 计划并返回一次性明文短码。"""
    service = ExecutionPlanService(db)
    plan, short_code = _prepare(service, "basic")

    assert plan.status == "PENDING_APPROVAL"
    assert len(short_code) == 8
    assert short_code == short_code.upper()
    assert plan.plan_digest and len(plan.plan_digest) == 64
    # 数据库只存哈希
    assert plan.approval_code_hash.startswith("pbkdf2_sha256$")
    assert short_code not in plan.approval_code_hash
    # 过期时间
    assert plan.expires_at is not None
    # 授权人快照
    assert plan.authorized_matrix_users == ["@alice:matrix.org"]
    # 完整步骤快照
    assert len(plan.steps) == 2
    keys = [s.step_key for s in plan.steps]
    assert keys == ["restart", "health"]
    frozen = plan.steps[0]
    assert frozen.parameters["control_action"] == "restart"
    assert frozen.status == "PENDING"
    assert frozen.attempt_count == 0


def test_prepare_is_idempotent_for_same_digest(db):
    """相同 digest 的重复 prepare 返回已有计划且不生成新短码。"""
    service = ExecutionPlanService(db)
    plan1, code1 = _prepare(service, "idempotent")
    plan2, code2 = _prepare(service, "idempotent")

    assert plan1.id == plan2.id
    assert code1 != ""
    assert code2 == ""
    assert plan2.status == "PENDING_APPROVAL"


def test_terminal_plan_is_not_reused(db):
    """已终态（如已拒绝）的计划不被重复 prepare 复用。"""
    service = ExecutionPlanService(db)
    plan, short_code = _prepare(service, "terminal")

    # 模拟计划进入终态
    plan.status = "REJECTED"
    db.commit()

    # 再次 prepare 相同 digest：原计划已终态，应创建新计划
    plan2, code2 = _prepare(service, "terminal")
    assert plan2.id != plan.id
    assert code2 != ""
    assert plan2.status == "PENDING_APPROVAL"


def test_prepare_validates_duplicate_step_keys(db):
    """重复 step_key 必须被拒绝。"""
    from app.services.execution_plan import PlanValidationError

    service = ExecutionPlanService(db)
    steps = _steps()
    steps.append(dict(steps[0]))  # 复制 step_key
    with pytest.raises(PlanValidationError):
        service.prepare(
            room_id=_room("dup"),
            request_event_id=_event("dup"),
            content_sha256="f" * 64,
            system_name="payment",
            service_name="api",
            environment="test",
            targets=["s1"],
            steps=steps,
            policy={},
            routing_config_revision="rev-dup",
            routing_ticket_digest="ticket-dup",
        )


def test_prepare_validates_dependency_references(db):
    """引用不存在步骤的依赖必须被拒绝。"""
    from app.services.execution_plan import PlanValidationError

    service = ExecutionPlanService(db)
    steps = _steps()
    steps[1]["dependencies"] = ["nonexistent"]
    with pytest.raises(PlanValidationError):
        service.prepare(
            room_id=_room("dep"),
            request_event_id=_event("dep"),
            content_sha256="g" * 64,
            system_name="payment",
            service_name="api",
            environment="test",
            targets=["s1"],
            steps=steps,
            policy={},
            routing_config_revision="rev-dep",
            routing_ticket_digest="ticket-dep",
        )
