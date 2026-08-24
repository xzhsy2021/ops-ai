from __future__ import annotations

import time
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


def test_parse_banned_ips_ignores_non_ip_dotted_tokens():
    """封禁 IP 提取只认合法 IPv4/IPv6，避免把日志/时间串误判为 IP。"""
    from app.services.security_daily import parse_daily_markdown

    md = """### 4. 当前被 fail2ban 封禁的 IP
   `- Banned IP list:   1.2.3.4 2001:db8::1 5.6.7.8
```
"""
    by_kind = {i["kind"]: i for i in parse_daily_markdown(md)}
    ips = by_kind["fail2ban_bans"]["ips"]
    assert ips == ["1.2.3.4", "2001:db8::1", "5.6.7.8"]


def test_parse_onsite_format_inline_failure_count():
    """现场生成格式：「### 2. 今日登录失败次数: N」同行为标题+计数要能解析。"""
    from app.services.security_daily import parse_daily_markdown

    by_kind = {i["kind"]: i for i in parse_daily_markdown(_sample_md(failures=7))}
    assert by_kind["login_failures"]["count"] == 7


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
        assert "ops.inspection.run_security_daily" in tools
        tool = {t.get("name"): t for t in listed.get("tools", [])}["ops.inspection.run_security_daily"]
        assert "fail2ban" in tool["description"]
        assert len(tool["description"]) > 50
        assert len(tool["recommended_use_cases"]) >= 3
        assert len(tool["example_prompts"]) >= 3
        assert "登录失败" in tool["keywords"]
        assert tool["force_taskize"] is True
    finally:
        db.close()
        engine.dispose()


def test_inspection_page_has_security_daily_entry():
    """巡检中心页面提供「安全日报」独立入口，可单独触发安全日报采集并展示历史。"""
    page = open("frontend/src/pages/InspectionCenterPage.tsx", encoding="utf-8").read()
    api = open("frontend/src/api.ts", encoding="utf-8").read()
    inspection_api = open("app/api/inspection.py", encoding="utf-8").read()

    assert "'security'" in page  # TabKey 含安全日报
    assert "'安全日报'" in page
    assert "runSecurityDaily" in page
    assert "secDailyResult" in page
    assert "securityDailyCollect" in page
    assert "securityDailyReports" in page

    # f9848f0 起 collect 支持指定服务器集合（servers?: string[]），断言随签名演进。
    assert "securityDailyCollect: (data?: { report_date?: string; persist_risks?: boolean; servers?: string[] }) => api.post('/inspection/security-daily/collect', data || {})" in api
    assert "securityDailyReports" in api

    assert '@router.post("/security-daily/collect")' in inspection_api
    assert '@router.get("/security-daily/reports")' in inspection_api
    assert "enqueue_tool_job" in inspection_api
    assert "ops.inspection.run_security_daily" in inspection_api


def test_security_daily_mcp_call_is_taskized(tmp_path, monkeypatch):
    """MCP/AI 调用 run_security_daily 时走统一任务中心，立即返回 job_id，不阻塞请求线程。"""
    import app.services.job_service as js
    from app.services.tool_context import ToolContext
    from app.services.tool_registry import register_builtin_tools, registry

    engine, Session = _sqlite_session(tmp_path)
    db = Session()
    try:
        captured = {}

        def _fake_enqueue(db_arg, *, tool_def, arguments, ctx, policy_result, auto_start=True):
            captured["tool"] = tool_def.name
            captured["args"] = arguments
            return {"id": "job-mcp-1", "status": "queued", "progress": 0}

        monkeypatch.setattr(js, "enqueue_tool_job", _fake_enqueue)
        register_builtin_tools()
        ctx = ToolContext(username="qclaw-ai", auth_type="tool_token", token_owner="qclaw",
                          is_admin=False, scopes=["ops:read", "ops:write"], allow_write=True)
        result = registry.call(
            db,
            "ops.inspection.run_security_daily",
            {"confirm_text": "CONFIRM ops.inspection.run_security_daily"},
            ctx,
        )
        assert result.get("job_id") == "job-mcp-1"
        assert captured["tool"] == "ops.inspection.run_security_daily"
        assert "confirm_text" in captured["args"]
    finally:
        db.close()
        engine.dispose()


def test_security_daily_job_executes_in_background(tmp_path, monkeypatch):
    """提交的任务由后台线程执行安全日报采集，进度写入任务中心，不阻塞调用方。"""
    import app.services.job_service as js
    import app.services.security_daily as sd
    from app.db.models import OperationJob
    from app.services.tool_context import ToolContext
    from app.services.tool_registry import register_builtin_tools, registry

    def _fake_collect_all(db_arg, report_date=None, *, persist_risks=True, **kw):
        return {
            "report_date": report_date or "2026-08-19",
            "servers": [{"name": "web01"}],
            "results": [{"server": "web01", "ok": True, "status": "ok", "max_risk": "LOW"}],
            "summary": {"server_count": 1, "ok_count": 1, "failed_count": 0, "high_count": 0},
            "archive": {},
        }

    monkeypatch.setattr(sd, "collect_all", _fake_collect_all)
    engine, Session = _sqlite_session(tmp_path)
    db = Session()
    try:
        monkeypatch.setattr(js, "SessionLocal", Session)
        register_builtin_tools()
        tool = registry.get("ops.inspection.run_security_daily")
        ctx = ToolContext(username="tester", auth_type="session", is_admin=True,
                          scopes=["*"], allow_write=True)
        job = js.enqueue_tool_job(
            db,
            tool_def=tool,
            arguments={"confirm_text": "CONFIRM ops.inspection.run_security_daily"},
            ctx=ctx,
            policy_result={},
        )
        job_id = job["id"]
        status = ""
        row = None
        for _ in range(100):
            db.expire_all()
            row = db.query(OperationJob).filter(OperationJob.id == job_id).first()
            status = row.status if row else ""
            if status in ("success", "failed"):
                break
            time.sleep(0.05)
        assert status == "success"
        result = (row.result_json or {}).get("result") or {}
        assert (result.get("summary") or {}).get("ok_count") == 1
    finally:
        db.close()
        engine.dispose()