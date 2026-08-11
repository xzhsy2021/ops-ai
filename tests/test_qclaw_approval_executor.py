"""测试 qclaw 审批执行器。"""
import pytest
from app.db.base import Base, SessionLocal, engine
from app.db.migrations.runner import run_schema_migrations
from app.services.action_approval import ActionApprovalService
from app.services.approval_executor import ApprovalExecutor


@pytest.fixture(scope="module")
def db():
    Base.metadata.create_all(engine)
    run_schema_migrations(engine)
    session = SessionLocal()
    yield session
    session.rollback()
    session.close()


def _prepare_approval(db, action_type="RELEASE", **kwargs):
    """创建一个已审批（EXECUTING 状态）的工单。"""
    service = ActionApprovalService(db)
    defaults = dict(
        action_type=action_type,
        tool_name=f"ops.approval.prepare_{action_type.lower()}",
        room_id="!test:example.com",
        request_event_id="$evt:example.com",
        content_sha256="abc123",
        system_name="test-system",
        service_name=None,
        environment="test",
        targets=["server-1"],
        action_parameters={},
        routing_config_revision="rev1",
        routing_ticket_digest="ticket123",
    )
    defaults.update(kwargs)
    approval, short_code = service.prepare(**defaults)
    # 消费审批码使其进入 EXECUTING
    consumed = service.consume(
        approval_id=approval.id,
        short_code=short_code,
        approver_matrix_id="@admin:example.com",
        room_id="!test:example.com",
        approval_event_id="$approval:example.com",
    )
    return consumed


def test_executor_returns_none_for_nonexistent(db):
    """不存在的审批 ID 返回 None。"""
    executor = ApprovalExecutor(db)
    result = executor.execute("nonexistent-id")
    assert result is None


def test_executor_returns_existing_if_not_executing(db):
    """非 EXECUTING 状态的工单直接返回。"""
    import uuid
    from app.db.models import AiActionApproval

    approval = AiActionApproval(
        id=str(uuid.uuid4()),
        action_type="RELEASE",
        tool_name="test",
        status="PENDING_APPROVAL",
    )
    db.add(approval)
    db.commit()

    executor = ApprovalExecutor(db)
    result = executor.execute(approval.id)
    assert result is not None
    assert result.status == "PENDING_APPROVAL"


def test_executor_rejects_unknown_action_type(db):
    """未知操作类型标记为 FAILED。"""
    approval = _prepare_approval(db, action_type="UNKNOWN")
    # 直接修改 action_type 为未知类型
    approval.action_type = "UNKNOWN"
    db.commit()

    executor = ApprovalExecutor(db)
    result = executor.execute(approval.id)
    assert result.status == "FAILED"
    assert "未知" in (result.failure_reason or "")


def test_executor_creates_operation_job(db):
    """执行器创建 OperationJob 记录。"""
    from unittest.mock import patch
    from app.db.models import OperationJob

    approval = _prepare_approval(db, action_type="RELEASE")
    executor = ApprovalExecutor(db)
    with patch.object(executor, "_execute_release", return_value={"action": "RELEASE", "ok": True}):
        result = executor.execute(approval.id)

    # 查找关联的 Job（source 已通用化为 "approval"，见 approval_executor.py）
    job = db.query(OperationJob).filter(
        OperationJob.source == "approval"
    ).order_by(OperationJob.created_at.desc()).first()
    assert job is not None
    assert job.source == "approval"
    assert "RELEASE" in job.title


def test_executor_marks_succeeded_or_failed(db):
    """执行后工单状态为 SUCCEEDED 或 FAILED。"""
    from unittest.mock import patch

    approval = _prepare_approval(db, action_type="RELEASE")
    executor = ApprovalExecutor(db)
    with patch.object(executor, "_execute_release", return_value={"action": "RELEASE", "ok": True}):
        result = executor.execute(approval.id)

    assert result.status in ("SUCCEEDED", "FAILED")
    assert result.executed_at is not None
    if result.status == "SUCCEEDED":
        assert result.execution_result is not None
    else:
        assert result.failure_reason is not None


def test_executor_marks_file_upload_failed_and_preserves_target_results(db, monkeypatch):
    from app.services.tool_adapters import file_transfer_tools

    monkeypatch.setattr(
        file_transfer_tools,
        "upload_file",
        lambda args, ctx, db: (_ for _ in ()).throw(RuntimeError("simulated sftp failure")),
    )
    approval = _prepare_approval(
        db,
        action_type="FILE_UPLOAD",
        action_parameters={
            "package_name": "frontend.tar.gz",
            "remote_path": "/srv/releases/frontend.tar.gz",
        },
    )

    result = ApprovalExecutor(db).execute(approval.id)

    assert result.status == "FAILED"
    assert result.execution_result["action"] == "FILE_UPLOAD"
    assert result.execution_result["fail_count"] == 1
    assert result.failure_reason
