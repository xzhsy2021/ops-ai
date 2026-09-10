"""MCP contract tests for ops.approval.temporary_access tool."""
import uuid

import pytest

from app.db.base import SessionLocal, Base, engine
from app.db.migrations.runner import run_schema_migrations
from app.services.tool_registry import registry, register_builtin_tools
from app.services.tool_context import ToolContext
from app.db.models import System, SystemEnvironment, TemporaryApprovalGrant
from app.services.message_context import MessageContext
from app.services.tool_token import (
    resolve_approver_identities,
    resolve_channel_bindings,
)

_RUN_ID = uuid.uuid4().hex[:8]

register_builtin_tools()


@pytest.fixture(autouse=True)
def _strong_signing_key(monkeypatch):
    monkeypatch.setattr(
        "app.services.qclaw_routing.QCLAW_APPROVAL_SIGNING_KEY",
        "temp-self-approval-tools-test-key-0123456789",
    )


@pytest.fixture(scope="module")
def db():
    Base.metadata.create_all(engine)
    run_schema_migrations(engine)
    session = SessionLocal()
    # Seed a test system with a configured original approver. 显式覆盖路由配置，
    # 避免依赖共享 DB 中可能配置的 rooms（房间作用域会拒绝测试用随机会话）。
    # 测试结束后恢复原始配置，避免污染共享开发库。
    existing = session.query(System).filter(System.name == "crypto-trader").first()
    routing = {
        "approvers": [{
            "channel": "matrix",
            "channel_account_id": "default",
            "sender_id": "@owner:matrix.org",
        }],
    }
    original_routing = dict(existing.message_routing) if existing is not None else None
    if existing is None:
        session.add(System(
            name="crypto-trader",
            display_name="Crypto Trader",
            message_routing=routing,
        ))
        session.add_all([
            SystemEnvironment(system_name="crypto-trader", name="test", category="test"),
            SystemEnvironment(system_name="crypto-trader", name="prod", category="prod"),
        ])
    else:
        existing.message_routing = dict(routing)
        session.add(existing)
    session.commit()
    yield session
    if original_routing is not None:
        existing = session.query(System).filter(System.name == "crypto-trader").first()
        existing.message_routing = original_routing
        session.add(existing)
        session.commit()
    session.rollback()
    session.close()


def _room(suffix: str) -> str:
    return f"!room-{_RUN_ID}-{suffix}:matrix.org"


def _event(suffix: str) -> str:
    return f"$evt-{_RUN_ID}-{suffix}:matrix.org"


def _context(*, channel="matrix", account="default", conversation=None, message=None, sender="@requester:matrix.org"):
    return MessageContext(
        channel=channel,
        channel_account_id=account,
        conversation_id=conversation or _room(f"ctx-{message or 'default'}"),
        message_id=message or _event("ctx"),
        sender_id=sender,
        content_sha256="a" * 64,
    )


def _in_same_conversation(context: MessageContext, *, message: str, sender: str) -> MessageContext:
    """生成与给定 context 同 conversation、不同 message id 的新 context。"""
    return MessageContext(
        channel=context.channel,
        channel_account_id=context.channel_account_id,
        conversation_id=context.conversation_id,
        message_id=message or _event("ctx"),
        sender_id=sender,
        content_sha256="a" * 64,
    )


def _ctx(approver_matrix_ids=None, bound_room_ids=None):
    return ToolContext(
        auth_type="tool_token",
        token_name="test-token",
        token_owner="test",
        allow_write=True,
        channel_bindings=resolve_channel_bindings(legacy=bound_room_ids or []),
        approver_identities=resolve_approver_identities(
            legacy=approver_matrix_ids or []
        ),
    )


# ── 注册契约 ──

def test_temporary_access_tool_is_registered():
    """ops.approval.temporary_access 已注册并包含 operation 和 message_context。"""
    tool = registry.get("ops.approval.temporary_access")
    assert tool is not None
    props = tool.input_schema["properties"]
    for field in (
        "operation", "message_context",
        "system_name", "environment",
        "beneficiary_identity", "duration_value", "duration_unit",
        "reason", "allowed_actions",
        "grant_id", "short_code", "revoke_reason",
        "status", "limit",
    ):
        assert field in props, f"temporary_access schema 缺少 {field}"
    assert "operation" in tool.input_schema["required"]
    assert tool.input_schema["additionalProperties"] is False
    assert "query" in props["operation"]["enum"], "operation enum 必须包含 query"


