"""qclaw Element Approval 端到端契约测试。

验证从消息路由 → 审批 prepare → 消费短码 → 执行器执行 的完整链路，
以及幂等性、过期、拒绝、并发消费等边界场景。

设计参考: docs/plans/2026-07-22-qclaw-element-approval-ops-design.md
集成文档: docs/qclaw-element-approval-integration.md
"""
import hashlib
import uuid
from datetime import timedelta
from unittest.mock import patch

import pytest

from app.db.base import SessionLocal, Base, engine
from app.db.migrations.runner import run_schema_migrations
from app.db.models import AiActionApproval, OperationJob
from app.services.action_approval import ActionApprovalService, _utcnow
from app.services.approval_executor import ApprovalExecutor
from app.services.qclaw_routing import (
    RoutingOutcome,
    resolve_message_target,
    issue_ticket,
    verify_ticket,
    compute_routing_revision,
)


# ──────────────────────────────────────────────────────────────
# 测试 fixtures
# ──────────────────────────────────────────────────────────────

_RUN_ID = uuid.uuid4().hex[:8]


def _room(suffix: str) -> str:
    return f"!e2e-{_RUN_ID}-{suffix}:matrix.org"


def _event(suffix: str) -> str:
    return f"$evt-{_RUN_ID}-{suffix}:matrix.org"


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# 路由配置示例（与设计文档一致）
ROUTING_SYSTEMS = [
    {
        "name": "crypto-trader",
        "message_routing": {
            "enabled": True,
            "aliases": ["量化", "量化交易"],
            "keywords": ["btc strategy", "crypto deploy"],
            "priority": 100,
        },
        "services": [
            {
                "name": "trader-api",
                "template_variables": {
                    "message_routing": {
                        "enabled": True,
                        "aliases": ["交易接口"],
                        "keywords": ["trader api"],
                        "priority": 80,
                    }
                },
            }
        ],
    },
    {
        "name": "payment",
        "message_routing": {
            "enabled": True,
            "aliases": ["支付"],
            "keywords": ["payment deploy"],
            "priority": 90,
        },
        "services": [],
    },
    {
        "name": "disabled-system",
        "message_routing": {
            "enabled": False,
            "aliases": ["should-not-match"],
            "keywords": ["disabled-keyword"],
            "priority": 200,
        },
        "services": [],
    },
]


@pytest.fixture(scope="module")
def db():
    """初始化数据库并应用迁移。"""
    Base.metadata.create_all(engine)
    run_schema_migrations(engine)
    session = SessionLocal()
    yield session
    session.rollback()
    session.close()


# ──────────────────────────────────────────────────────────────
# 端到端流程测试
# ──────────────────────────────────────────────────────────────


