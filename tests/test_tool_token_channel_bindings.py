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


def test_create_round_trips_generic_fields_and_derives_legacy_aliases(tmp_path):
    from app.services.tool_token import create_tool_token, token_to_dict

    engine = _engine(tmp_path)
    db = _session(engine)
    try:
        created = create_tool_token(
            db,
            name="multichannel",
            owner="admin",
            scopes=["ops:read"],
            channel_bindings=[
                MATRIX_ROOM,
                {
                    "channel": "wechat",
                    "channel_account_id": "corp-a",
                    "conversation_id": "group-7",
                },
            ],
            approver_identities=[
                MATRIX_APPROVER,
                {
                    "channel": "telegram",
                    "channel_account_id": "bot-a",
                    "sender_id": "42",
                },
            ],
        )

        record = created["record"]
        data = token_to_dict(record)
        assert record.channel_bindings == data["channel_bindings"]
        assert record.approver_identities == data["approver_identities"]
        assert data["bound_room_ids"] == ["!ops:example.org"]
        assert data["approver_matrix_ids"] == ["@alice:example.org"]
        assert record.bound_room_ids == []
        assert record.approver_matrix_ids == []
    finally:
        db.close()
        engine.dispose()


def test_create_accepts_legacy_aliases_but_persists_only_generic_policy(tmp_path):
    from app.services.tool_token import create_tool_token, token_to_dict

    engine = _engine(tmp_path)
    db = _session(engine)
    try:
        record = create_tool_token(
            db,
            name="matrix-compatible",
            owner="admin",
            scopes=["ops:read"],
            bound_room_ids=["!ops:example.org"],
            approver_matrix_ids=["@alice:example.org"],
        )["record"]

        assert record.channel_bindings == [MATRIX_ROOM]
        assert record.approver_identities == [MATRIX_APPROVER]
        assert record.bound_room_ids == []
        assert record.approver_matrix_ids == []
        assert token_to_dict(record)["bound_room_ids"] == ["!ops:example.org"]
    finally:
        db.close()
        engine.dispose()


def test_token_to_dict_never_uses_legacy_database_columns_as_policy_source():
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
        channel_bindings=[],
        approver_identities=[],
        bound_room_ids=["!stale:example.org"],
        approver_matrix_ids=["@stale:example.org"],
    )

    data = token_to_dict(token)
    assert data["channel_bindings"] == []
    assert data["approver_identities"] == []
    assert data["bound_room_ids"] == []
    assert data["approver_matrix_ids"] == []


def test_enforce_conversation_binding_requires_a_complete_matching_context():
    from app.services.message_context import MessageContext
    from app.services.tool_token import enforce_conversation_binding

    context = MessageContext(
        channel="matrix",
        channel_account_id="default",
        conversation_id="!ops:example.org",
        message_id="$event",
        sender_id="@caller:example.org",
        content_sha256="a" * 64,
    )
    enforce_conversation_binding([MATRIX_ROOM], message_context=context)
    enforce_conversation_binding(
        [MATRIX_ROOM],
        channel="matrix",
        channel_account_id="default",
        conversation_id="!ops:example.org",
    )
    enforce_conversation_binding([], message_context=None)

    for kwargs in (
        {},
        {"message_context": {"channel": "matrix"}},
        {
            "channel": "wechat",
            "channel_account_id": "default",
            "conversation_id": "!ops:example.org",
        },
    ):
        with pytest.raises(HTTPException) as exc:
            enforce_conversation_binding([MATRIX_ROOM], **kwargs)
        assert exc.value.status_code == 403


