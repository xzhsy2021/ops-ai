import asyncio
import base64
import hashlib
import hmac
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api import approvals as approvals_api
from app.api import deploy_v2
from app.services import qclaw_routing
from app.services.message_context import MessageContext
from app.services.qclaw_routing import (
    RoutingOutcome,
    compute_routing_revision,
    issue_ticket,
    resolve_message_target,
    verify_ticket,
)
from app.services.tool_adapters import approval_tools
from app.services.tool_context import ToolContext
from app.services.tool_registry import registry
from scripts import preflight_start_check


STRONG_KEY = "task5-review-signing-key-0123456789abcdef"


def _identity(channel="wechat", sender_id="owner-1"):
    return {
        "channel": channel,
        "channel_account_id": "primary",
        "sender_id": sender_id,
    }


def _message_context() -> MessageContext:
    return MessageContext(
        channel="wechat",
        channel_account_id="primary",
        conversation_id="conversation-1",
        message_id="message-1",
        sender_id="requester-1",
        content_sha256="a" * 64,
    )


def _systems():
    return {
        "crypto-trader": {
            "message_routing": {
                "enabled": True,
                "aliases": ["量化"],
                "approvers": [_identity()],
            },
            "services": [{
                "name": "strategy",
                "template_variables": {"message_routing": {}},
            }],
        }
    }


def _ticket(monkeypatch):
    monkeypatch.setattr(qclaw_routing, "QCLAW_APPROVAL_SIGNING_KEY", STRONG_KEY)
    systems = [{**value, "name": name} for name, value in _systems().items()]
    revision = compute_routing_revision(systems)
    ticket = issue_ticket(
        _message_context(),
        "crypto-trader",
        "strategy",
        revision,
    )
    return ticket, revision


def test_all_prepare_tools_verify_ticket_and_derive_persisted_ticket_fields(monkeypatch):
    ticket, revision = _ticket(monkeypatch)
    captured = []
    approver_calls = []

    class FakeActionApprovalService:
        def __init__(self, db):
            pass

        def prepare(self, **kwargs):
            captured.append(kwargs)
            return SimpleNamespace(
                id="approval-1",
                action_digest="digest-1",
                expires_at=None,
                status="PENDING_APPROVAL",
                action_type=kwargs["action_type"],
            ), "12345678"

    class FakeExecutionPlanService:
        def __init__(self, db):
            pass

        def prepare(self, **kwargs):
            captured.append(kwargs)
            return SimpleNamespace(
                id="plan-1",
                plan_digest="plan-digest",
                status="PENDING_APPROVAL",
                expires_at=None,
                system_name=kwargs["system_name"],
                service_name=kwargs["service_name"],
                environment=kwargs["environment"],
                targets=kwargs["targets"],
                steps=[],
                temporary_grant_id=None,
            ), "87654321"

    monkeypatch.setattr(approval_tools, "get_all_systems", _systems)
    monkeypatch.setattr(approval_tools, "ActionApprovalService", FakeActionApprovalService)
    monkeypatch.setattr(approval_tools, "ExecutionPlanService", FakeExecutionPlanService)

    class FakeGrantService:
        """Task 8 集成后 prepare 会查询临时授权；本测试只关心票据字段，用 fake 隔离。"""
        def __init__(self, db):
            pass

        def is_self_approval_allowed(self, **kwargs):
            return False

        def get_active_grant(self, **kwargs):
            return None

    monkeypatch.setattr(
        "app.services.temporary_approval.TemporaryApprovalService",
        FakeGrantService,
    )
    monkeypatch.setattr(
        approval_tools,
        "_lookup_approvers",
        lambda *args, **kwargs: approver_calls.append(kwargs) or ["owner-1"],
    )
    monkeypatch.setattr(
        approval_tools,
        "_freeze_file_upload_parameters",
        lambda *args, **kwargs: (
            {"remote_path": "/srv/app.tar.gz", "overwrite": False},
            "app.tar.gz",
            "b" * 64,
            123,
            "/srv/app.tar.gz",
        ),
    )

    base = {
        "message_context": _message_context().to_dict(),
        "routing_ticket": ticket.ticket,
        "system_name": "crypto-trader",
        "service_name": "strategy",
        "environment": "test",
    }
    ctx = ToolContext(auth_type="tool_token", scopes=["ops:read"])
    approval_tools.approval_prepare_service_control(
        {**base, "control_action": "restart", "targets": ["server-1"]}, ctx, None
    )
    approval_tools.approval_prepare_file_upload(
        {
            **base,
            "targets": ["server-1"],
            "action_parameters": {"remote_path": "/srv/app.tar.gz"},
        },
        ctx,
        None,
    )
    approval_tools.approval_prepare_plan(
        {**base, "targets": ["server-1"], "steps": []}, ctx, None
    )

    expected_digest = hashlib.sha256(ticket.ticket.encode("utf-8")).hexdigest()
    assert len(captured) == 3
    assert all(item["routing_config_revision"] == revision for item in captured)
    assert all(item["routing_ticket_digest"] == expected_digest for item in captured)
    for item in captured:
        mc = item["message_context"]
        assert mc.channel == "wechat"
        assert mc.channel_account_id == "primary"
        assert mc.conversation_id == "conversation-1"
        assert mc.message_id == "message-1"
        assert mc.sender_id == "requester-1"
        assert mc.content_sha256 == "a" * 64
    assert approver_calls == [
        {"channel": "wechat", "channel_account_id": "primary"},
        {"channel": "wechat", "channel_account_id": "primary"},
        {"channel": "wechat", "channel_account_id": "primary"},
    ]
    for tool_name in (
        "ops.approval.prepare_service_control",
        "ops.approval.prepare_file_upload",
        "ops.approval.prepare_plan",
    ):
        schema = registry.get(tool_name).input_schema
        assert "routing_ticket" in schema["required"]
        assert "message_context" in schema["properties"]
        assert "routing_config_revision" not in schema["properties"]
        assert "routing_ticket_digest" not in schema["properties"]


