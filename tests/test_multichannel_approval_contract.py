import json
from datetime import timedelta

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.migrations.runner import run_schema_migrations
from app.db.models import AiActionApproval, ExecutionPlan
from app.services.action_approval import ActionApprovalService, compute_action_digest
from app.services.execution_plan import ExecutionPlanService, compute_plan_digest
from app.services.message_context import MessageContext


def _context(channel="matrix", *, account="default", conversation="room-1", message="message-1", sender="alice"):
    return MessageContext(
        channel=channel,
        channel_account_id=account,
        conversation_id=conversation,
        message_id=message,
        sender_id=sender,
        content_sha256="a" * 64,
    )


def _engine(tmp_path, *, migrate=True):
    engine = create_engine(f"sqlite:///{tmp_path / 'approval-contract.db'}")
    Base.metadata.create_all(engine)
    if migrate:
        run_schema_migrations(engine)
    return engine


def _action_prepare(service, context, **overrides):
    values = dict(
        action_type="RELEASE",
        tool_name="ops.deploy",
        message_context=context,
        system_name="payment",
        service_name="api",
        environment="prod",
        targets=["s1"],
        action_parameters={"version": "1.0.0"},
        routing_config_revision="rev-1",
        routing_ticket_digest="ticket-1",
        authorized_identities=[
            {"channel": context.channel, "channel_account_id": context.channel_account_id, "sender_id": "approver"}
        ],
    )
    values.update(overrides)
    return service.prepare(**values)


def _plan_prepare(service, context, **overrides):
    values = dict(
        message_context=context,
        system_name="payment",
        service_name="api",
        environment="prod",
        targets=["s1"],
        steps=[{
            "step_key": "restart",
            "action_type": "SERVICE_CONTROL",
            "parameters": {"control_action": "restart"},
            "dependencies": [],
        }],
        policy={},
        routing_config_revision="rev-1",
        routing_ticket_digest="ticket-1",
        authorized_identities=[
            {"channel": context.channel, "channel_account_id": context.channel_account_id, "sender_id": "approver"}
        ],
    )
    values.update(overrides)
    return service.prepare(**values)


def test_approval_and_plan_models_have_generic_context_fields_and_indexes(tmp_path):
    engine = _engine(tmp_path, migrate=False)
    for table in ("ai_action_approvals", "execution_plans"):
        columns = {column["name"] for column in inspect(engine).get_columns(table)}
        assert {
            "channel", "channel_account_id", "conversation_id", "request_message_id",
            "request_sender_id", "approval_message_id", "authorized_identities",
            "temporary_grant_id", "requested_by", "approved_by", "rejected_by",
        } <= columns
        indexes = inspect(engine).get_indexes(table)
        assert any(
            index["column_names"] == ["channel", "channel_account_id", "conversation_id", "request_message_id"]
            for index in indexes
        )


def test_matrix_legacy_rows_are_backfilled_once(tmp_path):
    engine = _engine(tmp_path, migrate=False)
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO ai_action_approvals "
            "(id, action_type, tool_name, room_id, request_event_id, content_sha256, "
            "request_payload) VALUES (:id, 'RELEASE', 'ops.deploy', :room, :event, :hash, :payload)"
        ), {
            "id": "legacy-approval",
            "room": "!ops:matrix.org",
            "event": "$request:matrix.org",
            "hash": "b" * 64,
            "payload": json.dumps({"authorized_matrix_users": ["@alice:matrix.org"]}),
        })
        connection.execute(text(
            "INSERT INTO execution_plans "
            "(id, status, plan_digest, room_id, request_event_id, content_sha256, "
            "authorized_matrix_users, manifest, policy) VALUES "
            "(:id, 'PENDING_APPROVAL', :digest, :room, :event, :hash, :users, :manifest, :policy)"
        ), {
            "id": "legacy-plan",
            "digest": "c" * 64,
            "room": "!ops:matrix.org",
            "event": "$plan:matrix.org",
            "hash": "d" * 64,
            "users": json.dumps(["@bob:matrix.org"]),
            "manifest": json.dumps({}),
            "policy": json.dumps({}),
        })

    first = run_schema_migrations(engine)
    second = run_schema_migrations(engine)
    assert "084_005_approval_context" in first
    assert "084_005_approval_context" not in second
    Session = sessionmaker(bind=engine)
    with Session() as session:
        approval = session.get(AiActionApproval, "legacy-approval")
        plan = session.get(ExecutionPlan, "legacy-plan")
        assert approval.channel == "matrix"
        assert approval.channel_account_id == "default"
        assert approval.conversation_id == "!ops:matrix.org"
        assert approval.request_message_id == "$request:matrix.org"
        assert approval.authorized_identities == [
            {"channel": "matrix", "channel_account_id": "default", "sender_id": "@alice:matrix.org"}
        ]
        assert plan.channel == "matrix"
        assert plan.conversation_id == "!ops:matrix.org"
        assert plan.request_message_id == "$plan:matrix.org"
        assert plan.authorized_identities == [
            {"channel": "matrix", "channel_account_id": "default", "sender_id": "@bob:matrix.org"}
        ]