def test_temporary_access_never_exposes_confirmation_hash(db):
    """工具返回结果不包含 confirmation_code_hash。"""
    from app.services.tool_adapters.approval_tools import temporary_access

    context = _context(message="tool-hash-check")
    owner_ctx = _in_same_conversation(context, message="tool-hash-check-approve", sender="@owner:matrix.org")
    result = temporary_access(
        args={
            "operation": "request",
            "message_context": context.to_dict(),
            "beneficiary_identity": context.actor_key,
            "system_name": "crypto-trader",
            "environment": "test",
            "allowed_actions": ["FILE_UPLOAD", "RELEASE", "SERVICE_CONTROL", "HEALTH_CHECK"],
            "reason": "urgent test fix",
            "duration_value": 1,
            "duration_unit": "day",
        },
        ctx=_ctx(approver_matrix_ids=["@owner:matrix.org"]),
        db=db,
    )
    assert result["ok"] is True
    assert result["status"] == "PENDING"
    assert "confirmation_code_hash" not in str(result)
    assert result["short_code"] and len(result["short_code"]) == 8

    confirmed = temporary_access(
        args={
            "operation": "confirm",
            "message_context": owner_ctx.to_dict(),
            "grant_id": result["grant_id"],
            "short_code": result["short_code"],
        },
        ctx=_ctx(approver_matrix_ids=["@owner:matrix.org"]),
        db=db,
    )
    assert confirmed["ok"] is True
    assert confirmed["status"] == "ACTIVE"
    assert "confirmation_code_hash" not in str(confirmed)
    assert confirmed["expires_at"] is not None


def test_temporary_access_request_and_confirm_and_revoke_lifecycle(db):
    """request → confirm → revoke 完整生命周期。"""
    from app.services.tool_adapters.approval_tools import temporary_access

    context = _context(message="tool-lifecycle")
    owner_ctx = _in_same_conversation(context, message="tool-lifecycle-approve", sender="@owner:matrix.org")
    revoke_ctx = _in_same_conversation(context, message="tool-lifecycle-revoke", sender="@owner:matrix.org")
    ctx = _ctx(approver_matrix_ids=["@owner:matrix.org"])

    requested = temporary_access(
        args={
            "operation": "request",
            "message_context": context.to_dict(),
            "beneficiary_identity": context.actor_key,
            "system_name": "crypto-trader",
            "environment": "test",
            "allowed_actions": ["SERVICE_CONTROL"],
            "reason": "restart needed",
            "duration_value": 2,
            "duration_unit": "day",
        },
        ctx=ctx,
        db=db,
    )
    assert requested["ok"] is True
    grant_id = requested["grant_id"]
    assert requested["beneficiary_actor_key"] == context.actor_key

    confirmed = temporary_access(
        args={
            "operation": "confirm",
            "message_context": owner_ctx.to_dict(),
            "grant_id": grant_id,
            "short_code": requested["short_code"],
        },
        ctx=ctx,
        db=db,
    )
    assert confirmed["ok"] is True
    assert confirmed["status"] == "ACTIVE"
    assert confirmed["approved_by_actor_key"] == owner_ctx.actor_key

    revoked = temporary_access(
        args={
            "operation": "revoke",
            "message_context": revoke_ctx.to_dict(),
            "grant_id": grant_id,
            "revoke_reason": "no longer needed",
        },
        ctx=ctx,
        db=db,
    )
    assert revoked["ok"] is True
    assert revoked["status"] == "REVOKED"
    assert revoked["revoked_by_actor_key"] == owner_ctx.actor_key


def test_temporary_access_derives_actor_from_message_context_only(db):
    """操作身份只从 message_context.sender_id 推导，不信任消息文本里的用户名。"""
    from app.services.tool_adapters.approval_tools import temporary_access

    # 请求方是 requester；确认方必须是 owner（配置的原始审批人）
    context = _context(message="tool-actor-derive")
    mallory_ctx = _in_same_conversation(context, message="tool-actor-derive-attack", sender="@mallory:matrix.org")
    ctx = _ctx(approver_matrix_ids=["@owner:matrix.org"])

    requested = temporary_access(
        args={
            "operation": "request",
            "message_context": context.to_dict(),
            "beneficiary_identity": context.actor_key,
            "system_name": "crypto-trader",
            "environment": "test",
            "allowed_actions": ["HEALTH_CHECK"],
            "reason": "verify health",
        },
        ctx=ctx,
        db=db,
    )
    assert requested["ok"] is True

    # 非授权用户确认失败，且 grant 保持 PENDING
    forged = temporary_access(
        args={
            "operation": "confirm",
            "message_context": mallory_ctx.to_dict(),
            "grant_id": requested["grant_id"],
            "short_code": requested["short_code"],
        },
        ctx=ctx,
        db=db,
    )
    assert forged["ok"] is False
    stored = db.query(TemporaryApprovalGrant).filter(
        TemporaryApprovalGrant.id == requested["grant_id"]
    ).one()
    assert stored.status == "PENDING"


