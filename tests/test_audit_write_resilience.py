"""审计/作业写入失败必须"可观测"，不得静默吞掉（2026-09-12 复盘修复）。

历史缺陷（app/services/audit_writer.py、app/services/job_service.py）：
  - audit_writer：队列满时的同步兜底写入失败、关停排空阶段失败都是 ``except: pass``；
    且 ``db = SessionLocal()`` 写在 try 之外，DB 不可用时异常会直接落到这两个 pass，
    审计记录静默丢失且日志里毫无痕迹。
  - job_service：作业失败时 ``_set_job_status(status="failed")`` 若抛错也被
    ``except: pass`` 吞掉 —— 作业会永久停留在 running 且没有任何日志。

修复原则：清理类失败（close/SSE 推送）保持静默容忍；**状态/审计类写入失败必须
logger.exception**，让故障可见。
"""
from __future__ import annotations

import logging
import queue

import pytest


@pytest.fixture()
def audit_writer():
    from app.services import audit_writer as module

    yield module
    # 恢复全局状态，避免影响其它测试
    module._shutdown_event.clear()
    module._writer_thread = None
    while not module._audit_queue.empty():
        try:
            module._audit_queue.get_nowait()
            module._audit_queue.task_done()
        except queue.Empty:
            break


def test_submit_audit_write_logs_when_full_queue_fallback_fails(audit_writer, monkeypatch, caplog):
    monkeypatch.setattr(audit_writer, "_audit_queue", queue.Queue(maxsize=1))
    audit_writer._audit_queue.put_nowait(lambda: None)  # 占满队列

    def boom():
        raise RuntimeError("db unavailable")

    with caplog.at_level(logging.ERROR, logger="app.services.audit_writer"):
        audit_writer.submit_audit_write(boom)  # 不应抛出

    assert "同步兜底写入失败" in caplog.text
    assert "db unavailable" in caplog.text or "RuntimeError" in caplog.text


def test_shutdown_drains_queue_and_logs_lost_records(audit_writer, monkeypatch, caplog):
    executed = []
    monkeypatch.setattr(audit_writer, "_writer_thread", None)
    monkeypatch.setattr(audit_writer, "_audit_queue", queue.Queue(maxsize=8))
    audit_writer._audit_queue.put_nowait(lambda: executed.append("ok"))

    def boom():
        raise RuntimeError("drain failure")

    audit_writer._audit_queue.put_nowait(boom)

    with caplog.at_level(logging.ERROR, logger="app.services.audit_writer"):
        audit_writer.shutdown_audit_writer(timeout=0)

    assert executed == ["ok"], "排空阶段要尽量把剩余审计写完"
    assert "关停排空阶段失败" in caplog.text
    assert audit_writer._audit_queue.empty()


def test_record_tool_call_async_logs_db_failure_without_raising(audit_writer, monkeypatch, caplog):
    import app.db as app_db

    def broken_session():
        raise RuntimeError("sqlite locked")

    monkeypatch.setattr(app_db, "SessionLocal", broken_session)
    monkeypatch.setattr(audit_writer, "_audit_queue", queue.Queue(maxsize=8))

    with caplog.at_level(logging.ERROR, logger="app.services.audit_writer"):
        audit_id = audit_writer.record_tool_call_async("ops.list_servers", ctx=None, input_args={})

    assert audit_id, "仍要返回 audit_id，便于调用侧关联"
    # 手工执行队列里的写入任务（测试环境不启动 writer 线程）
    task = audit_writer._audit_queue.get_nowait()
    task()  # 不得抛出
    assert "审计写入失败" in caplog.text
    assert "sqlite locked" in caplog.text or "RuntimeError" in caplog.text


def test_record_plan_event_async_logs_db_failure_without_raising(audit_writer, monkeypatch, caplog):
    import app.db as app_db

    def broken_session():
        raise RuntimeError("plan event db down")

    monkeypatch.setattr(app_db, "SessionLocal", broken_session)
    monkeypatch.setattr(audit_writer, "_audit_queue", queue.Queue(maxsize=8))

    with caplog.at_level(logging.ERROR, logger="app.services.audit_writer"):
        event_id = audit_writer.record_plan_event_async("plan-1", "executed", actor="alice")

    assert event_id
    task = audit_writer._audit_queue.get_nowait()
    task()
    assert "计划事件写入失败" in caplog.text


def test_job_status_and_audit_write_failures_are_logged(monkeypatch, caplog):
    """作业失败状态写回失败 = 作业可能永远 running，必须留下 ERROR 日志。"""
    from app.services import job_service

    class FakeJob:
        id = "job-1"
        status = "queued"
        source_tool = "ops.list_servers"
        request_json = {"tool": "ops.list_servers", "arguments": {}, "context": {}}
        progress = 0
        started_at = None
        finished_at = None
        result_json = None
        error_message = None
        worker_id = None
        updated_at = None

    class FakeQuery:
        def filter(self, *args, **kwargs):
            return self

        def first(self):
            return FakeJob()

    class FakeDB:
        def query(self, *args, **kwargs):
            return FakeQuery()

        def close(self):
            pass

    monkeypatch.setattr(job_service, "SessionLocal", lambda: FakeDB())

    def boom_status(*args, **kwargs):
        raise RuntimeError("status write failed")

    monkeypatch.setattr(job_service, "_set_job_status", boom_status)

    with caplog.at_level(logging.ERROR, logger="app.services.job_service"):
        job_service._execute_tool_job("job-1")  # 不得抛出

    assert "状态写回失败" in caplog.text
    assert "job-1" in caplog.text
