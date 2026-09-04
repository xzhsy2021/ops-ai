from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture()
def sqlite_session(tmp_path):
    from app.db.models import Base

    engine = create_engine(
        f"sqlite:///{tmp_path / 'record_page_contract.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def test_report_center_batch_delete_removes_records_and_files(sqlite_session, tmp_path, monkeypatch):
    from app.db.models import ReportArtifact
    from app.services.report_center import delete_reports

    monkeypatch.setenv("REPORT_DIR", str(tmp_path / "reports"))
    db = sqlite_session
    paths = []
    for idx in range(2):
        path = tmp_path / "reports" / f"report-{idx}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")
        paths.append(path)
        db.add(ReportArtifact(
            id=f"report_{idx}",
            report_type="diagnostics",
            title=f"report {idx}",
            target_type="system",
            target_id="local",
            status="ready",
            format="json",
            file_path=str(path),
            created_at=_now() - timedelta(minutes=idx),
            updated_at=_now() - timedelta(minutes=idx),
        ))
    db.commit()

    result = delete_reports(db, ["report_0", "report_1"], actor="alice")

    assert result["report_ids"] == ["report_0", "report_1"]
    assert result["deleted"] == 2
    assert db.query(ReportArtifact).count() == 0
    assert all(not path.exists() for path in paths)


def test_db_exports_support_offset_pagination_and_batch_delete(sqlite_session, tmp_path, monkeypatch):
    from app.db.models import ReportArtifact
    from app.services.db_query_export import DbQueryExportService

    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("DB_EXPORT_DIR", str(tmp_path / "exports"))
    db = sqlite_session
    created = _now()
    paths = []
    for idx in range(3):
        path = tmp_path / "exports" / f"export-{idx}.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("id\n1\n", encoding="utf-8")
        paths.append(path)
        db.add(ReportArtifact(
            id=f"export_{idx}",
            report_type="db_query_export",
            title=f"export {idx}",
            target_type="database",
            target_id="ops",
            status="ready",
            format="csv",
            file_path=str(path),
            created_at=created - timedelta(minutes=idx),
            updated_at=created - timedelta(minutes=idx),
        ))
    db.commit()

    svc = DbQueryExportService(db)
    page = svc.list_exports(limit=1, offset=1)
    assert page["total"] == 3
    assert page["limit"] == 1
    assert page["offset"] == 1
    assert [item["id"] for item in page["items"]] == ["export_1"]

    result = svc.delete_exports(["export_0", "export_2"])
    assert result["export_ids"] == ["export_0", "export_2"]
    assert result["deleted"] == 2
    assert db.query(ReportArtifact).filter(ReportArtifact.report_type == "db_query_export").count() == 1
    assert not paths[0].exists()
    assert paths[1].exists()
    assert not paths[2].exists()


def test_tool_records_are_paginated_and_batch_cleanup_removes_events(sqlite_session):
    from app.db.models import ToolCallLog, ToolPlan, ToolPlanEvent
    from app.services.tool_records import delete_tool_records, list_tool_call_logs, list_tool_plans

    db = sqlite_session
    created = _now()
    for idx in range(3):
        db.add(ToolCallLog(
            id=f"call_{idx}",
            tool_name="ops.test",
            username="alice",
            status="success",
            risk_level="low",
            created_at=created - timedelta(minutes=idx),
        ))
        db.add(ToolPlan(
            id=f"plan_{idx}",
            plan_type="deploy",
            status="executed",
            created_by="alice",
            risk_level="medium",
            created_at=created - timedelta(minutes=idx),
            updated_at=created - timedelta(minutes=idx),
        ))
        db.flush()
        db.add(ToolPlanEvent(plan_id=f"plan_{idx}", event_type="done", actor="alice", message="done", created_at=created))
    db.commit()

    calls = list_tool_call_logs(db, limit=1, offset=1, user={"username": "alice", "is_admin": True})
    plans = list_tool_plans(db, limit=1, offset=1, user={"username": "alice", "is_admin": True})
    assert calls["total"] == 3
    assert calls["offset"] == 1
    assert [item["id"] for item in calls["items"]] == ["call_1"]
    assert plans["total"] == 3
    assert plans["offset"] == 1
    assert [item["id"] for item in plans["items"]] == ["plan_1"]

    result = delete_tool_records(db, call_ids=["call_0"], plan_ids=["plan_0"], actor="admin")
    assert result["deleted"]["tool_call_logs"] == 1
    assert result["deleted"]["tool_plans"] == 1
    assert result["deleted"]["tool_plan_events"] == 1
    assert db.query(ToolCallLog).filter_by(id="call_0").count() == 0
    assert db.query(ToolPlan).filter_by(id="plan_0").count() == 0
    assert db.query(ToolPlanEvent).filter_by(plan_id="plan_0").count() == 0


