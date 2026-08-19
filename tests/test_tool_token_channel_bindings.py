from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker


MATRIX_ROOM = {
    "channel": "matrix",
    "channel_account_id": "default",
    "conversation_id": "!ops:example.org",
}
MATRIX_APPROVER = {
    "channel": "matrix",
    "channel_account_id": "default",
    "sender_id": "@alice:example.org",
}


def _engine(tmp_path, name="tool-token-channel-bindings.db"):
    from app.db.models import Base

    engine = create_engine(
        f"sqlite:///{tmp_path / name}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    return engine


def _session(engine):
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)()


def test_strict_generic_normalizers_dedupe_and_reject_malformed_entries():
    from app.services.tool_token import (
        normalize_approver_identities,
        normalize_channel_bindings,
    )

    assert normalize_channel_bindings([MATRIX_ROOM, dict(MATRIX_ROOM)]) == [MATRIX_ROOM]
    assert normalize_approver_identities([MATRIX_APPROVER, dict(MATRIX_APPROVER)]) == [
        MATRIX_APPROVER
    ]

    with pytest.raises(ValueError, match="channel_bindings must be a list"):
        normalize_channel_bindings(MATRIX_ROOM)
    with pytest.raises(ValueError, match=r"channel_bindings\[0\]"):
        normalize_channel_bindings([{"channel": "matrix"}])
    with pytest.raises(ValueError, match=r"approver_identities\[0\]"):
        normalize_approver_identities(
            [
                {
                    "channel": "unknown",
                    "channel_account_id": "default",
                    "sender_id": "alice",
                }
            ]
        )


def test_legacy_aliases_normalize_and_conflicts_are_rejected():
    from app.services.tool_token import (
        resolve_approver_identities,
        resolve_channel_bindings,
    )

    assert resolve_channel_bindings(legacy=["!ops:example.org"]) == [MATRIX_ROOM]
    assert resolve_approver_identities(legacy=["@alice:example.org"]) == [
        MATRIX_APPROVER
    ]
    assert resolve_channel_bindings(generic=[MATRIX_ROOM], legacy=["!ops:example.org"]) == [
        MATRIX_ROOM
    ]

    with pytest.raises(ValueError, match="channel_bindings conflicts with bound_room_ids"):
        resolve_channel_bindings(
            generic=[MATRIX_ROOM],
            legacy=["!different:example.org"],
        )
    with pytest.raises(
        ValueError,
        match="approver_identities conflicts with approver_matrix_ids",
    ):
        resolve_approver_identities(
            generic=[MATRIX_APPROVER],
            legacy=["@bob:example.org"],
        )


def test_create_tool_token_no_longer_stores_bindings(tmp_path):
    """Phase 2：create 不再接收/存储任何房间或审批人绑定。"""
    from app.services.tool_token import create_tool_token, token_to_dict

    engine = _engine(tmp_path)
    db = _session(engine)
    try:
        created = create_tool_token(
            db,
            name="multichannel",
            owner="admin",
            scopes=["ops:read"],
        )
        record = created["record"]
        assert record.channel_bindings == []
        assert record.approver_identities == []
        assert record.bound_room_ids == []
        assert record.approver_matrix_ids == []
        data = token_to_dict(record)
        assert "channel_bindings" not in data
        assert "approver_identities" not in data
        assert "bound_room_ids" not in data
        assert "approver_matrix_ids" not in data
    finally:
        db.close()
        engine.dispose()


def test_token_to_dict_no_longer_exports_binding_columns():
    from app.services.tool_token import token_to_dict

    token = SimpleNamespace(
        id="token-1",
        name="legacy-stale",
        owner="admin",
        description="",
        scopes=["ops:read"],
        allow_write=False,
        allow_prod=False,
        token_prefix="ops_tool_",
        created_at=None,
        expires_at=None,
        last_used_at=None,
        revoked_at=None,
    )

    data = token_to_dict(token)
    assert "channel_bindings" not in data
    assert "approver_identities" not in data
    assert "bound_room_ids" not in data
    assert "approver_matrix_ids" not in data


