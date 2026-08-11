"""测试 qclaw 路由解析和签名票据。"""
from copy import deepcopy

import pytest
from datetime import datetime, timezone

from app.services.message_context import MessageContext
from app.services.qclaw_routing import (
    RoutingOutcome,
    resolve_message_target,
    issue_ticket,
    verify_ticket,
    normalize_text,
    compute_routing_revision,
)


# ── 测试用的系统配置 ──

SYSTEMS = [
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
        "name": "dovo",
        "message_routing": {
            "enabled": True,
            "aliases": ["印尼", "idn"],
            "keywords": ["dovo deploy", "印尼游戏"],
            "priority": 50,
        },
        "services": [],
    },
    {
        "name": "disabled-system",
        "message_routing": {
            "enabled": False,
            "aliases": ["禁用"],
            "keywords": ["禁用关键词"],
            "priority": 200,
        },
        "services": [],
    },
]


# ── 路由解析测试 ──

def test_exact_system_name_wins_over_keyword():
    """精确系统名匹配优先于关键词匹配。"""
    # "crypto-trader" 既是系统名也包含关键词 "crypto deploy"
    decision = resolve_message_target("crypto-trader", SYSTEMS)
    assert decision.outcome == RoutingOutcome.RESOLVED
    assert decision.system_name == "crypto-trader"
    assert decision.matched_by == "system_name"


def test_exact_system_alias_resolves():
    """精确匹配系统别名。"""
    decision = resolve_message_target("量化", SYSTEMS)
    assert decision.outcome == RoutingOutcome.RESOLVED
    assert decision.system_name == "crypto-trader"
    assert decision.matched_by == "system_alias"


def test_unique_service_alias_resolves_parent_system():
    """唯一服务别名解析到父系统。"""
    decision = resolve_message_target("交易接口", SYSTEMS)
    assert decision.outcome == RoutingOutcome.RESOLVED
    assert decision.system_name == "crypto-trader"
    assert decision.service_name == "trader-api"
    assert decision.matched_by == "service_alias"


def test_unique_highest_priority_keyword_resolves():
    """唯一最高优先级关键词匹配。"""
    # "btc strategy" 只在 crypto-trader (priority=100) 中
    decision = resolve_message_target("请执行 btc strategy 更新", SYSTEMS)
    assert decision.outcome == RoutingOutcome.RESOLVED
    assert decision.system_name == "crypto-trader"
    assert decision.matched_by == "system_keyword"


def test_equal_priority_keyword_match_is_ambiguous():
    """同优先级关键词多匹配为 AMBIGUOUS。"""
    systems = [
        {
            "name": "sys-a",
            "message_routing": {
                "enabled": True,
                "aliases": [],
                "keywords": ["共享关键词"],
                "priority": 100,
            },
            "services": [],
        },
        {
            "name": "sys-b",
            "message_routing": {
                "enabled": True,
                "aliases": [],
                "keywords": ["共享关键词"],
                "priority": 100,
            },
            "services": [],
        },
    ]
    decision = resolve_message_target("共享关键词部署", systems)
    assert decision.outcome == RoutingOutcome.AMBIGUOUS
    assert len(decision.candidates) == 2


def test_unknown_message_is_unmatched():
    """未知消息为 UNMATCHED。"""
    decision = resolve_message_target("完全无关的消息内容", SYSTEMS)
    assert decision.outcome == RoutingOutcome.UNMATCHED


def test_disabled_routing_entry_is_ignored():
    """禁用的路由配置被忽略。"""
    # "禁用关键词" 只在 disabled-system 中，但该系统路由被禁用
    decision = resolve_message_target("禁用关键词", SYSTEMS)
    assert decision.outcome == RoutingOutcome.UNMATCHED


# ── 签名票据测试 ──

def _message_context(channel: str = "matrix", **overrides) -> MessageContext:
    values = {
        "channel": channel,
        "channel_account_id": "primary",
        "conversation_id": "conversation-1",
        "message_id": "message-1",
        "sender_id": "sender-1",
        "content_sha256": "a" * 64,
    }
    values.update(overrides)
    return MessageContext(**values)


@pytest.mark.parametrize("channel", ["matrix", "wechat", "telegram"])
def test_ticket_is_bound_to_complete_message_context(channel):
    revision = compute_routing_revision(SYSTEMS)
    context = _message_context(channel)
    ticket = issue_ticket(
        message_context=context,
        system_name="crypto-trader",
        service_name=None,
        routing_config_revision=revision,
    )
    assert ticket.ticket
    assert ticket.digest
    assert ticket.expires_at > datetime.now(timezone.utc)

    ok = verify_ticket(
        ticket.ticket,
        expected_message_context=context,
        expected_system_name="crypto-trader",
        expected_service_name=None,
        expected_revision=revision,
    )
    assert ok is True


