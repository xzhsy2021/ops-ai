"""工具/接口"总数"口径回归测试（2026-09-12 复盘修复）。

背景缺陷（与已修复的巡检风险计数 12 vs 11 属同一类）：多处把 **limit 截断后的条数**
当成 total / "查询到 N 个"，于是 AI 与报表拿到的总数偏小：

- ``audit_chain.list_operation_chains``：``total = len(items[:limit])``，且 5 个数据源
  查询各自都带 ``.limit(limit)``，limit=50 时总数永远显示 50；
- ``tool_adapters/server_tools.py``：``_filter_items`` 内部已按 limit 截断，
  调用方仍用 ``len(items)`` 充当 total 与「查询到 N 台/个」，list_environments 更直接写
  ``len(items[:limit])``；
- ``report_center`` 的 ``operation_chains_index`` 报表：``summary.total = len(截断后的 items)``。

本文件锁定修复后的行为：total 必须是真实总数，returned 表示本次真正返回的条数。
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.models import Service, SystemEnvironment
from app.services.tool_adapters.server_tools import (
    _filter_items,
    list_environments_tool,
    list_servers_tool,
    list_services_tool,
    list_systems_tool,
)


@pytest.fixture()
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'totals.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autocommit=False, autoflush=False)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ────────────────────────── server_tools ──────────────────────────


def test_filter_items_returns_page_and_true_total():
    items = [{"name": f"server-{i}"} for i in range(5)]
    page, total = _filter_items(items, "", 2)
    assert len(page) == 2
    assert total == 5, "total 必须是过滤后的真实总数，而不是截断后的条数"

    page, total = _filter_items(items, "server-1", 100)
    assert len(page) == 1
    assert total == 1


def test_list_servers_tool_reports_true_total(monkeypatch):
    import app.maintenance.server_assets as assets

    servers = [{"id": f"s{i}", "name": f"srv-{i}", "host": f"10.0.0.{i}", "group": "g"} for i in range(3)]
    monkeypatch.setattr(assets, "list_server_assets", lambda db: servers)

    out = list_servers_tool({"limit": 1}, None, None)
    assert out["total"] == 3
    assert len(out["items"]) == 1
    assert "3" in out["summary"], f"summary 应报出真实总数：{out['summary']}"
    assert "仅返回前 1 条" in out["summary"], "截断时必须显式说明，避免 AI 误判规模"


def test_list_systems_tool_reports_true_total(monkeypatch):
    import app.services.tool_adapters.server_tools as server_tools

    systems = {f"sys-{i}": {"display_name": f"系统{i}", "servers": [], "services": [], "environments": []} for i in range(3)}
    monkeypatch.setattr(server_tools._inventory, "list_systems", lambda: systems)

    out = list_systems_tool({"limit": 1}, None, None)
    assert out["total"] == 3
    assert len(out["items"]) == 1
    assert "3" in out["summary"]


def test_list_services_tool_reports_true_total(db):
    for i in range(3):
        db.add(Service(system_name="crypto-trader", name=f"svc-{i}", template="generic_backend_direct", template_variables={}))
    db.commit()

    out = list_services_tool({"limit": 1}, None, db)
    assert out["total"] == 3
    assert len(out["items"]) == 1
    assert "3" in out["summary"]

    narrowed = list_services_tool({"limit": 10, "keyword": "svc-1"}, None, db)
    assert narrowed["total"] == 1
    assert len(narrowed["items"]) == 1
    assert "仅返回前" not in narrowed["summary"], "未截断时不应出现截断说明"


def test_list_environments_tool_reports_true_total(db):
    for i, name in enumerate(("prod", "test", "staging")):
        db.add(SystemEnvironment(system_name="crypto-trader", name=name, category="test", servers=[{"id": f"s{i}"}]))
    db.commit()

    out = list_environments_tool({"limit": 1}, None, db)
    assert out["total"] == 3, "历史缺陷：total 写成 len(items[:limit])，limit=1 时只报 1"
    assert len(out["items"]) == 1
    assert "查询到 3 个环境" in out["summary"]
    assert "仅返回前 1 条" in out["summary"]


# ────────────────────────── 审计链路 ──────────────────────────


def test_list_operation_chains_reports_true_total(db):
    from app.db.models import ToolCallLog
    from app.services.audit_chain import list_operation_chains

    for i in range(3):
        db.add(ToolCallLog(
            id=f"call{i}", tool_name="ops.list_servers", username="alice", input_args="{}",
            result_preview="{}", status="success", risk_level="low", created_at=_now(),
        ))
    db.commit()

    out = list_operation_chains(db, limit=1)
    assert out["total"] == 3, "历史缺陷：total = len(items[:limit])，limit=1 时只报 1"
    assert out["returned"] == 1
    assert len(out["items"]) == 1
    assert out["truncated"] is True

    full = list_operation_chains(db, limit=50)
    assert full["total"] == 3
    assert full["returned"] == 3
    assert full["truncated"] is False


def test_list_operation_chains_filters_are_reflected_in_total(db):
    from app.db.models import ToolCallLog
    from app.services.audit_chain import list_operation_chains

    db.add(ToolCallLog(id="c1", tool_name="a", username="alice", input_args="{}", result_preview="{}",
                       status="success", risk_level="low", created_at=_now()))
    db.add(ToolCallLog(id="c2", tool_name="b", username="alice", input_args="{}", result_preview="{}",
                       status="failed", risk_level="high", created_at=_now()))
    db.commit()

    assert list_operation_chains(db, limit=1, status="failed")["total"] == 1
    assert list_operation_chains(db, limit=1, risk="high")["total"] == 1
    assert list_operation_chains(db, limit=1, risk="nonexistent")["total"] == 0


def test_list_operation_chains_deployment_risk_is_derived_in_total(db):
    """部署的 risk_level 是派生值（生产=high、其余=medium），count 必须与列表口径一致。"""
    from app.db.models import Deployment
    from app.services.audit_chain import list_operation_chains

    db.add(Deployment(id="d1", system="ops", service="web", environment="prod", status="success",
                      servers="s1", started_at=_now(), finished_at=_now(), created_by="alice"))
    db.add(Deployment(id="d2", system="ops", service="web", environment="test", status="success",
                      servers="s2", started_at=_now(), finished_at=_now(), created_by="alice"))
    db.commit()

    high = list_operation_chains(db, limit=10, kind="deployment", risk="high")
    medium = list_operation_chains(db, limit=10, kind="deployment", risk="medium")
    assert high["total"] == 1 and [i["chain_id"] for i in high["items"]] == ["deployment:d1"]
    assert medium["total"] == 1 and [i["chain_id"] for i in medium["items"]] == ["deployment:d2"]
    assert list_operation_chains(db, limit=10, kind="deployment")["total"] == 2


# ────────────────────────── 报表 ──────────────────────────


def test_operation_chains_index_report_uses_source_total(db, monkeypatch):
    """报表 summary.total 必须来自数据源的 total，而不是截断后的 len(items)。"""
    import app.services.audit_chain as audit_chain
    from app.services.report_center import _payload_for_report

    monkeypatch.setattr(
        audit_chain,
        "list_operation_chains",
        lambda db, limit=200: {"items": [{"chain_id": "chain-1"}], "total": 999, "returned": 1},
    )
    payload = _payload_for_report(db, "operation_chains_index")
    assert payload["summary"]["total"] == 999
    assert payload["summary"]["returned"] == 1
