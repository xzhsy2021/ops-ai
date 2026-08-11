from __future__ import annotations

from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest

from app.services.message_context import (
    SUPPORTED_CHANNELS,
    MessageContext,
    message_context_schema,
    normalize_conversation_binding,
    normalize_identity,
    normalize_message_context,
)
from app.services.tool_context import ToolContext


SHA256 = "a" * 64


def _message_context(channel: str = "matrix") -> dict[str, str]:
    return {
        "channel": channel,
        "channel_account_id": "primary",
        "conversation_id": "room-1",
        "message_id": "message-1",
        "sender_id": "user-1",
        "content_sha256": SHA256,
    }


@pytest.mark.parametrize("channel", ["matrix", "wechat", "telegram"])
def test_message_context_builds_stable_keys_and_round_trips(channel):
    ctx = MessageContext.from_dict(_message_context(channel))

    assert ctx.actor_key == f"{channel}:primary:user-1"
    assert ctx.conversation_key == f"{channel}:primary:room-1"
    assert ctx.to_dict() == _message_context(channel)


def test_message_context_is_frozen_and_normalizes_only_harmless_text():
    raw = _message_context()
    raw.update(
        {
            "channel": " MATRIX ",
            "channel_account_id": " Account-A ",
            "conversation_id": " !Room:Example.org ",
            "message_id": " $Event:Example.org ",
            "sender_id": " @Alice:Example.org ",
            "content_sha256": ("AB" * 32),
        }
    )

    ctx = MessageContext.from_dict(raw)

    assert ctx.channel == "matrix"
    assert ctx.channel_account_id == "Account-A"
    assert ctx.conversation_id == "!Room:Example.org"
    assert ctx.message_id == "$Event:Example.org"
    assert ctx.sender_id == "@Alice:Example.org"
    assert ctx.content_sha256 == "ab" * 32
    with pytest.raises(FrozenInstanceError):
        ctx.sender_id = "other"  # type: ignore[misc]


@pytest.mark.parametrize("channel", ["email", "", None])
def test_message_context_rejects_unsupported_channel(channel):
    raw = _message_context()
    raw["channel"] = channel

    with pytest.raises(ValueError, match="channel"):
        MessageContext.from_dict(raw)


@pytest.mark.parametrize(
    "field",
    [
        "channel_account_id",
        "conversation_id",
        "message_id",
        "sender_id",
        "content_sha256",
    ],
)
def test_message_context_rejects_missing_or_blank_stable_fields(field):
    missing = _message_context()
    missing.pop(field)
    with pytest.raises(ValueError, match=field):
        MessageContext.from_dict(missing)

    blank = _message_context()
    blank[field] = "  "
    with pytest.raises(ValueError, match=field):
        MessageContext.from_dict(blank)


@pytest.mark.parametrize(
    "digest",
    ["a" * 63, "a" * 65, "g" * 64, "sha256:" + SHA256, 123],
)
def test_message_context_rejects_malformed_sha256(digest):
    raw = _message_context()
    raw["content_sha256"] = digest

    with pytest.raises(ValueError, match="content_sha256"):
        MessageContext.from_dict(raw)


def test_message_context_rejects_unknown_fields():
    raw = _message_context()
    raw["display_name"] = "Alice"

    with pytest.raises(ValueError, match="unexpected"):
        MessageContext.from_dict(raw)


def test_message_context_rejects_colon_in_channel_account_id():
    raw = _message_context()
    raw["channel_account_id"] = "primary:west"

    with pytest.raises(ValueError, match="channel_account_id"):
        MessageContext.from_dict(raw)


def test_valid_message_context_tuples_cannot_collide():
    tuples = [
        ("tenant", "ops:alice", "ops:room"),
        ("tenant.ops", "alice", "room"),
        ("tenant-ops", "ops:alice", "ops:room"),
        ("tenant_ops", "alice", "room"),
    ]
    contexts = []
    for account_id, sender_id, conversation_id in tuples:
        raw = _message_context()
        raw.update(
            {
                "channel_account_id": account_id,
                "sender_id": sender_id,
                "conversation_id": conversation_id,
            }
        )
        contexts.append(MessageContext.from_dict(raw))

    assert len({context.actor_key for context in contexts}) == len(tuples)
    assert len({context.conversation_key for context in contexts}) == len(tuples)


def test_message_context_rejects_non_string_unknown_field_keys_cleanly():
    raw = _message_context()
    raw[1] = "unexpected"

    with pytest.raises(ValueError, match="unexpected"):
        MessageContext.from_dict(raw)


@pytest.mark.parametrize("event_field", ["request_event_id", "event_id"])
def test_legacy_matrix_fields_normalize_at_boundary_only(event_field):
    raw = {
        "room_id": " !ops:example.org ",
        event_field: " $event ",
        "sender_matrix_id": " @alice:example.org ",
        "content_sha256": "A" * 64,
    }

    ctx = normalize_message_context(raw)

    assert ctx.to_dict() == {
        "channel": "matrix",
        "channel_account_id": "default",
        "conversation_id": "!ops:example.org",
        "message_id": "$event",
        "sender_id": "@alice:example.org",
        "content_sha256": SHA256,
    }


@pytest.mark.parametrize(
    "raw",
    [
        {**_message_context(), "room_id": "!legacy:example.org"},
        {
            "channel": "matrix",
            "room_id": "!legacy:example.org",
            "request_event_id": "$event",
            "sender_matrix_id": "@alice:example.org",
            "content_sha256": SHA256,
        },
        {
            "room_id": "!ops:example.org",
            "request_event_id": "$one",
            "event_id": "$two",
            "sender_matrix_id": "@alice:example.org",
            "content_sha256": SHA256,
        },
        {
            "room_id": "!ops:example.org",
            "request_event_id": "$event",
            "content_sha256": SHA256,
        },
    ],
)
def test_message_context_rejects_ambiguous_or_partial_legacy_input(raw):
    with pytest.raises(ValueError):
        normalize_message_context(raw)


