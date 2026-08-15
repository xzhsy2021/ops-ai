"""Contract tests for qclaw room binding on Tool Tokens (Phase 2).

Phase 2 已彻底移除 token 级房间绑定：Token 不再存储 bound_room_ids /
channel_bindings，房间作用域统一到系统级 message_routing.rooms。

Covers:
- `_normalize_bound_room_ids` input normalization (still a utility)
- token-level enforcement (`enforce_room_binding` / `enforce_conversation_binding`) removed
- `create_tool_token` / `token_to_dict` no longer store / export bindings
- API payload no longer accepts bound_room_ids
- Frontend rendered binding editor is gone
- System-level `_enforce_system_room` replaces the token-level gate
"""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def _sqlite_session(tmp_path):
    from app.db.models import Base

    engine = create_engine(
        f"sqlite:///{tmp_path / 'tool_token_room_binding.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    return engine, Session


def test_normalize_bound_room_ids_strips_blanks_and_dedupes():
    from app.services.tool_token import _normalize_bound_room_ids

    assert _normalize_bound_room_ids(None) == []
    assert _normalize_bound_room_ids([]) == []
    assert _normalize_bound_room_ids("") == []
    assert _normalize_bound_room_ids("  ") == []
    # newline-separated (one room per line), preserve first-seen order
    assert _normalize_bound_room_ids("!a:matrix.org\n!b\n!a:matrix.org\n!c") == [
        "!a:matrix.org", "!b", "!c",
    ]
    # Matrix room IDs are allowed to contain commas; do NOT split on ","
    # because that would corrupt IDs like "!x,y:server" if anyone uses them.
    assert _normalize_bound_room_ids("!a:matrix.org, !b") == ["!a:matrix.org, !b"]
    # list input: non-string entries are dropped silently (no auto "123"
    # / "None" room IDs).
    assert _normalize_bound_room_ids(["!a", "", "!b", "!a", 123, None]) == ["!a", "!b"]
    # unknown shape -> empty (not crash)
    assert _normalize_bound_room_ids(12345) == []


def test_token_level_room_enforcement_removed():
    """Phase 2：enforce_room_binding / enforce_conversation_binding 已从
    tool_token 移除，token 级房间校验不再存在。"""
    import app.services.tool_token as tt

    assert not hasattr(tt, "enforce_room_binding")
    assert not hasattr(tt, "enforce_conversation_binding")


def test_create_and_round_trip_bound_room_ids(tmp_path):
    """token 不再存储房间绑定；create 后四个绑定列均为空，dict 不再导出。"""
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
        assert record.channel_bindings == []
        assert record.bound_room_ids == []
        assert record.approver_identities == []
        assert record.approver_matrix_ids == []
        as_dict = token_to_dict(record)
        assert "bound_room_ids" not in as_dict
        assert "channel_bindings" not in as_dict
    finally:
        db.close()
        engine.dispose()


def test_create_token_without_binding_defaults_to_empty_list(tmp_path):
    from app.services.tool_token import create_tool_token

    engine, Session = _sqlite_session(tmp_path)
    db = Session()
    try:
        created = create_tool_token(
            db,
            name="unbound-qclaw",
            owner="admin",
            scopes=["ops:read"],
        )
        assert created["record"].channel_bindings == []
        assert created["record"].bound_room_ids == []
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
    assert "bound_room_ids" not in data
    assert "channel_bindings" not in data


def test_create_token_payload_declares_bound_room_ids_field():
    from app.api.tools import CreateToolTokenPayload, UpdateToolTokenPayload

    # 房间绑定已统一到系统级，Token 载荷不再接收 bound_room_ids。
    create = CreateToolTokenPayload(name="x")
    assert not hasattr(create, "bound_room_ids")
    assert not hasattr(create, "channel_bindings")

    update = UpdateToolTokenPayload()
    assert not hasattr(update, "bound_room_ids")
    assert not hasattr(update, "channel_bindings")


def test_frontend_removed_room_binding_editor_from_token_panel():
    panel = open("frontend/src/pages/tools/ToolTokenPanel.tsx", encoding="utf-8").read()
    page = open("frontend/src/pages/ToolAccessPage.tsx", encoding="utf-8").read()

    # 房间绑定已统一到系统设置，Token 面板不再渲染 RoomEditor / 房间徽标。
    assert "RoomEditor" not in panel
    assert "bindings" not in panel
    assert "editBindings" not in panel
    assert "房间: " not in panel
    # 面板以提示引导用户到系统设置统一配置房间。
    assert "已迁移至系统设置" in panel
    # create/update 不再透传 token 级房间绑定到 API。
    assert "channel_bindings" not in panel
    assert "bound_room_ids: data.bound_room_ids || []" not in page
    assert "bound_room_ids: data.bound_room_ids ?? []" not in page


def test_system_room_enforcement_replaces_token_level_gate():
    """Phase 2：token 级房间校验已移除，改为系统级 message_routing.rooms
    作用域。审批准备/临时授权/审批执行等工具在知晓 system_name 后调用
    _enforce_system_room 强制。
    """
    src = open("app/services/tool_adapters/approval_tools.py", encoding="utf-8").read()
    registry_src = open("app/services/tool_registry.py", encoding="utf-8").read()

    # token 级 enforce_conversation_binding 已彻底移除。
    assert "enforce_conversation_binding" not in src
    assert "channel_bindings" not in src
    # 系统级房间作用域函数存在并被调用。
    assert "_enforce_system_room" in src
    assert "def _enforce_system_room" in src
    # registry 不再做 token 级绑定校验。
    assert "enforce_conversation_binding" not in registry_src
    assert "channel_bindings" not in registry_src
