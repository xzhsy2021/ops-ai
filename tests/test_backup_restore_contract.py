import sqlite3

import pytest

@pytest.fixture()
def sqlite_session(tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.db.models import Base

    engine = create_engine(
        f"sqlite:///{tmp_path / 'backup_policy.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    session = Session()
    try:
        yield session, Session
    finally:
        session.close()
        engine.dispose()


def _init_db(path, value):
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS items (id INTEGER PRIMARY KEY, value TEXT)")
        conn.execute("DELETE FROM items")
        conn.execute("INSERT INTO items(value) VALUES (?)", (value,))
        conn.commit()
    finally:
        conn.close()


def _read_value(path):
    conn = sqlite3.connect(path)
    try:
        return conn.execute("SELECT value FROM items LIMIT 1").fetchone()[0]
    finally:
        conn.close()


def test_backup_verify_restore_and_delete_require_strong_confirmation(tmp_path, monkeypatch):
    from app.db import base as db_base
    from app.services import backup_service

    db_path = tmp_path / "ops.db"
    backup_dir = tmp_path / "backups"
    _init_db(db_path, "before")
    monkeypatch.setattr(db_base, "DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("BACKUP_DIR", str(backup_dir))

    created = backup_service.create_database_backup(actor="tester", reason="contract", keep_max=10)

    assert created["file"].startswith("ops_backup_")
    assert created["verification"]["valid"] is True
    assert backup_service.list_database_backups()[0]["file"] == created["file"]

    verification = backup_service.verify_backup_file(created["file"])
    assert verification["status"] == "ok"
    assert verification["table_count"] >= 1
    assert verification["sha256"]

    _init_db(db_path, "after")
    with pytest.raises(backup_service.BackupServiceError):
        backup_service.restore_database_backup(created["file"], confirm_text="RESTORE wrong", actor="tester")

    restored = backup_service.restore_database_backup(
        created["file"],
        confirm_text=f"RESTORE {created['file']}",
        actor="tester",
        create_safety_backup=True,
    )
    assert restored["restored"] is True
    assert restored["safety_backup"]["file"].startswith("ops_backup_before_restore_")
    assert _read_value(db_path) == "before"

    with pytest.raises(backup_service.BackupServiceError):
        backup_service.delete_database_backup(created["file"], confirm_text="DELETE wrong", actor="tester")
    deleted = backup_service.delete_database_backup(created["file"], confirm_text=f"DELETE {created['file']}", actor="tester")
    assert deleted["deleted"] is True


def test_mcp_backup_tools_are_registered_and_policy_gated(sqlite_session):
    from app.services.tool_context import ToolContext
    from app.services.tool_registry import register_builtin_tools, registry

    db, _ = sqlite_session
    register_builtin_tools()
    ctx = ToolContext(username="tester", auth_type="session", is_admin=True, scopes=["*"], allow_write=True)

    listed = registry.list_tools(db, ctx, include_disabled=True, include_schema=True, limit=500)
    tools = {item["name"]: item for item in listed["tools"]}

    for name in ["ops.list_backups", "ops.verify_backup"]:
        assert name in tools

    assert tools["ops.list_backups"]["risk"] == "low"
