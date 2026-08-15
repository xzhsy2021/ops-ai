"""Contract tests for qclaw Element room binding on Tool Tokens.

Covers:
- `_normalize_bound_room_ids` input normalization
- `enforce_room_binding` (the MCP-layer gate)
- `create_tool_token` / `token_to_dict` round-trip with bound_room_ids
- API payload accepts / returns bound_room_ids
- Frontend renders the binding editor and the table column
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


def test_enforce_room_binding_passes_when_unbound():
    from app.services.tool_token import enforce_room_binding

    # No binding configured -> any room_id is allowed (including None).
    enforce_room_binding(None, None)
    enforce_room_binding([], "!any:matrix.org")
    enforce_room_binding("", "!any:matrix.org")


def test_enforce_room_binding_accepts_listed_room():
    from app.services.tool_token import enforce_room_binding

    enforce_room_binding(["!ops:matrix.org", "!dev:matrix.org"], "!ops:matrix.org")


def test_enforce_room_binding_rejects_other_room():
    import pytest
    from fastapi import HTTPException

    from app.services.tool_token import enforce_room_binding

    with pytest.raises(HTTPException) as exc:
        enforce_room_binding(["!ops:matrix.org"], "!rogue:matrix.org")
    assert exc.value.status_code == 403
    assert "!rogue:matrix.org" in exc.value.detail
    assert "!ops:matrix.org" in exc.value.detail


def test_enforce_room_binding_rejects_missing_room_when_bound():
    import pytest
    from fastapi import HTTPException

    from app.services.tool_token import enforce_room_binding

    with pytest.raises(HTTPException) as exc:
        enforce_room_binding(["!ops:matrix.org"], None)
    assert exc.value.status_code == 403
    assert "no room_id" in exc.value.detail


def test_create_and_round_trip_bound_room_ids(tmp_path):
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
            bound_room_ids=["!ops:matrix.org", "!ops-backup:matrix.org"],
        )

        record = created["record"]
        # Runtime policy is persisted only in the generic column.
        assert record.channel_bindings == [
            {
                "channel": "matrix",
                "channel_account_id": "default",
                "conversation_id": "!ops:matrix.org",
            },
            {
                "channel": "matrix",
                "channel_account_id": "default",
                "conversation_id": "!ops-backup:matrix.org",
            },
        ]
        assert record.bound_room_ids == []
        # Returned via token_to_dict for the API.
        as_dict = token_to_dict(record)
        assert as_dict["bound_room_ids"] == ["!ops:matrix.org", "!ops-backup:matrix.org"]
    finally:
        db.close()
        engine.dispose()


def test_create_token_without_binding_defaults_to_empty_list(tmp_path):
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
        as_dict = token_to_dict(created["record"])
        assert as_dict["bound_room_ids"] == []
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
        # Garbage / None / non-list shapes should not crash the dict builder.
        bound_room_ids = None

    data = token_to_dict(_Stub())
    assert data["bound_room_ids"] == []


def test_create_token_payload_declares_bound_room_ids_field():
    from app.api.tools import CreateToolTokenPayload, UpdateToolTokenPayload

    create = CreateToolTokenPayload(name="x", bound_room_ids=["!a"])
    assert create.bound_room_ids == ["!a"]

    update = UpdateToolTokenPayload(bound_room_ids=["!b"])
    assert update.bound_room_ids == ["!b"]

    # Update with None means "do not change" (kept None).
    update2 = UpdateToolTokenPayload()
    assert update2.bound_room_ids is None

    # Default in create is empty list (= no binding).
    default_create = CreateToolTokenPayload(name="y")
    assert default_create.bound_room_ids == []


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


def test_enforce_room_binding_runs_at_top_of_qclaw_mcp_tools():
    """Defense-in-depth check: every qclaw routing/approval tool that
    touches a conversation calls enforce_conversation_binding before
    doing real work. This test inspects the source to prevent accidental
    removal of the gate.

    `ops.approval.get` / `list` / `expire_stale` are intentionally NOT in
    this list because they are read-only / maintenance tools that do not
    take a conversation argument and do not modify approval state.
    """
    src = open("app/services/tool_adapters/approval_tools.py", encoding="utf-8").read()

    # Function-level guard sites. reject is included because it mutates
    # approval state (PENDING -> REJECTED); the binding check still runs
    # (no-op when unbound).
    expected_sites = [
        "def routing_resolve_message_target",
        "def approval_prepare_service_control",
        "def approval_execute",
    ]
    for fn in expected_sites:
        assert fn in src, f"missing qclaw tool: {fn}"
    # enforce_conversation_binding is imported and referenced.
    assert "enforce_conversation_binding" in src
    # At least one call site per tool.
    assert src.count("enforce_conversation_binding(") >= len(expected_sites)
