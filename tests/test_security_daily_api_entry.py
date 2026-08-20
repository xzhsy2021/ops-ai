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


def test_security_daily_collect_endpoint_calls_collect_all(tmp_path, monkeypatch):
    import app.services.security_daily as sd

    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("REPORT_DIR", str(tmp_path / "reports"))
    engine, db = _sqlite_session()
    try:
        captured = {}

        def _fake_collect_all(db_arg, report_date=None, persist_risks=True):
            captured["report_date"] = report_date
            captured["persist_risks"] = persist_risks
            return {
                "report_date": "2026-08-19",
                "servers": [{"name": "web01"}],
                "results": [{"server": "web01", "ok": True, "status": "ok",
                             "max_risk": "LOW", "reasons": [], "items_count": 4,
                             "summary": {"login_failures": 0, "banned_ips": 0, "load_avg": 0.2}}],
                "summary": {"report_date": "2026-08-19", "server_count": 1, "ok_count": 1,
                            "failed_count": 0, "high_count": 0, "medium_count": 0,
                            "login_failures_total": 0, "banned_ips_total": 0},
                "archive": {},
            }

        monkeypatch.setattr(sd, "collect_all", _fake_collect_all)
        client = _make_client(db, monkeypatch)
        res = client.post("/api/v2/inspection/security-daily/collect", json={"persist_risks": True})
        assert res.status_code == 200
        data = res.json().get("data") or {}
        assert data["report_date"] == "2026-08-19"
        assert captured["report_date"] is None
        assert captured["persist_risks"] is True
    finally:
        db.close()
        engine.dispose()