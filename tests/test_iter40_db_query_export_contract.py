from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def _sqlite_session(tmp_path):
    from app.db.models import Base

    engine = create_engine(
        f"sqlite:///{tmp_path / 'iter40_db_export.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    return engine, Session


def test_db_query_export_service_masks_sensitive_and_exports_csv(tmp_path, monkeypatch):
    from app.db.models import Server
    from app.services.db_query_export import DbQueryExportService

    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "data"))
    engine, Session = _sqlite_session(tmp_path)
    db = Session()
    try:
        db.add(Server(name="demo", host="127.0.0.1", user="root", password="secret123"))
        db.commit()

        svc = DbQueryExportService(db)
        tables = svc.list_tables()
        assert any(t["name"] == "servers" for t in tables["tables"])

        result = svc.query_readonly(sql="SELECT name, password FROM servers", operator="tester", limit=10)
        assert result["row_count"] == 1
        assert "password" in result["sensitive_columns_masked"]
        assert result["rows"][0]["password"] == "***MASKED***"

        export = svc.export_query_result(sql="SELECT name, host FROM servers", fmt="csv", operator="tester", filename_hint="servers")
        item = export["export"]
        assert item["report_type"] == "db_query_export"
        assert item["format"] == "csv"
        path = svc.download_path(item["id"])
        assert path.exists()
        assert "demo" in path.read_text(encoding="utf-8-sig")
    finally:
        db.close()
        engine.dispose()


def test_db_tools_are_registered_and_readonly(tmp_path):
    from app.services.tool_context import ToolContext
    from app.services.tool_registry import register_builtin_tools, registry

    engine, Session = _sqlite_session(tmp_path)
    db = Session()
    try:
        register_builtin_tools()
        ctx = ToolContext(username="tester", auth_type="session", is_admin=True, scopes=["*"], allow_write=True)
        listed = registry.list_tools(db, ctx, include_disabled=True, include_schema=False, limit=1200)
        tools = {item["name"]: item for item in listed["tools"]}
        for name in [
            "ops.db.list_tables",
            "ops.db.describe_table",
            "ops.db.query_readonly",
            "ops.db.export_query_result",
            "ops.db.list_exports",
            "ops.db.get_export",
        ]:
            assert name in tools
        assert tools["ops.db.query_readonly"]["write"] is False
        assert tools["ops.db.export_query_result"]["risk"] == "medium"
    finally:
        db.close()
        engine.dispose()


def test_db_query_blocks_write_sql(tmp_path):
    from fastapi import HTTPException
    from app.services.db_query_export import DbQueryExportService

    engine, Session = _sqlite_session(tmp_path)
    db = Session()
    try:
        svc = DbQueryExportService(db)
        try:
            svc.query_readonly(sql="UPDATE servers SET host = 'x'", operator="tester")
            assert False, "write SQL should be blocked"
        except HTTPException as exc:
            assert exc.status_code in {400, 403}
    finally:
        db.close()
        engine.dispose()