def test_ticket_expires_after_fifteen_minutes():
    revision = compute_routing_revision(SYSTEMS)
    ticket = issue_ticket(
        message_context=_message_context(),
        system_name="crypto-trader",
        service_name=None,
        routing_config_revision=revision,
    )
    now = datetime.now(timezone.utc)
    delta = ticket.expires_at - now
    assert 890 < delta.total_seconds() < 910


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("channel", "telegram"),
        ("channel_account_id", "secondary"),
        ("conversation_id", "conversation-2"),
        ("message_id", "message-2"),
        ("sender_id", "sender-2"),
        ("content_sha256", "b" * 64),
    ],
)
def test_ticket_rejects_cross_context_tampering(field, value):
    revision = compute_routing_revision(SYSTEMS)
    context = _message_context()
    ticket = issue_ticket(
        message_context=context,
        system_name="crypto-trader",
        service_name=None,
        routing_config_revision=revision,
    )
    other = _message_context(**{field: value})
    ok = verify_ticket(
        ticket.ticket,
        expected_message_context=other,
        expected_system_name="crypto-trader",
        expected_service_name=None,
        expected_revision=revision,
    )
    assert ok is False


def test_ticket_rejects_tampered_signature():
    """篡改签名后验证失败。"""
    revision = compute_routing_revision(SYSTEMS)
    ticket = issue_ticket(
        message_context=_message_context(),
        system_name="crypto-trader",
        service_name=None,
        routing_config_revision=revision,
    )
    # 篡改签名
    parts = ticket.ticket.rsplit(".", 1)
    tampered = f"{parts[0]}.deadbeef"
    ok = verify_ticket(
        tampered,
        expected_message_context=_message_context(),
        expected_system_name="crypto-trader",
        expected_service_name=None,
        expected_revision=revision,
    )
    assert ok is False


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("aliases", ["新策略服务"]),
        (
            "approvers",
            [{
                "channel": "wechat",
                "channel_account_id": "primary",
                "sender_id": "owner-2",
            }],
        ),
    ],
)
def test_disabled_service_routing_changes_revision_and_invalidates_ticket(
    field, replacement
):
    systems = [{
        "name": "crypto-trader",
        "message_routing": {"enabled": True},
        "services": [{
            "name": "strategy",
            "template_variables": {
                "message_routing": {
                    "enabled": False,
                    "aliases": ["策略服务"],
                    "keywords": ["strategy deploy"],
                    "priority": 50,
                    "approvers": [{
                        "channel": "wechat",
                        "channel_account_id": "primary",
                        "sender_id": "owner-1",
                    }],
                }
            },
        }],
    }]
    decision = resolve_message_target("策略服务", systems)
    assert decision.outcome == RoutingOutcome.RESOLVED
    assert decision.service_name == "strategy"

    revision = compute_routing_revision(systems)
    context = _message_context("wechat")
    ticket = issue_ticket(
        message_context=context,
        system_name=decision.system_name,
        service_name=decision.service_name,
        routing_config_revision=revision,
    )
    changed = deepcopy(systems)
    changed[0]["services"][0]["template_variables"]["message_routing"][field] = replacement
    changed_revision = compute_routing_revision(changed)

    assert changed_revision != revision
    assert not verify_ticket(
        ticket.ticket,
        expected_message_context=context,
        expected_system_name=decision.system_name,
        expected_service_name=decision.service_name,
        expected_revision=changed_revision,
    )


def test_routing_revision_is_independent_of_system_and_service_order():
    systems = [
        {
            "name": "system-b",
            "message_routing": {"enabled": True, "aliases": ["b"]},
            "services": [
                {"name": "service-z", "template_variables": {"message_routing": {}}},
                {"name": "service-a", "template_variables": {"message_routing": {}}},
            ],
        },
        {
            "name": "system-a",
            "message_routing": {"enabled": True, "aliases": ["a"]},
            "services": [],
        },
    ]
    reordered = list(reversed(deepcopy(systems)))
    reordered[1]["services"].reverse()

    assert compute_routing_revision(systems) == compute_routing_revision(reordered)


def test_core_ticket_api_rejects_legacy_matrix_context_mapping():
    revision = compute_routing_revision(SYSTEMS)
    legacy_context = {
        "room_id": "!room:example.org",
        "event_id": "$event",
        "sender_matrix_id": "@requester:example.org",
        "content_sha256": "a" * 64,
    }
    with pytest.raises(ValueError):
        issue_ticket(
            message_context=legacy_context,
            system_name="crypto-trader",
            service_name=None,
            routing_config_revision=revision,
        )

    ticket = issue_ticket(
        message_context=_message_context(),
        system_name="crypto-trader",
        service_name=None,
        routing_config_revision=revision,
    )
    assert not verify_ticket(
        ticket.ticket,
        expected_message_context=legacy_context,
        expected_system_name="crypto-trader",
        expected_service_name=None,
        expected_revision=revision,
    )