def test_token_level_enforcement_helpers_removed():
    import app.services.tool_token as tt

    assert not hasattr(tt, "enforce_conversation_binding")
    assert not hasattr(tt, "enforce_room_binding")
    assert not hasattr(tt, "matching_approver_sender_ids")


def test_tool_context_ignores_legacy_policy_inputs_and_derives_aliases():
    from app.services.tool_context import ToolContext

    legacy = ToolContext(
        bound_room_ids=["!ops:example.org"],
        approver_matrix_ids=["@alice:example.org"],
    )
    assert legacy.channel_bindings == []
    assert legacy.approver_identities == []
    assert legacy.bound_room_ids == []
    assert legacy.approver_matrix_ids == []

    generic = ToolContext(
        channel_bindings=[MATRIX_ROOM],
        approver_identities=[MATRIX_APPROVER],
        bound_room_ids=["!stale:example.org"],
        approver_matrix_ids=["@stale:example.org"],
    )
    assert generic.bound_room_ids == ["!ops:example.org"]
    assert generic.approver_matrix_ids == ["@alice:example.org"]


def _message_context(*, conversation_id="group-7"):
    return {
        "channel": "wechat",
        "channel_account_id": "corp-a",
        "conversation_id": conversation_id,
        "message_id": "msg-1",
        "sender_id": "owner-1",
        "content_sha256": "a" * 64,
    }


@pytest.mark.parametrize("stream", [False, True])
def test_mcp_call_no_longer_enforces_token_level_binding(tmp_path, stream):
    """Phase 2：registry 不再做 token 级房间校验；不同 message_context /
    room_id 的调用不再因 token 绑定被 403，仅由各工具在系统级强制。"""
    from app.services.mcp_capability_service import mcp_call_tool, mcp_call_tool_stream
    from app.services.tool_context import ToolContext
    from app.services.tool_registry import registry

    engine = _engine(tmp_path, f"mcp-no-gate-{stream}.db")
    db = _session(engine)
    tool_name = f"ops.contract.no_gate_{stream}"

    @registry.register(
        name=tool_name,
        description="token-level gate removed integration",
        input_schema={
            "type": "object",
            "properties": {
                "message_context": {"type": "object"},
                "room_id": {"type": "string"},
            },
            "additionalProperties": False,
        },
        scopes=["ops:read"],
        category="routing",
        streamable=True,
    )
    def _guarded(args, ctx, db, stream_callback=None):
        if stream_callback:
            stream_callback({"event": "guarded"})
        return {"accepted": True}

    # token 仍可声明 channel_bindings，但不再被用于拦截。
    ctx = ToolContext(
        username="tester",
        auth_type="session",
        is_admin=True,
        scopes=["*"],
        channel_bindings=[
            {
                "channel": "wechat",
                "channel_account_id": "corp-a",
                "conversation_id": "group-7",
            }
        ],
    )
    invoke = mcp_call_tool_stream if stream else mcp_call_tool
    try:
        for arguments in (
            {"message_context": _message_context()},
            {"message_context": _message_context(conversation_id="group-8")},
            {"room_id": "!other:example.org"},
        ):
            result = invoke(
                db,
                ctx,
                {"name": tool_name.replace(".", "_"), "arguments": arguments},
            )
            assert result["isError"] is False
    finally:
        registry._tools.pop(tool_name, None)
        db.close()
        engine.dispose()