def test_temporary_access_rejects_prod_environment_and_forbidden_actions(db):
    """生产环境与非允许动作被拒绝。"""
    from app.services.tool_adapters.approval_tools import temporary_access

    context = _context(message="tool-prod-reject")
    ctx = _ctx(approver_matrix_ids=["@owner:matrix.org"])

    prod = temporary_access(
        args={
            "operation": "request",
            "message_context": context.to_dict(),
            "beneficiary_identity": context.actor_key,
            "system_name": "crypto-trader",
            "environment": "prod",
            "allowed_actions": ["SERVICE_CONTROL"],
            "reason": "prod change",
        },
        ctx=ctx,
        db=db,
    )
    assert prod["ok"] is False

    dml = temporary_access(
        args={
            "operation": "request",
            "message_context": context.to_dict(),
            "beneficiary_identity": context.actor_key,
            "system_name": "crypto-trader",
            "environment": "test",
            "allowed_actions": ["DML"],
            "reason": "data change",
        },
        ctx=ctx,
        db=db,
    )
    assert dml["ok"] is False


# ── query 操作 ──


def _request_grant(db, *, message, beneficiary="@requester:matrix.org", actions=("SERVICE_CONTROL",), reason="query test"):
    """辅助：发起一条临时授权申请，返回 (工具结果, 发起 context)。"""
    from app.services.tool_adapters.approval_tools import temporary_access

    context = _context(message=message, sender=beneficiary)
    result = temporary_access(
        args={
            "operation": "request",
            "message_context": context.to_dict(),
            "beneficiary_identity": context.actor_key,
            "system_name": "crypto-trader",
            "environment": "test",
            "allowed_actions": list(actions),
            "reason": reason,
        },
        ctx=_ctx(approver_matrix_ids=["@owner:matrix.org"]),
        db=db,
    )
    assert result["ok"] is True, result
    return result, context


def test_temporary_access_query_returns_beneficiary(db):
    """query 返回授权列表并含受益人字段（本会话内可见他人作为受益人的授权）。"""
    from app.services.tool_adapters.approval_tools import temporary_access

    grant, context = _request_grant(db, message="tool-query-beneficiary")
    result = temporary_access(
        args={
            "operation": "query",
            "message_context": context.to_dict(),
        },
        ctx=_ctx(approver_matrix_ids=["@owner:matrix.org"]),
        db=db,
    )
    assert result["ok"] is True
    assert result["total"] >= 1
    matched = next(
        (item for item in result["items"] if item["id"] == grant["grant_id"]),
        None,
    )
    assert matched is not None, result["items"]
    assert matched["beneficiary_actor_key"] == context.actor_key
    assert matched["status"] == "PENDING"
    assert matched["allowed_actions"] == ["SERVICE_CONTROL"]


def test_temporary_access_query_filters_by_status_and_system(db):
    """query 支持 status / system_name 过滤。"""
    from app.services.tool_adapters.approval_tools import temporary_access

    grant, context = _request_grant(db, message="tool-query-filter")
    ctx = _ctx(approver_matrix_ids=["@owner:matrix.org"])

    active = temporary_access(
        args={
            "operation": "query",
            "message_context": context.to_dict(),
            "status": "ACTIVE",
        },
        ctx=ctx,
        db=db,
    )
    assert active["ok"] is True
    assert all(item["status"] == "ACTIVE" for item in active["items"])

    pending = temporary_access(
        args={
            "operation": "query",
            "message_context": context.to_dict(),
            "status": "PENDING",
            "system_name": "crypto-trader",
        },
        ctx=ctx,
        db=db,
    )
    assert pending["ok"] is True
    matched = [item for item in pending["items"] if item["id"] == grant["grant_id"]]
    assert matched, pending["items"]

    # 未知系统名返回错误而不是空列表（与 request 的失败语义一致）
    missing = temporary_access(
        args={
            "operation": "query",
            "message_context": context.to_dict(),
            "system_name": "no-such-system",
        },
        ctx=ctx,
        db=db,
    )
    assert missing["ok"] is False