def test_digests_include_canonical_message_context():
    context = _context()
    action_args = dict(
        action_type="RELEASE", message_context=context, system_name="payment",
        service_name="api", environment="prod", targets=["s1"],
        action_parameters={"version": "1.0.0"}, routing_config_revision="rev-1",
    )
    assert compute_action_digest(**action_args) != compute_action_digest(
        **{**action_args, "message_context": _context(conversation="room-2")}
    )
    plan_args = dict(
        message_context=context, system_name="payment", service_name="api", environment="prod",
        targets=["s1"], steps=[{"step_key": "x", "action_type": "HEALTH_CHECK", "parameters": {}, "dependencies": []}],
        policy={}, routing_config_revision="rev-1", routing_ticket_digest="ticket-1",
    )
    assert compute_plan_digest(**plan_args) != compute_plan_digest(
        **{**plan_args, "message_context": _context(account="secondary")}
    )


@pytest.mark.parametrize("service_kind", ["action", "plan"])
def test_unauthorized_consume_does_not_mutate_pending_request(tmp_path, service_kind):
    engine = _engine(tmp_path)
    Session = sessionmaker(bind=engine)
    with Session() as session:
        context = _context(channel="wechat", account="ops")
        service = ActionApprovalService(session) if service_kind == "action" else ExecutionPlanService(session)
        request, code = (_action_prepare(service, context) if service_kind == "action" else _plan_prepare(service, context))
        approval_context = _context(channel="wechat", account="ops", sender="mallory", message="approval-1")
        result = service.consume(
            request.id,
            code,
            approval_context=approval_context,
            digest=request.action_digest if service_kind == "action" else request.plan_digest,
        )
        assert result is None
        session.refresh(request)
        assert request.status == "PENDING_APPROVAL"
        assert request.rejected_by is None


@pytest.mark.parametrize("channel", ["matrix", "wechat", "telegram"])
@pytest.mark.parametrize("service_kind", ["action", "plan"])
def test_all_supported_channels_can_prepare_and_consume(tmp_path, channel, service_kind):
    engine = _engine(tmp_path)
    Session = sessionmaker(bind=engine)
    with Session() as session:
        request_context = _context(
            channel=channel,
            account="primary",
            conversation=f"conversation-{channel}",
            message=f"request-{channel}",
            sender="requester",
        )
        service = ActionApprovalService(session) if service_kind == "action" else ExecutionPlanService(session)
        request, code = (
            _action_prepare(service, request_context, authorized_identities=[
                {"channel": channel, "channel_account_id": "primary", "sender_id": "approver"}
            ])
            if service_kind == "action"
            else _plan_prepare(service, request_context, authorized_identities=[
                {"channel": channel, "channel_account_id": "primary", "sender_id": "approver"}
            ])
        )
        approval_context = _context(
            channel=channel,
            account="primary",
            conversation=f"conversation-{channel}",
            message=f"approval-{channel}",
            sender="approver",
        )
        consumed = service.consume(
            request.id,
            code,
            approval_context=approval_context,
            digest=request.action_digest if service_kind == "action" else request.plan_digest,
        )
        assert consumed is not None
        assert consumed.approved_by == f"{channel}:primary:approver"
        assert consumed.requested_by == f"{channel}:primary:requester"
        assert consumed.approval_message_id == f"approval-{channel}"


def test_prepare_fails_closed_when_effective_approvers_are_empty(tmp_path):
    engine = _engine(tmp_path)
    Session = sessionmaker(bind=engine)
    with Session() as session:
        with pytest.raises(ValueError, match="authorized"):
            _action_prepare(ActionApprovalService(session), _context(), authorized_identities=[])
        with pytest.raises(ValueError, match="authorized"):
            _plan_prepare(ExecutionPlanService(session), _context(), authorized_identities=[])


def test_approval_tools_execute_schemas_accept_generic_context():
    from app.services.tool_registry import register_builtin_tools, registry

    register_builtin_tools()

    for name in ("ops.approval.execute", "ops.approval.execute_plan"):
        schema = registry.get(name).input_schema
        assert "message_context" in schema["properties"]
        assert schema["properties"]["message_context"]["type"] == "object"


def test_approval_list_filters_generic_conversation_and_returns_context(tmp_path):
    from app.services.tool_adapters.approval_tools import approval_list
    from app.services.tool_context import ToolContext

    engine = _engine(tmp_path)
    Session = sessionmaker(bind=engine)
    context = _context(channel="telegram", account="bot-1", conversation="chat-1")
    other = _context(channel="telegram", account="bot-1", conversation="chat-2")
    with Session() as session:
        service = ActionApprovalService(session)
        _action_prepare(service, context)
        _action_prepare(service, other)
        result = approval_list(
            {"message_context": context.to_dict()},
            ToolContext(auth_type="tool_token"),
            session,
        )
    assert result["total"] == 1
    assert result["items"][0]["message_context"] == context.to_dict()