@pytest.mark.parametrize("channel", ["wechat", "telegram"])
def test_legacy_matrix_approval_ignores_cross_channel_token_approvers(
    tmp_path, monkeypatch, channel
):
    """系统 message_routing 为审批人唯一来源：跨渠道 token 审批人被忽略，
    请求按系统配置的矩阵审批人授权，而非被拒绝。"""
    from app.services.tool_adapters import approval_tools
    from app.services.tool_context import ToolContext

    engine = _engine(tmp_path, f"approval-channel-deny-{channel}.db")
    db = _session(engine)
    ctx = ToolContext(
        auth_type="tool_token",
        token_name="cross-channel",
        scopes=["ops:read"],
        approver_identities=[
            {
                "channel": channel,
                "channel_account_id": "primary",
                "sender_id": "owner-1",
            }
        ],
    )
    monkeypatch.setattr(
        approval_tools,
        "get_all_systems",
        lambda: {
            "crypto-trader": {
                "name": "crypto-trader",
                "message_routing": {"approvers": ["@fallback:example.org"]},
                "services": [],
            }
        },
    )
    try:
        # 合法路由票据：revision 由 OPS 根据当前配置计算
        from app.services.qclaw_routing import (
            compute_routing_revision,
            issue_ticket,
        )
        from app.services.message_context import MessageContext

        monkeypatch.setattr(
            "app.services.qclaw_routing.QCLAW_APPROVAL_SIGNING_KEY",
            "channel-bindings-test-key-0123456789abcdef",
        )
        systems = approval_tools._routing_systems()
        revision = compute_routing_revision(systems)
        legacy_context = MessageContext(
            channel="matrix",
            channel_account_id="default",
            conversation_id="!ops:example.org",
            message_id="$event",
            sender_id="@requester:matrix.org",
            content_sha256="b" * 64,
        )
        ticket = issue_ticket(
            legacy_context,
            "crypto-trader",
            "strategy",
            revision,
        )
        # 系统配置的矩阵审批人授权请求；跨渠道 token 审批人被忽略，不触发拒绝。
        result = approval_tools.approval_prepare_service_control(
            {
                "room_id": "!ops:example.org",
                "request_event_id": "$event",
                "sender_matrix_id": "@requester:matrix.org",
                "content_sha256": "b" * 64,
                "system_name": "crypto-trader",
                "service_name": "strategy",
                "environment": "test",
                "control_action": "restart",
                "targets": ["server-1"],
                "routing_ticket": ticket.ticket,
            },
            ctx,
            db,
        )
        assert result["authorized_approvers"] == ["@fallback:example.org"]
    finally:
        db.close()
        engine.dispose()