class TestEndToEndReleaseFlow:
    """端到端发布审批流程：路由 → prepare → consume → execute"""

    def test_full_release_flow_with_mocked_deploy(self, db):
        """完整发布流程，部署部分 mock 处理。

        验证点:
        1. 路由解析返回 RESOLVED + ticket
        2. ticket 验证通过
        3. prepare 返回 approval_id 和 short_code
        4. consume 成功，状态变为 EXECUTING
        5. executor 创建 OperationJob 并标记 SUCCEEDED
        """
        room_id = _room("full-flow")
        request_event = _event("full-flow")
        # 使用关键词 "crypto deploy" 触发匹配（aliases 是精确匹配，关键词是包含匹配）
        message_text = "@qclaw-bot crypto deploy 量化 release-1.2.3.tar.gz 到 prod"
        content_sha = _content_hash(message_text)

        # Step 1: 路由解析
        decision = resolve_message_target(message_text, ROUTING_SYSTEMS)
        assert decision.outcome == RoutingOutcome.RESOLVED
        assert decision.system_name == "crypto-trader"
        assert decision.routing_config_revision is not None

        # Step 2: 签发并验证路由票据
        ticket = issue_ticket(
            room_id=room_id,
            event_id=request_event,
            content_sha256=content_sha,
            system_name=decision.system_name,
            service_name=decision.service_name,
            routing_config_revision=decision.routing_config_revision,
        )
        assert ticket.ticket and ticket.digest
        assert verify_ticket(
            ticket_str=ticket.ticket,
            expected_room_id=room_id,
            expected_event_id=request_event,
            expected_content_sha256=content_sha,
            expected_system_name=decision.system_name,
            expected_service_name=decision.service_name,
            expected_revision=decision.routing_config_revision,
        )

        # Step 3: prepare 审批工单
        service = ActionApprovalService(db)
        approval, short_code = service.prepare(
            action_type="RELEASE",
            tool_name="ops.approval.prepare_release",
            room_id=room_id,
            request_event_id=request_event,
            content_sha256=content_sha,
            system_name=decision.system_name,
            service_name=decision.service_name,
            environment="prod",
            targets=["server1", "server2"],
            action_parameters={"pipeline": "default", "variables": {}},
            routing_config_revision=decision.routing_config_revision,
            routing_ticket_digest=ticket.digest,
            risk_level="high",
            ai_reason="用户在 Element 房间请求发布 v1.2.3 到 prod",
            package_name="release-1.2.3.tar.gz",
            package_sha256="a" * 64,
            package_size_bytes=12345678,
        )
        assert approval.status == "PENDING_APPROVAL"
        assert len(short_code) == 8
        assert approval.action_digest is not None

        # Step 4: consume 消费短码（模拟授权用户在 Element 回复"批准 <短码>"）
        approval_event = _event("approval-full-flow")
        consumed = service.consume(
            approval_id=approval.id,
            short_code=short_code,
            approver_matrix_id="@alice:matrix.org",
            room_id=room_id,
            approval_event_id=approval_event,
        )
        assert consumed is not None
        assert consumed.status == "EXECUTING"
        assert consumed.approved_by == "@alice:matrix.org"
        assert consumed.consumed_at is not None

        # Step 5: 执行器执行（mock 部署部分，避免依赖真实部署逻辑）
        with patch.object(
            ApprovalExecutor,
            "_execute_release",
            return_value={
                "action": "RELEASE",
                "deployment_id": "mock-deploy-123",
                "system": "crypto-trader",
                "environment": "prod",
                "servers": ["server1", "server2"],
                "package": "release-1.2.3.tar.gz",
                "message": "mock 部署成功",
            },
        ):
            executor = ApprovalExecutor(db)
            result = executor.execute(approval.id)

        assert result is not None
        assert result.status == "SUCCEEDED"
        assert result.execution_job_id is not None
        assert result.execution_result["deployment_id"] == "mock-deploy-123"
        assert result.executed_at is not None

        # 验证 OperationJob 已创建
        job = db.query(OperationJob).filter(OperationJob.id == result.execution_job_id).first()
        assert job is not None
        assert job.status == "success"
        assert job.source == "approval"

    def test_routing_unmatched_does_not_create_approval(self, db):
        """路由失败时不应创建审批工单"""
        decision = resolve_message_target("完全无法识别的消息内容", ROUTING_SYSTEMS)
        assert decision.outcome == RoutingOutcome.UNMATCHED
        # qclaw 应在此时终止，不调用 prepare


