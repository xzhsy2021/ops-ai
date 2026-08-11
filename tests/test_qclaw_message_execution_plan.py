"""qclaw 消息触发 → 执行计划 端到端契约测试。

验证一条 Element 消息的完整链路：路由 → prepare_plan（一次审批）→
execute_plan（按冻结步骤顺序执行），以及幂等性与计划变化需重新审批。
"""
import hashlib
import uuid
from unittest.mock import patch

import pytest

from app.db.base import SessionLocal, Base, engine
from app.db.migrations.runner import run_schema_migrations
from app.db.models import AiActionApproval, ExecutionPlan
from app.services.message_context import MessageContext
from app.services.qclaw_routing import (
    RoutingOutcome,
    resolve_message_target,
    issue_ticket,
    verify_ticket,
)
from app.services.tool_context import ToolContext
from app.services.tool_registry import register_builtin_tools

_RUN_ID = uuid.uuid4().hex[:8]

register_builtin_tools()


def _room(suffix: str) -> str:
    return f"!room-{_RUN_ID}-{suffix}:matrix.org"


def _event(suffix: str) -> str:
    return f"$evt-{_RUN_ID}-{suffix}:matrix.org"


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


ROUTING_SYSTEMS = [
    {
        "name": "crypto-trader",
        "message_routing": {
            "enabled": True,
            "aliases": ["量化", "量化交易"],
            "keywords": ["btc strategy", "crypto deploy"],
            "priority": 100,
            "approvers": ["@admin:matrix.org"],
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
                        "approvers": [],
                    }
                },
            }
        ],
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


def _ctx(bound_room_ids=None):
    from app.services.tool_token import resolve_channel_bindings

    return ToolContext(
        auth_type="tool_token",
        token_name="test-token",
        token_owner="test",
        allow_write=True,
        channel_bindings=resolve_channel_bindings(legacy=bound_room_ids or []),
    )


def _multi_step_plan():
    """模拟一条消息解析出的多步骤计划（重启 → 健康检查）。"""
    return [
        {
            "step_key": "restart",
            "action_type": "SERVICE_CONTROL",
            "parameters": {"control_action": "restart", "targets": ["cc-test2", "cc-test3"]},
            "dependencies": [],
        },
        {
            "step_key": "health",
            "action_type": "HEALTH_CHECK",
            "parameters": {"targets": ["cc-test2", "cc-test3"]},
            "dependencies": ["restart"],
        },
    ]