def test_central_registry_no_longer_gates_on_token_bindings(
    tmp_path, monkeypatch
):
    """Phase 2：token 级绑定不再拦截任何调度路径（direct / stream / REST）。
    不同 message_context / room_id 的调用均能正常分发到工具。"""
    import asyncio

    from app.api import tools as tools_api
    from app.services.tool_context import ToolContext
    from app.services.tool_registry import registry

    engine = _engine(tmp_path, "central-registry-channel-guard.db")
    db = _session(engine)
    tool_name = "ops.contract.central_channel_guard"
    handled = []

    @registry.register(
        name=tool_name,
        description="central channel guard integration",
        input_schema={
            "type": "object",
            "properties": {
                "message_context": {"type": "object"},
                "room_id": {"type": "string"},
            },
            "additionalProperties": False,
        },
        scopes=["ops:read"],
        category="routing",
        streamable=True,
    )
    def _guarded(args, ctx, db, stream_callback=None):
        handled.append(args)
        return {"accepted": True}

    def _ctx(channel, account_id, conversation_id):
        return ToolContext(
            username="tester",
            auth_type="session",
            is_admin=True,
            scopes=["*"],
            channel_bindings=[
                {
                    "channel": channel,
                    "channel_account_id": account_id,
                    "conversation_id": conversation_id,
                }
            ],
        )

    def _generic_args(channel, account_id, conversation_id):
        return {
            "message_context": {
                "channel": channel,
                "channel_account_id": account_id,
                "conversation_id": conversation_id,
                "message_id": "msg-1",
                "sender_id": "owner-1",
                "content_sha256": "c" * 64,
            }
        }

    try:
        # direct dispatch：token 声明不同会话，仍正常分发（不再被 403）。
        result = registry.call(
            db, tool_name, {}, _ctx("wechat", "corp-a", "group-7")
        )["result"]
        assert result == {"accepted": True}

        direct_stream_result = registry.call(
            db,
            tool_name,
            _generic_args("telegram", "bot-a", "chat-8"),
            _ctx("telegram", "bot-a", "chat-7"),
            stream_callback=lambda chunk: None,
        )["result"]
        assert direct_stream_result == {"accepted": True}

        rand_ctx = _ctx("matrix", "secondary", "!ops:example.org")
        monkeypatch.setattr(
            tools_api,
            "get_tool_context",
            lambda request, session: rand_ctx,
        )
        rest_result = tools_api.call_tool(
            tools_api.ToolCallPayload(
                tool=tool_name,
                arguments={"room_id": "!any:example.org"},
            ),
            SimpleNamespace(),
            db,
        )
        assert rest_result["data"]["result"] == {"accepted": True}

        rest_stream_result = asyncio.run(
            tools_api.call_tool_stream(
                tools_api.ToolCallPayload(
                    tool=tool_name,
                    arguments={"message_context": {"channel": "wechat"}},
                ),
                SimpleNamespace(),
                db,
            )
        )
        # streamable 工具返回 StreamingResponse：消费流并校验 done 事件结果。
        from fastapi.responses import StreamingResponse

        assert isinstance(rest_stream_result, StreamingResponse)

        async def _collect():
            chunks = ""
            async for chunk in rest_stream_result.body_iterator:
                chunks += chunk
            return chunks

        stream_body = asyncio.run(_collect())
        assert '"accepted": true' in stream_body
        assert "event: done" in stream_body

        assert len(handled) == 4
    finally:
        registry._tools.pop(tool_name, None)
        db.close()
        engine.dispose()


def test_bound_approval_list_filters_matrix_room_and_hides_non_matrix_rows(tmp_path):
    from app.services.action_approval import ActionApprovalService
    from app.services.tool_context import ToolContext
    from app.services.tool_registry import register_builtin_tools, registry

    engine = _engine(tmp_path, "bound-approval-list.db")
    db = _session(engine)
    try:
        service = ActionApprovalService(db)
        for index, room_id in enumerate(("!ops:example.org", "!other:example.org")):
            service.prepare(
                action_type="SERVICE_CONTROL",
                tool_name="ops.approval.prepare_service_control",
                room_id=room_id,
                request_event_id=f"$event-{index}",
                content_sha256=str(index + 1) * 64,
                system_name="crypto-trader",
                service_name="strategy",
                environment="test",
                targets=["server-1"],
                action_parameters={"control_action": "restart"},
                routing_config_revision="rev-1",
                routing_ticket_digest=f"ticket-{index}",
            )

        register_builtin_tools()
        tool = registry.get("ops.approval.list")
        assert "message_context" in tool.input_schema["properties"]
        assert "room_id" in tool.input_schema["properties"]

        matrix_ctx = ToolContext(
            username="tester",
            auth_type="session",
            is_admin=True,
            scopes=["*"],
            channel_bindings=[MATRIX_ROOM],
        )
        matrix_result = registry.call(
            db,
            "ops.approval.list",
            {"room_id": "!ops:example.org"},
            matrix_ctx,
        )["result"]
        assert matrix_result["total"] == 1
        assert len(matrix_result["items"]) == 1

        wechat_ctx = ToolContext(
            username="tester",
            auth_type="session",
            is_admin=True,
            scopes=["*"],
            channel_bindings=[
                {
                    "channel": "wechat",
                    "channel_account_id": "corp-a",
                    "conversation_id": "group-7",
                }
            ],
        )
        wechat_result = registry.call(
            db,
            "ops.approval.list",
            {"message_context": _message_context()},
            wechat_ctx,
        )["result"]
        assert wechat_result == {"ok": True, "total": 0, "items": []}

        unbound_ctx = ToolContext(
            username="tester",
            auth_type="tool_token",
            scopes=["*"],
        )
        unbound_wechat_result = registry.call(
            db,
            "ops.approval.list",
            {"message_context": _message_context()},
            unbound_ctx,
        )["result"]
        assert unbound_wechat_result == {"ok": True, "total": 0, "items": []}

        unbound_matrix_result = registry.call(
            db,
            "ops.approval.list",
            {
                "message_context": {
                    "channel": "matrix",
                    "channel_account_id": "default",
                    "conversation_id": "!ops:example.org",
                    "message_id": "$list-event",
                    "sender_id": "@caller:example.org",
                    "content_sha256": "d" * 64,
                }
            },
            unbound_ctx,
        )["result"]
        assert unbound_matrix_result["total"] == 1
        assert len(unbound_matrix_result["items"]) == 1
    finally:
        db.close()
        engine.dispose()