class TestIdempotencyAndReplay:
    """幂等性和重放保护"""

    def test_prepare_is_idempotent_for_same_manifest(self, db):
        """相同 manifest 重复 prepare 应返回已有工单，不生成新短码"""
        suffix = "idempotent"
        service = ActionApprovalService(db)

        common = dict(
            action_type="RELEASE",
            tool_name="ops.approval.prepare_release",
            room_id=_room(suffix),
            request_event_id=_event(suffix),
            content_sha256=_content_hash(f"msg-{suffix}"),
            system_name="crypto-trader",
            service_name=None,
            environment="prod",
            targets=["server1"],
            action_parameters={"v": "1.0.0"},
            routing_config_revision=compute_routing_revision(ROUTING_SYSTEMS),
            routing_ticket_digest=f"ticket-{_RUN_ID}-{suffix}",
        )

        approval1, code1 = service.prepare(**common)
        approval2, code2 = service.prepare(**common)

        assert approval1.id == approval2.id
        assert code1 != ""  # 第一次返回明文
        assert code2 == ""  # 第二次返回空字符串（幂等）

    def test_consume_same_short_code_twice_fails(self, db):
        """同一短码不能被消费两次"""
        suffix = "double-consume"
        service = ActionApprovalService(db)

        approval, short_code = service.prepare(
            action_type="ROLLBACK",
            tool_name="ops.approval.prepare_rollback",
            room_id=_room(suffix),
            request_event_id=_event(suffix),
            content_sha256=_content_hash(f"msg-{suffix}"),
            system_name="crypto-trader",
            service_name=None,
            environment="prod",
            targets=["server1"],
            action_parameters={"deployment_id": "dep-123"},
            routing_config_revision=compute_routing_revision(ROUTING_SYSTEMS),
            routing_ticket_digest=f"ticket-{_RUN_ID}-{suffix}",
        )

        # 第一次消费成功
        first = service.consume(
            approval_id=approval.id,
            short_code=short_code,
            approver_matrix_id="@alice:matrix.org",
            room_id=_room(suffix),
            approval_event_id=_event(f"approval-{suffix}-1"),
        )
        assert first is not None
        assert first.status == "EXECUTING"

        # 第二次消费失败（已消费）
        second = service.consume(
            approval_id=approval.id,
            short_code=short_code,
            approver_matrix_id="@bob:matrix.org",
            room_id=_room(suffix),
            approval_event_id=_event(f"approval-{suffix}-2"),
        )
        assert second is None

    def test_wrong_short_code_fails(self, db):
        """错误短码消费失败"""
        suffix = "wrong-code"
        service = ActionApprovalService(db)

        approval, short_code = service.prepare(
            action_type="DML",
            tool_name="ops.approval.prepare_dml",
            room_id=_room(suffix),
            request_event_id=_event(suffix),
            content_sha256=_content_hash(f"msg-{suffix}"),
            system_name="crypto-trader",
            service_name=None,
            environment="prod",
            targets=[],
            action_parameters={
                "database_connection_id": "db-1",
                "sql_text": "UPDATE users SET active=1",
                "max_affected_rows": 100,
            },
            routing_config_revision=compute_routing_revision(ROUTING_SYSTEMS),
            routing_ticket_digest=f"ticket-{_RUN_ID}-{suffix}",
        )

        # 错误短码
        wrong = service.consume(
            approval_id=approval.id,
            short_code="00000000",
            approver_matrix_id="@alice:matrix.org",
            room_id=_room(suffix),
            approval_event_id=_event(f"approval-{suffix}"),
        )
        assert wrong is None

        # 正确短码仍可消费
        right = service.consume(
            approval_id=approval.id,
            short_code=short_code,
            approver_matrix_id="@alice:matrix.org",
            room_id=_room(suffix),
            approval_event_id=_event(f"approval-{suffix}"),
        )
        assert right is not None

    def test_cross_room_replay_fails(self, db):
        """跨房间重放攻击失败：A 房间 prepare 的审批码不能在 B 房间消费"""
        suffix = "cross-room"
        service = ActionApprovalService(db)

        room_a = _room(f"{suffix}-A")
        room_b = _room(f"{suffix}-B")

        approval, short_code = service.prepare(
            action_type="RELEASE",
            tool_name="ops.approval.prepare_release",
            room_id=room_a,
            request_event_id=_event(suffix),
            content_sha256=_content_hash(f"msg-{suffix}"),
            system_name="crypto-trader",
            service_name=None,
            environment="prod",
            targets=["server1"],
            action_parameters={"v": "1.0"},
            routing_config_revision=compute_routing_revision(ROUTING_SYSTEMS),
            routing_ticket_digest=f"ticket-{_RUN_ID}-{suffix}",
        )

        # 在 B 房间消费应失败
        cross = service.consume(
            approval_id=approval.id,
            short_code=short_code,
            approver_matrix_id="@alice:matrix.org",
            room_id=room_b,  # 不同房间
            approval_event_id=_event(f"approval-{suffix}"),
        )
        assert cross is None


