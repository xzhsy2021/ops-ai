from __future__ import annotations

from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def _sqlite_session(tmp_path):
    from app.db.models import Base

    engine = create_engine(
        f"sqlite:///{tmp_path / 'job_progress.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    return engine, Session


def _fake_server_result(server_id):
    return {
        "run": {"id": f"run-{server_id}", "server_id": server_id, "status": "COMPLETED"},
        "success": True,
        "failed": 0,
        "summary": f"{server_id} done",
    }


def test_batch_inspection_updates_job_progress_per_server(tmp_path, monkeypatch):
    """批量巡检执行期间 OperationJob.progress 应随已完成的服务器数递增。

    Regression: 之前 progress 固定卡在 35%（_execute_tool_job 的固定阶段），
    直到整个 batch 同步完成才跳到 90/100，导致任务中心进度不更新。
    """
    from app.db.models import OperationJob
    from app.services import inspection_center as svc

    engine, Session = _sqlite_session(tmp_path)
    db = Session()
    try:
        job = OperationJob(
            id="job-progress-test",
            job_type="mcp_tool",
            source_tool="ops.inspection.run_servers_batch",
            title="批量服务器巡检",
            status="running",
            progress=35,
            risk_level="high",
            request_json={},
            result_json={},
        )
        db.add(job)
        db.commit()

        # concurrency=1 串行执行。任务中心通过独立连接轮询 OperationJob.progress。
        # 测试通过包装 commit 捕获每次进度写入值，验证执行期间进度从 35 递增。
        def _server(server_id, categories, trigger_type, created_by, generate_report, command_timeout_seconds, run_timeout_seconds):
            return _fake_server_result(server_id)

        monkeypatch.setattr(svc, "_run_one_server_in_new_session", _server)
        monkeypatch.setattr(svc, "resolve_servers_for_inspection", lambda *a, **kw: {"eligible_ids": ["srv-1", "srv-2", "srv-3"], "skipped_count": 0, "skipped": [], "total_requested": 3})
        import app.db.base as db_base

        committed_progress = []

        def _tracking_sl():
            session = Session()
            original_commit = session.commit

            def tracked_commit(*a, **kw):
                # 读取该 session 中 job 的挂起修改值（identity map 返回内存中已设置的 percent）
                row = session.query(OperationJob).filter(OperationJob.id == "job-progress-test").first()
                committed_progress.append(row.progress if row else None)
                return original_commit(*a, **kw)

            session.commit = tracked_commit
            return session

        monkeypatch.setattr(db_base, "SessionLocal", _tracking_sl)

        result = svc.run_servers_batch_inspection(
            db,
            server_ids=["srv-1", "srv-2", "srv-3"],
            categories=["DISK"],
            concurrency=1,
            job_id="job-progress-test",
        )

        assert result["success"] == 3
        # 每次服务器完成都会向 OperationJob 写入递增的进度值
        assert len(committed_progress) == 3, committed_progress
        assert all(isinstance(p, int) for p in committed_progress), committed_progress
        assert max(committed_progress) > 35, committed_progress
        assert committed_progress == sorted(committed_progress), committed_progress
        db.refresh(job)
        # handler 内部进度封顶 90%，最终 100 由 _execute_tool_job 在 handler 返回后设置
        assert job.progress == 90
    finally:
        db.close()
        engine.dispose()


def test_batch_inspection_without_job_id_still_works(tmp_path, monkeypatch):
    """未传入 job_id 时批量巡检行为不变（无 job 进度更新）。"""
    from app.services import inspection_center as svc

    engine, Session = _sqlite_session(tmp_path)
    db = Session()
    try:
        monkeypatch.setattr(svc, "_run_one_server_in_new_session", lambda **kw: _fake_server_result(kw.get("server_id", "srv")))
        monkeypatch.setattr(svc, "resolve_servers_for_inspection", lambda *a, **kw: {"eligible_ids": ["srv-1"], "skipped_count": 0, "skipped": [], "total_requested": 1})

        result = svc.run_servers_batch_inspection(
            db,
            server_ids=["srv-1"],
            categories=["DISK"],
        )
        assert result["success"] == 1
    finally:
        db.close()
        engine.dispose()


def test_execute_tool_job_injects_job_id_into_handler_context(tmp_path, monkeypatch):
    """_execute_tool_job 应将 job_id 注入 ctx，handler 读取后透传给批量巡检，
    使执行期间进度得以回写。"""
    import app.db.base as db_base
    import app.services.job_service as job_service
    from app.db.models import OperationJob
    from app.services.tool_context import ToolContext

    engine, Session = _sqlite_session(tmp_path)
    db = Session()
    try:
        monkeypatch.setattr(db_base, "SessionLocal", Session)
        monkeypatch.setattr(job_service, "SessionLocal", Session)

        job = OperationJob(
            id="job-ctx-inject",
            job_type="mcp_tool",
            source_tool="ops.inspection.run_servers_batch",
            title="批量服务器巡检",
            status="queued",
            progress=0,
            risk_level="high",
            request_json={
                "tool": "ops.inspection.run_servers_batch",
                "arguments": {
                    "server_ids": ["srv-1", "srv-2", "srv-3"],
                    "categories": ["DISK"],
                    "concurrency": 1,
                    "batch_size": 10,
                    "command_timeout_seconds": 60,
                    "run_timeout_seconds": 300,
                    "skip_disabled": True,
                    "confirm_text": "确认巡检 xyz",
                },
                "context": ToolContext(
                    username="tester",
                    auth_type="session",
                    is_admin=True,
                    scopes=["*"],
                    allow_write=True,
                ).to_audit_dict(),
                "policy": {"risk_policy": {"must_create_job": True}},
            },
            result_json={},
        )
        db.add(job)
        db.commit()

        captured = {}

        def _fake_batch(db_arg, **kwargs):
            captured["job_id"] = kwargs.get("job_id")
            return {
                "summary": "batch done",
                "eligible": 3,
                "success": 3,
                "failed": 0,
                "runs": [{"id": f"run-{i}", "status": "COMPLETED"} for i in range(3)],
            }

        with patch("app.services.inspection_center.run_servers_batch_inspection", _fake_batch), \
             patch("app.services.tool_adapters.inspection_tools._validate_batch_confirmation", lambda a: {
                 "eligible_ids": ["srv-1", "srv-2", "srv-3"],
                 "categories": ["DISK"],
                 "groups": [],
                 "skip_disabled": True,
                 "execution_plan": {"concurrency": 1, "batch_size": 10, "command_timeout_seconds": 60, "run_timeout_seconds": 300},
             }), \
             patch("app.services.tool_policy.enforce_tool_policy", return_value={
                 "risk_level": "high",
                 "risk_policy": {"must_create_job": True},
                 "allowed": True,
             }):
            job_service._execute_tool_job("job-ctx-inject")

        assert captured.get("job_id") == "job-ctx-inject", captured
        db.refresh(job)
        assert job.status == "success"
        assert job.progress == 100
    finally:
        db.close()
        engine.dispose()