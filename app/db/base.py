import os
import logging
from sqlalchemy import create_engine, text, inspect, event
from sqlalchemy.orm import sessionmaker, declarative_base
from app.core.config import ensure_runtime_dirs, get_database_path


def build_database_url() -> str:
    return os.getenv("DATABASE_URL", f"sqlite:///{get_database_path()}")


ensure_runtime_dirs()
DATABASE_URL = build_database_url()

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    echo=False,
    pool_size=10,
    max_overflow=20,
    pool_recycle=1800,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
)


if DATABASE_URL.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()
logger = logging.getLogger(__name__)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


_MIGRATIONS = [
    {
        "table": "pipeline_steps",
        "column": "config",
        "sql": "ALTER TABLE pipeline_steps ADD COLUMN config TEXT",
    },
    {
        "table": "users",
        "column": "role",
        "sql": "ALTER TABLE users ADD COLUMN role VARCHAR(32) DEFAULT 'operator'",
    },
    {
        "table": "users",
        "column": "session_version",
        "sql": "ALTER TABLE users ADD COLUMN session_version INTEGER DEFAULT 1",
    },
    {
        "table": "deployments",
        "column": "version",
        "sql": "ALTER TABLE deployments ADD COLUMN version VARCHAR(128)",
    },
    {
        "table": "cleanup_jobs",
        "column": "execution_window_start",
        "sql": "ALTER TABLE cleanup_jobs ADD COLUMN execution_window_start VARCHAR(8)",
    },
    {
        "table": "cleanup_jobs",
        "column": "execution_window_end",
        "sql": "ALTER TABLE cleanup_jobs ADD COLUMN execution_window_end VARCHAR(8)",
    },
        {
        "table": "database_connections",
        "column": "description",
        "sql": "ALTER TABLE database_connections ADD COLUMN description TEXT",
    },
    {
        "table": "database_connections",
        "column": "use_ssh_tunnel",
        "sql": "ALTER TABLE database_connections ADD COLUMN use_ssh_tunnel BOOLEAN DEFAULT 0",
    },
    {
        "table": "database_connections",
        "column": "ssh_host",
        "sql": "ALTER TABLE database_connections ADD COLUMN ssh_host VARCHAR(255)",
    },
    {
        "table": "database_connections",
        "column": "ssh_port",
        "sql": "ALTER TABLE database_connections ADD COLUMN ssh_port INTEGER DEFAULT 22",
    },
    {
        "table": "database_connections",
        "column": "ssh_username",
        "sql": "ALTER TABLE database_connections ADD COLUMN ssh_username VARCHAR(64)",
    },
    {
        "table": "database_connections",
        "column": "ssh_password_encrypted",
        "sql": "ALTER TABLE database_connections ADD COLUMN ssh_password_encrypted TEXT",
    },
    {
        "table": "database_connections",
        "column": "ssh_key_path",
        "sql": "ALTER TABLE database_connections ADD COLUMN ssh_key_path VARCHAR(255)",
    },
    {
        "table": "database_connections",
        "column": "ssh_key_passphrase_encrypted",
        "sql": "ALTER TABLE database_connections ADD COLUMN ssh_key_passphrase_encrypted TEXT",
    },
    {
        "table": "database_connections",
        "column": "ssh_remote_bind_host",
        "sql": "ALTER TABLE database_connections ADD COLUMN ssh_remote_bind_host VARCHAR(255)",
    },
]


def _run_migrations():
    insp = inspect(engine)
    for mig in _MIGRATIONS:
        table = mig["table"]
        column = mig["column"]
        if not insp.has_table(table):
            continue
        existing = [c["name"] for c in insp.get_columns(table)]
        if column not in existing:
            try:
                with engine.connect() as conn:
                    conn.execute(text(mig["sql"]))
                    conn.commit()
            except Exception:
                logger.exception("Failed to apply migration: %s.%s", table, column)
                raise


def init_db():
    Base.metadata.create_all(bind=engine)
    _run_migrations()
    try:
        from app.db.migrations.runner import run_schema_migrations
        run_schema_migrations(engine)
    except Exception:
        import logging
        logging.getLogger(__name__).exception("Failed to run schema migrations")
        raise
    try:
        from app.db.migrations.runner import migrate_read_only_default
        migrate_read_only_default()
    except Exception:
        import logging
        logging.getLogger(__name__).exception("Failed to apply read_only default migration")
    try:
        from app.db.migrations.systems_migration import migrate_systems_blob_to_table
        migrate_systems_blob_to_table()
    except Exception:
        import logging
        logging.getLogger(__name__).exception("Failed to migrate systems blob to table")
    _ensure_indexes()


def _ensure_indexes():
    indexes = [
        "CREATE INDEX IF NOT EXISTS ix_deploy_logs_created_at_id ON deploy_logs(created_at DESC, id DESC)",
        "CREATE INDEX IF NOT EXISTS ix_audit_records_created_at ON audit_records(created_at)",
        "CREATE INDEX IF NOT EXISTS ix_audit_records_actor ON audit_records(target_name)",
        "CREATE INDEX IF NOT EXISTS ix_audit_records_action ON audit_records(action)",
        "CREATE INDEX IF NOT EXISTS ix_tool_call_logs_tool_created ON tool_call_logs(tool_name, created_at)",
        "CREATE INDEX IF NOT EXISTS ix_audit_records_actor_created ON audit_records(target_name, created_at)",
    ]
    with engine.begin() as conn:
        for sql in indexes:
            try:
                conn.execute(text(sql))
            except Exception:
                import logging
                logging.getLogger(__name__).warning("Index creation skipped: %s", sql)
