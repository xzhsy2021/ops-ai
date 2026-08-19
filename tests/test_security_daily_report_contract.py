from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def _sqlite_session(tmp_path):
    from app.db.models import Base

    engine = create_engine(
        f"sqlite:///{tmp_path / 'security_daily.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    return engine, Session


def _sample_md(failures: int = 0, banned: str = "") -> str:
    return f"""### 1. 今日登录记录 (aureport, 最近25条)

Login Report
============================================
# date time auid host term exe success event
============================================
<no events of interest were found>


### 2. 今日登录失败次数: {failures}

### 3. 今日账户变更
无

### 4. 当前被 fail2ban 封禁的 IP
   - Banned IP list: {banned}

### 5. 系统负载
 08:00:01 up 21 days, 17:16,  0 user,  load average: 0.23, 0.05, 0.02
"""


def test_parse_daily_markdown_structures_sections():
    from app.services.security_daily import parse_daily_markdown

    items = parse_daily_markdown(_sample_md())
    by_kind = {i["kind"]: i for i in items}
    assert set(by_kind) >= {"login_records", "login_failures", "fail2ban_bans", "system_load"}
    assert by_kind["login_failures"]["count"] == 0
    assert by_kind["fail2ban_bans"]["ips"] == []
    assert by_kind["system_load"]["load_avg"]["min1"] == 0.23


def test_evaluate_risk_thresholds():
    from app.services.security_daily import evaluate_risk, parse_daily_markdown

    assert evaluate_risk(parse_daily_markdown(_sample_md()))[0] == "LOW"
    high = evaluate_risk(parse_daily_markdown(_sample_md(
        failures=25, banned="1.2.3.4 5.6.7.8 9.10.11.12 13.14.15.16 17.18.19.20")))
    assert high[0] == "HIGH"
    med = evaluate_risk(parse_daily_markdown(_sample_md(failures=11)))
    assert med[0] == "HIGH"


def test_collect_persist_and_summarize_roundtrip(tmp_path, monkeypatch):
    from app.db.models import SecurityDailyReport, SecurityRisk

    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("REPORT_DIR", str(tmp_path / "reports"))
    engine, Session = _sqlite_session(tmp_path)
    db = Session()
    try:
        # 直接测试入库：构造一条已采集记录并走 summarize
        from app.services.security_daily import collect_server_report

        fake_server = {
            "name": "web01",
            "id": "abc123",
            "host": "127.0.0.1",
            "port": 22,
            "user": "root",
            "status": "online",
            "metadata_json": {"security_monitor": {"enabled": True}},
        }
        md = _sample_md(failures=25)
        monkeypatch.setattr("app.services.security_daily.read_daily_markdown",
                            lambda srv, date, timeout=30: {
                                "ok": True, "path": f"/var/log/security-daily/{date}.md",
                                "md": md, "missing": False, "error": "", "code": 0,
                            })
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        # 直接调用采集单机（含风险持久化）
        result = collect_server_report(db, fake_server, now)
        assert result["ok"] is True
        assert result["max_risk"] == "HIGH"

        report = db.query(SecurityDailyReport).filter_by(server_name="web01", report_date=now).first()
        assert report is not None
        assert report.max_risk == "HIGH"

        risks = db.query(SecurityRisk).filter(SecurityRisk.server_id == "web01").all()
        assert any(r.kind == "login_failures" for r in risks)

        # summarize 读取
        from app.services.tool_adapters.security_report_tools import summarize_security_reports
        summary = summarize_security_reports({"limit": 20}, None, db)
        assert summary["high_count"] == 1
        assert len(summary["risk_items"]) >= 1
    finally:
        db.close()
        engine.dispose()


def test_security_daily_report_tools_registered(tmp_path):
    from app.services.tool_registry import register_builtin_tools, registry
    from app.services.tool_context import ToolContext

    engine, Session = _sqlite_session(tmp_path)
    db = Session()
    try:
        register_builtin_tools()
        ctx = ToolContext(username="tester", auth_type="session", is_admin=True,
                          scopes=["*"], allow_write=True)
        listed = registry.list_tools(db, ctx, include_disabled=True, include_schema=True, limit=1000)
        tools = {t.get("name") for t in listed.get("tools", [])}
        assert "ops.security_report.summarize" in tools
        assert "ops.security_report.collect" in tools
        assert "ops.security_module.install" in tools
    finally:
        db.close()
        engine.dispose()