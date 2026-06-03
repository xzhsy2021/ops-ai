import types

import pytest
from sqlalchemy import text
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture()
def sqlite_session(tmp_path):
    from app.db.models import Base

    engine = create_engine(
        f"sqlite:///{tmp_path / 'release_reliability.db'}",
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


def test_variable_precedence_excludes_application_fallback(monkeypatch):
    from app.deploy.schemas import DeployRequest
    from app.api.deploy._shared import _merge_release_variables

    req = DeployRequest(
        system="test-system",
        service="test-svc",
        environment="test",
        version="1.0",
    )
    monkeypatch.setattr(
        "app.api.deploy._shared._load_system_cfg",
        lambda _: {"variables": {"shared_key": "from_system", "system_only": "sys_val"}},
    )
    monkeypatch.setattr(
        "app.api.deploy._shared._environment_variables_for_system",
        lambda *_: {"env_key": "from_env", "shared_key": "from_env"},
    )
    monkeypatch.setattr(
        "app.api.deploy._shared._find_dovo_group",
        lambda *_: None,
    )
    monkeypatch.setattr(
        "app.api.deploy._shared._find_config_service",
        lambda *_: {"template_variables": {"svc_key": "from_svc", "shared_key": "from_svc"}},
    )

    merged = _merge_release_variables(req)

    assert merged["system_only"] == "sys_val"
    assert merged["shared_key"] == "from_svc"
    assert merged["svc_key"] == "from_svc"
    assert merged["env_key"] == "from_env"

    runtime_req = DeployRequest(
        system="test-system",
        service="test-svc",
        environment="test",
        variables={"shared_key": "from_runtime", "runtime_only": "rt_val"},
    )
    runtime_merged = _merge_release_variables(runtime_req)
    assert runtime_merged["shared_key"] == "from_runtime"
    assert runtime_merged["runtime_only"] == "rt_val"


def test_server_resolution_blocks_when_nothing_resolved(monkeypatch):
    from app.deploy.schemas import DeployRequest
    from app.api.deploy._shared import _derive_servers, _service_servers_for_environment, _server_names_for_group, _select_servers_for_service_env, _find_dovo_group

    empty_req = DeployRequest(
        system="test-system",
        service="nonexistent-svc",
        environment="test",
    )

    def _no_env_servers(*args):
        return []

    def _no_group_servers(*args):
        return []

    def _no_dovo_group(*args):
        return None

    monkeypatch.setattr(
        "app.api.deploy._shared._find_config_service",
        lambda *_: {"servers": [], "servers_by_env": {}},
    )
    monkeypatch.setattr(
        "app.api.deploy._shared._service_servers_for_environment",
        _no_env_servers,
    )
    monkeypatch.setattr(
        "app.api.deploy._shared._server_names_for_group",
        _no_group_servers,
    )
    monkeypatch.setattr(
        "app.api.deploy._shared._find_dovo_group",
        _no_dovo_group,
    )

    result = _derive_servers(empty_req)
    assert result == [], f"Should block with empty list, got {result}"


def test_status_helpers_normalize_legacy_cancelled():
    from app.deploy.state import is_canceled_status, is_terminal_status, normalize_status, task_cancel_requested

    assert normalize_status("cancelled") == "canceled"
    assert is_canceled_status("cancelled") is True
    assert is_terminal_status("cancelled") is True
    assert task_cancel_requested(types.SimpleNamespace(status="cancelled", cancel_requested=False)) is True


def test_repository_updates_normalize_terminal_status(sqlite_session):
    from app.db import DeployTaskRepository, DeploymentRepository

    db, _ = sqlite_session
    deployment = DeploymentRepository(db).create(system="ops", service="api", servers="local")
    task = DeployTaskRepository(db).create(deployment_id=deployment.id, task_id="task-cancel")

    assert DeploymentRepository(db).update_status(deployment.id, "cancelled", "legacy spelling") is True
    assert DeployTaskRepository(db).update_status(task.id, "cancelled", result="operator cancel") is True

    db.expire_all()
    deployment = DeploymentRepository(db).get_by_id(deployment.id)
    task = DeployTaskRepository(db).get_by_id(task.id)

    assert deployment.status == "canceled"
    assert deployment.finished_at is not None
    assert task.status == "canceled"
    assert task.finished_at is not None


def test_deploy_log_repository_create_many(sqlite_session):
    from app.db import DeployLogRepository

    db, _ = sqlite_session
    repo = DeployLogRepository(db)
    count = repo.create_many([
        {"task_id": "task-1", "deployment_id": "dep-1", "level": "info", "message": "first", "step_name": "s1"},
        {"task_id": "task-1", "deployment_id": "dep-1", "level": "warning", "message": "second", "step_name": "s2"},
    ])

    logs = repo.list_by_task("task-1", limit=10)

    assert count == 2
    assert [log.message for log in logs] == ["first", "second"]
    assert [log.level for log in logs] == ["info", "warning"]


def test_deploy_log_buffer_flushes_by_batch_and_explicit_flush(monkeypatch):
    from app.api import deploy_v2

    written = []

    class FakeRepo:
        def __init__(self, db):
            pass

        def create_many(self, rows):
            written.extend(rows)
            return len(rows)

    monkeypatch.setattr(deploy_v2, "DeployLogRepository", FakeRepo)
    monkeypatch.setattr(deploy_v2, "_get_log_db", lambda: object())

    buffer = deploy_v2._DeployLogBuffer()
    buffer.batch_size = 3
    buffer.flush_interval = 999

    buffer.append({"task_id": "t", "level": "info", "message": "a"})
    buffer.append({"task_id": "t", "level": "info", "message": "b"})
    assert written == []

    buffer.append({"task_id": "t", "level": "info", "message": "c"})
    assert [row["message"] for row in written] == ["a", "b", "c"]

    buffer.append({"task_id": "t", "level": "info", "message": "d"})
    assert [row["message"] for row in written] == ["a", "b", "c"]
    buffer.flush()
    assert [row["message"] for row in written] == ["a", "b", "c", "d"]


def test_pipeline_task_does_not_override_cancel_after_step_success(monkeypatch, sqlite_session):
    import asyncio
    import config_manager
    from app.api import deploy_v2
    from app.db import DeployTaskRepository, DeploymentRepository
    from app.deploy.schemas import DeployRequest

    db, Session = sqlite_session
    deployment = DeploymentRepository(db).create(system="ops", service="api", servers="local")
    deployment_id = deployment.id
    DeployTaskRepository(db).create(deployment_id=deployment_id, task_id="task-cancel-after-run")
    db.close()

    monkeypatch.setattr(deploy_v2, "SessionLocal", Session)
    monkeypatch.setattr(config_manager, "get_server_by_name", lambda name: {"name": name, "host": "127.0.0.1"})
    monkeypatch.setattr(deploy_v2, "_connect_ssh", lambda srv: types.SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(deploy_v2, "_merge_release_variables", lambda req, db: dict(req.variables or {}))
    monkeypatch.setattr(deploy_v2, "_distribute_package_to_server", lambda *args, **kwargs: None)

    class FakePipelineEngine:
        def __init__(self, *args, **kwargs):
            pass

        async def run(self, task_id, deployment_id, steps, ctx_data):
            fresh = Session()
            try:
                DeployTaskRepository(fresh).request_cancel(task_id)
            finally:
                fresh.close()
            return {"success": True}

    monkeypatch.setattr(deploy_v2, "PipelineEngine", FakePipelineEngine)

    asyncio.run(deploy_v2._run_pipeline_task(
        "task-cancel-after-run",
        deployment_id,
        DeployRequest(system="ops", service="api", servers=["local"], file_name="pkg.tar.gz"),
        [{"type": "command", "name": "noop", "config": {}}],
    ))

    verify = Session()
    try:
        task = DeployTaskRepository(verify).get_by_id("task-cancel-after-run")
        deployment = DeploymentRepository(verify).get_by_id(deployment_id)
        assert task.status == "canceled"
        assert deployment.status == "canceled"
    finally:
        verify.close()


def test_service_model_has_pipeline_id_column():
    from app.db.models import Service

    assert "pipeline_id" in Service.__table__.columns.keys()
    assert Service.__table__.columns["pipeline_id"].type.length == 64


def test_service_pipeline_id_migration_compatibility(tmp_path):
    from app.db.models import Base
    from app.db.migrations.runner import run_schema_migrations

    db_path = tmp_path / "service_pipeline_id_migration.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    try:
        Base.metadata.create_all(engine)
        with engine.begin() as conn:
            conn.execute(text("DROP TABLE services"))
            conn.execute(text("""
                CREATE TABLE services (
                    id VARCHAR(32) PRIMARY KEY,
                    name VARCHAR(64) NOT NULL,
                    display_name VARCHAR(128),
                    system_name VARCHAR(64) NOT NULL,
                    repo VARCHAR(255),
                    build_cmd VARCHAR(255),
                    start_cmd VARCHAR(255),
                    template VARCHAR(64),
                    template_variables TEXT,
                    servers TEXT,
                    created_at DATETIME,
                    updated_at DATETIME
                )
            """))

        applied = run_schema_migrations(engine)

        with engine.connect() as conn:
            cols = {row[1] for row in conn.execute(text("PRAGMA table_info(services)")).fetchall()}

        assert "pipeline_id" in cols
        assert any(version.startswith("058_") for version in applied)
    finally:
        engine.dispose()


def test_build_confirmation_falls_back_to_service_pipeline_id(monkeypatch):
    from app.deploy.schemas import DeployRequest
    from app.api.deploy._shared import _build_confirmation

    monkeypatch.setattr(
        "app.api.deploy._shared._find_config_service",
        lambda system, service, environment: {"name": service, "pipeline_id": "pipe-svc-default", "template_variables": {}},
    )
    monkeypatch.setattr(
        "app.api.deploy._shared._service_topology",
        lambda *args: {"server_groups": [], "servers": ["local"]},
    )
    monkeypatch.setattr(
        "app.api.deploy._shared._package_service_match",
        lambda *args: {"status": "ok", "message": "匹配", "package": {}},
    )
    monkeypatch.setattr(
        "app.api.deploy._shared._db_pipeline_steps",
        lambda db, pipeline_id: [{"type": "command", "name": "default", "config": {}}],
    )
    monkeypatch.setattr(
        "app.api.deploy._shared._rollback_plan_for",
        lambda *args: {"safe": True},
    )

    req = DeployRequest(
        system="ops",
        service="api",
        environment="test",
        file_name="pkg.tar.gz",
    )

    confirmation = _build_confirmation(req, None, {"username": "tester", "role": "admin"})

    assert confirmation["summary"]["pipeline_id"] == "pipe-svc-default"


def test_runtime_task_tables_are_migrated_for_deployment_history(tmp_path):
    from app.db.models import Base
    from app.db.migrations.runner import run_schema_migrations

    db_path = tmp_path / "runtime_task_migration.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    try:
        Base.metadata.create_all(engine)
        with engine.begin() as conn:
            conn.execute(text("DROP TABLE deployment_server_tasks"))
            conn.execute(text("DROP TABLE deployment_step_tasks"))
            conn.execute(text("DROP TABLE deployment_package_distributions"))
            conn.execute(text("""
                CREATE TABLE deployment_server_tasks (
                    id VARCHAR(32) PRIMARY KEY,
                    deployment_id VARCHAR(32) NOT NULL,
                    server_name VARCHAR(128) NOT NULL,
                    status VARCHAR(16),
                    current_step VARCHAR(128),
                    message TEXT,
                    started_at DATETIME,
                    finished_at DATETIME,
                    created_at DATETIME,
                    updated_at DATETIME
                )
            """))
            conn.execute(text("""
                CREATE TABLE deployment_step_tasks (
                    id VARCHAR(32) PRIMARY KEY,
                    deployment_id VARCHAR(32) NOT NULL,
                    server_task_id VARCHAR(32),
                    server_name VARCHAR(128),
                    step_name VARCHAR(128) NOT NULL,
                    step_type VARCHAR(64) NOT NULL,
                    sort_order INTEGER,
                    status VARCHAR(16),
                    message TEXT,
                    started_at DATETIME,
                    finished_at DATETIME,
                    created_at DATETIME,
                    updated_at DATETIME
                )
            """))
            conn.execute(text("""
                CREATE TABLE deployment_package_distributions (
                    id VARCHAR(32) PRIMARY KEY,
                    deployment_id VARCHAR(32) NOT NULL,
                    server_name VARCHAR(128) NOT NULL,
                    package_name VARCHAR(255) NOT NULL,
                    local_path TEXT,
                    remote_path TEXT,
                    local_sha256 VARCHAR(64),
                    remote_sha256 VARCHAR(64),
                    size_bytes INTEGER,
                    status VARCHAR(16),
                    message TEXT,
                    started_at DATETIME,
                    finished_at DATETIME,
                    created_at DATETIME
                )
            """))

        applied = run_schema_migrations(engine)

        with engine.connect() as conn:
            server_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(deployment_server_tasks)")).fetchall()}
            step_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(deployment_step_tasks)")).fetchall()}
            dist_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(deployment_package_distributions)")).fetchall()}

        assert "task_id" in server_cols
        assert "task_id" in step_cols
        assert "task_id" in dist_cols
        assert "reused" in dist_cols
        assert "duration_ms" in dist_cols
        assert "updated_at" in dist_cols
        assert any(version.startswith("053_") for version in applied)
    finally:
        engine.dispose()


def test_runtime_task_models_keep_legacy_runtime_columns():
    from app.db.models import (
        DeploymentPackageDistribution,
        DeploymentServerTask,
        DeploymentStepTask,
    )

    assert "current_step" in DeploymentServerTask.__table__.columns.keys()
    assert "updated_at" in DeploymentServerTask.__table__.columns.keys()
    assert "sort_order" in DeploymentStepTask.__table__.columns.keys()
    assert "updated_at" in DeploymentStepTask.__table__.columns.keys()
    assert "started_at" in DeploymentPackageDistribution.__table__.columns.keys()
    assert "finished_at" in DeploymentPackageDistribution.__table__.columns.keys()


def test_runtime_task_tables_gain_legacy_runtime_columns_when_missing(tmp_path):
    from app.db.models import Base
    from app.db.migrations.runner import run_schema_migrations

    db_path = tmp_path / "runtime_task_legacy_columns.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    try:
        Base.metadata.create_all(engine)
        with engine.begin() as conn:
            conn.execute(text("DROP TABLE deployment_server_tasks"))
            conn.execute(text("DROP TABLE deployment_step_tasks"))
            conn.execute(text("DROP TABLE deployment_package_distributions"))
            conn.execute(text("""
                CREATE TABLE deployment_server_tasks (
                    id VARCHAR(32) PRIMARY KEY,
                    deployment_id VARCHAR(32) NOT NULL,
                    task_id VARCHAR(32),
                    server_name VARCHAR(128) NOT NULL,
                    status VARCHAR(16),
                    message TEXT,
                    started_at DATETIME,
                    finished_at DATETIME,
                    created_at DATETIME
                )
            """))
            conn.execute(text("""
                CREATE TABLE deployment_step_tasks (
                    id VARCHAR(32) PRIMARY KEY,
                    deployment_id VARCHAR(32) NOT NULL,
                    task_id VARCHAR(32),
                    server_task_id VARCHAR(32),
                    server_name VARCHAR(128),
                    step_name VARCHAR(128) NOT NULL,
                    step_type VARCHAR(64) NOT NULL,
                    status VARCHAR(16),
                    message TEXT,
                    started_at DATETIME,
                    finished_at DATETIME,
                    created_at DATETIME
                )
            """))
            conn.execute(text("""
                CREATE TABLE deployment_package_distributions (
                    id VARCHAR(32) PRIMARY KEY,
                    deployment_id VARCHAR(32) NOT NULL,
                    task_id VARCHAR(32),
                    server_name VARCHAR(128) NOT NULL,
                    package_name VARCHAR(255) NOT NULL,
                    local_path TEXT,
                    remote_path TEXT,
                    local_sha256 VARCHAR(64),
                    remote_sha256 VARCHAR(64),
                    size_bytes INTEGER,
                    status VARCHAR(16),
                    reused BOOLEAN DEFAULT 0,
                    message TEXT,
                    duration_ms INTEGER,
                    created_at DATETIME,
                    updated_at DATETIME
                )
            """))

        applied = run_schema_migrations(engine)

        with engine.connect() as conn:
            server_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(deployment_server_tasks)")).fetchall()}
            step_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(deployment_step_tasks)")).fetchall()}
            dist_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(deployment_package_distributions)")).fetchall()}

        assert "current_step" in server_cols
        assert "updated_at" in server_cols
        assert "sort_order" in step_cols
        assert "updated_at" in step_cols
        assert "started_at" in dist_cols
        assert "finished_at" in dist_cols
        assert any(version.startswith("053_") for version in applied)
    finally:
        engine.dispose()


def test_merge_release_variables_uses_runtime_service_environment_system_precedence(monkeypatch):
    from app.api.deploy import _shared
    from app.deploy.schemas import DeployRequest

    monkeypatch.setattr(_shared, "_load_system_cfg", lambda system: {
        "variables": {
            "shared": "system",
            "system_only": "system-value",
        },
        "environments": {
            "prod": {
                "variables": {
                    "shared": "environment",
                    "env_only": "environment-value",
                }
            }
        },
    })
    monkeypatch.setattr(_shared, "_find_dovo_group", lambda system, service: None)
    monkeypatch.setattr(_shared, "_find_config_service", lambda system, service, environment: {
        "name": service,
        "display_name": "API",
        "template": "generic_backend_direct",
        "template_variables": {
            "shared": "service",
            "service_only": "service-value",
            "service_dir": "/srv/api",
        },
    })

    req = DeployRequest(
        system="ops",
        service="api",
        environment="prod",
        file_name="pkg.tar.gz",
        variables={
            "shared": "runtime",
            "runtime_only": "runtime-value",
        },
    )

    variables = _shared._merge_release_variables(req, None)

    assert variables["shared"] == "runtime"
    assert variables["runtime_only"] == "runtime-value"
    assert variables["service_only"] == "service-value"
    assert variables["env_only"] == "environment-value"
    assert variables["system_only"] == "system-value"


def test_derive_servers_does_not_fallback_to_system_default_servers(monkeypatch):
    from app.api.deploy import _shared
    from app.deploy.schemas import DeployRequest

    monkeypatch.setattr(_shared, "_find_config_service", lambda system, service, environment: {
        "name": service,
        "servers": [],
        "template_variables": {},
    })
    monkeypatch.setattr(_shared, "_service_servers_for_environment", lambda svc, environment: [])
    monkeypatch.setattr(_shared, "_find_dovo_group", lambda system, service: None)
    monkeypatch.setattr(_shared, "_select_servers_for_service_env", lambda names, svc, service, environment: list(names))
    monkeypatch.setattr(_shared, "_load_system_cfg", lambda system: {"servers": ["system-default-1", "system-default-2"]})

    req = DeployRequest(
        system="ops",
        service="api",
        environment="prod",
        file_name="pkg.tar.gz",
    )

    assert _shared._derive_servers(req, None) == []


def test_schema_migration_does_not_mark_missing_alter_table_as_applied(tmp_path):
    from app.db.migrations.runner import run_schema_migrations

    db_path = tmp_path / "schema_migration_missing_table.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    try:
        with engine.begin() as conn:
            conn.execute(text("""
                CREATE TABLE schema_migrations (
                    version VARCHAR(64) PRIMARY KEY,
                    name VARCHAR(255) NOT NULL,
                    applied_at DATETIME NOT NULL,
                    checksum VARCHAR(128)
                )
            """))

        applied = run_schema_migrations(engine)

        with engine.connect() as conn:
            versions = {row[0] for row in conn.execute(text("SELECT version FROM schema_migrations")).fetchall()}

        assert "053_001_deployment_server_tasks_task_id" not in applied
        assert "053_001_deployment_server_tasks_task_id" not in versions
    finally:
        engine.dispose()


def test_schema_migration_backfills_record_for_existing_column(tmp_path):
    from app.db.migrations.runner import run_schema_migrations

    db_path = tmp_path / "schema_migration_existing_column.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    try:
        with engine.begin() as conn:
            conn.execute(text("""
                CREATE TABLE schema_migrations (
                    version VARCHAR(64) PRIMARY KEY,
                    name VARCHAR(255) NOT NULL,
                    applied_at DATETIME NOT NULL,
                    checksum VARCHAR(128)
                )
            """))
            conn.execute(text("""
                CREATE TABLE deployment_server_tasks (
                    id VARCHAR(32) PRIMARY KEY,
                    deployment_id VARCHAR(32) NOT NULL,
                    task_id VARCHAR(32),
                    server_name VARCHAR(128) NOT NULL,
                    status VARCHAR(16),
                    message TEXT,
                    started_at DATETIME,
                    finished_at DATETIME,
                    created_at DATETIME
                )
            """))

        applied = run_schema_migrations(engine)

        with engine.connect() as conn:
            versions = {row[0] for row in conn.execute(text("SELECT version FROM schema_migrations")).fetchall()}

        assert "053_001_deployment_server_tasks_task_id" in applied
        assert "053_001_deployment_server_tasks_task_id" in versions
    finally:
        engine.dispose()


def test_runtime_repository_rolls_back_after_commit_failure(tmp_path):
    from app.db import DeploymentRepository, DeploymentRuntimeRepository
    from app.db.models import Base

    db_path = tmp_path / "runtime_repo_rollback.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    try:
        Base.metadata.create_all(engine)
        with engine.begin() as conn:
            conn.execute(text("DROP TABLE deployment_server_tasks"))
            conn.execute(text("""
                CREATE TABLE deployment_server_tasks (
                    id VARCHAR(32) PRIMARY KEY,
                    deployment_id VARCHAR(32) NOT NULL,
                    server_name VARCHAR(128) NOT NULL,
                    status VARCHAR(16),
                    current_step VARCHAR(128),
                    message TEXT,
                    started_at DATETIME,
                    finished_at DATETIME,
                    created_at DATETIME,
                    updated_at DATETIME
                )
            """))

        session = Session()
        try:
            deployment = DeploymentRepository(session).create(system="ops", service="api", servers="local")

            with pytest.raises(Exception):
                DeploymentRuntimeRepository(session).create_server_task(
                    deployment.id,
                    "task-schema-drift",
                    "server-a",
                )

            assert session.execute(text("SELECT 1")).scalar_one() == 1
            assert DeploymentRepository(session).get_by_id(deployment.id).id == deployment.id
        finally:
            session.close()
    finally:
        engine.dispose()


def test_pipeline_task_handles_server_task_creation_failure(monkeypatch, sqlite_session):
    import asyncio
    from app.api import deploy_v2
    from app.db import DeployTaskRepository, DeploymentRepository
    from app.deploy.schemas import DeployRequest

    db, Session = sqlite_session
    deployment = DeploymentRepository(db).create(system="ops", service="api", servers="local")
    deployment_id = deployment.id
    DeployTaskRepository(db).create(deployment_id=deployment_id, task_id="task-server-task-create-fails")
    db.close()

    class FailingRuntimeRepo:
        def __init__(self, db):
            self.db = db

        def create_server_task(self, *args, **kwargs):
            raise RuntimeError("runtime task insert failed")

        def update_server_task(self, *args, **kwargs):
            return False

    monkeypatch.setattr(deploy_v2, "SessionLocal", Session)
    monkeypatch.setattr(deploy_v2, "DeploymentRuntimeRepository", FailingRuntimeRepo)
    monkeypatch.setattr(deploy_v2, "_log_to_db", lambda *args, **kwargs: None)
    monkeypatch.setattr(deploy_v2, "_flush_deploy_logs", lambda: None)
    monkeypatch.setattr(deploy_v2, "_send_release_notification", lambda *args, **kwargs: None)

    asyncio.run(deploy_v2._run_pipeline_task(
        "task-server-task-create-fails",
        deployment_id,
        DeployRequest(system="ops", service="api", servers=["local"], file_name="pkg.tar.gz"),
        [{"type": "command", "name": "noop", "config": {}}],
    ))

    verify = Session()
    try:
        task = DeployTaskRepository(verify).get_by_id("task-server-task-create-fails")
        deployment = DeploymentRepository(verify).get_by_id(deployment_id)
        assert task.status == "failed"
        assert task.result == "Deployment failed"
        assert deployment.status == "failed"
        assert deployment.message == "Deployment failed"
    finally:
        verify.close()


def test_deploy_worker_marks_invalid_payload_task_failed_cleanly(monkeypatch, sqlite_session):
    import asyncio
    from app.api import deploy_v2
    from app.db import DeployTaskRepository, DeploymentRepository

    db, Session = sqlite_session
    deployment = DeploymentRepository(db).create(system="ops", service="api", servers="local")
    deployment_id = deployment.id
    DeployTaskRepository(db).create(
        deployment_id=deployment_id,
        task_id="task-invalid-payload",
        payload_json="{}",
    )
    db.close()

    async def stop_after_cycle(_seconds):
        raise StopAsyncIteration()

    monkeypatch.setattr(deploy_v2, "SessionLocal", Session)
    monkeypatch.setattr(deploy_v2, "_run_pipeline_task", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("pipeline should not run")))
    monkeypatch.setattr(deploy_v2, "_run_rollback_task", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("rollback should not run")))
    monkeypatch.setattr(deploy_v2, "_flush_deploy_logs", lambda: None)
    monkeypatch.setattr(asyncio, "sleep", stop_after_cycle)

    with pytest.raises(StopAsyncIteration):
        asyncio.run(deploy_v2._deploy_worker_loop())

    verify = Session()
    try:
        task = DeployTaskRepository(verify).get_by_id("task-invalid-payload")
        deployment = DeploymentRepository(verify).get_by_id(deployment_id)
        assert task.status == "failed"
        assert task.result == "Invalid deployment task payload: request must be an object"
        assert deployment.status == "failed"
        assert deployment.message == "Invalid deployment task payload: request must be an object"
    finally:
        verify.close()


