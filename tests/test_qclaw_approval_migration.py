"""Test qclaw approval migration: AiActionApproval extended columns."""
import sqlalchemy as sa
from sqlalchemy import create_engine, inspect

from app.db.base import Base
from app.db.migrations.runner import run_schema_migrations


REQUIRED_COLUMNS = {
    "action_digest",
    "approval_code_hash",
    "room_id",
    "request_event_id",
    "approval_event_id",
    "content_sha256",
    "routing_ticket_digest",
    "routing_config_revision",
    "expires_at",
    "consumed_at",
    "rejected_by",
    "rejected_at",
    "package_name",
    "package_sha256",
    "package_size_bytes",
    "execution_job_id",
    "execution_result",
    "failure_reason",
    "updated_at",
}


def test_ai_action_approval_has_qclaw_contract_columns():
    """迁移后 ai_action_approvals 必须包含所有 qclaw 扩展字段。"""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    run_schema_migrations(engine)

    columns = {
        item["name"]
        for item in inspect(engine).get_columns("ai_action_approvals")
    }
    assert REQUIRED_COLUMNS <= columns, (
        f"Missing columns: {REQUIRED_COLUMNS - columns}"
    )


def test_approval_lifecycle_statuses_accepted():
    """ORM 模型接受所有 qclaw 生命周期状态值。"""
    from app.db.models import AiActionApproval

    valid_statuses = {
        "PENDING_APPROVAL",
        "REJECTED",
        "EXPIRED",
        "STALE",
        "EXECUTING",
        "BLOCKED",
        "RUNNING",
        "SUCCEEDED",
        "FAILED",
    }
    # 确保所有状态可以被赋值
    for status in valid_statuses:
        approval = AiActionApproval(
            action_type="RELEASE",
            tool_name="ops.approval.prepare_release",
            status=status,
        )
        assert approval.status == status


def test_approval_indexes_exist():
    """迁移后必要的索引存在。"""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    run_schema_migrations(engine)

    insp = inspect(engine)
    indexes = {idx["name"] for idx in insp.get_indexes("ai_action_approvals")}
    expected = {
        "ix_ai_approval_action_digest",
        "ix_ai_approval_expires_at",
        "ix_ai_approval_job_id",
        "ix_ai_approval_room_event",
    }
    assert expected <= indexes, f"Missing indexes: {expected - indexes}"