def test_tool_record_cleanup_rejects_active_plans_without_force(sqlite_session):
    from app.db.models import ToolPlan
    from app.services.tool_records import delete_tool_records

    db = sqlite_session
    db.add(ToolPlan(id="plan_active", plan_type="deploy", status="ready", created_by="alice", created_at=_now(), updated_at=_now()))
    db.commit()

    with pytest.raises(HTTPException) as excinfo:
        delete_tool_records(db, plan_ids=["plan_active"], actor="admin")

    assert excinfo.value.status_code == 409
    assert db.query(ToolPlan).filter_by(id="plan_active").count() == 1


def test_dml_execution_history_supports_offset_pagination_and_batch_delete(sqlite_session):
    from app.db.models import DmlExecutionLog
    from app.services.db_query_export import DbQueryExportService

    db = sqlite_session
    created = _now()
    for idx in range(3):
        db.add(DmlExecutionLog(
            id=f"dml_{idx}",
            preview_id=f"preview_{idx}",
            source="local_ops_db",
            connection_id="",
            connection_name="local_ops_db",
            database_name="ops",
            statement_type="UPDATE",
            table_name="report_artifacts",
            history_sql=f"UPDATE report_artifacts SET status = 'archived' WHERE id = '{idx}'",
            affected_rows=1,
            risk_level="high",
            status="success",
            reason="contract test",
            operator="alice",
            created_at=created - timedelta(minutes=idx),
        ))
    db.commit()

    svc = DbQueryExportService(db)
    page = svc.list_dml_executions(limit=1, offset=1)
    assert page["pagination"]["total"] == 3
    assert page["pagination"]["offset"] == 1
    assert [item["id"] for item in page["items"]] == ["dml_1"]

    result = svc.delete_dml_executions(["dml_0", "dml_2"])
    assert result["execution_ids"] == ["dml_0", "dml_2"]
    assert result["deleted"] == 2
    assert db.query(DmlExecutionLog).count() == 1
    assert db.query(DmlExecutionLog).filter_by(id="dml_1").count() == 1


def test_command_execution_history_supports_offset_pagination_and_batch_delete(sqlite_session):
    from app.db.models import CommandExecutionLog
    from app.services.command_history import count_executions, delete_executions, query_executions

    db = sqlite_session
    created = _now()
    for idx in range(3):
        db.add(CommandExecutionLog(
            id=f"cmd_{idx}",
            server_name="srv-a",
            username="root",
            command=f"echo {idx}",
            exit_code=0,
            stdout_preview=str(idx),
            stderr_preview="",
            duration_ms=idx + 1,
            risk_level="safe",
            created_at=created - timedelta(minutes=idx),
        ))
    db.commit()

    page = query_executions(db, server_name="srv-a", limit=1, offset=1)
    assert count_executions(db, server_name="srv-a") == 3
    assert [item.id for item in page] == ["cmd_1"]

    result = delete_executions(db, ["cmd_0", "cmd_2"])
    assert result["log_ids"] == ["cmd_0", "cmd_2"]
    assert result["deleted"] == 2
    assert db.query(CommandExecutionLog).count() == 1
    assert db.query(CommandExecutionLog).filter_by(id="cmd_1").count() == 1