class TestExpiryAndReject:
    """过期和拒绝场景"""

    def test_expired_approval_cannot_be_consumed(self, db):
        """过期审批码不能消费"""
        suffix = "expired"
        service = ActionApprovalService(db)

        approval, short_code = service.prepare(
            action_type="RELEASE",
            tool_name="ops.approval.prepare_release",
            room_id=_room(suffix),
            request_event_id=_event(suffix),
            content_sha256=_content_hash(f"msg-{suffix}"),
            system_name="crypto-trader",
            service_name=None,
            environment="prod",
            targets=["server1"],
            action_parameters={"v": "1.0"},
            routing_config_revision=compute_routing_revision(ROUTING_SYSTEMS),
            routing_ticket_digest=f"ticket-{_RUN_ID}-{suffix}",
        )

        # 手动将过期时间设为过去
        approval.expires_at = _utcnow() - timedelta(minutes=1)
        db.commit()

        result = service.consume(
            approval_id=approval.id,
            short_code=short_code,
            approver_matrix_id="@alice:matrix.org",
            room_id=_room(suffix),
            approval_event_id=_event(f"approval-{suffix}"),
        )
        assert result is None

        # 应被标记为 EXPIRED
        db.refresh(approval)
        assert approval.status == "EXPIRED"

    def test_expire_stale_sweeps_pending_approvals(self, db):
        """expire_stale 批量清理过期工单"""
        suffix = "stale-sweep"
        service = ActionApprovalService(db)

        # 创建 2 个工单，1 个过期，1 个未过期
        for i in range(2):
            approval, _ = service.prepare(
                action_type="RELEASE",
                tool_name="ops.approval.prepare_release",
                room_id=_room(f"{suffix}-{i}"),
                request_event_id=_event(f"{suffix}-{i}"),
                content_sha256=_content_hash(f"msg-{suffix}-{i}"),
                system_name="crypto-trader",
                service_name=None,
                environment="prod",
                targets=[f"server{i}"],
                action_parameters={"i": i},
                routing_config_revision=compute_routing_revision(ROUTING_SYSTEMS),
                routing_ticket_digest=f"ticket-{_RUN_ID}-{suffix}-{i}",
            )
            if i == 0:
                approval.expires_at = _utcnow() - timedelta(minutes=5)
                db.commit()

        count = service.expire_stale()
        assert count >= 1

    def test_rejected_approval_is_terminal(self, db):
        """拒绝后工单进入终态，不能再消费"""
        suffix = "reject-terminal"
        service = ActionApprovalService(db)

        approval, short_code = service.prepare(
            action_type="PACKAGE_CLEANUP",
            tool_name="ops.approval.prepare_package_cleanup",
            room_id=_room(suffix),
            request_event_id=_event(suffix),
            content_sha256=_content_hash(f"msg-{suffix}"),
            system_name="crypto-trader",
            service_name=None,
            environment="prod",
            targets=[],
            action_parameters={"package_ids": ["pkg-1", "pkg-2"]},
            routing_config_revision=compute_routing_revision(ROUTING_SYSTEMS),
            routing_ticket_digest=f"ticket-{_RUN_ID}-{suffix}",
        )

        # 拒绝
        rejected = service.reject(
            approval_id=approval.id,
            rejecter_matrix_id="@bob:matrix.org",
        )
        assert rejected is not None
        assert rejected.status == "REJECTED"
        assert rejected.rejected_by == "@bob:matrix.org"

        # 拒绝后不能再消费
        consume_after_reject = service.consume(
            approval_id=approval.id,
            short_code=short_code,
            approver_matrix_id="@alice:matrix.org",
            room_id=_room(suffix),
            approval_event_id=_event(f"approval-{suffix}"),
        )
        assert consume_after_reject is None

        # 已拒绝工单再次 reject 返回 None
        second_reject = service.reject(
            approval_id=approval.id,
            rejecter_matrix_id="@charlie:matrix.org",
        )
        assert second_reject is None