class TestMessageExecutionPlanFlow:
    """一条消息 = 一个执行计划 = 一次审批"""

    def test_full_message_flow_creates_one_plan_one_approval(self, db):
        """完整消息流：路由 → prepare_plan → execute_plan，全程只产生一次审批。"""
        from app.services.tool_adapters.approval_tools import (
            approval_prepare_plan,
            approval_execute_plan,
        )

        suffix = "full-msg"
        room_id = _room(suffix)
        request_event = _event(suffix)
        message_text = "@qclaw-bot crypto deploy strategy 重启"
        content_sha = _content_hash(message_text)
        ctx = _ctx(bound_room_ids=[room_id])

        # Step 1: 路由解析产生一次决策 + 票据
        decision = resolve_message_target(message_text, ROUTING_SYSTEMS)
        assert decision.outcome == RoutingOutcome.RESOLVED
        assert decision.system_name == "crypto-trader"

        message_context = MessageContext(
            channel="matrix",
            channel_account_id="default",
            conversation_id=room_id,
            message_id=request_event,
            sender_id="@requester:matrix.org",
            content_sha256=content_sha,
        )
        ticket = issue_ticket(
            message_context=message_context,
            system_name=decision.system_name,
            service_name=decision.service_name,
            routing_config_revision=decision.routing_config_revision,
        )
        assert verify_ticket(
            ticket_str=ticket.ticket,
            expected_message_context=message_context,
            expected_system_name=decision.system_name,
            expected_service_name=decision.service_name,
            expected_revision=decision.routing_config_revision,
        )

        # Step 2: prepare_plan 创建一次审批（一个计划 + 一个短码）
        prepared = approval_prepare_plan(
            args={
                "room_id": room_id,
                "request_event_id": request_event,
                "content_sha256": content_sha,
                "system_name": decision.system_name,
                "service_name": decision.service_name,
                "environment": "test",
                "targets": ["cc-test2", "cc-test3"],
                "steps": _multi_step_plan(),
                "policy": {"continue_on_error": False, "max_retries": 0},
                "routing_config_revision": decision.routing_config_revision,
                "routing_ticket_digest": ticket.digest,
                "ai_reason": "用户在 Element 房间请求重启 strategy 服务",
            },
            ctx=ctx,
            db=db,
        )
        assert prepared["status"] == "PENDING_APPROVAL"
        assert len(prepared["short_code"]) == 8
        assert prepared["step_count"] == 2

        # 数据库中仅一条待审批执行计划
        pending_plans = db.query(ExecutionPlan).filter(
            ExecutionPlan.status == "PENDING_APPROVAL",
            ExecutionPlan.room_id == room_id,
        ).all()
        assert len(pending_plans) == 1

        # Step 3: execute_plan 消费短码并顺序执行步骤
        approval_count_before = db.query(AiActionApproval).count()
        order = []

        def svc_handler(plan, step, db):
            order.append(("SERVICE_CONTROL", step.step_key))
            return {"ok": True, "results": []}

        def health_handler(plan, step, db):
            order.append(("HEALTH_CHECK", step.step_key))
            return {"ok": True, "results": []}

        with patch.dict(
            "app.services.plan_executor.STEP_HANDLERS",
            {"SERVICE_CONTROL": svc_handler, "HEALTH_CHECK": health_handler},
        ):
            result = approval_execute_plan(
                args={
                    "plan_id": prepared["plan_id"],
                    "short_code": prepared["short_code"],
                    "approver_matrix_id": "@admin:matrix.org",
                    "room_id": room_id,
                    "approval_event_id": _event(f"approve-{suffix}"),
                },
                ctx=ctx,
                db=db,
            )

        assert result["ok"] is True
        assert result["status"] == "SUCCEEDED"
        assert [a for a, _ in order] == ["SERVICE_CONTROL", "HEALTH_CHECK"]

        # 没有产生新的单动作审批；计划与步骤均成功
        assert db.query(AiActionApproval).count() == approval_count_before
        plan = db.query(ExecutionPlan).filter(ExecutionPlan.id == prepared["plan_id"]).first()
        assert plan.status == "SUCCEEDED"
        assert all(s.status == "SUCCEEDED" for s in plan.steps)

    def test_execute_plan_runs_steps_in_order_without_new_approvals(self, db):
        """审批一次后，多个步骤按顺序执行，不再产生新审批记录。"""
        from app.services.tool_adapters.approval_tools import (
            approval_prepare_plan,
            approval_execute_plan,
        )

        suffix = "ordered"
        room_id = _room(suffix)
        request_event = _event(suffix)
        content_sha = _content_hash(f"msg-{suffix}")
        ctx = _ctx(bound_room_ids=[room_id])

        decision = resolve_message_target("量化 trader api 重启", ROUTING_SYSTEMS)
        assert decision.outcome == RoutingOutcome.RESOLVED

        prepared = approval_prepare_plan(
            args={
                "room_id": room_id,
                "request_event_id": request_event,
                "content_sha256": content_sha,
                "system_name": decision.system_name,
                "service_name": decision.service_name,
                "environment": "test",
                "targets": ["cc-test2"],
                "steps": _multi_step_plan(),
                "policy": {"continue_on_error": False, "max_retries": 0},
                "routing_config_revision": decision.routing_config_revision,
                "routing_ticket_digest": f"ticket-{_RUN_ID}-{suffix}",
            },
            ctx=ctx,
            db=db,
        )

        approval_count_before = db.query(AiActionApproval).count()

        # mock 两个真实业务 handler，验证步骤按顺序被调用
        order = []

        def svc_handler(plan, step, db):
            order.append(("SERVICE_CONTROL", step.step_key))
            return {"ok": True, "results": []}

        def health_handler(plan, step, db):
            order.append(("HEALTH_CHECK", step.step_key))
            return {"ok": True, "results": []}

        with patch.dict(
            "app.services.plan_executor.STEP_HANDLERS",
            {"SERVICE_CONTROL": svc_handler, "HEALTH_CHECK": health_handler},
        ):
            result = approval_execute_plan(
                args={
                    "plan_id": prepared["plan_id"],
                    "short_code": prepared["short_code"],
                    "approver_matrix_id": "@admin:matrix.org",
                    "room_id": room_id,
                    "approval_event_id": _event(f"approve-{suffix}"),
                },
                ctx=ctx,
                db=db,
            )

        assert result["ok"] is True
        assert result["status"] == "SUCCEEDED"
        assert [a for a, _ in order] == ["SERVICE_CONTROL", "HEALTH_CHECK"]
        # 没有产生新的单动作审批
        assert db.query(AiActionApproval).count() == approval_count_before
        # 步骤都已成功，且不重复
        plan = db.query(ExecutionPlan).filter(ExecutionPlan.id == prepared["plan_id"]).first()
        assert plan.status == "SUCCEEDED"
        assert all(s.status == "SUCCEEDED" for s in plan.steps)

    def test_file_upload_is_one_step_in_the_single_plan_approval(self, db, monkeypatch):
        from pathlib import Path
        from app.services.tool_adapters import file_transfer_tools
        from app.services.tool_adapters import approval_tools
        from app.services.tool_adapters.approval_tools import (
            approval_prepare_plan,
            approval_execute_plan,
        )

        upload_calls = []

        def fake_upload(args, ctx, db):
            upload_calls.append(args)
            return {"ok": True, "server": args["server"]}

        monkeypatch.setattr(file_transfer_tools, "upload_file", fake_upload)
        monkeypatch.setattr(
            approval_tools,
            "_resolve_source",
            lambda params: (Path("C:/ops/uploads/frontend.tar.gz"), "frontend.tar.gz"),
        )
        monkeypatch.setattr(
            approval_tools,
            "inspect_package_file",
            lambda source, filename, policy, calculate_sha256: {
                "blockers": [],
                "sha256": "a" * 64,
                "size_bytes": 12,
            },
        )
        suffix = "plan-upload"
        room_id = _room(suffix)
        ctx = _ctx(bound_room_ids=[room_id])
        approval_count_before = db.query(AiActionApproval).count()

        prepared = approval_prepare_plan(
            args={
                "room_id": room_id,
                "request_event_id": _event(suffix),
                "content_sha256": _content_hash(suffix),
                "system_name": "crypto-trader",
                "service_name": "trader-api",
                "environment": "test",
                "targets": ["cc-test2"],
                "steps": [{
                    "step_key": "upload",
                    "action_type": "FILE_UPLOAD",
                    "parameters": {
                        "action_parameters": {
                            "package_name": "frontend.tar.gz",
                            "remote_path": "/srv/releases/frontend.tar.gz",
                            "expected_sha256": "a" * 64,
                            "expected_size_bytes": 12,
                        },
                    },
                    "dependencies": [],
                }],
                "policy": {"continue_on_error": False},
                "routing_config_revision": f"rev-{_RUN_ID}-{suffix}",
                "routing_ticket_digest": f"ticket-{_RUN_ID}-{suffix}",
            },
            ctx=ctx,
            db=db,
        )

        result = approval_execute_plan(
            args={
                "plan_id": prepared["plan_id"],
                "short_code": prepared["short_code"],
                "approver_matrix_id": "@admin:matrix.org",
                "room_id": room_id,
                "approval_event_id": _event(f"approve-{suffix}"),
            },
            ctx=ctx,
            db=db,
        )

        assert result["ok"] is True
        assert result["status"] == "SUCCEEDED"
        assert db.query(AiActionApproval).count() == approval_count_before
        assert upload_calls[0]["server"] == "cc-test2"

    def test_batch_file_upload_freezes_package_manifest_before_approval(self, db, monkeypatch):
        from pathlib import Path
        from app.services.tool_adapters import approval_tools
        from app.services.tool_adapters.approval_tools import approval_prepare_plan

        monkeypatch.setattr(
            approval_tools,
            "_resolve_source",
            lambda params: (Path("C:/ops/uploads/frontend.tar.gz"), "frontend.tar.gz"),
        )
        monkeypatch.setattr(
            approval_tools,
            "inspect_package_file",
            lambda source, filename, policy, calculate_sha256: {
                "blockers": [],
                "sha256": "c" * 64,
                "size_bytes": 12,
            },
        )

        suffix = "plan-upload-freeze"
        room_id = _room(suffix)
        prepared = approval_prepare_plan(
            args={
                "room_id": room_id,
                "request_event_id": _event(suffix),
                "content_sha256": _content_hash(suffix),
                "system_name": "crypto-trader",
                "service_name": "trader-api",
                "environment": "test",
                "targets": ["cc-test2"],
                "steps": [{
                    "step_key": "upload",
                    "action_type": "FILE_UPLOAD",
                    "parameters": {
                        "action_parameters": {
                            "package_name": "frontend.tar.gz",
                            "remote_path": "/srv/releases/frontend.tar.gz",
                        },
                    },
                    "dependencies": [],
                }],
                "policy": {"continue_on_error": False},
                "routing_config_revision": f"rev-{_RUN_ID}-{suffix}",
                "routing_ticket_digest": f"ticket-{_RUN_ID}-{suffix}",
            },
            ctx=_ctx(bound_room_ids=[room_id]),
            db=db,
        )

        plan = db.query(ExecutionPlan).filter(ExecutionPlan.id == prepared["plan_id"]).first()
        action_parameters = plan.steps[0].parameters["action_parameters"]
        assert action_parameters["expected_sha256"] == "c" * 64
        assert action_parameters["expected_size_bytes"] == 12
        assert prepared["steps"][0]["approval_details"] == {
            "package_name": "frontend.tar.gz",
            "remote_path": "/srv/releases/frontend.tar.gz",
            "overwrite": False,
            "expected_sha256": "c" * 64,
            "expected_size_bytes": 12,
            "targets": ["cc-test2"],
        }

    def test_duplicate_message_is_idempotent(self, db):
        """重复消息（相同 plan_digest）不生成第二个待审批计划或新短码。"""
        from app.services.tool_adapters.approval_tools import approval_prepare_plan

        suffix = "dupe"
        room_id = _room(suffix)
        ctx = _ctx(bound_room_ids=[room_id])

        args = {
            "room_id": room_id,
            "request_event_id": _event(suffix),
            "content_sha256": _content_hash(f"msg-{suffix}"),
            "system_name": "crypto-trader",
            "service_name": None,
            "environment": "test",
            "targets": ["cc-test2"],
            "steps": _multi_step_plan(),
            "policy": {"continue_on_error": False, "max_retries": 0},
            "routing_config_revision": "rev-dupe",
            "routing_ticket_digest": f"ticket-{_RUN_ID}-{suffix}",
        }

        r1 = approval_prepare_plan(dict(args), ctx=ctx, db=db)
        r2 = approval_prepare_plan(dict(args), ctx=ctx, db=db)

        assert r1["plan_id"] == r2["plan_id"]
        assert r1["short_code"] != ""
        assert r2["short_code"] == ""

        plans = db.query(ExecutionPlan).filter(ExecutionPlan.plan_digest == r1["plan_digest"]).all()
        assert len(plans) == 1

    def test_changed_environment_requires_new_plan(self, db):
        """环境变化（prod vs test）必须产生新的计划（新 digest）。"""
        from app.services.tool_adapters.approval_tools import approval_prepare_plan

        suffix = "env-change"
        room_id = _room(suffix)
        ctx = _ctx(bound_room_ids=[room_id])

        base = {
            "room_id": room_id,
            "request_event_id": _event(suffix),
            "content_sha256": _content_hash(f"msg-{suffix}"),
            "system_name": "crypto-trader",
            "service_name": None,
            "environment": "test",
            "targets": ["cc-test2"],
            "steps": _multi_step_plan(),
            "policy": {"continue_on_error": False, "max_retries": 0},
            "routing_config_revision": "rev-env",
            "routing_ticket_digest": f"ticket-{_RUN_ID}-{suffix}",
        }

        r_test = approval_prepare_plan(dict(base), ctx=ctx, db=db)
        r_prod = approval_prepare_plan({**base, "environment": "prod"}, ctx=ctx, db=db)

        assert r_test["plan_id"] != r_prod["plan_id"]
        assert r_test["plan_digest"] != r_prod["plan_digest"]
        assert r_prod["short_code"] != ""

    def test_changed_steps_requires_new_plan(self, db):
        """步骤清单变化（新增步骤）必须产生新的计划。"""
        from app.services.tool_adapters.approval_tools import approval_prepare_plan

        suffix = "step-change"
        room_id = _room(suffix)
        ctx = _ctx(bound_room_ids=[room_id])

        base = {
            "room_id": room_id,
            "request_event_id": _event(suffix),
            "content_sha256": _content_hash(f"msg-{suffix}"),
            "system_name": "crypto-trader",
            "service_name": None,
            "environment": "test",
            "targets": ["cc-test2"],
            "policy": {"continue_on_error": False, "max_retries": 0},
            "routing_config_revision": "rev-steps",
            "routing_ticket_digest": f"ticket-{_RUN_ID}-{suffix}",
        }

        extra_step = [
            * _multi_step_plan(),
            {
                "step_key": "log",
                "action_type": "HEALTH_CHECK",
                "parameters": {"targets": ["cc-test2"]},
                "dependencies": ["health"],
            },
        ]

        r1 = approval_prepare_plan({**base, "steps": _multi_step_plan()}, ctx=ctx, db=db)
        r2 = approval_prepare_plan({**base, "steps": extra_step}, ctx=ctx, db=db)

        assert r1["plan_id"] != r2["plan_id"]
        assert r1["step_count"] == 2
        assert r2["step_count"] == 3