def test_message_context_schema_is_closed_and_reusable():
    schema = message_context_schema()

    assert SUPPORTED_CHANNELS == frozenset({"matrix", "wechat", "telegram"})
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert schema["required"] == [
        "channel",
        "channel_account_id",
        "conversation_id",
        "message_id",
        "sender_id",
        "content_sha256",
    ]
    assert set(schema["properties"]["channel"]["enum"]) == SUPPORTED_CHANNELS
    account_schema = schema["properties"]["channel_account_id"]
    assert account_schema["pattern"] == "^[A-Za-z0-9._-]{1,128}$"
    assert account_schema["maxLength"] == 128
    assert schema["properties"]["content_sha256"]["pattern"] == "^[0-9a-fA-F]{64}$"

    schema["required"].clear()
    assert message_context_schema()["required"]


@pytest.mark.parametrize("channel", ["matrix", "wechat", "telegram"])
def test_structured_identity_and_conversation_binding_normalize(channel):
    assert normalize_identity(
        {
            "channel": f" {channel.upper()} ",
            "channel_account_id": " primary ",
            "sender_id": " User-ID ",
        }
    ) == {
        "channel": channel,
        "channel_account_id": "primary",
        "sender_id": "User-ID",
    }
    assert normalize_conversation_binding(
        {
            "channel": f" {channel.upper()} ",
            "channel_account_id": " primary ",
            "conversation_id": " Room-ID ",
        }
    ) == {
        "channel": channel,
        "channel_account_id": "primary",
        "conversation_id": "Room-ID",
    }


def test_legacy_matrix_identity_and_binding_strings_normalize():
    assert normalize_identity(" @alice:example.org ") == {
        "channel": "matrix",
        "channel_account_id": "default",
        "sender_id": "@alice:example.org",
    }
    assert normalize_conversation_binding(" !ops:example.org ") == {
        "channel": "matrix",
        "channel_account_id": "default",
        "conversation_id": "!ops:example.org",
    }


@pytest.mark.parametrize(
    ("normalizer", "raw"),
    [
        (
            normalize_identity,
            {
                "channel": "matrix",
                "channel_account_id": "primary:west",
                "sender_id": "@alice:example.org",
            },
        ),
        (
            normalize_conversation_binding,
            {
                "channel": "matrix",
                "channel_account_id": "primary:west",
                "conversation_id": "!ops:example.org",
            },
        ),
    ],
)
def test_identity_and_binding_reject_colon_in_channel_account_id(normalizer, raw):
    with pytest.raises(ValueError, match="channel_account_id"):
        normalizer(raw)


@pytest.mark.parametrize(
    ("normalizer", "raw"),
    [
        (normalize_identity, {"channel": "matrix", "sender_id": "@alice:x"}),
        (
            normalize_identity,
            {
                "channel": "matrix",
                "channel_account_id": "default",
                "sender_id": "@alice:x",
                "conversation_id": "!room:x",
            },
        ),
        (
            normalize_conversation_binding,
            {"channel": "matrix", "conversation_id": "!room:x"},
        ),
        (normalize_conversation_binding, "  "),
    ],
)
def test_identity_and_binding_normalizers_fail_closed(normalizer, raw):
    with pytest.raises(ValueError):
        normalizer(raw)


def test_tool_context_audit_serializes_generic_and_legacy_bindings():
    ctx = ToolContext(
        username="tester",
        channel_bindings=[
            {
                "channel": "wechat",
                "channel_account_id": "primary",
                "conversation_id": "group-1",
            }
        ],
        approver_identities=[
            {
                "channel": "wechat",
                "channel_account_id": "primary",
                "sender_id": "owner-1",
            }
        ],
        bound_room_ids=["!legacy:example.org"],
        approver_matrix_ids=["@legacy:example.org"],
    )

    audit = ctx.to_audit_dict()

    assert audit["channel_bindings"] == ctx.channel_bindings
    assert audit["approver_identities"] == ctx.approver_identities
    assert audit["bound_room_ids"] == []
    assert audit["approver_matrix_ids"] == []


def test_queued_job_context_round_trips_generic_bindings(tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db.models import Base
    from app.services.job_service import _ctx_from_payload, create_tool_job

    engine = create_engine(f"sqlite:///{tmp_path / 'message-context-jobs.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    db = Session()
    try:
        ctx = ToolContext(
            username="tester",
            auth_type="tool_token",
            channel_bindings=[
                {
                    "channel": "telegram",
                    "channel_account_id": "bot-a",
                    "conversation_id": "chat-9",
                }
            ],
            approver_identities=[
                {
                    "channel": "telegram",
                    "channel_account_id": "bot-a",
                    "sender_id": "42",
                }
            ],
        )
        job = create_tool_job(
            db,
            tool_def=SimpleNamespace(name="ops.test", title="Test", risk="high"),
            arguments={},
            ctx=ctx,
            policy_result={"allowed": True},
        )
        stored = job["request"]["context"]
        restored = _ctx_from_payload(stored)

        assert stored["channel_bindings"] == ctx.channel_bindings
        assert stored["approver_identities"] == ctx.approver_identities
        assert restored.channel_bindings == ctx.channel_bindings
        assert restored.approver_identities == ctx.approver_identities
    finally:
        db.close()
        engine.dispose()
