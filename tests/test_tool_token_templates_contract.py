from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def _sqlite_session(tmp_path):
    from app.db.models import Base

    engine = create_engine(
        f"sqlite:///{tmp_path / 'tool_token_contract.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    return engine, Session


def test_recommended_tool_token_templates_are_backend_source_of_truth():
    from app.services.tool_token import recommended_tool_token_templates

    templates = recommended_tool_token_templates()
    by_key = {item["key"]: item for item in templates}

    assert list(by_key.keys()) == [
        "readonly-ai",
        "inspection-ai",
        "operator-human",
        "qclaw-mcp",
        "admin-breakglass",
    ]

    readonly = by_key["readonly-ai"]
    assert readonly["scopes"] == ["ops:read", "audit:read", "server:read"]
    assert readonly["allow_write"] is False
    assert readonly["allow_prod"] is False

    inspection = by_key["inspection-ai"]
    assert inspection["scopes"] == ["ops:read", "ops:write", "server:read", "audit:read"]
    assert inspection["allow_write"] is True
    assert inspection["allow_prod"] is False
    assert "confirm_text" in inspection["notes"]

    operator = by_key["operator-human"]
    assert "deploy:plan" in operator["scopes"]
    assert "deploy:precheck" in operator["scopes"]
    assert "deploy:execute" not in operator["scopes"]
    assert operator["allow_write"] is True
    assert operator["allow_prod"] is False

    qclaw = by_key["qclaw-mcp"]
    assert qclaw["scopes"] == ["ops:read"]
    assert qclaw["allow_write"] is False
    assert qclaw["allow_prod"] is False
    # qclaw 必须 NOT 持有任何危险 scope
    dangerous_scopes = {"deploy:execute", "package:write", "package:cleanup", "db:write", "*"}
    assert not (set(qclaw["scopes"]) & dangerous_scopes)
    assert "short_code" in qclaw["notes"] or "approval" in qclaw["notes"].lower()

    admin = by_key["admin-breakglass"]
    assert admin["scopes"] == ["*"]
    assert admin["allow_write"] is True
    assert admin["allow_prod"] is True
    assert admin["expires_in_days"] <= 7


def test_frontend_consumes_backend_token_templates_with_fallback():
    api = open("frontend/src/api.ts", encoding="utf-8").read()
    page = open("frontend/src/pages/ToolAccessPage.tsx", encoding="utf-8").read()
    panel = open("frontend/src/pages/tools/ToolTokenPanel.tsx", encoding="utf-8").read()

    assert "tokenTemplates:" in api
    assert "/tools/token-templates" in api
    assert "capabilityTools.tokenTemplates()" in page
    assert "tokenTemplates={tokenTemplates}" in page
    assert "tokenTemplates?: TokenTemplate[]" in panel
    assert "FALLBACK_TOKEN_TEMPLATES" in panel


def test_tool_token_description_is_persisted_and_returned(tmp_path):
    from app.services.tool_token import create_tool_token, token_to_dict

    engine, Session = _sqlite_session(tmp_path)
    db = Session()
    try:
        created = create_tool_token(
            db,
            name="inspection-ai",
            owner="admin",
            description="Path A inspection assistant token",
            scopes=["ops:read", "ops:write"],
            allow_write=True,
            expires_in_days=30,
        )

        record = token_to_dict(created["record"])
        assert record["description"] == "Path A inspection assistant token"
    finally:
        db.close()
        engine.dispose()


def test_tool_token_zero_expiry_days_creates_non_expiring_token(tmp_path):
    from app.services.tool_token import create_tool_token, token_to_dict

    engine, Session = _sqlite_session(tmp_path)
    db = Session()
    try:
        created = create_tool_token(
            db,
            name="readonly-ai",
            owner="admin",
            scopes=["ops:read"],
            allow_write=False,
            expires_in_days=0,
        )

        record = token_to_dict(created["record"])
        assert record["expires_at"] is None
    finally:
        db.close()
        engine.dispose()


def test_tool_token_description_migration_is_declared():
    text = open("app/db/migrations/runner.py", encoding="utf-8").read()

    assert "tool_tokens_description" in text
    assert "ALTER TABLE tool_tokens ADD COLUMN description TEXT" in text