def test_dml_and_command_history_clients_expose_pagination_and_batch_delete_controls():
    api = open("frontend/src/api.ts", encoding="utf-8").read()
    db_page = open("frontend/src/pages/DatabaseToolsPage.tsx", encoding="utf-8").read()
    server_page = open("frontend/src/pages/ServerDetailPage.tsx", encoding="utf-8").read()

    assert "deleteExecuteHistory" in api
    assert "deleteExecuteHistories" in api
    assert "deleteExecHistory" in api
    assert "deleteExecHistories" in api
    assert "historyOffset" in db_page
    assert "historyPageSize" in db_page
    assert "selectedHistoryIds" in db_page
    assert "deleteSelectedDmlHistory" in db_page
    assert "historyOffset" in server_page
    assert "historyPageSize" in server_page
    assert "selectedLogIds" in server_page
    assert "deleteSelectedLogs" in server_page


def test_audit_log_api_and_client_expose_offset_without_delete_endpoint():
    backend = open("app/api/task_center.py", encoding="utf-8").read()
    api = open("frontend/src/api.ts", encoding="utf-8").read()
    page = open("frontend/src/pages/AuditLogPage.tsx", encoding="utf-8").read()

    assert "async def list_audit(request: Request, response: Response, limit: int = 200, offset: int = 0" in backend
    assert "rows = rows[offset:offset + limit]" in backend
    assert "list: (params?: { limit?: number; offset?: number; action?: string })" in api
    assert "auditLog.list({ limit: pageSize, offset, action: effAction || undefined })" in page
    assert "deleteMany" not in api.split("export const auditLog = {", 1)[1].split("export const capabilityTools", 1)[0]


def test_record_pages_expose_batch_delete_and_server_pagination_controls():
    api = open("frontend/src/api.ts", encoding="utf-8").read()
    report_page = open("frontend/src/pages/ReportCenterPage.tsx", encoding="utf-8").read()
    db_page = open("frontend/src/pages/DatabaseToolsPage.tsx", encoding="utf-8").read()
    tool_page = open("frontend/src/pages/ToolAccessPage.tsx", encoding="utf-8").read()
    mcp_audit_page = open("frontend/src/pages/McpAuditPage.tsx", encoding="utf-8").read()

    assert "deleteMany: (data: { report_ids: string[] })" in api
    assert "deleteExports: (data: { export_ids: string[] })" in api
    assert "calls: (params?: { limit?: number; offset?: number; tool?: string; status?: string })" in api
    assert "plans: (params?: { limit?: number; offset?: number; plan_type?: string; status?: string })" in api
    assert "deleteRecords: (data: { call_ids?: string[]; plan_ids?: string[]; force?: boolean })" in api

    assert "selectedReportIds" in report_page
    assert "reports.deleteMany({ report_ids: ids })" in report_page
    assert "批量删除" in report_page or "鎵归噺鍒犻櫎" in report_page

    assert "exportOffset" in db_page
    assert "selectedExportIds" in db_page
    assert "dbTools.deleteExports({ export_ids: ids })" in db_page
    assert "dbTools.exports({ limit: exportPageSize, offset: exportOffset })" in db_page

    assert "callOffset" in tool_page
    assert "planOffset" in tool_page
    assert "selectedCallIds" in tool_page
    assert "selectedPlanIds" in tool_page
    assert "capabilityTools.deleteRecords" in tool_page
    assert "ToolRecordPagination" in tool_page

    assert "offset: (page - 1) * pageSize" in mcp_audit_page
    assert "setTotal(Number(data?.total || 0))" in mcp_audit_page
    assert "paged = items" not in mcp_audit_page
