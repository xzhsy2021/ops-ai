"""巡检任务生命周期契约回归（2026-09-12 复盘第 5 轮）。

缺陷：run 会永久停在 RUNNING（无任何回收机制）
--------------------------------------------------------------------------------
``create_server_inspection_run`` / ``create_project_inspection_run`` 建库时状态直接是
``RUNNING``。而执行链路上有若干"未收尾就退出"的路径：

1. ``execute_server_inspection_run`` / ``execute_project_inspection_run`` 里
   ``db.query(...)``、``_load_thresholds(db)``、``_get_project_with_relations(db, pid)``
   都在 ``try`` **之外**——这些位置抛错（典型是 SQLite 并发下的 "database is locked"）
   时函数直接冒泡，run 永远留在 RUNNING；
2. ``execute_server_inspection_runs_batch`` 的 worker 失败只记录到 ``errors`` 列表，
   **不写库**，所以调用方看到"失败 N 台"，历史/概览里却一直显示"执行中"；
3. ``_run_one_server_in_new_session``（批量同步接口用）worker 抛错时同样只冒泡；
4. API 层 ``_run_servers_batch_background`` 完全没有 try/except：批量入口整体失败时，
   ``/servers/batch-start`` 预建的一批 RUNNING run 全部成为僵尸；
5. 以上任何中断（尤其是**后端进程重启/部署**，后台任务是进程内的）都没有任何回收机制，
   概览页会永久显示"执行中"。

本文件锁定：
- 执行器任一步骤失败都必须把 run 收尾为 FAILED（幂等，不覆盖已终结状态）；
- 僵尸回收器只回收"执行器确实已停"的 run，不得误杀批量排队中的 run。
"""
from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture()
def insp_db(tmp_path, monkeypatch):
    """隔离数据库；并让 SessionLocal 指向它，便于测试独立会话兜底逻辑。"""
    from app.db.base import Base

    engine = create_engine(f"sqlite:///{tmp_path / 'insp.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    session = factory()

    import app.db.base as db_base

    monkeypatch.setattr(db_base, "SessionLocal", factory, raising=False)
    try:
        yield SimpleNamespace(db=session, factory=factory, engine=engine)
    finally:
        session.close()
        engine.dispose()


def _new_run(db, *, status: str = "RUNNING", updated_ago_seconds: int = 0, server_id: str = "srv-1"):
    from app.services import inspection_center as ic
    from app.db.models import InspectionRun
    from uuid import uuid4

    now = ic._now()
    run = InspectionRun(
        id=uuid4().hex,
        scope_type="SERVER",
        scope_kind="SERVER",
        server_id=server_id,
        trigger_type="MANUAL",
        status=status,
        score=100,
        categories=["login"],
        summary="巡检任务已创建，等待执行。",
        created_by="tester",
        started_at=now - timedelta(seconds=updated_ago_seconds),
        created_at=now - timedelta(seconds=updated_ago_seconds),
        updated_at=now - timedelta(seconds=updated_ago_seconds),
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


# ---------------------------------------------------------------------------
# 1. 执行器失败必须收尾（此前 try 之外的步骤会冒泡并留下 RUNNING）
# ---------------------------------------------------------------------------

def test_execute_run_marks_failed_when_threshold_load_raises(insp_db, monkeypatch):
    from app.services import inspection_center as ic

    run = _new_run(insp_db.db)

    def _boom(db=None):
        raise RuntimeError("simulated threshold failure")

    monkeypatch.setattr(ic, "_load_thresholds", _boom)
    monkeypatch.setattr(ic, "_rule_execution_specs", lambda *a, **k: [])

    ic.execute_server_inspection_run(insp_db.db, run_id=run.id, server_id="srv-1")

    insp_db.db.expire_all()
    from app.db.models import InspectionRun

    row = insp_db.db.query(InspectionRun).filter(InspectionRun.id == run.id).first()
    assert row.status == "FAILED", "执行步骤抛错后 run 不得停在 RUNNING"
    assert row.finished_at is not None


def test_project_execute_run_marks_failed_when_project_lookup_raises(insp_db, monkeypatch):
    from app.services import inspection_center as ic
    from app.db.models import InspectionRun

    run = _new_run(insp_db.db, server_id="", status="RUNNING")
    run.scope_type = "PROJECT"
    run.scope_kind = "PROJECT"
    run.project_id = "proj-1"
    insp_db.db.commit()

    def _boom(db, pid):
        raise RuntimeError("simulated project lookup failure")

    monkeypatch.setattr(ic, "_get_project_with_relations", _boom)

    ic.execute_project_inspection_run(insp_db.db, run_id=run.id, project_id="proj-1")

    insp_db.db.expire_all()
    row = insp_db.db.query(InspectionRun).filter(InspectionRun.id == run.id).first()
    assert row.status == "FAILED"
    assert row.finished_at is not None


def test_batch_worker_failure_marks_run_failed(insp_db, monkeypatch):
    """批量 worker 抛错时，调用方记录的 errors 必须与库中状态一致。"""
    from app.services import inspection_center as ic
    from app.db.models import InspectionRun

    run = _new_run(insp_db.db)

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated worker crash")

    monkeypatch.setattr(ic, "execute_server_inspection_run", _boom)

    result = ic.execute_server_inspection_runs_batch(
        [{"run_id": run.id, "server_id": "srv-1", "categories": ["login"]}],
        concurrency=1,
        batch_size=1,
    )

    assert result["failed"] == 1
    assert result["errors"][0]["run_id"] == run.id
    insp_db.db.expire_all()
    row = insp_db.db.query(InspectionRun).filter(InspectionRun.id == run.id).first()
    assert row.status == "FAILED", "worker 失败后 run 不得停在 RUNNING"


def test_run_one_server_in_new_session_marks_run_failed(insp_db, monkeypatch):
    """批量同步接口的 worker：创建成功但执行抛错时也要收尾。"""
    from app.services import inspection_center as ic
    from app.db.models import InspectionRun

    monkeypatch.setattr(ic, "assert_server_inspectable", lambda sid: {"id": sid})

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated execute crash")

    monkeypatch.setattr(ic, "execute_server_inspection_run", _boom)

    with pytest.raises(RuntimeError):
        ic._run_one_server_in_new_session(
            server_id="srv-1",
            categories=["login"],
            trigger_type="MANUAL",
            created_by="tester",
            generate_report=False,
            command_timeout_seconds=5,
            run_timeout_seconds=30,
        )

    insp_db.db.expire_all()
    rows = insp_db.db.query(InspectionRun).filter(InspectionRun.server_id == "srv-1").all()
    assert rows, "应已创建 run 行"
    assert all(r.status == "FAILED" for r in rows), [r.status for r in rows]


# ---------------------------------------------------------------------------
# 2. mark_run_failed 幂等且不覆盖已终结状态
# ---------------------------------------------------------------------------

def test_mark_run_failed_does_not_clobber_finished_run(insp_db):
    from app.services import inspection_center as ic
    from app.db.models import InspectionRun

    run = _new_run(insp_db.db, status="SUCCESS")
    changed = ic.mark_run_failed(insp_db.db, run.id, "不应改写")

    assert changed is False
    insp_db.db.expire_all()
    row = insp_db.db.query(InspectionRun).filter(InspectionRun.id == run.id).first()
    assert row.status == "SUCCESS"
    assert row.summary != "不应改写"


def test_mark_run_failed_is_idempotent(insp_db):
    from app.services import inspection_center as ic
    from app.db.models import InspectionRun

    run = _new_run(insp_db.db)
    assert ic.mark_run_failed(insp_db.db, run.id, "第一次") is True
    assert ic.mark_run_failed(insp_db.db, run.id, "第二次") is False
    insp_db.db.expire_all()
    row = insp_db.db.query(InspectionRun).filter(InspectionRun.id == run.id).first()
    assert row.status == "FAILED"
    assert row.summary == "第一次"


def test_mark_run_failed_uses_fresh_session_when_given_none(insp_db):
    """进程重启场景：调用方没有可用会话时也要能收尾。"""
    from app.services import inspection_center as ic
    from app.db.models import InspectionRun

    run = _new_run(insp_db.db)
    assert ic.mark_run_failed(None, run.id, "独立会话兜底") is True

    insp_db.db.expire_all()
    row = insp_db.db.query(InspectionRun).filter(InspectionRun.id == run.id).first()
    assert row.status == "FAILED"
    assert "独立会话兜底" in row.summary


# ---------------------------------------------------------------------------
# 3. 僵尸回收器：只回收"执行器已停"的 run，不误杀排队中的 run
# ---------------------------------------------------------------------------

def test_reaper_marks_stale_running_run_failed(insp_db):
    from app.services import inspection_center as ic
    from app.db.models import InspectionRun

    run = _new_run(insp_db.db, updated_ago_seconds=ic.INSPECTION_STALE_RUN_SECONDS + 600)
    reaped = ic.reap_stale_inspection_runs(insp_db.db)

    assert reaped == [run.id]
    insp_db.db.expire_all()
    row = insp_db.db.query(InspectionRun).filter(InspectionRun.id == run.id).first()
    assert row.status == "FAILED"
    assert row.finished_at is not None


def test_reaper_keeps_fresh_running_run(insp_db):
    from app.services import inspection_center as ic
    from app.db.models import InspectionRun

    run = _new_run(insp_db.db, updated_ago_seconds=5)

    assert ic.reap_stale_inspection_runs(insp_db.db) == []
    insp_db.db.expire_all()
    row = insp_db.db.query(InspectionRun).filter(InspectionRun.id == run.id).first()
    assert row.status == "RUNNING"


def test_reaper_keeps_queued_siblings_while_batch_is_alive(insp_db):
    """批量排队中的 run 自身不会更新；只要同批有 run 在推进就不能判定为僵尸。"""
    from app.services import inspection_center as ic
    from app.db.models import InspectionRun

    active = _new_run(insp_db.db, server_id="srv-active", updated_ago_seconds=2)
    queued = _new_run(insp_db.db, server_id="srv-queued", updated_ago_seconds=ic.INSPECTION_STALE_RUN_SECONDS + 600)

    assert ic.reap_stale_inspection_runs(insp_db.db) == []

    insp_db.db.expire_all()
    rows = {r.id: r for r in insp_db.db.query(InspectionRun).all()}
    assert rows[queued.id].status == "RUNNING", "同批仍在推进时不得误杀排队任务"
    assert rows[active.id].status == "RUNNING"


def test_reaper_ignores_terminal_runs(insp_db):
    from app.services import inspection_center as ic
    from app.db.models import InspectionRun

    done = _new_run(insp_db.db, status="SUCCESS", updated_ago_seconds=ic.INSPECTION_STALE_RUN_SECONDS + 600)

    assert ic.reap_stale_inspection_runs(insp_db.db) == []
    insp_db.db.expire_all()
    row = insp_db.db.query(InspectionRun).filter(InspectionRun.id == done.id).first()
    assert row.status == "SUCCESS"


def test_overview_reaps_zombie_runs(insp_db):
    """概览页自愈：僵尸不再出现在 running_runs 里。"""
    from app.services import inspection_center as ic
    from app.db.models import InspectionRun

    zombie = _new_run(insp_db.db, updated_ago_seconds=ic.INSPECTION_STALE_RUN_SECONDS + 600)

    data = ic.overview(insp_db.db)

    assert all(item.get("run_id") != zombie.id for item in data.get("running_runs") or [])
    insp_db.db.expire_all()
    row = insp_db.db.query(InspectionRun).filter(InspectionRun.id == zombie.id).first()
    assert row.status == "FAILED"
