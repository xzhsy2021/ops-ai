"""message_context 与顶层「兼容字段」并存时的合并语义。

背景（2026-09-11 17:33:01 实测）：agent 调 ops.routing.resolve_message_target 时
同时传了 message_context 与 room_id / event_id / sender_matrix_id，三者取值与
message_context 完全一致，却因旧规则「并存即 400」被卡断，白跑一轮后重试才成功。
新规则：兼容字段一致 → 合并放行；真冲突 → 400 并指名冲突字段。

覆盖：
1. 原始载荷（一致冗余）不再 400。
2. 冲突房间/发起人 → 400 且错误信息指名字段。
3. 摘要可冗余：context 缺摘要时用顶层合法摘要补入；两者都合法时以 context 为准。
4. prepare_plan 消费端同规则。
5. resolve 显式返回 room_scope_ok，未授权房间在 next_step 里直接点明会被 403。
"""
import hashlib

import pytest
from fastapi import HTTPException

from app.services.tool_adapters import approval_tools
from app.services.tool_registry import register_builtin_tools
from tests.test_multichannel_routing_tools import _identity
from tests.test_routing_sha_autocompute import (
    STRONG_KEY,
    _ctx,
    _seed_test_env,
    _systems_kw,
)

register_builtin_tools()

# 17:33:01 那次失败调用的原文
MSG = "智能助手AIbot: token已授权，服务已配置映射，再次检查链路"
# 带路由关键词（"量化测试"），用于需要 RESOLVED 的用例
MSG_HIT = "智能助手AIbot 量化测试环境 检查链路"
ROOM = "!riQvnhtyzunVoYIoXp:hubtel.xyz"
EVENT = "$oUJJkzHcKg0QPADcFiG4Jt4_Gjn6hnTFKb3O2A79dM4"
SENDER = "@jack.han:hubtel.xyz"


@pytest.fixture(autouse=True)
def _signing_key(monkeypatch):
    monkeypatch.setattr(
        "app.services.qclaw_routing.QCLAW_APPROVAL_SIGNING_KEY",
        STRONG_KEY,
    )


def _message_context(message_text, *, digest=None, conversation_id=ROOM, sender_id=SENDER):
    return {
        "channel": "matrix",
        "channel_account_id": "default",
        "conversation_id": conversation_id,
        "message_id": EVENT,
        "sender_id": sender_id,
        "content_sha256": digest
        if digest is not None
        else hashlib.sha256(message_text.encode("utf-8")).hexdigest(),
    }


def _approver():
    return _identity("matrix", SENDER, account="default")


def _systems_with_rooms(rooms):
    systems = _systems_kw([_approver()])
    systems["crypto-trader"]["message_routing"]["rooms"] = rooms
    return systems


def test_resolve_accepts_redundant_legacy_fields(monkeypatch):
    """回归 17:33:01：兼容字段与 message_context 取值一致时不再 400。"""
    monkeypatch.setattr(approval_tools, "get_all_systems", lambda: _systems_kw([_approver()]))
    context = _message_context(MSG)
    result = approval_tools.routing_resolve_message_target(
        {
            "message_text": MSG,
            "message_context": dict(context),
            "room_id": context["conversation_id"],
            "event_id": context["message_id"],
            "sender_matrix_id": context["sender_id"],
        },
        _ctx(),
        None,
    )
    assert result["message_context"]["conversation_id"] == ROOM
    assert result["message_context"]["message_id"] == EVENT
    assert result["message_context"]["sender_id"] == SENDER


def test_resolve_keeps_context_values_after_merge(monkeypatch):
    """合并后签发的票据绑定的是 message_context 的上下文（逐字段核对）。"""
    monkeypatch.setattr(approval_tools, "get_all_systems", lambda: _systems_kw([_approver()]))
    context = _message_context(MSG_HIT)
    result = approval_tools.routing_resolve_message_target(
        {
            "message_text": MSG_HIT,
            "message_context": dict(context),
            "room_id": ROOM,
            "event_id": EVENT,
            "sender_matrix_id": SENDER,
        },
        _ctx(),
        None,
    )
    assert result["outcome"] == "RESOLVED"
    assert result["message_context"] == context
    assert result["ticket"]


def test_resolve_rejects_conflicting_legacy_room(monkeypatch):
    """兼容字段与 message_context 真冲突时才拒绝，并指名冲突字段。"""
    monkeypatch.setattr(approval_tools, "get_all_systems", lambda: _systems_kw([_approver()]))
    with pytest.raises(HTTPException) as exc:
        approval_tools.routing_resolve_message_target(
            {
                "message_text": MSG,
                "message_context": _message_context(MSG),
                "room_id": "!other-room:hubtel.xyz",
            },
            _ctx(),
            None,
        )
    assert exc.value.status_code == 400
    detail = str(exc.value.detail)
    assert "room_id" in detail and "conversation_id" in detail