class TestRoutingTicketIntegrity:
    """路由票据完整性验证"""

    def test_ticket_rejects_modified_content_hash(self, db):
        """票据绑定 content_sha256，修改后验证失败"""
        suffix = "ticket-tamper"
        room_id = _room(suffix)
        event_id = _event(suffix)
        original_hash = _content_hash(f"original-{suffix}")
        tampered_hash = _content_hash(f"tampered-{suffix}")

        decision = resolve_message_target("部署 量化", ROUTING_SYSTEMS)
        ticket = issue_ticket(
            room_id=room_id,
            event_id=event_id,
            content_sha256=original_hash,
            system_name=decision.system_name,
            service_name=decision.service_name,
            routing_config_revision=decision.routing_config_revision,
        )

        # 原始 hash 验证通过
        assert verify_ticket(
            ticket_str=ticket.ticket,
            expected_room_id=room_id,
            expected_event_id=event_id,
            expected_content_sha256=original_hash,
            expected_system_name=decision.system_name,
            expected_service_name=decision.service_name,
            expected_revision=decision.routing_config_revision,
        )

        # 篡改 hash 验证失败
        assert not verify_ticket(
            ticket_str=ticket.ticket,
            expected_room_id=room_id,
            expected_event_id=event_id,
            expected_content_sha256=tampered_hash,
            expected_system_name=decision.system_name,
            expected_service_name=decision.service_name,
            expected_revision=decision.routing_config_revision,
        )

    def test_ticket_rejects_wrong_room(self, db):
        """票据绑定 room_id，跨房间验证失败"""
        suffix = "ticket-room"
        decision = resolve_message_target("部署 量化", ROUTING_SYSTEMS)
        ticket = issue_ticket(
            room_id=_room(suffix),
            event_id=_event(suffix),
            content_sha256=_content_hash(f"msg-{suffix}"),
            system_name=decision.system_name,
            service_name=decision.service_name,
            routing_config_revision=decision.routing_config_revision,
        )

        # 不同房间验证失败
        assert not verify_ticket(
            ticket_str=ticket.ticket,
            expected_room_id="!wrong-room:matrix.org",
            expected_event_id=_event(suffix),
            expected_content_sha256=_content_hash(f"msg-{suffix}"),
            expected_system_name=decision.system_name,
            expected_service_name=decision.service_name,
            expected_revision=decision.routing_config_revision,
        )

    def test_routing_config_revision_changes_with_config(self, db):
        """路由配置变更后 revision 改变"""
        rev1 = compute_routing_revision(ROUTING_SYSTEMS)

        # 修改配置：禁用 crypto-trader
        modified_systems = [
            {
                **ROUTING_SYSTEMS[0],
                "message_routing": {**ROUTING_SYSTEMS[0]["message_routing"], "enabled": False},
            },
            *ROUTING_SYSTEMS[1:],
        ]
        rev2 = compute_routing_revision(modified_systems)

        assert rev1 != rev2

    def test_disabled_system_not_resolved(self, db):
        """禁用路由的系统不应被解析"""
        # disabled-system 的别名 "should-not-match" 不应匹配
        decision = resolve_message_target("should-not-match", ROUTING_SYSTEMS)
        assert decision.outcome == RoutingOutcome.UNMATCHED


