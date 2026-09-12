"""风险问题计数口径契约测试。

回归背景：巡检总览卡显示「未闭环」= OPEN + PROCESSING（例如 12），
而风险问题列表默认只筛 status=OPEN（例如 11），用户看到"数字与列表不一致"。

契约：
- overview.open_issue_count == list_issues(status="OPEN,PROCESSING").total
- overview.pending_issue_count == list_issues(status="OPEN").total
- overview.processing_issue_count == list_issues(status="PROCESSING").total
- 等级分布 / recent_issues 同样只统计未闭环
"""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def _sqlite_session(tmp_path):
    from app.db.models import Base

    engine = create_engine(
        f"sqlite:///{tmp_path / 'inspection_issue_counts.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    return engine, Session


def _seed(db):
    from app.db.models import InspectionIssue

    db.add_all([
        InspectionIssue(id="i-open-high", run_id="r1", item_result_id="x1", scope_type="SERVER",
                        server_id="s1", title="open high", risk_level="HIGH", status="OPEN"),
        InspectionIssue(id="i-open-low", run_id="r1", item_result_id="x2", scope_type="SERVER",
                        server_id="s1", title="open low", risk_level="LOW", status="OPEN"),
        InspectionIssue(id="i-processing", run_id="r1", item_result_id="x3", scope_type="SERVER",
                        server_id="s1", title="processing high", risk_level="HIGH", status="PROCESSING"),
        InspectionIssue(id="i-fixed", run_id="r1", item_result_id="x4", scope_type="SERVER",
                        server_id="s1", title="fixed high", risk_level="HIGH", status="FIXED"),
        InspectionIssue(id="i-verified", run_id="r1", item_result_id="x5", scope_type="SERVER",
                        server_id="s1", title="verified medium", risk_level="MEDIUM", status="VERIFIED"),
        InspectionIssue(id="i-ignored", run_id="r1", item_result_id="x6", scope_type="SERVER",
                        server_id="s1", title="ignored low", risk_level="LOW", status="IGNORED"),
    ])
    db.commit()


def _prepare(monkeypatch, tmp_path):
    from app.services import inspection_center as svc

    engine, Session = _sqlite_session(tmp_path)
    # 概览会统计资产数量，测试内不依赖真实清单服务
    monkeypatch.setattr(svc, "list_servers", lambda: [])
    monkeypatch.setattr(svc, "list_projects", lambda: [])
    db = Session()
    _seed(db)
    return svc, engine, db


def test_overview_unclosed_count_matches_issue_list_total(monkeypatch, tmp_path):
    svc, engine, db = _prepare(monkeypatch, tmp_path)
    try:
        overview = svc.overview(db)
        unclosed = svc.list_issues(db, status="OPEN,PROCESSING")["total"]
        pending = svc.list_issues(db, status="OPEN")["total"]
        processing = svc.list_issues(db, status="PROCESSING")["total"]

        assert unclosed == 3 and pending == 2 and processing == 1
        assert overview["open_issue_count"] == unclosed
        assert overview["pending_issue_count"] == pending
        assert overview["processing_issue_count"] == processing
        assert overview["open_issue_count"] == overview["pending_issue_count"] + overview["processing_issue_count"]
    finally:
        db.close()
        engine.dispose()


def test_overview_level_counts_and_recent_only_cover_unclosed(monkeypatch, tmp_path):
    svc, engine, db = _prepare(monkeypatch, tmp_path)
    try:
        overview = svc.overview(db)

        # 已修复/已验证/已忽略不计入概览风险统计
        assert overview["high_issue_count"] == 2
        assert overview["medium_issue_count"] == 0
        assert overview["low_issue_count"] == 1
        assert {i["id"] for i in overview["recent_issues"]} == {"i-open-high", "i-open-low", "i-processing"}
    finally:
        db.close()
        engine.dispose()


def test_list_issues_status_filter_single_and_multi(monkeypatch, tmp_path):
    svc, engine, db = _prepare(monkeypatch, tmp_path)
    try:
        assert svc.list_issues(db, status="")["total"] == 6
        assert svc.list_issues(db, status="open")["total"] == 2          # 单值：大小写不敏感，精确匹配
        assert svc.list_issues(db, status="FIXED")["total"] == 1
        assert svc.list_issues(db, status="OPEN,PROCESSING")["total"] == 3  # 多值：未闭环
        assert svc.list_issues(db, status=" OPEN , processing ")["total"] == 3  # 容错空格/大小写
        assert svc.list_issues(db, status="NOPE")["total"] == 0
        assert svc.list_issues(db, status="FIXED,VERIFIED,IGNORED")["total"] == 3
    finally:
        db.close()
        engine.dispose()


def test_mcp_risk_list_matches_unclosed_semantics(monkeypatch, tmp_path):
    """MCP ops.risk.list 与 REST 列表同口径：支持逗号多状态，total 为未截断总数。"""
    from app.services.tool_adapters.risk_tools import list_risks

    svc, engine, db = _prepare(monkeypatch, tmp_path)
    try:
        unclosed = list_risks({"status": "OPEN,PROCESSING"}, None, db)
        assert unclosed["total"] == 3
        assert unclosed["returned"] == 3
        assert {i["status"] for i in unclosed["items"]} == {"OPEN", "PROCESSING"}

        # total 不受 limit 截断
        limited = list_risks({"status": "OPEN,PROCESSING", "limit": 1}, None, db)
        assert limited["total"] == 3
        assert limited["returned"] == 1

        assert list_risks({"status": "OPEN"}, None, db)["total"] == 2
        assert list_risks({}, None, db)["total"] == 6
        assert list_risks({"status": "nope"}, None, db)["total"] == 0
    finally:
        db.close()
        engine.dispose()
