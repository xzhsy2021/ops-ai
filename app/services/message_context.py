from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Mapping


SUPPORTED_CHANNELS = frozenset({"matrix", "wechat", "telegram"})

_MESSAGE_FIELDS = (
    "channel",
    "channel_account_id",
    "conversation_id",
    "message_id",
    "sender_id",
    "content_sha256",
)
_GENERIC_IDENTITY_FIELDS = frozenset(_MESSAGE_FIELDS[:-1])
_LEGACY_IDENTITY_FIELDS = frozenset(
    {"room_id", "request_event_id", "event_id", "sender_matrix_id"}
)
_CHANNEL_ACCOUNT_ID_PATTERN = r"^[A-Za-z0-9._-]{1,128}$"
_CHANNEL_ACCOUNT_ID_RE = re.compile(_CHANNEL_ACCOUNT_ID_PATTERN)
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


def _required_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-blank string")
    return value.strip()


def _channel(value: Any) -> str:
    channel = _required_text(value, "channel").lower()
    if channel not in SUPPORTED_CHANNELS:
        raise ValueError(f"unsupported channel: {channel}")
    return channel


def _channel_account_id(value: Any) -> str:
    account_id = _required_text(value, "channel_account_id")
    if not _CHANNEL_ACCOUNT_ID_RE.fullmatch(account_id):
        raise ValueError(
            "channel_account_id must be a 1-128 character ASCII slug containing "
            "only letters, digits, dots, underscores, or hyphens"
        )
    return account_id


def _closed_mapping(
    value: Any,
    *,
    required: frozenset[str],
    name: str,
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    fields = set(value)
    missing = required - fields
    if missing:
        raise ValueError(f"{name} missing required field(s): {', '.join(sorted(missing))}")
    unexpected = fields - required
    if unexpected:
        labels = ", ".join(sorted(str(field) for field in unexpected))
        raise ValueError(f"{name} contains unexpected field(s): {labels}")
    return value


@dataclass(frozen=True)
class MessageContext:
    channel: str
    channel_account_id: str
    conversation_id: str
    message_id: str
    sender_id: str
    content_sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "channel", _channel(self.channel))
        object.__setattr__(
            self,
            "channel_account_id",
            _channel_account_id(self.channel_account_id),
        )
        for field_name in (
            "conversation_id",
            "message_id",
            "sender_id",
        ):
            normalized = _required_text(getattr(self, field_name), field_name)
            object.__setattr__(self, field_name, normalized)
        digest = _required_text(self.content_sha256, "content_sha256")
        if not _SHA256_RE.fullmatch(digest):
            raise ValueError("content_sha256 must be a 64-character hexadecimal SHA-256")
        object.__setattr__(self, "content_sha256", digest.lower())

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "MessageContext":
        data = _closed_mapping(
            value,
            required=frozenset(_MESSAGE_FIELDS),
            name="message_context",
        )
        return cls(**{field_name: data[field_name] for field_name in _MESSAGE_FIELDS})

    def to_dict(self) -> Dict[str, str]:
        return {field_name: getattr(self, field_name) for field_name in _MESSAGE_FIELDS}

    @property
    def actor_key(self) -> str:
        return f"{self.channel}:{self.channel_account_id}:{self.sender_id}"

    @property
    def conversation_key(self) -> str:
        return f"{self.channel}:{self.channel_account_id}:{self.conversation_id}"


def message_context_schema() -> Dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "channel": {"type": "string", "enum": sorted(SUPPORTED_CHANNELS)},
            "channel_account_id": {
                "type": "string",
                "minLength": 1,
                "maxLength": 128,
                "pattern": _CHANNEL_ACCOUNT_ID_PATTERN,
            },
            "conversation_id": {"type": "string", "minLength": 1},
            "message_id": {"type": "string", "minLength": 1},
            "sender_id": {"type": "string", "minLength": 1},
            "content_sha256": {
                "type": "string",
                "pattern": "^[0-9a-fA-F]{64}$",
            },
        },
        "required": list(_MESSAGE_FIELDS),
        "additionalProperties": False,
    }


def normalize_message_context(value: Any) -> MessageContext:
    if isinstance(value, MessageContext):
        return value
    if not isinstance(value, Mapping):
        raise ValueError("message_context must be an object")

    fields = set(value)
    generic_fields = fields & _GENERIC_IDENTITY_FIELDS
    legacy_fields = fields & _LEGACY_IDENTITY_FIELDS
    if generic_fields and legacy_fields:
        raise ValueError("message_context cannot mix generic and legacy Matrix identity fields")
    if not legacy_fields:
        return MessageContext.from_dict(value)

    allowed = _LEGACY_IDENTITY_FIELDS | {"content_sha256"}
    unexpected = fields - allowed
    if unexpected:
        labels = ", ".join(sorted(str(field) for field in unexpected))
        raise ValueError(
            f"legacy message_context contains unexpected field(s): {labels}"
        )
    if "request_event_id" in fields and "event_id" in fields:
        raise ValueError(
            "legacy message_context cannot contain both request_event_id and event_id"
        )
    event_field = "request_event_id" if "request_event_id" in fields else "event_id"
    required = {"room_id", event_field, "sender_matrix_id", "content_sha256"}
    missing = required - fields
    if missing:
        raise ValueError(
            f"legacy message_context missing required field(s): {', '.join(sorted(missing))}"
        )
    return MessageContext(
        channel="matrix",
        channel_account_id="default",
        conversation_id=value["room_id"],
        message_id=value[event_field],
        sender_id=value["sender_matrix_id"],
        content_sha256=value["content_sha256"],
    )


def normalize_identity(value: Any) -> Dict[str, str]:
    if isinstance(value, str):
        data: Mapping[str, Any] = {
            "channel": "matrix",
            "channel_account_id": "default",
            "sender_id": value,
        }
    else:
        data = _closed_mapping(
            value,
            required=frozenset({"channel", "channel_account_id", "sender_id"}),
            name="identity",
        )
    return {
        "channel": _channel(data["channel"]),
        "channel_account_id": _channel_account_id(data["channel_account_id"]),
        "sender_id": _required_text(data["sender_id"], "sender_id"),
    }


def normalize_conversation_binding(value: Any) -> Dict[str, str]:
    if isinstance(value, str):
        data: Mapping[str, Any] = {
            "channel": "matrix",
            "channel_account_id": "default",
            "conversation_id": value,
        }
    else:
        data = _closed_mapping(
            value,
            required=frozenset({"channel", "channel_account_id", "conversation_id"}),
            name="conversation binding",
        )
    return {
        "channel": _channel(data["channel"]),
        "channel_account_id": _channel_account_id(data["channel_account_id"]),
        "conversation_id": _required_text(data["conversation_id"], "conversation_id"),
    }