class TestActionTypeDispatch:
    """验证 4 种操作类型都能正确分发"""

    def _prepare_approval(self, db, action_type, suffix, action_parameters):
        """辅助：prepare 一个审批工单"""
        service = ActionApprovalService(db)
        approval, short_code = service.prepare(
            action_type=action_type,
            tool_name=f"ops.approval.prepare_{action_type.lower()}",
            room_id=_room(suffix),
            request_event_id=_event(suffix),
            content_sha256=_content_hash(f"msg-{suffix}"),
            system_name="crypto-trader",
            service_name=None,
            environment="prod",
            targets=["server1"] if action_type != "DML" else [],
            action_parameters=action_parameters,
            routing_config_revision=compute_routing_revision(ROUTING_SYSTEMS),
            routing_ticket_digest=f"ticket-{_RUN_ID}-{suffix}",
        )
        return approval, short_code

    def test_rollback_dispatch(self, db):
        """ROLLBACK 操作正确分发"""
        suffix = "dispatch-rollback"
        approval, short_code = self._prepare_approval(
            db, "ROLLBACK", suffix, {"deployment_id": "dep-456"}
        )

        # 消费
        service = ActionApprovalService(db)
        consumed = service.consume(
            approval_id=approval.id,
            short_code=short_code,
            approver_matrix_id="@alice:matrix.org",
            room_id=_room(suffix),
            approval_event_id=_event(f"approval-{suffix}"),
        )
        assert consumed.status == "EXECUTING"

        # 执行（mock 回滚）
        with patch.object(
            ApprovalExecutor,
            "_execute_rollback",
            return_value={"action": "ROLLBACK", "deployment_id": "dep-456", "message": "回滚已触发"},
        ):
            executor = ApprovalExecutor(db)
            result = executor.execute(approval.id)

        assert result.status == "SUCCEEDED"
        assert result.execution_result["action"] == "ROLLBACK"

    def test_dml_dispatch(self, db):
        """DML 操作正确分发"""
        suffix = "dispatch-dml"
        approval, short_code = self._prepare_approval(
            db,
            "DML",
            suffix,
            {
                "database_connection_id": "db-conn-1",
                "sql_text": "UPDATE users SET active=1 WHERE id=100",
                "max_affected_rows": 10,
            },
        )

        service = ActionApprovalService(db)
        consumed = service.consume(
            approval_id=approval.id,
            short_code=short_code,
            approver_matrix_id="@alice:matrix.org",
            room_id=_room(suffix),
            approval_event_id=_event(f"approval-{suffix}"),
        )
        assert consumed.status == "EXECUTING"

        with patch.object(
            ApprovalExecutor,
            "_execute_dml",
            return_value={
                "action": "DML",
                "database_connection_id": "db-conn-1",
                "affected_rows": 1,
                "sql_text": "UPDATE users SET active=1 WHERE id=100",
            },
        ):
            executor = ApprovalExecutor(db)
            result = executor.execute(approval.id)

        assert result.status == "SUCCEEDED"
        assert result.execution_result["affected_rows"] == 1

    def test_package_cleanup_dispatch(self, db):
        """PACKAGE_CLEANUP 操作正确分发"""
        suffix = "dispatch-cleanup"
        approval, short_code = self._prepare_approval(
            db, "PACKAGE_CLEANUP", suffix, {"package_ids": ["pkg-1", "pkg-2", "pkg-3"]}
        )

        service = ActionApprovalService(db)
        consumed = service.consume(
            approval_id=approval.id,
            short_code=short_code,
            approver_matrix_id="@alice:matrix.org",
            room_id=_room(suffix),
            approval_event_id=_event(f"approval-{suffix}"),
        )
        assert consumed.status == "EXECUTING"

        with patch.object(
            ApprovalExecutor,
            "_execute_package_cleanup",
            return_value={
                "action": "PACKAGE_CLEANUP",
                "package_ids": ["pkg-1", "pkg-2", "pkg-3"],
                "cleaned_count": 3,
            },
        ):
            executor = ApprovalExecutor(db)
            result = executor.execute(approval.id)

        assert result.status == "SUCCEEDED"
        assert result.execution_result["cleaned_count"] == 3

    def test_execution_failure_marks_failed(self, db):
        """执行器抛异常时标记 FAILED 并记录 failure_reason"""
        suffix = "dispatch-failure"
        approval, short_code = self._prepare_approval(
            db, "RELEASE", suffix, {"v": "1.0"}
        )

        service = ActionApprovalService(db)
        consumed = service.consume(
            approval_id=approval.id,
            short_code=short_code,
            approver_matrix_id="@alice:matrix.org",
            room_id=_room(suffix),
            approval_event_id=_event(f"approval-{suffix}"),
        )
        assert consumed.status == "EXECUTING"

        # mock _execute_release 抛异常
        with patch.object(
            ApprovalExecutor,
            "_execute_release",
            side_effect=RuntimeError("部署目标服务器不可达: connection refused"),
        ):
            executor = ApprovalExecutor(db)
            result = executor.execute(approval.id)

        assert result.status == "FAILED"
        assert "部署目标服务器不可达" in (result.failure_reason or "")
        assert result.execution_result is not None
        assert "error" in result.execution_result

        # OperationJob 也标记为 failed
        job = db.query(OperationJob).filter(OperationJob.id == result.execution_job_id).first()
        assert job.status == "failed"
        assert job.error_message is not None


