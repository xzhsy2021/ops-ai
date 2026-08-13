from copy import deepcopy

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import approvals as approvals_api
from app.config.systems import get_all_systems, save_system
from app.db.base import Base
from app.db.models import System
from app.services.message_context import MessageContext
from app.services.qclaw_routing import (
    RoutingOutcome,
    compute_routing_revision,
    resolve_message_target,
)
from app.services.tool_adapters import approval_tools
from app.services.tool_context import ToolContext
from app.services.tool_registry import registry


@pytest.fixture(autouse=True)
def _strong_signing_key(monkeypatch):
    monkeypatch.setattr(
        "app.services.qclaw_routing.QCLAW_APPROVAL_SIGNING_KEY",
        "multichannel-routing-test-key-0123456789abcdef",
    )


def _context(channel="matrix", **overrides):
    values = {
        "channel": channel,
        "channel_account_id": "primary",
        "conversation_id": "conversation-1",
        "message_id": "message-1",
        "sender_id": "requester-1",
        "content_sha256": "a" * 64,
    }
    values.update(overrides)
    return MessageContext(**values)


def _identity(channel, sender_id, account="primary"):
    return {
        "channel": channel,
        "channel_account_id": account,
        "sender_id": sender_id,
    }


def _systems(approvers):
    return {
        "crypto-trader": {
            "message_routing": {
                "enabled": True,
                "aliases": ["量化"],
                "keywords": [],
                "priority": 10,
                "approvers": approvers,
            },
            "services": [],
        }
    }


@pytest.mark.parametrize("channel", ["matrix", "wechat", "telegram"])
def test_registered_routing_tool_uses_generic_context_and_token_approvers(
    monkeypatch, channel
):
    context = _context(channel)
    token_approver = _identity(channel, "token-approver")
    configured_approver = _identity(channel, "configured-approver")
    monkeypatch.setattr(approval_tools, "get_all_systems", lambda: _systems([configured_approver]))
    monkeypatch.setattr("app.services.tool_registry.enforce_tool_policy", lambda *args: {})
    monkeypatch.setattr("app.services.tool_registry.record_tool_call_async", lambda **kwargs: "audit-1")
    ctx = ToolContext(
        auth_type="tool_token",
        scopes=["ops:read"],
        channel_bindings=[{
            "channel": channel,
            "channel_account_id": "primary",
            "conversation_id": "conversation-1",
        }],
        approver_identities=[token_approver],
    )

    response = registry.call(
        None,
        "ops.routing.resolve_message_target",
        {"message_text": "量化", "message_context": context.to_dict()},
        ctx,
    )["result"]

    assert response["outcome"] == "RESOLVED"
    assert response["message_context"] == context.to_dict()
    assert response["configured_approver_identities"] == [configured_approver]
    assert response["approver_identities"] == [token_approver]
    assert response["approvers"] == ["token-approver"]
    assert response["approver_actor_keys"] == [f"{channel}:primary:token-approver"]


def test_routing_tool_legacy_matrix_input_normalizes_only_at_handler_boundary(monkeypatch):
    monkeypatch.setattr(approval_tools, "get_all_systems", lambda: _systems(["@ops:example.org"]))
    ctx = ToolContext(auth_type="tool_token", scopes=["ops:read"])
    result = approval_tools.routing_resolve_message_target(
        {
            "message_text": "量化",
            "room_id": "!room:example.org",
            "event_id": "$event",
            "sender_matrix_id": "@requester:example.org",
            "content_sha256": "b" * 64,
        },
        ctx,
        None,
    )
    assert result["message_context"] == {
        "channel": "matrix",
        "channel_account_id": "default",
        "conversation_id": "!room:example.org",
        "message_id": "$event",
        "sender_id": "@requester:example.org",
        "content_sha256": "b" * 64,
    }
    assert result["approver_identities"] == [
        _identity("matrix", "@ops:example.org", account="default")
    ]


def test_legacy_routing_input_requires_stable_sender(monkeypatch):
    monkeypatch.setattr(approval_tools, "get_all_systems", lambda: _systems([]))
    with pytest.raises(HTTPException) as exc:
        approval_tools.routing_resolve_message_target(
            {
                "message_text": "量化",
                "room_id": "!room:example.org",
                "event_id": "$event",
                "content_sha256": "b" * 64,
            },
            ToolContext(auth_type="tool_token", scopes=["ops:read"]),
            None,
        )
    assert exc.value.status_code == 400


@pytest.mark.parametrize(
    "approvers",
    [
        [_identity("telegram", "owner")],
        [{"channel": "signal", "channel_account_id": "primary", "sender_id": "owner"}],
        None,
    ],
)
def test_configured_approvers_without_valid_current_channel_fail_closed(monkeypatch, approvers):
    monkeypatch.setattr(approval_tools, "get_all_systems", lambda: _systems(approvers))
    with pytest.raises(HTTPException) as exc:
        approval_tools.routing_resolve_message_target(
            {"message_text": "量化", "message_context": _context("wechat").to_dict()},
            ToolContext(auth_type="tool_token", scopes=["ops:read"]),
            None,
        )
    assert exc.value.status_code == 403


def test_service_approvers_override_system_and_revision_is_order_independent():
    a = _identity("wechat", "owner-a")
    b = _identity("telegram", "owner-b")
    systems = list(_systems([a]).values())
    systems[0]["name"] = "crypto-trader"
    systems[0]["services"] = [{
        "name": "strategy",
        "template_variables": {
            "message_routing": {
                "enabled": True,
                "aliases": ["策略"],
                "approvers": [b],
            }
        },
    }]
    decision = resolve_message_target("策略", systems)
    assert decision.outcome == RoutingOutcome.RESOLVED
    assert decision.approvers == (b,)

    reordered = deepcopy(systems)
    reordered[0]["message_routing"]["approvers"] = list(reversed([a, b]))
    systems[0]["message_routing"]["approvers"] = [a, b]
    assert compute_routing_revision(systems) == compute_routing_revision(reordered)


def test_message_routing_api_model_normalizes_legacy_and_rejects_invalid_identity():
    config = approvals_api.MessageRoutingConfig(approvers=["@legacy:example.org"])
    assert config.model_dump()["approvers"] == [
        _identity("matrix", "@legacy:example.org", account="default")
    ]
    with pytest.raises(ValidationError):
        approvals_api.MessageRoutingConfig(
            approvers=[{"channel": "signal", "channel_account_id": "primary", "sender_id": "owner"}]
        )


def test_save_and_read_system_normalizes_routing_approvers(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    local_session = sessionmaker(bind=engine)
    monkeypatch.setattr("app.db.base.SessionLocal", local_session)
    try:
        assert save_system(
            "crypto-trader",
            {
                "display_name": "Crypto Trader",
                "message_routing": {
                    "enabled": True,
                    "approvers": ["@legacy:example.org"],
                },
            },
        )
        with local_session() as db:
            stored = db.query(System).filter_by(name="crypto-trader").one()
            assert stored.message_routing["approvers"] == [
                _identity("matrix", "@legacy:example.org", account="default")
            ]
        assert get_all_systems()["crypto-trader"]["message_routing"]["approvers"] == [
            _identity("matrix", "@legacy:example.org", account="default")
        ]
    finally:
        engine.dispose()
