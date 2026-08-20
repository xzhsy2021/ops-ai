from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import InspectionItemConfig, InspectionItemResult, InspectionRun


def _sqlite_session(tmp_path):
    from app.db.models import Base

    engine = create_engine(
        f"sqlite:///{tmp_path / 'overview_running.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    return engine, Session


def _make_running_run(db, run_id="run-running-1", scope_type="SERVER"):
    db.add(InspectionRun(
        id=run_id,
        scope_type=scope_type,
        status="RUNNING",
        server_id="srv-1",
        categories=["DISK", "MEMORY"],
        score=100,
    ))
    db.commit()


def _make_item_configs(db):
    db.add_all([
        InspectionItemConfig(item_code="DISK_01", item_name="磁盘空间", category="DISK", scope_type="SERVER", enabled=True),
        InspectionItemConfig(item_code="DISK_02", item_name="inode", category="DISK", scope_type="SERVER", enabled=True),
        InspectionItemConfig(item_code="MEM_01", item_name="内存", category="MEMORY", scope_type="SERVER", enabled=True),
    ])
    db.commit()


def test_overview_returns_running_runs_with_progress(tmp_path):
    from app.services.inspection_center import overview

    engine, Session = _sqlite_session(tmp_path)
    db = Session()
    try:
        _make_running_run(db)
        _make_item_configs(db)
        db.add(InspectionItemResult(
            run_id="run-running-1", scope_type="SERVER", server_id="srv-1",
            category="DISK", item_code="DISK_01", item_name="磁盘空间",
            status="PASS", risk_level="NONE",
        ))
        db.add(InspectionItemResult(
            run_id="run-running-1", scope_type="SERVER", server_id="srv-1",
            category="DISK", item_code="DISK_02", item_name="inode",
            status="RISK", risk_level="HIGH",
        ))
        db.commit()

        data = overview(db)
        running = data.get("running_runs") or []
        assert len(running) == 1
        item = running[0]
        assert item["id"] == "run-running-1"
        assert item["status"] == "RUNNING"
        assert item["items_done"] == 2
        assert item["items_total"] == 3
        assert 0 < item["progress_percent"] < 100
    finally:
        db.close()
        engine.dispose()


def test_overview_running_empty_when_no_active(tmp_path):
    from app.services.inspection_center import overview

    engine, Session = _sqlite_session(tmp_path)
    db = Session()
    try:
        db.add(InspectionRun(id="run-done", scope_type="SERVER", status="COMPLETED", server_id="srv-1"))
        db.commit()
        data = overview(db)
        assert (data.get("running_runs") or []) == []
    finally:
        db.close()
        engine.dispose()