def test_tool_token_create_and_update_audit_ignores_binding_fields(
    tmp_path, monkeypatch
):
    """Phase 2：create/update 审计不再包含任何房间/审批人绑定信息；
    传入的绑定字段被 pydantic 忽略，不会进入审计或落库。"""
    from app.api import tools as tools_api

    engine = _engine(tmp_path, "tool-token-generic-audit.db")
    db = _session(engine)
    audit_calls = []
    monkeypatch.setattr(
        tools_api,
        "require_auth",
        lambda request, session: {"username": "admin", "is_admin": True},
    )
    monkeypatch.setattr(tools_api, "audit", lambda *args: audit_calls.append(args))
    monkeypatch.setattr(tools_api, "_bump_capability_version", lambda session: None)

    create_bindings = [
        {
            "channel": "wechat",
            "channel_account_id": "corp-a",
            "conversation_id": "group-7",
        }
    ]
    create_approvers = [
        {
            "channel": "telegram",
            "channel_account_id": "bot-a",
            "sender_id": "42",
        }
    ]
    try:
        response = tools_api.create_token(
            tools_api.CreateToolTokenPayload(
                name="multichannel-audit",
                scopes=["ops:read"],
                channel_bindings=create_bindings,
                approver_identities=create_approvers,
            ),
            SimpleNamespace(),
            db,
        )
        token_id = response["data"]["record"]["id"]
        tools_api.update_token(
            token_id,
            tools_api.UpdateToolTokenPayload(name="multichannel-audit-renamed"),
            SimpleNamespace(),
            db,
        )

        details = {call[0]: call[3] for call in audit_calls}
        create_detail = details["tool.token.create"]
        update_detail = details["tool.token.update"]
        # 审计明细不再包含绑定字段。
        assert "channel_bindings" not in create_detail
        assert "approver_identities" not in create_detail
        assert "bound_room_ids" not in create_detail
        assert "approver_matrix_ids" not in create_detail
        assert "channel_bindings" not in update_detail
        assert "approver_identities" not in update_detail
        # 仍保留核心字段用于追溯。
        assert "scopes=ops:read" in create_detail
        assert "allow_write=False" in create_detail
        assert "allow_prod=False" in create_detail
    finally:
        db.close()
        engine.dispose()


