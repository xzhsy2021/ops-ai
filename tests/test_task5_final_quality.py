from pathlib import Path

import pytest
from pydantic import ValidationError

from app.api.approvals import MessageRoutingConfig
from app.config.systems import normalize_message_routing_config


ROOT = Path(__file__).resolve().parents[1]


def test_message_routing_normalizer_is_complete_and_deduplicates():
    assert normalize_message_routing_config(
        {
            "enabled": True,
            "aliases": [" quant ", "quant"],
            "keywords": [" risk ", "risk"],
            "priority": 20,
            "approvers": [
                "@owner:example.org",
                {
                    "channel": "matrix",
                    "channel_account_id": "default",
                    "sender_id": "@owner:example.org",
                },
            ],
        }
    ) == {
        "enabled": True,
        "aliases": ["quant"],
        "keywords": ["risk"],
        "priority": 20,
        "approvers": [
            {
                "channel": "matrix",
                "channel_account_id": "default",
                "sender_id": "@owner:example.org",
            }
        ],
    }


@pytest.mark.parametrize(
    "routing",
    [
        {"enabled": "false"},
        {"aliases": "quant"},
        {"keywords": [{"value": "risk"}]},
        {"priority": True},
        {"priority": 1001},
        {"unsupported": "value"},
    ],
)
def test_message_routing_rejects_malformed_types_consistently(routing):
    with pytest.raises(ValueError):
        normalize_message_routing_config(routing)
    with pytest.raises(ValidationError):
        MessageRoutingConfig.model_validate(routing)


def test_signing_key_examples_and_docs_fail_closed():
    for filename in (
        ".env.example",
        ".env.local.example",
        ".env.docker.example",
        ".env.windows.example",
    ):
        source = (ROOT / filename).read_text(encoding="utf-8")
        assert "APPROVAL_SIGNING_KEY=" in source
        assert "secrets.token_urlsafe" in source
        assert "QCLAW_APPROVAL_SIGNING_KEY" in source

    docs = (ROOT / "docs/qclaw-element-approval-integration.md").read_text(encoding="utf-8")
    assert "APPROVAL_SIGNING_KEY" in docs
    assert "至少 32" in docs
    assert "secrets.token_urlsafe" in docs
    assert "openssl rand" in docs
    assert "dev fallback" not in docs
