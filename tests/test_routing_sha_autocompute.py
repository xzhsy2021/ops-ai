"""routing_resolve_message_target 未传 content_sha256 时自动计算的契约测试。

背景（2026-09-01）：zeroclaw 报"路由票据要求消息 content_sha256，当前工具链
无法生成该校验值"被卡断。MessageContext 强制 64 位 hex 摘要，agent 端本地
计算是多余负担且易错（hex 大小写/编码差异）。修复：resolve 工具未显式传
content_sha256 时自动按 message_text（UTF-8）计算，agent 只需传消息原文。
"""
import hashlib

import pytest

from app.services.tool_adapters import approval_tools
from app.services.tool_registry import register_builtin_tools
from tests.test_multichannel_routing_tools import _identity, _systems

register_builtin_tools()

STRONG_KEY = "routing-autocompute-test-key-0123456789abcdef"


@pytest.fixture(autouse=True)
def _signing_key(monkeypatch):
    monkeypatch.setattr(
        "app.services.qclaw_routing.QCLAW_APPROVAL_SIGNING_KEY",
        STRONG_KEY,
    )


def _ctx():
    from app.services.tool_context import ToolContext

    return ToolContext(auth_type="tool_token", scopes=["ops:read"])


def _systems_kw(approvers):
    """带关键词的系统 fixture：消息含'量化'字样即可命中 system_keyword。"""
    return {
        "crypto-trader": {
            "message_routing": {
                "enabled": True,
                "aliases": ["量化"],
                "keywords": ["量化测试", "量化交易"],
                "priority": 10,
                "approvers": approvers,
            },
            "services": [],
        }
    }


def test_resolve_autocomputes_content_sha256_from_message_text(monkeypatch):
    """未传 content_sha256：自动按消息原文（UTF-8）计算摘要并签发票据。"""
    monkeypatch.setattr(approval_tools, "get_all_systems", lambda: _systems_kw([_identity("matrix", "@ops:example.org", account="default")]))
    message_text = "智能助手AIbot 量化测试环境 使用附件 创建前端发版审批工单 使用ops能力操作"
    result = approval_tools.routing_resolve_message_target(
        {
            "message_text": message_text,
            "room_id": "!room:hubtel.xyz",
            "event_id": "$evt:hubtel.xyz",
            "sender_matrix_id": "@jack.han:hubtel.xyz",
            # 故意不传 content_sha256
        },
        _ctx(),
        None,
    )
    expected = hashlib.sha256(message_text.encode("utf-8")).hexdigest()
    assert result["outcome"] == "RESOLVED"
    assert result["message_context"]["content_sha256"] == expected
    # 票据必须已签发（绑定自动计算的摘要）
    assert result["ticket"]
    assert result["ticket_digest"]


def test_resolve_explicit_content_sha256_still_wins(monkeypatch):
    """显式传 content_sha256 时保持原值（不覆盖），兼容既有调用方。"""
    monkeypatch.setattr(approval_tools, "get_all_systems", lambda: _systems([_identity("matrix", "@ops:example.org", account="default")]))
    explicit = "b" * 64
    result = approval_tools.routing_resolve_message_target(
        {
            "message_text": "量化",
            "room_id": "!room:example.org",
            "event_id": "$event",
            "sender_matrix_id": "@requester:example.org",
            "content_sha256": explicit,
        },
        _ctx(),
        None,
    )
    assert result["message_context"]["content_sha256"] == explicit