def test_temporary_access_query_scoped_to_conversation(db):
    """query 只返回当前会话的授权：其他会话的记录不可见。"""
    from app.services.tool_adapters.approval_tools import temporary_access

    grant, context = _request_grant(db, message="tool-query-scope")

    # 另一会话的调用者查不到这条授权
    outsider = _context(message="tool-query-scope-outsider", sender="@owner:matrix.org")
    result = temporary_access(
        args={
            "operation": "query",
            "message_context": outsider.to_dict(),
        },
        ctx=_ctx(approver_matrix_ids=["@owner:matrix.org"]),
        db=db,
    )
    assert result["ok"] is True
    assert all(item["id"] != grant["grant_id"] for item in result["items"])

    # 按 grant_id 精确查询同样受会话作用域限制
    by_id_outside = temporary_access(
        args={
            "operation": "query",
            "message_context": outsider.to_dict(),
            "grant_id": grant["grant_id"],
        },
        ctx=_ctx(approver_matrix_ids=["@owner:matrix.org"]),
        db=db,
    )
    assert by_id_outside["ok"] is True
    assert by_id_outside["total"] == 0

    # 原会话按 grant_id 精确查询可见
    by_id = temporary_access(
        args={
            "operation": "query",
            "message_context": context.to_dict(),
            "grant_id": grant["grant_id"],
        },
        ctx=_ctx(approver_matrix_ids=["@owner:matrix.org"]),
        db=db,
    )
    assert by_id["ok"] is True
    assert by_id["total"] == 1
    assert by_id["items"][0]["id"] == grant["grant_id"]


def test_temporary_access_query_never_exposes_confirmation_hash_or_code(db):
    """query 结果不包含确认码哈希与确认码。"""
    from app.services.tool_adapters.approval_tools import temporary_access

    grant, context = _request_grant(db, message="tool-query-hash")
    result = temporary_access(
        args={
            "operation": "query",
            "message_context": context.to_dict(),
            "grant_id": grant["grant_id"],
        },
        ctx=_ctx(approver_matrix_ids=["@owner:matrix.org"]),
        db=db,
    )
    assert result["ok"] is True
    assert result["total"] == 1
    assert "confirmation_code_hash" not in str(result)
    assert "short_code" not in str(result)


def test_temporary_access_query_rejects_invalid_status_and_limit(db):
    """query 的非法 status / limit 被拒绝或收敛。"""
    from app.services.tool_adapters.approval_tools import temporary_access

    context = _context(message="tool-query-invalid")
    ctx = _ctx(approver_matrix_ids=["@owner:matrix.org"])

    bad_status = temporary_access(
        args={
            "operation": "query",
            "message_context": context.to_dict(),
            "status": "BOGUS",
        },
        ctx=ctx,
        db=db,
    )
    assert bad_status["ok"] is False

    # limit 超上限被收敛为 200，非法值被拒绝
    huge = temporary_access(
        args={
            "operation": "query",
            "message_context": context.to_dict(),
            "limit": 500,
        },
        ctx=ctx,
        db=db,
    )
    assert huge["ok"] is True

    broken = temporary_access(
        args={
            "operation": "query",
            "message_context": context.to_dict(),
            "limit": "not-a-number",
        },
        ctx=ctx,
        db=db,
    )
    assert broken["ok"] is False


def test_temporary_access_query_reflects_lifecycle_states(db):
    """query 反映 confirm/revoke 后的状态变化。"""
    from app.services.tool_adapters.approval_tools import temporary_access

    requested, context = _request_grant(db, message="tool-query-lifecycle")
    ctx = _ctx(approver_matrix_ids=["@owner:matrix.org"])
    owner_ctx = _in_same_conversation(context, message="tool-query-lifecycle-confirm", sender="@owner:matrix.org")

    confirmed = temporary_access(
        args={
            "operation": "confirm",
            "message_context": owner_ctx.to_dict(),
            "grant_id": requested["grant_id"],
            "short_code": requested["short_code"],
        },
        ctx=ctx,
        db=db,
    )
    assert confirmed["ok"] is True

    after_confirm = temporary_access(
        args={
            "operation": "query",
            "message_context": context.to_dict(),
            "grant_id": requested["grant_id"],
        },
        ctx=ctx,
        db=db,
    )
    assert after_confirm["ok"] is True
    assert after_confirm["items"][0]["status"] == "ACTIVE"

    revoked = temporary_access(
        args={
            "operation": "revoke",
            "message_context": owner_ctx.to_dict(),
            "grant_id": requested["grant_id"],
            "revoke_reason": "done",
        },
        ctx=ctx,
        db=db,
    )
    assert revoked["ok"] is True

    after_revoke = temporary_access(
        args={
            "operation": "query",
            "message_context": context.to_dict(),
            "status": "REVOKED",
        },
        ctx=ctx,
        db=db,
    )
    assert after_revoke["ok"] is True
    matched = [item for item in after_revoke["items"] if item["id"] == requested["grant_id"]]
    assert matched, after_revoke["items"]
    assert matched[0]["status"] == "REVOKED"