def test_prepare_rejects_bad_ticket_before_creating_approval(monkeypatch):
    monkeypatch.setattr(qclaw_routing, "QCLAW_APPROVAL_SIGNING_KEY", STRONG_KEY)
    monkeypatch.setattr(approval_tools, "get_all_systems", _systems)

    class MustNotCreate:
        def __init__(self, db):
            raise AssertionError("approval service was created before ticket validation")

    monkeypatch.setattr(approval_tools, "ActionApprovalService", MustNotCreate)
    with pytest.raises(HTTPException) as exc:
        approval_tools.approval_prepare_service_control(
            {
                "message_context": _message_context().to_dict(),
                "routing_ticket": "invalid",
                "system_name": "crypto-trader",
                "service_name": "strategy",
                "environment": "test",
                "control_action": "restart",
                "targets": ["server-1"],
            },
            ToolContext(auth_type="tool_token", scopes=["ops:read"]),
            None,
        )
    assert exc.value.status_code in {400, 403}


def test_system_api_round_trips_structured_system_and_service_approvers(monkeypatch):
    store = {}

    def save_system(name, value):
        store[name] = deepcopy(value)
        return True

    monkeypatch.setattr("app.config.systems.save_system", save_system)
    monkeypatch.setattr("app.config.systems.get_system_by_name", lambda name: deepcopy(store.get(name)))
    monkeypatch.setattr(deploy_v2, "require_auth", lambda request, db: {"is_admin": True})
    monkeypatch.setattr(deploy_v2, "audit", lambda *args, **kwargs: None)
    request = SimpleNamespace(state=SimpleNamespace(username="admin"))
    system_approver = _identity("wechat", "system-owner")
    service_approver = _identity("telegram", "service-owner")
    services = [{
        "name": "strategy",
        "template_variables": {
            "message_routing": {
                "enabled": False,
                "aliases": ["策略"],
                "approvers": [service_approver],
            }
        },
    }]

    asyncio.run(deploy_v2.create_system_v2(
        {
            "name": "crypto-trader",
            "message_routing": {"enabled": True, "approvers": [system_approver]},
            "services": services,
        },
        request,
        None,
    ))
    updated = _identity("telegram", "system-owner-2")
    asyncio.run(deploy_v2.update_system_v2(
        "crypto-trader",
        {
            "message_routing": {"enabled": True, "approvers": [updated]},
            "services": services,
        },
        request,
        None,
    ))
    response = asyncio.run(deploy_v2.get_system_v2("crypto-trader", request))["data"]

    assert response["message_routing"]["approvers"] == [updated]
    # 服务级 message_routing 已收敛到系统级（4ee754b）：服务只保留名称，
    # 服务级路由不再往返保留。
    assert "message_routing" not in response["services"][0].get("template_variables", {})