def test_approver_matching_distinguishes_unrestricted_from_no_channel_match():
    from app.services.tool_token import matching_approver_sender_ids

    assert (
        matching_approver_sender_ids(
            [], channel="wechat", channel_account_id="corp-a"
        )
        is None
    )
    assert matching_approver_sender_ids(
        [MATRIX_APPROVER], channel="wechat", channel_account_id="corp-a"
    ) == []
    assert matching_approver_sender_ids(
        [MATRIX_APPROVER], channel="matrix", channel_account_id="default"
    ) == ["@alice:example.org"]


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
def test_mcp_call_paths_enforce_generic_message_context(tmp_path, stream):
    from app.services.mcp_capability_service import mcp_call_tool, mcp_call_tool_stream
    from app.services.tool_context import ToolContext
    from app.services.tool_registry import registry

    engine = _engine(tmp_path, f"mcp-generic-guard-{stream}.db")
    db = _session(engine)
    tool_name = f"ops.contract.generic_guard_{stream}"

    @registry.register(
        name=tool_name,
        description="generic channel guard integration",
        input_schema={
            "type": "object",
            "properties": {"message_context": {"type": "object"}},
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
        result = invoke(
            db,
            ctx,
            {
                "name": tool_name.replace(".", "_"),
                "arguments": {"message_context": _message_context()},
            },
        )
        assert result["isError"] is False

        for arguments in (
            {},
            {"message_context": {"channel": "wechat"}},
            {"message_context": _message_context(conversation_id="group-8")},
        ):
            with pytest.raises(HTTPException) as exc:
                invoke(
                    db,
                    ctx,
                    {"name": tool_name.replace(".", "_"), "arguments": arguments},
                )
            assert exc.value.status_code == 403
    finally:
        registry._tools.pop(tool_name, None)
        db.close()
        engine.dispose()


@pytest.mark.parametrize("stream", [False, True])
def test_mcp_call_paths_normalize_legacy_matrix_room_context(tmp_path, stream):
    from app.services.mcp_capability_service import mcp_call_tool, mcp_call_tool_stream
    from app.services.tool_context import ToolContext
    from app.services.tool_registry import registry

    engine = _engine(tmp_path, f"mcp-matrix-guard-{stream}.db")
    db = _session(engine)
    tool_name = f"ops.contract.matrix_guard_{stream}"

    @registry.register(
        name=tool_name,
        description="legacy Matrix channel guard integration",
        input_schema={
            "type": "object",
            "properties": {"room_id": {"type": "string"}},
            "additionalProperties": False,
        },
        scopes=["ops:read"],
        category="approval_prepare",
        streamable=True,
    )
    def _guarded(args, ctx, db, stream_callback=None):
        return {"accepted": True}

    ctx = ToolContext(
        username="tester",
        auth_type="session",
        is_admin=True,
        scopes=["*"],
        channel_bindings=[MATRIX_ROOM],
    )
    invoke = mcp_call_tool_stream if stream else mcp_call_tool
    try:
        result = invoke(
            db,
            ctx,
            {
                "name": tool_name.replace(".", "_"),
                "arguments": {"room_id": "!ops:example.org"},
            },
        )
        assert result["isError"] is False
        with pytest.raises(HTTPException) as exc:
            invoke(
                db,
                ctx,
                {
                    "name": tool_name.replace(".", "_"),
                    "arguments": {"room_id": "!other:example.org"},
                },
            )
        assert exc.value.status_code == 403
    finally:
        registry._tools.pop(tool_name, None)
        db.close()
        engine.dispose()


@pytest.mark.parametrize("channel", ["wechat", "telegram"])
def test_legacy_matrix_approval_rejects_cross_channel_token_approvers(
    tmp_path, monkeypatch, channel
):
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
        with pytest.raises(HTTPException) as exc:
            approval_tools.approval_prepare_service_control(
                {
                    "room_id": "!ops:example.org",
                    "request_event_id": "$event",
                    "content_sha256": "b" * 64,
                    "system_name": "crypto-trader",
                    "service_name": "strategy",
                    "environment": "test",
                    "control_action": "restart",
                    "targets": ["server-1"],
                    "routing_config_revision": "rev-1",
                    "routing_ticket_digest": "ticket-1",
                },
                ctx,
                db,
            )
        assert exc.value.status_code == 403
        assert "authorized approver" in str(exc.value.detail).lower()
    finally:
        db.close()
        engine.dispose()


def test_central_registry_guard_covers_direct_rest_stream_and_stdio_dispatch(
    tmp_path, monkeypatch
):
    import asyncio

    from app.api import tools as tools_api
    from app.mcp import server as stdio_server
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
        with pytest.raises(HTTPException) as direct_exc:
            registry.call(db, tool_name, {}, _ctx("wechat", "corp-a", "group-7"))
        assert direct_exc.value.status_code == 403

        with pytest.raises(HTTPException) as direct_stream_exc:
            registry.call(
                db,
                tool_name,
                _generic_args("telegram", "bot-a", "chat-8"),
                _ctx("telegram", "bot-a", "chat-7"),
                stream_callback=lambda chunk: None,
            )
        assert direct_stream_exc.value.status_code == 403

        active_ctx = [_ctx("matrix", "secondary", "!ops:example.org")]
        monkeypatch.setattr(
            tools_api,
            "get_tool_context",
            lambda request, session: active_ctx[0],
        )
        with pytest.raises(HTTPException) as rest_exc:
            tools_api.call_tool(
                tools_api.ToolCallPayload(
                    tool=tool_name,
                    arguments={"room_id": "!ops:example.org"},
                ),
                SimpleNamespace(),
                db,
            )
        assert rest_exc.value.status_code == 403

        active_ctx[0] = _ctx("wechat", "corp-a", "group-7")
        with pytest.raises(HTTPException) as rest_stream_exc:
            asyncio.run(
                tools_api.call_tool_stream(
                    tools_api.ToolCallPayload(
                        tool=tool_name,
                        arguments={"message_context": {"channel": "wechat"}},
                    ),
                    SimpleNamespace(),
                    db,
                )
            )
        assert rest_stream_exc.value.status_code == 403

        active_ctx[0] = _ctx("telegram", "bot-a", "chat-7")

        def _stdio_request(method, path, payload):
            assert method == "POST"
            assert path == "/api/v2/tools/call"
            return tools_api.call_tool(
                tools_api.ToolCallPayload(**payload),
                SimpleNamespace(),
                db,
            )

        monkeypatch.setattr(stdio_server, "_request", _stdio_request)
        with pytest.raises(HTTPException) as stdio_exc:
            stdio_server._call_tool_for_mcp(
                {
                    "name": tool_name.replace(".", "_"),
                    "arguments": _generic_args("telegram", "bot-a", "chat-8"),
                }
            )
        assert stdio_exc.value.status_code == 403
        assert handled == []
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
    finally:
        db.close()
        engine.dispose()


def test_tool_token_create_and_update_audit_generic_binding_policy(
    tmp_path, monkeypatch
):
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
    update_bindings = [
        {
            "channel": "telegram",
            "channel_account_id": "bot-a",
            "conversation_id": "chat-9",
        }
    ]
    update_approvers = [
        {
            "channel": "wechat",
            "channel_account_id": "corp-a",
            "sender_id": "owner-1",
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
            tools_api.UpdateToolTokenPayload(
                channel_bindings=update_bindings,
                approver_identities=update_approvers,
            ),
            SimpleNamespace(),
            db,
        )

        details = {call[0]: call[3] for call in audit_calls}
        create_detail = details["tool.token.create"]
        update_detail = details["tool.token.update"]
        assert (
            "channel_bindings="
            + json.dumps(create_bindings, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        ) in create_detail
        assert (
            "approver_identities="
            + json.dumps(create_approvers, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        ) in create_detail
        assert (
            "channel_bindings="
            + json.dumps(update_bindings, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        ) in update_detail
        assert (
            "approver_identities="
            + json.dumps(update_approvers, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        ) in update_detail
    finally:
        db.close()
        engine.dispose()


def test_token_and_preview_contexts_read_only_generic_columns(tmp_path, monkeypatch):
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
        assert runtime.channel_bindings == token.channel_bindings
        assert runtime.approver_identities == token.approver_identities
        assert runtime.bound_room_ids == []
        assert preview.channel_bindings == token.channel_bindings
        assert preview.approver_identities == token.approver_identities
        assert subject["channel_bindings"] == token.channel_bindings
        assert subject["approver_identities"] == token.approver_identities
    finally:
        db.close()
        engine.dispose()


def test_api_payloads_accept_generic_and_legacy_fields_and_track_omission():
    from app.api.tools import CreateToolTokenPayload, UpdateToolTokenPayload

    create = CreateToolTokenPayload(
        name="generic",
        channel_bindings=[MATRIX_ROOM],
        approver_identities=[MATRIX_APPROVER],
    )
    assert create.channel_bindings == [MATRIX_ROOM]
    assert create.approver_identities == [MATRIX_APPROVER]

    update = UpdateToolTokenPayload(channel_bindings=[], approver_identities=[])
    assert update.channel_bindings == []
    assert update.approver_identities == []
    assert update.bound_room_ids is None
    assert update.approver_matrix_ids is None


def test_update_explicit_empty_clears_generic_bindings(tmp_path, monkeypatch):
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
            UpdateToolTokenPayload(channel_bindings=[], approver_identities=[]),
            SimpleNamespace(),
            db,
        )
        db.refresh(token)
        assert token.channel_bindings == []
        assert token.approver_identities == []
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