def test_token_and_preview_contexts_ignore_legacy_binding_columns(tmp_path, monkeypatch):
    """Phase 2：即使 DB 中残留旧绑定列数据，运行时/预览上下文也不再携带它们。"""
    from app.api.tools import ToolPolicyPreviewPayload, _ctx_from_token, _preview_context_from_payload
    from app.db.models import ToolToken
    from app.services.tool_token import hash_token

    engine = _engine(tmp_path)
    db = _session(engine)
    try:
        raw = "ops_tool_test-secret"
        token = ToolToken(
            name="wechat-token",
            owner="admin",
            token_hash=hash_token(raw),
            token_prefix="ops_tool_test",
            scopes=["ops:read"],
            channel_bindings=[
                {
                    "channel": "wechat",
                    "channel_account_id": "corp-a",
                    "conversation_id": "group-7",
                }
            ],
            approver_identities=[
                {
                    "channel": "wechat",
                    "channel_account_id": "corp-a",
                    "sender_id": "owner-1",
                }
            ],
            bound_room_ids=["!stale:example.org"],
            approver_matrix_ids=["@stale:example.org"],
        )
        db.add(token)
        db.commit()
        db.refresh(token)
        request = SimpleNamespace(client=None, headers={})

        runtime = _ctx_from_token(request, db, raw)
        preview, subject = _preview_context_from_payload(
            ToolPolicyPreviewPayload(tool="ops.test", token_id=token.id),
            request,
            db,
            {"username": "admin", "is_admin": True},
        )
        # 绑定列不再进入上下文——token 级绑定已彻底移除。
        assert runtime.channel_bindings == []
        assert runtime.approver_identities == []
        assert runtime.bound_room_ids == []
        assert runtime.approver_matrix_ids == []
        assert preview.channel_bindings == []
        assert preview.approver_identities == []
        assert preview.bound_room_ids == []
        assert preview.approver_matrix_ids == []
        # 预览主体不再导出绑定字段。
        assert "channel_bindings" not in subject
        assert "approver_identities" not in subject
        assert "bound_room_ids" not in subject
        assert "approver_matrix_ids" not in subject
    finally:
        db.close()
        engine.dispose()


def test_api_payloads_remove_binding_fields_and_ignore_extra():
    """Phase 2：create/update payload 不再声明任何绑定字段；传入的额外
    绑定字段被 pydantic 忽略（不放行也不报错）。"""
    from app.api.tools import CreateToolTokenPayload, UpdateToolTokenPayload

    create = CreateToolTokenPayload(name="generic")
    update = UpdateToolTokenPayload(name="renamed")

    # 绑定字段已从 payload 中移除。
    assert not hasattr(create, "channel_bindings")
    assert not hasattr(create, "approver_identities")
    assert not hasattr(create, "bound_room_ids")
    assert not hasattr(create, "approver_matrix_ids")
    assert not hasattr(update, "channel_bindings")
    assert not hasattr(update, "approver_identities")
    assert not hasattr(update, "bound_room_ids")
    assert not hasattr(update, "approver_matrix_ids")

    # 额外传入的绑定字段被静默忽略，不报错。
    create_extra = CreateToolTokenPayload(
        name="generic",
        channel_bindings=[MATRIX_ROOM],
        approver_identities=[MATRIX_APPROVER],
        bound_room_ids=["!x:example.org"],
        approver_matrix_ids=["@x:example.org"],
    )
    assert create_extra.name == "generic"


def test_update_does_not_touch_legacy_binding_columns(tmp_path, monkeypatch):
    """Phase 2：update 不再修改绑定列；存量 token 的绑定残留由迁移脚本
    clear_token_bindings 清空，而非通过接口。"""
    import app.api.tools as tools_api
    from app.api.tools import UpdateToolTokenPayload
    from app.db.models import ToolToken

    engine = _engine(tmp_path)
    db = _session(engine)
    try:
        token = ToolToken(
            name="clear-me",
            owner="admin",
            token_hash="hash-clear",
            token_prefix="ops_tool_clear",
            scopes=["ops:read"],
            channel_bindings=[MATRIX_ROOM],
            approver_identities=[MATRIX_APPROVER],
        )
        db.add(token)
        db.commit()
        db.refresh(token)
        monkeypatch.setattr(
            tools_api,
            "require_auth",
            lambda request, session: {"username": "admin", "is_admin": True},
        )
        monkeypatch.setattr(tools_api, "audit", lambda *args, **kwargs: None)
        monkeypatch.setattr(tools_api, "_bump_capability_version", lambda session: None)

        tools_api.update_token(
            token.id,
            UpdateToolTokenPayload(name="renamed"),
            SimpleNamespace(),
            db,
        )
        db.refresh(token)
        # 名称更新生效，但绑定列保持原样（接口不再管理它们）。
        assert token.name == "renamed"
        assert token.channel_bindings == [MATRIX_ROOM]
    finally:
        db.close()
        engine.dispose()