def test_system_alias_collision_is_ambiguous_and_order_independent():
    systems = [
        {"name": "system-b", "message_routing": {"enabled": True, "aliases": ["共享"]}},
        {"name": "system-a", "message_routing": {"enabled": True, "aliases": ["共享"]}},
    ]
    first = resolve_message_target("共享", systems)
    second = resolve_message_target("共享", list(reversed(systems)))
    assert first.outcome == RoutingOutcome.AMBIGUOUS
    assert second.outcome == RoutingOutcome.AMBIGUOUS
    assert first.candidates == second.candidates == ("system-a", "system-b")
    assert first.routing_config_revision == second.routing_config_revision


def test_verify_ticket_rejects_signed_non_object_payload(monkeypatch):
    monkeypatch.setattr(qclaw_routing, "QCLAW_APPROVAL_SIGNING_KEY", STRONG_KEY)
    payload_b64 = base64.urlsafe_b64encode(json.dumps(["not", "an", "object"]).encode()).decode()
    signature = hmac.new(STRONG_KEY.encode(), payload_b64.encode(), hashlib.sha256).hexdigest()
    assert not verify_ticket(
        f"{payload_b64}.{signature}",
        _message_context(),
        "crypto-trader",
        "strategy",
        "revision",
    )


@pytest.mark.parametrize("endpoint", ["system"])
def test_routing_update_returns_500_when_save_fails(monkeypatch, endpoint):
    system = {
        "message_routing": {},
        "services": [{"name": "strategy", "template_variables": {}}],
    }
    monkeypatch.setattr(approvals_api, "get_system_by_name", lambda name: deepcopy(system))
    monkeypatch.setattr(approvals_api, "save_system", lambda *args: False)
    monkeypatch.setattr(approvals_api, "require_admin", lambda request, db: {"is_admin": True})
    config = approvals_api.MessageRoutingConfig(enabled=True, approvers=[_identity()])
    with pytest.raises(HTTPException) as exc:
        if endpoint == "system":
            approvals_api.update_system_routing("crypto-trader", config, request=object(), db=None)
        else:
            approvals_api.update_service_routing(
                "crypto-trader", "strategy", config, request=object(), db=None
            )
    assert exc.value.status_code == 500


def test_signing_key_fails_closed_and_preflight_reports_missing_or_weak_key(monkeypatch):
    monkeypatch.setattr(qclaw_routing, "QCLAW_APPROVAL_SIGNING_KEY", "")
    with pytest.raises(RuntimeError):
        issue_ticket(_message_context(), "crypto-trader", "strategy", "revision")

    missing = preflight_start_check._approval_signing_key_check({})
    weak = preflight_start_check._approval_signing_key_check(
        {"APPROVAL_SIGNING_KEY": "dev-fallback-key-do-not-use-in-production"}
    )
    strong = preflight_start_check._approval_signing_key_check(
        {"APPROVAL_SIGNING_KEY": STRONG_KEY}
    )
    assert missing.status == "error"
    assert weak.status == "error"
    assert strong.status == "ok"
