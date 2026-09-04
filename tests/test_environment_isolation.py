"""环境隔离硬闸契约测试（2026-09-04 设计：targets ⊆ 环境权威清单）。

覆盖：
- 越界拒绝：test 计划引用 prod 服务器 → EnvironmentTargetViolation
- 合法通过：targets ⊆ 环境清单
- fail-closed：环境未绑服务器（空清单）→ 一律拒绝
- fail-closed：缺 environment 标签 → 拒绝
- execute 相位复核：审批通过后环境绑定收紧 → 计划 REJECTED
- prepare 相位：service 链路创建越界计划直接抛
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.services.execution_plan import (  # noqa: E402
    EnvironmentTargetViolation,
    validate_targets_in_environment,
)


@pytest.fixture()
def env(tmp_path):
    from app.db.models import Base

    engine = create_engine(
        f"sqlite:///{tmp_path / 'env_isolation.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    db = Session()
    yield db
    db.close()
    engine.dispose()


def _seed_env(db, system="t-sys", env="test", server_ids=("t-host-1", "t-host-2")):
    from app.db.models import System, SystemEnvironment

    if not db.query(System).filter(System.name == system).first():
        db.add(System(name=system, display_name=system))
        db.flush()
    row = db.query(SystemEnvironment).filter(
        SystemEnvironment.system_name == system,
        SystemEnvironment.name == env,
    ).first()
    if row is None:
        row = SystemEnvironment(system_name=system, name=env, category=env)
        db.add(row)
    row.servers = [{"id": sid} for sid in server_ids]
    db.commit()
    return row


def test_oversized_targets_rejected(env):
    db = env
    _seed_env(db)
    with pytest.raises(EnvironmentTargetViolation) as ei:
        validate_targets_in_environment(db, "t-sys", "test", ["t-host-1", "prod-host-9"])
    assert "prod-host-9" in str(ei.value)
    assert "t-sys@test" in str(ei.value)


def test_in_environment_targets_pass(env):
    db = env
    _seed_env(db)
    validate_targets_in_environment(db, "t-sys", "test", ["t-host-1", "t-host-2"])  # 不抛
    validate_targets_in_environment(db, "t-sys", "test", [])  # 空 targets 合法


def test_empty_binding_fails_closed(env):
    db = env
    _seed_env(db, env="staging", server_ids=())  # 环境存在但未绑服务器
    with pytest.raises(EnvironmentTargetViolation) as ei:
        validate_targets_in_environment(db, "t-sys", "staging", ["t-host-1"])
    assert "fail-closed" in str(ei.value)


def test_missing_environment_label_fails_closed(env):
    db = env
    with pytest.raises(EnvironmentTargetViolation) as ei:
        validate_targets_in_environment(db, "t-sys", "", ["t-host-1"])
    assert "缺少 environment" in str(ei.value)


def test_unknown_environment_fails_closed(env):
    db = env
    with pytest.raises(EnvironmentTargetViolation):
        validate_targets_in_environment(db, "t-sys", "no-such-env", ["t-host-1"])


def test_prepare_link_rejects_oversized_plan(env):
    """闸2 集成：prepare_plan 越界即抛（不落库）。"""
    from app.services.execution_plan import ExecutionPlanService
    from app.services.tool_context import ToolContext
    from app.services.message_context import MessageContext

    db = env
    _seed_env(db)
    ctx = MessageContext(
        channel="matrix", channel_account_id="default", conversation_id="!r:x",
        message_id="$m1", sender_id="@approver:x", content_sha256="0" * 64,
    )
    service = ExecutionPlanService(db)
    with pytest.raises(EnvironmentTargetViolation):
        service.prepare(
            room_id="!r:x",
            request_event_id="$m1",
            content_sha256="0" * 64,
            system_name="t-sys",
            service_name=None,
            environment="test",
            targets=["prod-host-9"],  # 越界
            steps=[{"step_key": "step-1", "action_type": "SERVICE_CONTROL",
                    "parameters": {"action_parameters": {"control_action": "restart"}}}],
            routing_config_revision="r1",
            routing_ticket_digest="d1",
            message_context=ctx,
            authorized_identities=[
                {"channel": "matrix", "channel_account_id": "default", "sender_id": "@approver:x"}
            ],
        )
    # 越界计划不落库
    from app.db.models import ExecutionPlan
    assert db.query(ExecutionPlan).filter(ExecutionPlan.system_name == "t-sys").count() == 0


def test_execute_phase_recheck_rejects_after_binding_shrinks(env):
    """闸3 集成：审批通过后环境清单收紧 → consume 时计划 REJECTED。

    场景：plan 创建时 t-host-1/t-host-2 都合法 → PENDING；
    审批窗口期内 staging 清单移除 t-host-2 → consume（批准短语验证通过）
    时闸3 复核失败 → 计划 REJECTED 不执行。
    """
    from app.db.models import ExecutionPlan, SystemEnvironment
    from app.services.execution_plan import ExecutionPlanService
    from app.services.message_context import MessageContext

    db = env
    _seed_env(db)
    ctx = MessageContext(
        channel="matrix", channel_account_id="default", conversation_id="!r:x",
        message_id="$m1", sender_id="@approver:x", content_sha256="0" * 64,
    )
    service = ExecutionPlanService(db)
    plan, short_code = service.prepare(
        room_id="!r:x",
        request_event_id="$m1",
        content_sha256="0" * 64,
        system_name="t-sys",
        service_name=None,
        environment="test",
        targets=["t-host-2"],
        steps=[{"step_key": "step-1", "action_type": "SERVICE_CONTROL",
                "parameters": {"action_parameters": {"control_action": "restart"}}}],
        routing_config_revision="r1",
        routing_ticket_digest="d1",
        message_context=ctx,
        authorized_identities=[
            {"channel": "matrix", "channel_account_id": "default", "sender_id": "@approver:x"}
        ],
    )
    assert plan.status == "PENDING_APPROVAL"

    # 审批窗口期内：环境清单收紧（t-host-2 移除）
    row = db.query(SystemEnvironment).filter(
        SystemEnvironment.system_name == "t-sys",
        SystemEnvironment.name == "test",
    ).first()
    row.servers = [{"id": "t-host-1"}]
    db.commit()

    # 审批人批准（短语正确）→ 但闸3 复核失败 → REJECTED
    consumed = service.consume(
        plan_id=plan.id,
        short_code=short_code,
        room_id="!r:x",
        approver_matrix_id="@approver:x",
        approval_context=MessageContext(
            channel="matrix", channel_account_id="default", conversation_id="!r:x",
            message_id="$m2", sender_id="@approver:x", content_sha256="0" * 64,
        ),
    )
    assert consumed is None
    db.refresh(plan)
    assert plan.status == "REJECTED"
    assert "environment isolation" in (plan.failure_reason or "")
