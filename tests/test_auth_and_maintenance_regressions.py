from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker


def _sqlite_session(tmp_path):
    from app.db.models import Base

    engine = create_engine(
        f"sqlite:///{tmp_path / 'auth_maintenance_regressions.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    return engine, Session


def test_password_reset_invalidates_existing_session_token(tmp_path):
    from app.core.auth_v2 import create_session_token, get_current_user, hash_password
    from app.db.models import User

    engine, Session = _sqlite_session(tmp_path)
    db = Session()
    try:
        user = User(
            username="alice",
            password_hash=hash_password("before-reset"),
            role="admin",
            is_admin=True,
            can_deploy=True,
            session_version=1,
        )
        db.add(user)
        db.commit()
        db.refresh(user)

        stale_token = create_session_token(user.username, user.session_version)
        request = SimpleNamespace(cookies={"ops_session_v2": stale_token})
        current = get_current_user(request, db)
        assert current is not None
        assert current["username"] == "alice"

        user.password_hash = hash_password("after-reset")
        user.session_version += 1
        db.commit()
        db.refresh(user)

        assert get_current_user(request, db) is None

        fresh_token = create_session_token(user.username, user.session_version)
        refreshed = get_current_user(SimpleNamespace(cookies={"ops_session_v2": fresh_token}), db)
        assert refreshed is not None
        assert refreshed["username"] == "alice"
    finally:
        db.close()
        engine.dispose()


def test_server_repository_does_not_write_plaintext_back_on_commit(tmp_path, monkeypatch):
    from app.db.models import Server
    from app.db.repository import ServerRepository

    monkeypatch.setenv("OPS_SECRET_KEY", "unit-test-secret")

    engine, Session = _sqlite_session(tmp_path)
    db = Session()
    try:
        repo = ServerRepository(db)
        created = repo.create(
            name="bastion-1",
            host="10.0.0.1",
            user="root",
            password="super-secret",
            key_content="PRIVATE-KEY",
        )
        server_id = created.id

        encrypted_row = db.query(Server).filter(Server.id == server_id).first()
        encrypted_password = encrypted_row.password
        encrypted_key_content = encrypted_row.key_content
        assert encrypted_password != "super-secret"
        assert encrypted_key_content != "PRIVATE-KEY"

        fetched = repo.get_by_id(server_id)
        assert fetched is not None
        assert fetched.password == "super-secret"
        assert fetched.key_content == "PRIVATE-KEY"

        # An unrelated commit in the same session must not persist decrypted fields.
        db.execute(text("SELECT 1"))
        db.commit()

        reloaded = db.query(Server).filter(Server.id == server_id).first()
        assert reloaded.password == encrypted_password
        assert reloaded.key_content == encrypted_key_content
    finally:
        db.close()
        engine.dispose()


def test_server_repository_update_persists_detached_copy_and_reencrypts_secret(tmp_path, monkeypatch):
    from app.db.models import Server
    from app.db.repository import ServerRepository

    monkeypatch.setenv("OPS_SECRET_KEY", "unit-test-secret")

    engine, Session = _sqlite_session(tmp_path)
    db = Session()
    try:
        repo = ServerRepository(db)
        created = repo.create(
            name="bastion-2",
            host="10.0.0.2",
            user="root",
            password="old-secret",
        )

        detached = repo.get_by_id(created.id)
        assert detached is not None
        detached.host = "10.0.0.3"
        detached.password = "new-secret"

        updated = repo.update(detached)
        assert updated.host == "10.0.0.3"
        assert updated.password == "new-secret"

        stored = db.query(Server).filter(Server.id == created.id).first()
        assert stored.host == "10.0.0.3"
        assert stored.password != "new-secret"
    finally:
        db.close()
        engine.dispose()


def test_session_version_migration_adds_missing_user_column(tmp_path):
    from app.db.migrations.runner import run_schema_migrations

    engine = create_engine(
        f"sqlite:///{tmp_path / 'legacy_users.db'}",
        connect_args={"check_same_thread": False},
    )
    try:
        with engine.begin() as conn:
            conn.execute(text(
                """
                CREATE TABLE users (
                    id VARCHAR(32) PRIMARY KEY,
                    username VARCHAR(64) UNIQUE NOT NULL,
                    password_hash VARCHAR(255) NOT NULL,
                    role VARCHAR(32),
                    is_admin BOOLEAN,
                    can_deploy BOOLEAN,
                    created_at DATETIME,
                    updated_at DATETIME
                )
                """
            ))

        applied = run_schema_migrations(engine)
        assert "056_001_users_session_version" in applied

        with engine.connect() as conn:
            columns = [row[1] for row in conn.execute(text("PRAGMA table_info(users)")).fetchall()]
        assert "session_version" in columns
    finally:
        engine.dispose()


def test_maintenance_operator_prefers_authenticated_session_state():
    from app.api.maintenance import _get_operator

    request = SimpleNamespace(
        state=SimpleNamespace(username="operator-user"),
        cookies={"ops_session_v2": "signed-token-without-legacy-username-cookie"},
    )

    assert _get_operator(request) == "operator-user"
