from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture()
def sqlite_session(tmp_path):
    from app.db.models import Base

    engine = create_engine(
        f"sqlite:///{tmp_path / 'task_center_delete.db'}",
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


def test_delete_runtime_tasks_removes_finished_tool_sql_cleanup_rows(sqlite_session):
    from app.db.models import CleanupJob, CleanupJobBatch, CleanupJobEvent, OperationJob, SqlQueryHistory
    from app.domain.runtime.jobs import delete_runtime_tasks

    db = sqlite_session
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    db.add(OperationJob(id="job_done", title="Tool done", status="failed", operator="alice", created_at=now, updated_at=now))
    db.add(SqlQueryHistory(id="sql_done", sql_text="select 1", status="success", created_at=now))
    db.add(CleanupJob(
        id="cleanup_done",
        name="cleanup",
        environment="test",
        connection_name="local",
        database_name="ops",
        table_name="logs",
        date_column="created_at",
        cutoff_time="2026-01-01",
        status="failed",
        created_by="alice",
        created_at=now,
    ))
    db.add(CleanupJobBatch(job_id="cleanup_done", batch_no=1, status="failed", started_at=now))
    db.add(CleanupJobEvent(job_id="cleanup_done", event_type="failed", message="failed", created_at=now))
    db.commit()

    result = delete_runtime_tasks(
        db,
        [
            {"kind": "tool", "id": "job_done"},
            {"kind": "sql", "id": "sql_done"},
            {"kind": "cleanup", "id": "cleanup_done"},
        ],
        confirm_text="DELETE TASKS 3",
        actor="alice",
    )

    assert result["deleted"]["tool"] == 1
    assert result["deleted"]["sql"] == 1
    assert result["deleted"]["cleanup"] == 1
    assert result["deleted"]["cleanup_job_batches"] == 1
    assert result["deleted"]["cleanup_job_events"] == 1
    assert db.query(OperationJob).count() == 0
    assert db.query(SqlQueryHistory).count() == 0
    assert db.query(CleanupJob).count() == 0
    assert db.query(CleanupJobBatch).count() == 0
    assert db.query(CleanupJobEvent).count() == 0


def test_delete_runtime_tasks_rejects_active_rows_without_force(sqlite_session):
    from app.db.models import OperationJob
    from app.domain.runtime.jobs import delete_runtime_tasks

    db = sqlite_session
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    db.add(OperationJob(id="job_running", title="Tool running", status="running", operator="alice", created_at=now, updated_at=now))
    db.commit()

    with pytest.raises(HTTPException) as excinfo:
        delete_runtime_tasks(
            db,
            [{"kind": "tool", "id": "job_running"}],
            confirm_text="DELETE TASKS 1",
            actor="alice",
        )

    assert excinfo.value.status_code == 409
    assert db.query(OperationJob).filter_by(id="job_running").count() == 1


def test_list_runtime_jobs_marks_stale_operation_jobs_failed(sqlite_session):
    from datetime import timedelta

    from app.db.models import OperationJob
    from app.domain.runtime.jobs import list_runtime_jobs

    db = sqlite_session
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    stale_at = now - timedelta(days=1)
    db.add(OperationJob(
        id="job_stale",
        title="Tool stale",
        source_tool="ops.inspection.run_server",
        status="running",
        progress=35,
        operator="alice",
        request_json={"tool": "ops.inspection.run_server", "arguments": {"server_id": "srv-a"}},
        created_at=stale_at,
        started_at=stale_at,
        updated_at=stale_at,
    ))
    db.add(OperationJob(
        id="job_recent",
        title="Tool recent",
        source_tool="ops.inspection.run_server",
        status="running",
        progress=35,
        operator="alice",
        request_json={"tool": "ops.inspection.run_server", "arguments": {"server_id": "srv-b"}},
        created_at=now,
        started_at=now,
        updated_at=now,
    ))
    db.commit()

    items = list_runtime_jobs(db, kind="tool", limit=10)

    by_id = {item["id"]: item for item in items}
    stale = db.query(OperationJob).filter_by(id="job_stale").one()
    recent = db.query(OperationJob).filter_by(id="job_recent").one()
    assert by_id["job_stale"]["status"] == "failed"
    assert stale.status == "failed"
    assert stale.finished_at is not None
    assert "stale" in (stale.error_message or "").lower()
    assert by_id["job_recent"]["status"] == "running"
    assert recent.status == "running"
    assert recent.finished_at is None


def test_task_center_frontend_exposes_single_and_batch_delete_actions():
    page = open("frontend/src/pages/TaskCenterPage.tsx", encoding="utf-8").read()
    api = open("frontend/src/api.ts", encoding="utf-8").read()

    assert "deleteMany" in api
    assert "delete: (kind: string, id: string" in api
    assert "selectedTaskKeys" in page
    assert "pendingTaskDeleteItems" in page
    assert "DELETE TASKS" in page
    assert "批量删除" in page
