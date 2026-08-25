"""Tests for qclaw Element approval lifecycle service."""
import uuid
from datetime import timedelta

import pytest

from app.db.base import SessionLocal, Base, engine
from app.db.migrations.runner import run_schema_migrations
from app.db.models import AiActionApproval
from app.services.action_approval import (
    ActionApprovalService,
    compute_action_digest,
    _utcnow,
)


# 每次测试运行使用唯一前缀，避免与历史数据冲突（数据库为持久化 SQLite 文件）
_RUN_ID = uuid.uuid4().hex[:8]


def _room(suffix: str) -> str:
    return f"!room-{_RUN_ID}-{suffix}:matrix.org"


def _event(suffix: str) -> str:
    return f"$evt-{_RUN_ID}-{suffix}:matrix.org"


@pytest.fixture(scope="module")
def db():
    # 确保表存在并应用 qclaw 扩展列迁移（幂等，对已有 DB 安全）
    Base.metadata.create_all(engine)
    run_schema_migrations(engine)
    session = SessionLocal()
    yield session
    session.rollback()
    session.close()


def _prepare(service, suffix, **overrides):
    """辅助函数：用统一前缀创建审批工单，避免跨测试运行冲突。"""
    defaults = dict(
        action_type="RELEASE",
        tool_name="ops.deploy",
        room_id=_room(suffix),
        request_event_id=_event(suffix),
        content_sha256="a" * 64,
        system_name="payment",
        service_name="api",
        environment="prod",
        targets=["s1", "s2"],
        action_parameters={"version": "1.0.0"},
        routing_config_revision=f"rev-{_RUN_ID}-{suffix}",
        routing_ticket_digest=f"ticket-{_RUN_ID}-{suffix}",
    )
    defaults.update(overrides)
    return service.prepare(**defaults)


def test_prepare_generates_one_time_short_code_and_hash_only(db):
    """prepare 返回明文确认短语，数据库只存哈希"""
    service = ActionApprovalService(db)
    approval, short_code = _prepare(service, "hash-only")

    # 描述性确认短语：批准<动作> <system>@<env> <指纹8位大写十六进制>
    assert short_code.startswith("批准")
    parts = short_code.split(" ")
    assert len(parts) == 3
    fingerprint = parts[2]
    assert len(fingerprint) == 8
    assert fingerprint == fingerprint.upper()
    # 数据库中只存哈希，不存明文
    assert approval.approval_code_hash is not None
    assert short_code not in approval.approval_code_hash
    assert approval.approval_code_hash.startswith("pbkdf2_sha256$")
    # 状态正确
    assert approval.status == "PENDING_APPROVAL"
    assert approval.action_digest is not None
    assert len(approval.action_digest) == 64
    # 过期时间已设置
    assert approval.expires_at is not None


def test_prepare_is_idempotent_for_same_action_digest(db):
    """相同 action_digest 的 prepare 返回已有工单"""
    service = ActionApprovalService(db)
    approval1, code1 = _prepare(service, "idempotent")
    approval2, code2 = _prepare(service, "idempotent")

    # 返回相同的工单
    assert approval1.id == approval2.id
    # 第二次不返回明文短码（幂等）
    assert code2 == ""
    # 第一次返回了明文短码
    assert code1 != ""


def test_changed_manifest_creates_different_action_digest():
    """不同参数产生不同 digest"""
    base = dict(
        action_type="RELEASE",
        room_id=_room("digest"),
        request_event_id=_event("digest"),
        content_sha256="c" * 64,
        system_name="payment",
        service_name="api",
        environment="prod",
        targets=["s1", "s2"],
        action_parameters={"version": "1.0.0"},
        routing_config_revision="rev-digest-1",
    )
    digest1 = compute_action_digest(**base)

    # 修改 content_sha256，digest 应该不同
    modified = dict(base)
    modified["content_sha256"] = "d" * 64
    digest2 = compute_action_digest(**modified)
    assert digest1 != digest2

    # targets 顺序不影响 digest（因为是 sorted）
    reordered = dict(base)
    reordered["targets"] = ["s2", "s1"]
    digest3 = compute_action_digest(**reordered)
    assert digest1 == digest3


