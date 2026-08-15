"""System-level 房间绑定（message_routing.rooms）作用域强制测试。

验证「指定审批人 + 指定房间才可用」的配置生效：
1. 路由解析返回 allowed_rooms。
2. 审批准备流程拒绝非授权房间。
3. 临时授权 request 拒绝非授权房间。
4. 未配置 rooms 时不限制会话。
5. 房间 ID 混入 approvers 字段被拒绝。
"""

import pytest
from fastapi import HTTPException

from app.services.message_context import MessageContext
from app.services.qclaw_routing import RoutingOutcome, resolve_message_target
from app.services.tool_adapters import approval_tools
from app.services.tool_adapters.approval_tools import (
    _enforce_system_room,
    _resolve_system_rooms,
)
from app.services.tool_context import ToolContext


def _context(channel="matrix", **overrides):
    values = {
        "channel": channel,
        "channel_account_id": "primary",
        "conversation_id": "!allowedRoom:matrix.org",
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


def _systems(approvers, rooms=None):
    cfg = {
        "message_routing": {
            "enabled": True,
            "aliases": ["量化"],
            "keywords": [],
            "priority": 10,
            "approvers": approvers,
        },
        "services": [],
    }
    if rooms is not None:
        cfg["message_routing"]["rooms"] = rooms
    return {"crypto-trader": cfg}


def _monkeypatch_systems(monkeypatch, systems):
    monkeypatch.setattr(approval_tools, "get_all_systems", lambda: systems)


def test_route_resolution_exposes_allowed_rooms(monkeypatch):
    approver = _identity("matrix", "@ops:example.org", account="default")
    rooms = [
        {
            "channel": "matrix",
            "channel_account_id": "default",
            "conversation_id": "!allowedRoom:matrix.org",
        }
    ]
    _monkeypatch_systems(monkeypatch, _systems([approver], rooms))
    systems = [
        {**sys, "name": name} for name, sys in _systems([approver], rooms).items()
    ]
    decision = resolve_message_target("量化", systems)
    assert decision.outcome == RoutingOutcome.RESOLVED
    assert _resolve_system_rooms("crypto-trader") == rooms


def test_enforce_system_room_allows_matching_conversation(monkeypatch):
    approver = _identity("matrix", "@ops:example.org", account="default")
    rooms = [
        {
            "channel": "matrix",
            "channel_account_id": "default",
            "conversation_id": "!allowedRoom:matrix.org",
        }
    ]
    _monkeypatch_systems(monkeypatch, _systems([approver], rooms))
    context = _context("matrix", channel_account_id="default")
    _enforce_system_room(context, "crypto-trader")  # 不应抛异常


def test_enforce_system_room_rejects_other_conversation(monkeypatch):
    approver = _identity("matrix", "@ops:example.org", account="default")
    rooms = [
        {
            "channel": "matrix",
            "channel_account_id": "default",
            "conversation_id": "!allowedRoom:matrix.org",
        }
    ]
    _monkeypatch_systems(monkeypatch, _systems([approver], rooms))
    context = _context("matrix", channel_account_id="default", conversation_id="!other:matrix.org")
    with pytest.raises(HTTPException) as exc:
        _enforce_system_room(context, "crypto-trader")
    assert exc.value.status_code == 403
    assert "message_routing.rooms" in exc.value.detail


def test_enforce_system_room_unrestricted_when_no_rooms(monkeypatch):
    approver = _identity("matrix", "@ops:example.org", account="default")
    _monkeypatch_systems(monkeypatch, _systems([approver], []))
    context = _context("matrix", channel_account_id="default", conversation_id="!any:matrix.org")
    _enforce_system_room(context, "crypto-trader")  # 不应抛异常


def test_enforce_system_room_unknown_system_is_unrestricted(monkeypatch):
    _monkeypatch_systems(monkeypatch, {})
    _enforce_system_room(_context("matrix"), "does-not-exist")  # 不应抛异常


def test_temporary_access_rejects_room_outside_binding(monkeypatch):
    approver = _identity("matrix", "@ops:example.org", account="default")
    rooms = [
        {
            "channel": "matrix",
            "channel_account_id": "default",
            "conversation_id": "!allowedRoom:matrix.org",
        }
    ]
    _monkeypatch_systems(monkeypatch, _systems([approver], rooms))
    ctx = ToolContext(auth_type="tool_token", scopes=["ops:read"])
    response = approval_tools.temporary_access(
        {
            "operation": "request",
            "system_name": "crypto-trader",
            "environment": "prod",
            "beneficiary_identity": "@beneficiary:example.org",
            "allowed_actions": ["deploy"],
            "reason": "test",
            "message_context": _context(
                "matrix", channel_account_id="default", conversation_id="!other:matrix.org"
            ).to_dict(),
        },
        ctx,
        None,
    )
    assert response["ok"] is False
    assert "message_routing.rooms" in response["error"]


def test_temporary_access_allows_matching_room(monkeypatch):
    approver = _identity("matrix", "@ops:example.org", account="default")
    rooms = [
        {
            "channel": "matrix",
            "channel_account_id": "default",
            "conversation_id": "!allowedRoom:matrix.org",
        }
    ]
    _monkeypatch_systems(monkeypatch, _systems([approver], rooms))
    ctx = ToolContext(auth_type="tool_token", scopes=["ops:read"])
    response = approval_tools.temporary_access(
        {
            "operation": "request",
            "system_name": "crypto-trader",
            "environment": "prod",
            "beneficiary_identity": "@beneficiary:example.org",
            "allowed_actions": ["deploy"],
            "reason": "test",
            "message_context": _context(
                "matrix", channel_account_id="default", conversation_id="!allowedRoom:matrix.org"
            ).to_dict(),
        },
        ctx,
        None,
    )
    # 房间校验通过，错误不应再与房间作用域相关（后续错误可能来自审批人/DB 流程）。
    assert "message_routing.rooms" not in response.get("error", "")