

def _sqlite_session(tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.db.models import Base

    engine = create_engine(
        f"sqlite:///{tmp_path / 'iter35_jobs.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    return engine, Session


def test_high_risk_tool_call_is_queued_as_unified_job(tmp_path, monkeypatch):
    from app.services.tool_context import ToolContext
    from app.services.tool_policy import save_capability_settings
    from app.services.tool_registry import register_builtin_tools, registry
    import app.services.job_service as job_service
    from app.services.job_service import get_operation_job

    engine, Session = _sqlite_session(tmp_path)
    db = Session()
    try:
        monkeypatch.setenv("BACKUP_DIR", str(tmp_path / "backups"))
        monkeypatch.setattr(job_service, "SessionLocal", Session)
        monkeypatch.setattr(job_service, "start_job_worker", lambda job_id: None)
        save_capability_settings(db, {
            "enabled": True,
            "http_tools_enabled": True,
            "read_only": False,
            "allow_backup_write": True,
            "allow_server_write": True,
            "allow_high_risk_tools": True,
            "allow_critical_risk_tools": False,
            "require_confirmation": True,
            "taskize_high_risk_tools": True,
        })
        register_builtin_tools()
        ctx = ToolContext(username="tester", auth_type="session", is_admin=True, scopes=["*"], allow_write=True)

        result = registry.call(db, "ops.file_write", {"server": "test-server", "path": "/tmp/test.txt", "content": "test", "confirm_text": "CONFIRM ops.file_write"}, ctx)

        assert result["message"] == "queued"
        assert result["job_id"]
        job = get_operation_job(db, result["job_id"])
        assert job is not None
        assert job["source_tool"] == "ops.file_write"
        assert job["risk_level"] == "high"
        assert job["status"] in {"queued", "running", "failed", "success"}

        # Unit test keeps the worker disabled; production starts the daemon worker
        # immediately. The important contract is that the high-risk call returns a
        # queued job instead of executing synchronously in the request path.
        assert job["status"] == "queued"
    finally:
        db.close()
        engine.dispose()


def test_high_risk_tool_worker_updates_job_to_terminal_status(tmp_path, monkeypatch):
    from app.services.tool_context import ToolContext
    from app.services.tool_policy import save_capability_settings
    from app.services.tool_registry import register_builtin_tools, registry
    import app.services.job_service as job_service
    from app.services.job_service import get_operation_job

    engine, Session = _sqlite_session(tmp_path)
    db = Session()
    try:
        monkeypatch.setenv("BACKUP_DIR", str(tmp_path / "backups"))
        monkeypatch.setattr(job_service, "SessionLocal", Session)
        monkeypatch.setattr(job_service, "start_job_worker", lambda job_id: job_service._execute_tool_job(job_id))
        save_capability_settings(db, {
            "enabled": True,
            "http_tools_enabled": True,
            "read_only": False,
            "allow_backup_write": True,
            "allow_server_write": True,
            "allow_high_risk_tools": True,
            "allow_critical_risk_tools": False,
            "require_confirmation": True,
            "taskize_high_risk_tools": True,
        })
        register_builtin_tools()
        ctx = ToolContext(username="tester", auth_type="session", is_admin=True, scopes=["*"], allow_write=True)

        result = registry.call(db, "ops.file_write", {"server": "test-server", "path": "/tmp/test.txt", "content": "test", "confirm_text": "CONFIRM ops.file_write"}, ctx)

        job = get_operation_job(db, result["job_id"])
        assert job is not None
        assert job["status"] in {"failed", "success"}
        assert job["progress"] == 100
        assert job["finished_at"]
    finally:
        db.close()
        engine.dispose()


def test_job_read_tools_are_registered(tmp_path):
    from app.services.tool_context import ToolContext
    from app.services.tool_registry import register_builtin_tools, registry

    engine, Session = _sqlite_session(tmp_path)
    db = Session()
    try:
        register_builtin_tools()
        ctx = ToolContext(username="tester", auth_type="session", is_admin=True, scopes=["*"], allow_write=True)
        listed = registry.list_tools(db, ctx, include_disabled=True, include_schema=False, limit=500)
        tools = {item["name"]: item for item in listed["tools"]}
        assert "ops.list_jobs" in tools
        assert "ops.get_job_status" in tools
        assert tools["ops.list_jobs"]["risk"] == "low"
        assert tools["ops.get_job_status"]["category"] == "job_read"
    finally:
        db.close()
        engine.dispose()
