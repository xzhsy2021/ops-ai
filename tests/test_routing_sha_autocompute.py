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
            "room_id": "!room:example.com",
            "event_id": "$evt:example.com",
            "sender_matrix_id": "@approver:example.com",
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
    # 自引导字段：告诉 agent 下一步同一轮内立即 prepare_plan
    assert "prepare_plan" in result["next_step"]
    assert "message_context" in result["next_step"]


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
                "conversation_id": "!room:example.com",
                "message_id": "$evt:example.com",
                "sender_id": "@approver:example.com",
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


def test_resolve_ignores_garbage_digest_and_autocomputes(monkeypatch):
    """调用方误把附件路径/文件名当 content_sha256 传（zeroclaw 实测案例）：
    OPS 忽略非法值并自动按原文补算，不再 400 卡断。"""
    monkeypatch.setattr(approval_tools, "get_all_systems", lambda: _systems_kw([_identity("matrix", "@ops:example.org", account="default")]))
    message_text = "智能助手AIbot 量化测试环境 使用附件 创建前端发版审批工单 使用ops能力操作"
    garbage = [
        "D:\\zeroclaw\\workspace\\matrix_files\\xxx_crypto-trader-web.tar.gz",
        "crypto-trader-web.tar.gz",
        "f94140bbd39cfd9f",  # 截断的包校验值
        "sha256:f94140bb",  # 带前缀
    ]
    for bad in garbage:
        result = approval_tools.routing_resolve_message_target(
            {
                "message_text": message_text,
                "message_context": {
                    "channel": "matrix",
                    "channel_account_id": "default",
                    "conversation_id": "!room:example.com",
                    "message_id": "$evt:example.com",
                    "sender_id": "@approver:example.com",
                    "content_sha256": bad,  # 垃圾值
                },
            },
            _ctx(),
            None,
        )
        expected = hashlib.sha256(message_text.encode("utf-8")).hexdigest()
        assert result["outcome"] == "RESOLVED", f"garbage digest broke resolve: {bad}"
        assert result["message_context"]["content_sha256"] == expected
        assert result["ticket"]


def test_prepare_plan_ignores_garbage_digest_and_backfills_from_ticket(monkeypatch):
    """prepare_plan 收到垃圾摘要同样忽略并从票据反填（端到端不卡断）。"""
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
            "room_id": "!room:example.com",
            "event_id": "$evt:example.com",
            "sender_matrix_id": "@approver:example.com",
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
        # 消息里带垃圾摘要（附件路径标识），模拟 zeroclaw 误传场景
        ctx_dict = dict(resolved["message_context"])
        ctx_dict["content_sha256"] = "D:\\zeroclaw\\workspace\\matrix_files\\abc.tar.gz"
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
        assert "execute_plan" in prepared["next_step"]
    finally:
        db.close()


def test_prepare_plan_backfills_missing_sha256_from_signed_ticket(monkeypatch):
    """prepare_plan 消费端：缺 sha 的 context 从签名票据反填真实摘要。

    agent 拷贝 resolve 输出时丢掉摘要字段也能通过——摘要以服务端签发票据
    为准（比信任调用方更安全），票据校验仍按完整上下文硬比对。
    """
    from app.db.base import Base
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    monkeypatch.setattr(approval_tools, "get_all_systems", lambda: _systems_kw([_identity("matrix", "@ops:example.org", account="default")]))
    monkeypatch.setattr(
        "app.services.tool_adapters.approval_tools._routing_systems",
        lambda: list(_systems_kw([_identity("matrix", "@ops:example.org", account="default")]).values()),
    )
    resolved = approval_tools.routing_resolve_message_target(
        {
            "message_text": "智能助手AIbot 量化测试环境 部署",
            "room_id": "!room:example.com",
            "event_id": "$evt:example.com",
            "sender_matrix_id": "@approver:example.com",
        },
        _ctx(),
        None,
    )
    assert resolved["ticket"]

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
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
