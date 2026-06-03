from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def _sqlite_session(tmp_path):
    from app.db.models import Base

    engine = create_engine(
        f"sqlite:///{tmp_path / 'iter39_reports.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    return engine, Session


def test_report_center_generates_operation_chain_artifact(tmp_path, monkeypatch):
    from app.db.models import NotificationEvent, ToolCallLog
    from app.services.report_center import generate_report, list_reports, report_download_path

    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("REPORT_DIR", str(tmp_path / "reports"))
    engine, Session = _sqlite_session(tmp_path)
    db = Session()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    try:
        db.add(ToolCallLog(id="call_report", tool_name="ops.analyze_diagnostics", username="alice", status="success", risk_level="low", result_preview='{"ok":true}', created_at=now))
        db.commit()

        result = generate_report(db, report_type="operation_chain", target_id="tool:call_report", fmt="json", created_by="alice")
        report = result["report"]

        assert report["report_type"] == "operation_chain"
        assert report["target_id"] == "tool:call_report"
        assert report["sha256"]
        assert report["size_bytes"] > 0
        assert report_download_path(db.query(__import__('app.db.models', fromlist=['ReportArtifact']).ReportArtifact).filter_by(id=report["id"]).first()).exists()

        listed = list_reports(db, report_type="operation_chain")
        assert listed["items"][0]["id"] == report["id"]
        assert db.query(NotificationEvent).filter(NotificationEvent.event_type == "report.created").count() == 1
    finally:
        db.close()
        engine.dispose()


def test_iter39_report_tools_are_registered_low_risk(tmp_path):
    from app.services.tool_context import ToolContext
    from app.services.tool_registry import register_builtin_tools, registry

    engine, Session = _sqlite_session(tmp_path)
    db = Session()
    try:
        register_builtin_tools()
        ctx = ToolContext(username="tester", auth_type="session", is_admin=True, scopes=["*"], allow_write=True)
        listed = registry.list_tools(db, ctx, include_disabled=True, include_schema=False, limit=1000)
        tools = {item["name"]: item for item in listed["tools"]}
        for name in ["ops.list_reports", "ops.get_report", "ops.get_report_summary", "ops.list_report_types", "ops.generate_report"]:
            assert name in tools
            assert tools[name]["risk"] == "low"
            assert tools[name]["write"] is False
        assert tools["ops.generate_report"]["category"] == "report_generate"
    finally:
        db.close()
        engine.dispose()


def test_generate_report_tool_creates_diagnostics_artifact(tmp_path, monkeypatch):
    from app.db.models import ReportArtifact
    from app.services.tool_context import ToolContext
    from app.services.tool_registry import register_builtin_tools, registry

    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("REPORT_DIR", str(tmp_path / "reports"))
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path / "data" / "backups"))
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "data" / "logs"))
    engine, Session = _sqlite_session(tmp_path)
    db = Session()
    try:
        register_builtin_tools()
        ctx = ToolContext(username="tester", auth_type="session", is_admin=True, scopes=["*"], allow_write=True)
        result = registry.call(db, "ops.generate_report", {"report_type": "diagnostics", "format": "md", "focus": "mcp"}, ctx)
        assert result["message"] == "success"
        assert result["risk"] == "low"
        assert result["result"]["report"]["format"] == "md"
        assert db.query(ReportArtifact).count() == 1
    finally:
        db.close()
        engine.dispose()