class TestApprovalQuery:
    """审批查询接口验证"""

    def test_get_nonexistent_returns_none(self, db):
        """查询不存在的 ID 返回 None"""
        service = ActionApprovalService(db)
        assert service.get("nonexistent-id-12345") is None

    def test_executor_returns_none_for_nonexistent(self, db):
        """执行器对不存在的 ID 返回 None"""
        executor = ApprovalExecutor(db)
        assert executor.execute("nonexistent-id-12345") is None

    def test_executor_skips_non_executing(self, db):
        """执行器对非 EXECUTING 状态的工单不重复执行"""
        suffix = "skip-non-executing"
        service = ActionApprovalService(db)
        approval, _ = service.prepare(
            action_type="RELEASE",
            tool_name="ops.approval.prepare_release",
            room_id=_room(suffix),
            request_event_id=_event(suffix),
            content_sha256=_content_hash(f"msg-{suffix}"),
            system_name="crypto-trader",
            service_name=None,
            environment="prod",
            targets=["server1"],
            action_parameters={"v": "1.0"},
            routing_config_revision=compute_routing_revision(ROUTING_SYSTEMS),
            routing_ticket_digest=f"ticket-{_RUN_ID}-{suffix}",
        )
        # 工单处于 PENDING_APPROVAL，执行器应直接返回工单不执行
        executor = ApprovalExecutor(db)
        result = executor.execute(approval.id)
        assert result is not None
        assert result.status == "PENDING_APPROVAL"
        assert result.execution_job_id is None  # 未创建 job


