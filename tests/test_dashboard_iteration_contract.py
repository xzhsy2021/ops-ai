import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture()
def sqlite_session(tmp_path):
    from app.db.models import Base

    engine = create_engine(
        f"sqlite:///{tmp_path / 'dashboard_iteration.db'}",
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


def test_dashboard_summary_aggregates_daily_ops(monkeypatch, sqlite_session):
    from app.db.models import DeployPackage, Deployment, Service
    from app.services import dashboard
    import app.services.server_ops as server_ops

    db = sqlite_session
    db.add(Service(name="api", display_name="API", system_name="ops"))
    db.add(Deployment(system="ops", service="api", environment="test", status="failed", servers="s1,s2", version="v1"))
    db.add(DeployPackage(package_name="api-v1.tar.gz", system="ops", service="api", size_bytes=1234, uploaded_by="tester"))
    db.commit()

    monkeypatch.setattr(server_ops, "get_all_servers", lambda: [
        {"name": "s1", "host": "127.0.0.1", "user": "root", "password": "x", "group": "test"},
        {"name": "s2", "host": "127.0.0.2", "user": "root"},
    ])
    monkeypatch.setattr(dashboard, "get_runtime_usage", lambda db: {
        "deployments_by_status": {"failed": 1},
        "deploy_tasks_by_status": {},
        "process": {"pid": 1},
        "runtime_limits": {},
        "terminal": {},
        "ssh_pool": {"active_connections": 0},
    })
    monkeypatch.setattr(dashboard, "get_storage_usage", lambda db: {
        "total_managed_size_human": "1.2 MB",
        "buckets": {"database": {"size_bytes": 1024}, "uploads": {"size_bytes": 1234}},
        "table_counts": {},
    })
    monkeypatch.setattr(dashboard, "build_system_health", lambda db: {
        "status": "healthy",
        "summary": {"errors": 0, "warnings": 0, "checks": 4},
        "checks": {
            "database": {"status": "ok"},
            "disk": {"status": "ok", "percent": 10},
            "backups": {"status": "warn", "message": "暂无备份"},
            "deploy_worker": {"status": "ok", "running": True},
        },
    })

    data = dashboard.build_dashboard_summary(db)

    assert data["metrics"]["services"] == 1
    assert data["metrics"]["servers"] == 2
    assert data["metrics"]["failed_deployments"] == 1
    assert data["packages"]["latest"]["package_name"] == "api-v1.tar.gz"
    assert any(r["title"] == "存在失败发布" for r in data["risks"])
    assert data["quick_actions"][0]["to"] == "/deploy"
    titles = [item["title"] for item in data["quick_actions"]]
    assert "维护工具" in titles
    assert "维护审批" not in titles


def test_release_confirmation_exposes_impact_plan_and_checklist():
    from app.api import deploy_v2
    from app.deploy.schemas import DeployRequest

    req = DeployRequest(system="ops", service="api", environment="prod", servers=["s1", "s2", "s3"], file_name="api-v2.tar.gz")
    steps = [{"name": "restart", "type": "command", "config": {"command": "systemctl restart api"}}]
    topology = {"service_dir": "/srv/api", "log_path": "/var/log/api.log"}
    rollback = {"safe": False, "mode": "manual", "description": "手工回滚"}

    impact = deploy_v2._build_confirmation_impact(req, topology, steps, rollback)
    reasons = deploy_v2._build_confirmation_risk_reasons(req, [], [], steps, rollback, {"status": "ok"})
    plan = deploy_v2._build_confirmation_execution_plan(steps, topology)
    checklist = deploy_v2._build_operator_checklist({
        "impact": impact,
        "rollback_plan": rollback,
        "requires_confirmation": True,
        "required_confirmation": "确认发布 ops/api 到 prod",
    })

    assert impact["server_count"] == 3
    assert impact["rollback_safe"] is False
    assert any("生产环境" in item for item in reasons)
    assert any("3 台服务器" in item for item in reasons)
    assert plan[0]["name"] == "确认目标目录"
    assert any("确认短语" in item["label"] for item in checklist)


def test_workbench_clock_is_rendered_in_visible_hero_copy():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    dashboard_page = (root / "frontend" / "src" / "pages" / "DashboardPage.tsx").read_text(encoding="utf-8")
    dashboard_styles = (root / "frontend" / "src" / "index.css").read_text(encoding="utf-8")

    assert "function DashboardClock" in dashboard_page
    assert "<DashboardClock now={now} />" in dashboard_page
    assert "<DashboardClock now={now} />" in dashboard_page
    assert "cc-command-hero" in dashboard_page
    assert "dashboard-clock-card" in dashboard_styles
    assert "grid-template-columns: minmax(138px, auto) minmax(0, 1fr)" in dashboard_styles


def test_runtime_snapshot_uses_cache_until_force_refresh(monkeypatch, sqlite_session):
    from app.services.runtime_resources import (
        build_runtime_snapshot,
        clear_runtime_resource_cache,
    )

    db = sqlite_session
    clear_runtime_resource_cache()

    monkeypatch.setattr(
        "app.services.runtime_resources._build_storage_usage",
        lambda db: {"generated_at": "2026-01-01T00:00:00", "total_managed_size_human": "1 MB", "buckets": {}, "table_counts": {}, "retention": {}},
    )
    monkeypatch.setattr(
        "app.services.runtime_resources._build_runtime_usage",
        lambda db: {"generated_at": "2026-01-01T00:00:00", "process": {"pid": 1, "uptime_seconds": 100}, "platform": {}, "runtime_limits": {}, "deploy_tasks_by_status": {}, "deployments_by_status": {}, "recent_tool_calls_24h": 0},
    )

    snapshot1 = build_runtime_snapshot(db, force=False)
    assert snapshot1["cache"]["hit"] is False
    assert snapshot1["runtime"]["process"]["pid"] == 1
    assert snapshot1["storage"]["total_managed_size_human"] == "1 MB"

    snapshot2 = build_runtime_snapshot(db, force=False)
    assert snapshot2["cache"]["hit"] is True
    assert snapshot2["runtime"]["process"]["pid"] == 1

    monkeypatch.setattr(
        "app.services.runtime_resources._build_storage_usage",
        lambda db: {"generated_at": "2026-02-01T00:00:00", "total_managed_size_human": "2 MB", "buckets": {}, "table_counts": {}, "retention": {}},
    )

    snapshot3 = build_runtime_snapshot(db, force=True)
    assert snapshot3["cache"]["hit"] is False
    assert snapshot3["storage"]["total_managed_size_human"] == "2 MB"

    snapshot4 = build_runtime_snapshot(db, force=False)
    assert snapshot4["cache"]["hit"] is True
    assert snapshot4["storage"]["total_managed_size_human"] == "2 MB"
