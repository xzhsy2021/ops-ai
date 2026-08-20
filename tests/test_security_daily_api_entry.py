from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base, get_db
from app.db.models import SecurityDailyReport


def _sqlite_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    return engine, session


def _make_client(db, monkeypatch):
    from app.api import inspection as inspection_api

    app = FastAPI()
    app.include_router(inspection_api.router)

    def _get_db():
        yield db

    app.dependency_overrides[get_db] = _get_db
    admin = {"id": 1, "username": "admin-user", "role": "admin", "is_admin": True}
    monkeypatch.setattr(inspection_api, "require_auth", lambda request, db: admin)
    monkeypatch.setattr(inspection_api, "audit", lambda *args, **kwargs: None)
    return TestClient(app)


def test_security_daily_reports_lists_history(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("REPORT_DIR", str(tmp_path / "reports"))
    engine, db = _sqlite_session()
    try:
        db.add(SecurityDailyReport(
            server_name="web01",
            report_date="2026-08-19",
            status="ok",
            max_risk="HIGH",
            summary={"login_failures": 25, "banned_ips": 3},
            error=None,
        ))
        db.commit()

        client = _make_client(db, monkeypatch)
        res = client.get("/api/v2/inspection/security-daily/reports")
        assert res.status_code == 200
        data = res.json().get("data") or {}
        assert data.get("total") == 1
        item = data["items"][0]
        assert item["server_name"] == "web01"
        assert item["max_risk"] == "HIGH"
        assert item["summary"]["login_failures"] == 25
    finally:
        db.close()
        engine.dispose()


def test_security_daily_collect_submits_background_job(tmp_path, monkeypatch):
    """POST /security-daily/collect 不再同步采集，而是提交统一任务中心后台任务并立即返回。"""
    import app.services.job_service as js

    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("REPORT_DIR", str(tmp_path / "reports"))
    engine, db = _sqlite_session()
    try:
        captured = {}

        def _fake_enqueue(db_arg, *, tool_def, arguments, ctx, policy_result):
            captured["tool"] = tool_def.name
            captured["arguments"] = arguments
            captured["ctx"] = ctx
            return {"id": "job-sec-1", "status": "queued", "progress": 0, "job_type": "mcp_tool"}

        monkeypatch.setattr(js, "enqueue_tool_job", _fake_enqueue)
        client = _make_client(db, monkeypatch)
        res = client.post("/api/v2/inspection/security-daily/collect", json={"persist_risks": True})
        assert res.status_code == 200
        data = res.json().get("data") or {}
        assert data["job_id"] == "job-sec-1"
        assert data["status"] == "queued"
        assert "task_center_url" in data
        assert captured["tool"] == "ops.inspection.run_security_daily"
        assert captured["arguments"]["confirm_text"] == "CONFIRM ops.inspection.run_security_daily"
        assert captured["arguments"]["persist_risks"] is True
        assert captured["arguments"]["report_date"] is None
        assert captured["ctx"].username == "admin-user"
    finally:
        db.close()
        engine.dispose()