class TestMCPExecuteChain:
    """验证 approval_execute MCP 工具 handler 的完整调用链。

    这是 P0 修复的核心验证：consume() 成功后必须调用 ApprovalExecutor.execute()，
    不能只返回 EXECUTING 状态。
    """

    def test_approval_execute_calls_executor_after_consume(self, db):
        """consume 成功后立即调用 executor.execute()，返回 SUCCEEDED 或 FAILED。

        验证：
        1. handler 返回 ok=True 且 status 为 SUCCEEDED（mock 执行方法）
        2. approval 状态从 EXECUTING → SUCCEEDED（executor 更新）
        3. execution_result 和 executed_at 已填充
        """
        from app.services.tool_adapters.approval_tools import approval_execute
        from app.services.tool_context import ToolContext

        suffix = "mcp-chain"
        room_id = _room(suffix)

        # 直接使用 ActionApprovalService 创建工单，跳过 MCP 路由
        service = ActionApprovalService(db)
        approval, short_code = service.prepare(
            action_type="RELEASE",
            tool_name="ops.approval.prepare_release",
            room_id=room_id,
            request_event_id=_event(suffix),
            content_sha256=_content_hash(f"msg-{suffix}"),
            system_name="crypto-trader",
            service_name=None,
            environment="prod",
            targets=["server1"],
            action_parameters={"v": "1.0"},
            routing_config_revision=compute_routing_revision(ROUTING_SYSTEMS),
            routing_ticket_digest=f"ticket-{_RUN_ID}-{suffix}",
        )

        ctx = ToolContext(
            auth_type="tool_token",
            token_name="test-token",
            token_owner="test",
            allow_write=True,
            bound_room_ids=[],
        )

        # mock 四个执行方法避免真实部署
        with patch.object(
            ApprovalExecutor, "_execute_release",
            return_value={"action": "RELEASE", "deployment_id": "mcp-deploy-1", "message": "ok"},
        ), patch.object(
            ApprovalExecutor, "_execute_rollback",
            return_value={"action": "ROLLBACK", "message": "ok"},
        ), patch.object(
            ApprovalExecutor, "_execute_dml",
            return_value={"action": "DML", "affected_rows": 1},
        ), patch.object(
            ApprovalExecutor, "_execute_package_cleanup",
            return_value={"action": "PACKAGE_CLEANUP", "cleaned_count": 0},
        ):
            result = approval_execute(
                args={
                    "approval_id": approval.id,
                    "short_code": short_code,
                    "approver_matrix_id": "@alice:matrix.org",
                    "room_id": room_id,
                    "approval_event_id": _event(f"approval-{suffix}"),
                },
                ctx=ctx,
                db=db,
            )

        # 关键断言：handler 返回 ok=True，且 status 为 SUCCEEDED（不是 EXECUTING）
        assert result["ok"] is True
        assert result["status"] == "SUCCEEDED"
        assert result["execution_result"] is not None
        assert result["executed_at"] is not None
        assert result["failure_reason"] is None

        # 验证数据库：approval 状态已更新为 SUCCEEDED
        db.refresh(approval)
        assert approval.status == "SUCCEEDED"
        assert approval.execution_result is not None
        assert approval.executed_at is not None

    def test_approval_execute_returns_failure_on_executor_error(self, db):
        """executor 抛异常时 handler 返回 ok=False 和 failure_reason。"""
        from app.services.tool_adapters.approval_tools import approval_execute
        from app.services.tool_context import ToolContext

        suffix = "mcp-chain-fail"
        room_id = _room(suffix)

        service = ActionApprovalService(db)
        approval, short_code = service.prepare(
            action_type="RELEASE",
            tool_name="ops.approval.prepare_release",
            room_id=room_id,
            request_event_id=_event(suffix),
            content_sha256=_content_hash(f"msg-{suffix}"),
            system_name="crypto-trader",
            service_name=None,
            environment="prod",
            targets=["server1"],
            action_parameters={"v": "1.0"},
            routing_config_revision=compute_routing_revision(ROUTING_SYSTEMS),
            routing_ticket_digest=f"ticket-{_RUN_ID}-{suffix}",
        )

        ctx = ToolContext(
            auth_type="tool_token",
            token_name="test-token",
            token_owner="test",
            allow_write=True,
            bound_room_ids=[],
        )

        with patch.object(
            ApprovalExecutor, "_execute_release",
            side_effect=RuntimeError("目标服务器不可达"),
        ):
            result = approval_execute(
                args={
                    "approval_id": approval.id,
                    "short_code": short_code,
                    "approver_matrix_id": "@alice:matrix.org",
                    "room_id": room_id,
                    "approval_event_id": _event(f"approval-{suffix}"),
                },
                ctx=ctx,
                db=db,
            )

        assert result["ok"] is False
        assert result["status"] == "FAILED"
        assert "目标服务器不可达" in (result["failure_reason"] or "")

        db.refresh(approval)
        assert approval.status == "FAILED"
        assert approval.failure_reason is not None

    def test_approval_execute_rejects_wrong_short_code(self, db):
        """错误短码时 handler 返回 ok=False，不调用 executor。"""
        from app.services.tool_adapters.approval_tools import approval_execute
        from app.services.tool_context import ToolContext

        suffix = "mcp-chain-wrong"
        room_id = _room(suffix)

        service = ActionApprovalService(db)
        approval, _ = service.prepare(
            action_type="RELEASE",
            tool_name="ops.approval.prepare_release",
            room_id=room_id,
            request_event_id=_event(suffix),
            content_sha256=_content_hash(f"msg-{suffix}"),
            system_name="crypto-trader",
            service_name=None,
            environment="prod",
            targets=["server1"],
            action_parameters={"v": "1.0"},
            routing_config_revision=compute_routing_revision(ROUTING_SYSTEMS),
            routing_ticket_digest=f"ticket-{_RUN_ID}-{suffix}",
        )

        ctx = ToolContext(
            auth_type="tool_token",
            token_name="test-token",
            token_owner="test",
            allow_write=True,
            bound_room_ids=[],
        )

        result = approval_execute(
            args={
                "approval_id": approval.id,
                "short_code": "00000000",  # 错误短码
                "approver_matrix_id": "@alice:matrix.org",
                "room_id": room_id,
                "approval_event_id": _event(f"approval-{suffix}"),
            },
            ctx=ctx,
            db=db,
        )

        assert result["ok"] is False
        assert "审批码无效" in result["error"]

        # 工单仍为 PENDING_APPROVAL（未被消费）
        db.refresh(approval)
        assert approval.status == "PENDING_APPROVAL"