def test_resolve_rejects_conflicting_legacy_sender(monkeypatch):
    monkeypatch.setattr(approval_tools, "get_all_systems", lambda: _systems_kw([_approver()]))
    with pytest.raises(HTTPException) as exc:
        approval_tools.routing_resolve_message_target(
            {
                "message_text": MSG,
                "message_context": _message_context(MSG),
                "sender_matrix_id": "@someone-else:hubtel.xyz",
            },
            _ctx(),
            None,
        )
    assert exc.value.status_code == 400
    assert "sender_matrix_id" in str(exc.value.detail)


def test_resolve_top_level_digest_fills_unbound_context(monkeypatch):
    """message_context 未带摘要时，顶层兼容字段的合法摘要被补入（不重算）。"""
    monkeypatch.setattr(approval_tools, "get_all_systems", lambda: _systems_kw([_approver()]))
    digest = "c" * 64
    result = approval_tools.routing_resolve_message_target(
        {
            "message_text": MSG_HIT,
            "message_context": _message_context(MSG_HIT, digest=""),
            "content_sha256": digest,
        },
        _ctx(),
        None,
    )
    assert result["outcome"] == "RESOLVED"
    assert result["message_context"]["content_sha256"] == digest


def test_resolve_context_digest_wins_over_top_level(monkeypatch):
    """两处都带合法摘要时不报错，以 message_context 内的为准。"""
    monkeypatch.setattr(approval_tools, "get_all_systems", lambda: _systems_kw([_approver()]))
    context = _message_context(MSG_HIT)
    result = approval_tools.routing_resolve_message_target(
        {
            "message_text": MSG_HIT,
            "message_context": dict(context),
            "content_sha256": "d" * 64,
        },
        _ctx(),
        None,
    )
    assert result["message_context"]["content_sha256"] == context["content_sha256"]


def test_resolve_reports_room_scope_ok(monkeypatch):
    """当前会话在授权房间内：room_scope_ok=True，next_step 不含 403 警告。"""
    rooms = [
        {"channel": "matrix", "channel_account_id": "default", "conversation_id": ROOM}
    ]
    monkeypatch.setattr(approval_tools, "get_all_systems", lambda: _systems_with_rooms(rooms))
    result = approval_tools.routing_resolve_message_target(
        {"message_text": MSG_HIT, "message_context": _message_context(MSG_HIT)},
        _ctx(),
        None,
    )
    assert result["outcome"] == "RESOLVED"
    assert result["room_scope_ok"] is True
    assert result["allowed_rooms"] == rooms
    assert "403" not in result["next_step"]


def test_resolve_flags_room_scope_mismatch(monkeypatch):
    """当前会话不在授权房间内：room_scope_ok=False，并在 next_step 里点明会被 403。"""
    rooms = [
        {
            "channel": "matrix",
            "channel_account_id": "default",
            "conversation_id": "!release-room:hubtel.xyz",
        }
    ]
    monkeypatch.setattr(approval_tools, "get_all_systems", lambda: _systems_with_rooms(rooms))
    result = approval_tools.routing_resolve_message_target(
        {"message_text": MSG_HIT, "message_context": _message_context(MSG_HIT)},
        _ctx(),
        None,
    )
    assert result["outcome"] == "RESOLVED"
    assert result["room_scope_ok"] is False
    assert "403" in result["next_step"]
    assert "message_routing.rooms" in result["next_step"]


def _prepare(monkeypatch, *, extra_args=None, rooms=None):
    from app.db.base import Base
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    systems = _systems_with_rooms(rooms) if rooms else _systems_kw([_approver()])
    monkeypatch.setattr(approval_tools, "get_all_systems", lambda: systems)
    monkeypatch.setattr(
        "app.services.tool_adapters.approval_tools._routing_systems",
        lambda: list(systems.values()),
    )
    resolved = approval_tools.routing_resolve_message_target(
        {"message_text": MSG_HIT, "message_context": _message_context(MSG_HIT)},
        _ctx(),
        None,
    )
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    _seed_test_env(db, resolved["system_name"])
    args = {
        "message_context": dict(resolved["message_context"]),
        "routing_ticket": resolved["ticket"],
        "system_name": resolved["system_name"],
        "service_name": resolved["service_name"],
        "environment": "test",
        "steps": [
            {
                "step_key": "health",
                "action_type": "HEALTH_CHECK",
                "parameters": {"targets": ["cc-test2"]},
            }
        ],
        "policy": {"continue_on_error": False},
    }
    if extra_args:
        args.update(extra_args)
    try:
        return approval_tools.approval_prepare_plan(args=args, ctx=_ctx(), db=db)
    finally:
        db.close()


def test_prepare_plan_accepts_redundant_legacy_fields(monkeypatch):
    """prepare_plan 消费端：一致冗余字段不再 400，工单正常创建。"""
    prepared = _prepare(
        monkeypatch,
        extra_args={
            "room_id": ROOM,
            "event_id": EVENT,
            "sender_matrix_id": SENDER,
        },
    )
    assert prepared["plan_id"]


def test_prepare_plan_rejects_conflicting_legacy_room(monkeypatch):
    """prepare_plan 消费端：冲突房间仍然拒绝。"""
    with pytest.raises(HTTPException) as exc:
        _prepare(monkeypatch, extra_args={"room_id": "!attacker-room:hubtel.xyz"})
    assert exc.value.status_code == 400
    assert "room_id" in str(exc.value.detail)
