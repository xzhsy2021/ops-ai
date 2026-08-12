from sqlalchemy import create_engine, inspect

from app.db.base import Base
from app.db.migrations.runner import run_schema_migrations


def test_temporary_approval_grants_table_and_indexes_are_migrated_once():
    engine = create_engine("sqlite://")

    first = run_schema_migrations(engine)
    second = run_schema_migrations(engine)

    assert "084_006_temporary_approval_grants" in first
    assert second == []
    columns = {item["name"] for item in inspect(engine).get_columns("temporary_approval_grants")}
    assert {
        "id", "beneficiary_actor_key", "channel", "channel_account_id", "conversation_id",
        "system_id", "environment_id", "allowed_actions", "reason", "starts_at", "expires_at",
        "status", "requested_by_actor_key", "approved_by_actor_key", "request_message_id",
        "confirmation_message_id", "request_digest", "confirmation_code_hash",
        "confirmation_expires_at", "confirmation_consumed_at", "confirmation_attempts", "active_scope_key", "revoked_by_actor_key",
        "revoked_at", "revoke_reason", "requested_duration_seconds", "created_at", "updated_at",
    } <= columns
    indexes = {item["name"] for item in inspect(engine).get_indexes("temporary_approval_grants")}
    assert {
        "ix_temporary_grant_status", "ix_temporary_grant_expires", "ix_temporary_grant_beneficiary",
        "ix_temporary_grant_scope", "uq_temporary_grant_active_scope_key",
    } <= indexes
