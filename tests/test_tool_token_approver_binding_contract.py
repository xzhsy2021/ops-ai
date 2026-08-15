"""Contract tests for qclaw Element approver resolution on Tool Tokens.

Covers:
- `_normalize_approver_ids` input normalization (legacy token field)
- `create_tool_token` no longer stores approver bindings (Phase 2)
- API payloads no longer accept approver_matrix_ids / approver_identities
- `_lookup_approvers` uses system/service message_routing as the sole source
- `_lookup_approvers` returns empty when no system config (no token fallback)
- consume() enforces the resolved authorized approver whitelist
"""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def _sqlite_session(tmp_path):
    from app.db.models import Base

    engine = create_engine(
        f"sqlite:///{tmp_path / 'tool_token_approver_binding.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    return engine, Session


def test_normalize_approver_ids_strips_blanks_and_dedupes():
    from app.services.tool_token import _normalize_approver_ids

    assert _normalize_approver_ids(None) == []
    assert _normalize_approver_ids([]) == []
    assert _normalize_approver_ids("") == []
    assert _normalize_approver_ids("  ") == []
    # newline-separated, preserve first-seen order
    assert _normalize_approver_ids("@a:matrix.org\n@b\n@a:matrix.org\n@c") == [
        "@a:matrix.org", "@b", "@c",
    ]
    # list input: non-string entries are dropped silently
    assert _normalize_approver_ids(["@a", "", "@b", "@a", 123, None]) == ["@a", "@b"]
    # unknown shape -> empty (not crash)
    assert _normalize_approver_ids(12345) == []


def test_create_and_round_trip_approver_matrix_ids(tmp_path):
    """token 不再存储审批人绑定；create 后四个绑定列均为空，dict 不再导出。"""
    from app.services.tool_token import create_tool_token, token_to_dict

    engine, Session = _sqlite_session(tmp_path)
    db = Session()
    try:
        created = create_tool_token(
            db,
            name="qclaw-prod",
            owner="admin",
            description="qclaw prod room",
            scopes=["ops:read"],
            allow_write=False,
            expires_in_days=30,
        )

        record = created["record"]
        assert record.approver_identities == []
        assert record.approver_matrix_ids == []
        assert record.channel_bindings == []
        assert record.bound_room_ids == []
        as_dict = token_to_dict(record)
        assert "approver_matrix_ids" not in as_dict
        assert "approver_identities" not in as_dict
        assert "channel_bindings" not in as_dict
        assert "bound_room_ids" not in as_dict
    finally:
        db.close()
        engine.dispose()


def test_create_token_without_approvers_defaults_to_empty_list(tmp_path):
    from app.services.tool_token import create_tool_token, token_to_dict

    engine, Session = _sqlite_session(tmp_path)
    db = Session()
    try:
        created = create_tool_token(
            db,
            name="unbound-qclaw",
            owner="admin",
            scopes=["ops:read"],
        )
        record = created["record"]
        assert record.approver_identities == []
        assert record.approver_matrix_ids == []
        as_dict = token_to_dict(record)
        assert "approver_matrix_ids" not in as_dict
    finally:
        db.close()
        engine.dispose()


def test_token_to_dict_normalizes_garbage_in_db():
    from app.services.tool_token import token_to_dict

    class _Stub:
        id = "x"
        name = "x"
        owner = "admin"
        description = ""
        scopes = ["ops:read"]
        allow_write = False
        allow_prod = False
        token_prefix = "ops_tool_"
        created_at = None
        expires_at = None
        last_used_at = None
        revoked_at = None

    data = token_to_dict(_Stub())
    assert "approver_matrix_ids" not in data
    assert "approver_identities" not in data


def test_create_token_payload_declares_approver_matrix_ids_field():
    from app.api.tools import CreateToolTokenPayload, UpdateToolTokenPayload

    # 审批人绑定已统一到系统级，Token 载荷不再接收 approver_matrix_ids。
    create = CreateToolTokenPayload(name="x")
    assert not hasattr(create, "approver_matrix_ids")
    assert not hasattr(create, "approver_identities")
    assert not hasattr(create, "channel_bindings")
    assert not hasattr(create, "bound_room_ids")

    update = UpdateToolTokenPayload()
    assert not hasattr(update, "approver_matrix_ids")
    assert not hasattr(update, "approver_identities")
    assert not hasattr(update, "channel_bindings")
    assert not hasattr(update, "bound_room_ids")


class _Ctx:
    def __init__(self, approver_identities=None):
        self.approver_identities = approver_identities or []


def test_lookup_approvers_prefers_system_config_over_token(monkeypatch):
    """系统设置 message_routing 为审批人唯一来源，优先于 token 级。"""
    from app.services.tool_adapters import approval_tools
    from app.services.tool_adapters.approval_tools import _lookup_approvers

    monkeypatch.setattr(approval_tools, "get_all_systems", lambda: {
        "crypto-trader": {
            "message_routing": {
                "approvers": [
                    {"channel": "matrix", "channel_account_id": "default", "sender_id": "@owner:matrix.org"},
                ],
            },
            "services": [],
        },
    })

    # token 级有不同的审批人，但系统配置优先，token 不覆盖系统。
    ctx = _Ctx(approver_identities=[
        {
            "channel": "matrix",
            "channel_account_id": "default",
            "sender_id": "@jack.han:matrix.org",
        },
        {
            "channel": "matrix",
            "channel_account_id": "default",
            "sender_id": "@alice:matrix.org",
        },
    ])
    result = _lookup_approvers(
        ctx,
        "crypto-trader",
        "crypto-exchange",
        channel="matrix",
        channel_account_id="default",
    )
    assert result == ["@owner:matrix.org"]


def test_lookup_approvers_no_system_config_returns_empty(monkeypatch):
    """已彻底移除 token 级 legacy 回退：系统未配置审批人时返回空列表。"""
    from app.services.tool_adapters import approval_tools
    from app.services.tool_adapters.approval_tools import _lookup_approvers

    monkeypatch.setattr(approval_tools, "get_all_systems", lambda: {})

    ctx = _Ctx(approver_identities=[
        {
            "channel": "matrix",
            "channel_account_id": "default",
            "sender_id": "@jack.han:matrix.org",
        },
        {
            "channel": "matrix",
            "channel_account_id": "default",
            "sender_id": "@alice:matrix.org",
        },
    ])
    # token 级 approver_identities 不再生效，系统未配置审批人 -> 空列表。
    assert _lookup_approvers(
        ctx,
        "crypto-trader",
        "crypto-exchange",
        channel="matrix",
        channel_account_id="default",
    ) == []


def test_lookup_approvers_falls_back_when_no_system_or_token_approvers(monkeypatch):
    from app.services.tool_adapters import approval_tools
    from app.services.tool_adapters.approval_tools import _lookup_approvers

    # 隔离全局 DB 状态：无系统/服务 message_routing 配置时回退为空列表
    monkeypatch.setattr(approval_tools, "get_all_systems", lambda: {})

    ctx = _Ctx()  # no token-level whitelist
    # No system/service message_routing configured -> empty list (backward
    # compatible: no approver restriction).
    assert _lookup_approvers(
        ctx,
        "crypto-trader",
        "crypto-exchange",
        channel="matrix",
        channel_account_id="default",
    ) == []
    assert _lookup_approvers(
        ctx,
        "",
        "",
        channel="matrix",
        channel_account_id="default",
    ) == []


def test_lookup_approvers_empty_ctx(monkeypatch):
    from app.services.tool_adapters import approval_tools
    from app.services.tool_adapters.approval_tools import _lookup_approvers

    # 隔离全局 DB 状态：无系统配置时回退为空列表
    monkeypatch.setattr(approval_tools, "get_all_systems", lambda: {})

    # ctx without approver_matrix_ids attribute (older code paths)
    class _BareCtx:
        pass

    assert _lookup_approvers(
        _BareCtx(),
        "crypto-trader",
        channel="matrix",
        channel_account_id="default",
    ) == []


def test_consume_enforces_token_whitelist_via_prepare(tmp_path):
    """Integration: prepare with a token whitelist -> consume by a whitelisted
    user succeeds, consume by a non-whitelisted user is rejected.
    """
    from app.services.action_approval import ActionApprovalService

    engine, Session = _sqlite_session(tmp_path)
    db = Session()
    try:
        service = ActionApprovalService(db)
        approval, short_code = service.prepare(
            action_type="SERVICE_CONTROL",
            tool_name="ops.approval.prepare_service_control",
            room_id="!ops:matrix.org",
            request_event_id="$evt:matrix.org",
            content_sha256="abc123",
            system_name="crypto-trader",
            service_name="crypto-exchange",
            environment="test",
            targets=["server-1"],
            action_parameters={"control_action": "restart"},
            routing_config_revision="rev1",
            routing_ticket_digest="ticket123",
            authorized_matrix_users=["@jack.han:matrix.org"],
        )

        # Non-whitelisted approver -> rejected (None), 请求状态不被改变
        rejected = service.consume(
            approval_id=approval.id,
            short_code=short_code,
            approver_matrix_id="@alice:matrix.org",
            room_id="!ops:matrix.org",
            approval_event_id="$approval:matrix.org",
        )
        assert rejected is None
        db.refresh(approval)
        # Task 6 设计：未授权尝试不改变请求状态（fail-closed 但保持 PENDING_APPROVAL）
        assert approval.status == "PENDING_APPROVAL"

        # A fresh approval consumed by a whitelisted approver -> EXECUTING
        approval2, short_code2 = service.prepare(
            action_type="SERVICE_CONTROL",
            tool_name="ops.approval.prepare_service_control",
            room_id="!ops:matrix.org",
            request_event_id="$evt2:matrix.org",
            content_sha256="def456",
            system_name="crypto-trader",
            service_name="crypto-exchange",
            environment="test",
            targets=["server-1"],
            action_parameters={"control_action": "restart"},
            routing_config_revision="rev1",
            routing_ticket_digest="ticket456",
            authorized_matrix_users=["@jack.han:matrix.org"],
        )
        accepted = service.consume(
            approval_id=approval2.id,
            short_code=short_code2,
            approver_matrix_id="@jack.han:matrix.org",
            room_id="!ops:matrix.org",
            approval_event_id="$approval2:matrix.org",
        )
        assert accepted is not None
        assert accepted.status == "EXECUTING"
        assert accepted.approved_by == "matrix:default:@jack.han:matrix.org"
    finally:
        db.close()
        engine.dispose()