def test_execute_deploy_plan_rejects_invalid_saved_payload(sqlite_session):
    from fastapi import HTTPException
    from app.services.tool_adapters import deploy_tools
    from app.db.models import ToolPlan

    db, _ = sqlite_session
    plan = ToolPlan(
        plan_type="deploy",
        status="ready",
        created_by="tester",
        source_tool="ops.create_deploy_plan",
        system="ops",
        service="api",
        environment="test",
        servers=["local"],
        package_name="pkg.tar.gz",
        payload={},
        confirmation={},
        precheck={"ok": True},
        risk_level="medium",
        confirm_text="CONFIRM",
    )
    db.add(plan)
    db.commit()
    db.refresh(plan)

    ctx = types.SimpleNamespace(
        role="admin",
        is_admin=True,
        can_deploy=True,
        allow_write=True,
        allow_prod=False,
        username="tester",
        token_owner="tester",
    )

    with pytest.raises(HTTPException) as exc_info:
        deploy_tools.execute_deploy_plan({"plan_id": plan.id, "confirm_text": "CONFIRM"}, ctx, db)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Invalid deployment task payload: request must be an object"


def test_pipeline_deployment_is_failed_when_lock_acquire_raises(monkeypatch, sqlite_session):
    import asyncio
    import json
    from fastapi import HTTPException
    from app.api.deploy import executions as exec_mod
    from app.api.deploy import _shared
    from app.db import DeploymentRepository

    db, Session = sqlite_session

    class FakeRequest:
        async def json(self):
            return {
                "system": "ops",
                "service": "api",
                "environment": "test",
                "file_name": "pkg.tar.gz",
                "servers": ["local"],
                "confirm_text": "CONFIRM",
                "variables": {},
            }

    monkeypatch.setattr(exec_mod, "require_auth", lambda request, db: {"username": "tester", "role": "admin", "is_admin": True, "can_deploy": True})
    monkeypatch.setattr(exec_mod, "require_deploy_for_env", lambda user, env: None)
    monkeypatch.setattr(exec_mod, "require_confirmed_high_risk", lambda *args, **kwargs: None)
    monkeypatch.setattr(exec_mod, "_derive_servers", lambda req, db: ["local"])
    monkeypatch.setattr(exec_mod, "_merge_release_variables", lambda req, db: {})
    monkeypatch.setattr(exec_mod, "_assert_environment_server_consistency", lambda env, servers: None)
    monkeypatch.setattr(exec_mod, "_build_confirmation", lambda req, db, user: {"blockers": [], "risk_level": "medium"})
    monkeypatch.setattr(exec_mod, "_assert_strict_deploy_confirmation", lambda req, data, confirmation: None)
    monkeypatch.setattr(exec_mod, "_db_pipeline_steps", lambda db, pipeline_id: [])
    monkeypatch.setattr(exec_mod, "_default_release_steps", lambda req, db: [])
    monkeypatch.setattr(exec_mod, "acquire_deployment_locks", lambda *args, **kwargs: (_ for _ in ()).throw(HTTPException(status_code=409, detail="lock conflict")))

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(exec_mod.deploy_execute_v2(FakeRequest(), db))

    assert exc_info.value.status_code == 409

    verify = Session()
    try:
        rows = DeploymentRepository(verify).list_all(limit=10)
        assert len(rows) == 1
        assert rows[0].status == "failed"
        assert rows[0].message == "Failed to acquire deployment locks"
    finally:
        verify.close()
