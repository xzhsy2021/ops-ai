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


def test_resolve_autocomputes_when_message_context_omits_sha256(monkeypatch):
    """message_context 路径同样免 sha：缺 content_sha256 时自动按原文补算。

    zeroclaw 实际场景（2026-09-01 卡断复现）：它按六字段 message_context
    调用但无法提供摘要。修复后 resolve 应自动补算并签发票据。
    """
    monkeypatch.setattr(approval_tools, "get_all_systems", lambda: _systems_kw([_identity("matrix", "@ops:example.org", account="default")]))
    message_text = "智能助手AIbot 量化测试环境 使用附件 创建前端发版审批工单 使用ops能力操作"
    result = approval_tools.routing_resolve_message_target(
        {
            "message_text": message_text,
            "message_context": {
                "channel": "matrix",
                "channel_account_id": "default",
                "conversation_id": "!room:hubtel.xyz",
                "message_id": "$evt:hubtel.xyz",
                "sender_id": "@jack.han:hubtel.xyz",
                # 故意缺 content_sha256
            },
        },
        _ctx(),
        None,
    )
    expected = hashlib.sha256(message_text.encode("utf-8")).hexdigest()
    assert result["outcome"] == "RESOLVED"
    assert result["message_context"]["content_sha256"] == expected
    assert result["ticket"]


def test_prepare_plan_backfills_sha256_from_signed_ticket(monkeypatch, tmp_path):
    """prepare_plan 消费端：缺 sha 的 context 从签名票据反填真实摘要。

    agent 把 resolve 输出的 message_context 拷贝时丢掉摘要字段也能通过——
    摘要以服务端签发票据为准（比信任调用方更安全），票据校验仍按完整
    上下文硬比对。
    """
    from app.db.base import Base
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    monkeypatch.setattr(approval_tools, "get_all_systems", lambda: _systems_kw([_identity("matrix", "@ops:example.org", account="default")]))
    monkeypatch.setattr(
        "app.services.tool_adapters.approval_tools._routing_systems",
        lambda: list(_systems_kw([_identity("matrix", "@ops:example.org", account="default")]).values()),
    )
    message_text = "智能助手AIbot 量化测试环境 部署"
    resolved = approval_tools.routing_resolve_message_target(
        {
            "message_text": message_text,
            "room_id": "!room:hubtel.xyz",
            "event_id": "$evt:hubtel.xyz",
            "sender_matrix_id": "@jack.han:hubtel.xyz",
        },
        _ctx(),
        None,
    )
    assert resolved["ticket"]

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        # 丢失 content_sha256 的 message_context（模拟 agent 拷贝丢失）
        ctx_dict = dict(resolved["message_context"])
        ctx_dict.pop("content_sha256")
        prepared = approval_tools.approval_prepare_plan(
            args={
                "message_context": ctx_dict,
                "routing_ticket": resolved["ticket"],
                "system_name": resolved["system_name"],
                "service_name": resolved["service_name"],
                "environment": "test",
                "steps": [
                    {
                        "step_key": "health",
                        "action_type": "HEALTH_CHECK",
                        "parameters": {"targets": ["cc-test2"]},
                    },
                ],
                "policy": {"continue_on_error": False},
            },
            ctx=_ctx(),
            db=db,
        )
        assert prepared["plan_id"]
        assert prepared["status"] == "PENDING_APPROVAL"
    finally:
        db.close()