def test_unauthorized_matrix_user_cannot_consume(db):
    """错误的审批码无法消费"""
    service = ActionApprovalService(db)
    approval, _ = _prepare(service, "wrong-code")

    # 错误的审批码
    result = service.consume(
        approval_id=approval.id,
        short_code="DEADBEEF",  # 错误的码
        approver_matrix_id="@alice:matrix.org",
        room_id=_room("wrong-code"),
        approval_event_id=_event("approve-wrong-code"),
    )
    assert result is None

    # 状态仍然是 PENDING_APPROVAL
    db.refresh(approval)
    assert approval.status == "PENDING_APPROVAL"
    assert approval.consumed_at is None


def test_wrong_room_cannot_consume(db):
    """错误的 room_id 无法消费"""
    service = ActionApprovalService(db)
    approval, short_code = _prepare(service, "wrong-room")

    # 正确的短码，错误的房间
    result = service.consume(
        approval_id=approval.id,
        short_code=short_code,
        approver_matrix_id="@alice:matrix.org",
        room_id="!room-wrong:matrix.org",  # 错误的房间
        approval_event_id=_event("approve-wrong-room"),
    )
    assert result is None

    db.refresh(approval)
    assert approval.status == "PENDING_APPROVAL"


def test_expired_approval_transitions_to_expired(db):
    """过期的工单标记为 EXPIRED"""
    service = ActionApprovalService(db)
    approval, short_code = _prepare(service, "expired")

    # 手动设置过期时间为过去
    approval.expires_at = _utcnow() - timedelta(seconds=10)
    db.commit()

    # 尝试消费，应该返回 None 并将状态置为 EXPIRED
    result = service.consume(
        approval_id=approval.id,
        short_code=short_code,
        approver_matrix_id="@alice:matrix.org",
        room_id=_room("expired"),
        approval_event_id=_event("approve-expired"),
    )
    assert result is None

    db.refresh(approval)
    assert approval.status == "EXPIRED"
    assert approval.consumed_at is None


def test_consume_is_atomic_under_two_concurrent_calls(db):
    """两次消费只有一个成功"""
    service = ActionApprovalService(db)
    approval, short_code = _prepare(service, "concurrent")

    # 使用两个独立 session 模拟并发
    db2 = SessionLocal()
    try:
        svc1 = ActionApprovalService(db)
        svc2 = ActionApprovalService(db2)

        result1 = svc1.consume(
            approval_id=approval.id,
            short_code=short_code,
            approver_matrix_id="@alice:matrix.org",
            room_id=_room("concurrent"),
            approval_event_id=_event("approve-1"),
        )
        result2 = svc2.consume(
            approval_id=approval.id,
            short_code=short_code,
            approver_matrix_id="@bob:matrix.org",
            room_id=_room("concurrent"),
            approval_event_id=_event("approve-2"),
        )

        # 只有一个成功
        successes = sum(1 for r in (result1, result2) if r is not None)
        assert successes == 1

        # 验证最终状态
        db.refresh(approval)
        assert approval.status == "EXECUTING"
        assert approval.consumed_at is not None
    finally:
        db2.close()


def test_reject_is_terminal(db):
    """拒绝后不能再消费"""
    service = ActionApprovalService(db)
    approval, short_code = _prepare(service, "reject")

    # 拒绝
    rejected = service.reject(approval.id, "@carol:matrix.org")
    assert rejected is not None
    assert rejected.status == "REJECTED"
    assert rejected.rejected_by == "matrix:default:@carol:matrix.org"
    assert rejected.rejected_at is not None

    # 尝试消费，应该失败
    result = service.consume(
        approval_id=approval.id,
        short_code=short_code,
        approver_matrix_id="@alice:matrix.org",
        room_id=_room("reject"),
        approval_event_id=_event("approve-after-reject"),
    )
    assert result is None

    # 状态仍然是 REJECTED
    db.refresh(approval)
    assert approval.status == "REJECTED"
    assert approval.consumed_at is None