def test_migration_adds_columns_backfills_legacy_rows_and_is_idempotent(tmp_path):
    from app.db.migrations.runner import run_schema_migrations

    engine = _engine(tmp_path, "legacy-backfill.db")
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE tool_tokens DROP COLUMN channel_bindings"))
        conn.execute(text("ALTER TABLE tool_tokens DROP COLUMN approver_identities"))
        conn.execute(
            text(
                "INSERT INTO tool_tokens "
                "(id, name, token_hash, owner, bound_room_ids, approver_matrix_ids) "
                "VALUES ('legacy', 'legacy', 'legacy-hash', 'admin', :rooms, :approvers)"
            ),
            {
                "rooms": json.dumps(["!ops:example.org"]),
                "approvers": json.dumps(["@alice:example.org"]),
            },
        )

    first = run_schema_migrations(engine)
    second = run_schema_migrations(engine)
    assert "084_002_tool_token_channel_bindings" in first
    assert "084_003_tool_token_approver_identities" in first
    assert "084_004_tool_token_binding_backfill" in first
    assert not ({
        "084_002_tool_token_channel_bindings",
        "084_003_tool_token_approver_identities",
        "084_004_tool_token_binding_backfill",
    } & set(second))
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT channel_bindings, approver_identities FROM tool_tokens "
                "WHERE id='legacy'"
            )
        ).one()
        assert json.loads(row[0]) == [MATRIX_ROOM]
        assert json.loads(row[1]) == [MATRIX_APPROVER]
    engine.dispose()


def test_backfill_preserves_nonempty_generic_values_even_if_legacy_is_malformed(tmp_path):
    from app.db.migrations.runner import run_schema_migrations

    engine = _engine(tmp_path, "preserve-generic.db")
    generic_room = {
        "channel": "wechat",
        "channel_account_id": "corp-a",
        "conversation_id": "group-7",
    }
    generic_approver = {
        "channel": "telegram",
        "channel_account_id": "bot-a",
        "sender_id": "42",
    }
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO tool_tokens "
                "(id, name, token_hash, owner, bound_room_ids, approver_matrix_ids, "
                "channel_bindings, approver_identities) VALUES "
                "('generic', 'generic', 'generic-hash', 'admin', '{bad', '{bad', "
                ":bindings, :approvers)"
            ),
            {
                "bindings": json.dumps([generic_room]),
                "approvers": json.dumps([generic_approver]),
            },
        )

    run_schema_migrations(engine)
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT channel_bindings, approver_identities FROM tool_tokens "
                "WHERE id='generic'"
            )
        ).one()
        assert json.loads(row[0]) == [generic_room]
        assert json.loads(row[1]) == [generic_approver]
    engine.dispose()


def test_malformed_legacy_json_rolls_back_and_leaves_backfill_unapplied(tmp_path):
    from app.db.migrations.runner import run_schema_migrations

    engine = _engine(tmp_path, "malformed-legacy.db")
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO tool_tokens "
                "(id, name, token_hash, owner, bound_room_ids, approver_matrix_ids, "
                "channel_bindings, approver_identities) VALUES "
                "('bad', 'bad', 'bad-hash', 'admin', '{bad', '[]', '[]', '[]')"
            )
        )

    with pytest.raises(RuntimeError, match="Invalid legacy ToolToken JSON"):
        run_schema_migrations(engine)

    with engine.connect() as conn:
        versions = {
            row[0]
            for row in conn.execute(text("SELECT version FROM schema_migrations")).fetchall()
        }
        row = conn.execute(
            text("SELECT channel_bindings, approver_identities FROM tool_tokens WHERE id='bad'")
        ).one()
        assert "084_004_tool_token_binding_backfill" not in versions
        assert json.loads(row[0]) == []
        assert json.loads(row[1]) == []
    engine.dispose()
