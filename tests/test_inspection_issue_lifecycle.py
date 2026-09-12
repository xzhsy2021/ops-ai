"""风险问题闭环生命周期与分流口径回归（2026-09-12 复盘第 7 轮）。

本轮修出的三类问题：

 1. ``update_issue`` 的闭环时间戳与状态不自洽（详情页"闭环时间线"直接展示这两个字段）：
    - 重开后仍保留 ``fixed_at``，看起来"已修好"；
    - 跳过 FIXED 直接 VERIFIED 时"有验证时间但无修复时间"；
    - 标记 IGNORED 后仍保留修复/验证时间；
    - 重复 PATCH 同一状态会把 ``fixed_at``/``verified_at`` 往后推（改写闭环时长）。
 2. ``deadline_at`` 全仓没有任何写入路径：列、序列化、前端"截止时间"都在，
    但永远是空 → 超期风险无法标注/统计。
 3. ``ops.risk.triage`` 只查 ``status=OPEN``，漏掉 PROCESSING（在办）风险，
    与平台既定口径"未闭环 = OPEN + PROCESSING"（overview.open_issue_count）不一致。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture()
def issue_db(tmp_path):
    from app.db.base import Base

    engine = create_engine(f"sqlite:///{tmp_path / 'issues.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autocommit=False, autoflush=False)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _seed_issue(db, *, status="OPEN", risk_level="HIGH", **kwargs):
    from app.db.models import InspectionIssue, InspectionRun
    from app.services.inspection_center import _now

    run = InspectionRun(
        id=uuid4().hex,
        scope_type="SERVER",
        server_id=kwargs.pop("server_id", "srv-1"),
        status="SUCCESS",
        categories=["DISK"],
        created_at=_now(),
        updated_at=_now(),
    )
    db.add(run)
    db.flush()
    row = InspectionIssue(
        id=uuid4().hex,
        run_id=run.id,
        scope_type="SERVER",
        server_id="srv-1",
        title=kwargs.pop("title", "磁盘使用率过高"),
        description="usage 92%",
        risk_level=risk_level,
        status=status,
        created_at=_now(),
        updated_at=_now(),
        **kwargs,
    )
    db.add(row)
    db.commit()
    return row


# ---------------------------------------------------------------------------
# 1. 状态机与闭环时间戳自洽
# ---------------------------------------------------------------------------

def test_fix_then_verify_sets_both_timestamps(issue_db):
    from app.services import inspection_center as ic

    row = _seed_issue(issue_db)
    fixed = ic.update_issue(issue_db, row.id, {"status": "FIXED"})
    assert fixed["status"] == "FIXED"
    assert fixed["fixed_at"] and not fixed["verified_at"]

    verified = ic.update_issue(issue_db, row.id, {"status": "VERIFIED"})
    assert verified["status"] == "VERIFIED"
    assert verified["fixed_at"] and verified["verified_at"]


def test_reopen_clears_closure_timestamps(issue_db):
    """重开后不得残留修复/验证时间（否则详情页显示"已修好"却状态是处理中）。"""
    from app.services import inspection_center as ic

    row = _seed_issue(issue_db)
    ic.update_issue(issue_db, row.id, {"status": "FIXED"})
    ic.update_issue(issue_db, row.id, {"status": "VERIFIED"})

    reopened = ic.update_issue(issue_db, row.id, {"status": "PROCESSING"})
    assert reopened["status"] == "PROCESSING"
    assert reopened["fixed_at"] is None, "重开后不应保留修复时间"
    assert reopened["verified_at"] is None, "重开后不应保留验证时间"


def test_ignored_clears_closure_timestamps(issue_db):
    from app.services import inspection_center as ic

    row = _seed_issue(issue_db)
    ic.update_issue(issue_db, row.id, {"status": "FIXED"})
    ignored = ic.update_issue(issue_db, row.id, {"status": "IGNORED"})
    assert ignored["fixed_at"] is None and ignored["verified_at"] is None


def test_direct_verify_backfills_fixed_at(issue_db):
    """跳过 FIXED 直接 VERIFIED：必须补上 fixed_at，不能出现"有验证无修复"。"""
    from app.services import inspection_center as ic

    row = _seed_issue(issue_db)
    verified = ic.update_issue(issue_db, row.id, {"status": "VERIFIED"})
    assert verified["verified_at"], "验证时间必须有"
    assert verified["fixed_at"], "验证时间存在时修复时间不得为空"


def test_repeated_same_status_keeps_original_timestamp(issue_db):
    """重复 PATCH 同一状态不得改写闭环时间（避免闭环时长被重复点击污染）。"""
    from app.services import inspection_center as ic

    row = _seed_issue(issue_db)
    first = ic.update_issue(issue_db, row.id, {"status": "FIXED"})
    second = ic.update_issue(issue_db, row.id, {"status": "FIXED"})
    assert second["fixed_at"] == first["fixed_at"], "重复 FIXED 不应刷新修复时间"

    first_v = ic.update_issue(issue_db, row.id, {"status": "VERIFIED"})
    second_v = ic.update_issue(issue_db, row.id, {"status": "VERIFIED"})
    assert second_v["verified_at"] == first_v["verified_at"], "重复 VERIFIED 不应刷新验证时间"


def test_invalid_status_rejected(issue_db):
    from fastapi import HTTPException

    from app.services import inspection_center as ic

    row = _seed_issue(issue_db)
    with pytest.raises(HTTPException) as exc:
        ic.update_issue(issue_db, row.id, {"status": "DONE"})
    assert exc.value.status_code == 400


def test_unknown_issue_404(issue_db):
    from fastapi import HTTPException

    from app.services import inspection_center as ic

    with pytest.raises(HTTPException) as exc:
        ic.update_issue(issue_db, "not-exist", {"status": "FIXED"})
    assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# 2. deadline_at 写入路径
# ---------------------------------------------------------------------------

def test_deadline_at_is_writable(issue_db):
    from app.services import inspection_center as ic

    row = _seed_issue(issue_db)
    deadline = "2026-10-01T12:00:00"
    result = ic.update_issue(issue_db, row.id, {"deadline_at": deadline})
    assert result["deadline_at"] == deadline

    issue_db.refresh(row)
    assert row.deadline_at is not None


def test_deadline_at_accepts_timezone_and_normalizes_to_utc(issue_db):
    from app.services import inspection_center as ic

    row = _seed_issue(issue_db)
    result = ic.update_issue(issue_db, row.id, {"deadline_at": "2026-10-01T20:00:00+08:00"})
    assert result["deadline_at"] == "2026-10-01T12:00:00", result["deadline_at"]

    zulu = ic.update_issue(issue_db, row.id, {"deadline_at": "2026-10-02T00:00:00Z"})
    assert zulu["deadline_at"] == "2026-10-02T00:00:00"


def test_deadline_at_can_be_cleared(issue_db):
    from app.services import inspection_center as ic

    row = _seed_issue(issue_db)
    ic.update_issue(issue_db, row.id, {"deadline_at": "2026-10-01T12:00:00"})
    cleared = ic.update_issue(issue_db, row.id, {"deadline_at": ""})
    assert cleared["deadline_at"] is None


def test_deadline_at_invalid_rejected(issue_db):
    from fastapi import HTTPException

    from app.services import inspection_center as ic

    row = _seed_issue(issue_db)
    with pytest.raises(HTTPException) as exc:
        ic.update_issue(issue_db, row.id, {"deadline_at": "2026/10/01"})
    assert exc.value.status_code == 400


# ---------------------------------------------------------------------------
# 3. ops.risk.triage 未闭环口径 + 超期维度
# ---------------------------------------------------------------------------

class _Ctx:
    user = "tester"


def test_triage_covers_processing_issues(issue_db):
    """未闭环 = OPEN + PROCESSING，triage 不得漏掉"处理中"的在办风险。"""
    from app.services.tool_adapters import risk_tools

    _seed_issue(issue_db, status="OPEN", risk_level="HIGH", title="A 高危未处理")
    _seed_issue(issue_db, status="PROCESSING", risk_level="HIGH", title="B 高危在办")
    _seed_issue(issue_db, status="FIXED", risk_level="HIGH", title="C 已修复")
    _seed_issue(issue_db, status="VERIFIED", risk_level="HIGH", title="D 已复核")
    _seed_issue(issue_db, status="IGNORED", risk_level="HIGH", title="E 已忽略")

    result = risk_tools.triage_risks({"limit": 50}, _Ctx(), issue_db)

    titles = [p["title"] for p in result["priorities"]]
    assert "A 高危未处理" in titles
    assert "B 高危在办" in titles, "PROCESSING（在办）风险必须进入分流清单"
    assert not {"C 已修复", "D 已复核", "E 已忽略"} & set(titles)
    assert "未闭环风险 2 个" in result["summary"], result["summary"]


def test_triage_marks_overdue_and_sorts_first(issue_db):
    """同等级内超期优先，并在条目上标注 overdue（工具描述承诺的"超期"维度）。"""
    from app.services.tool_adapters import risk_tools

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    past = (now - timedelta(days=2)).replace(microsecond=0).isoformat()
    future = (now + timedelta(days=5)).replace(microsecond=0).isoformat()
    _seed_issue(issue_db, status="OPEN", risk_level="HIGH", title="A 未超期", deadline_at=datetime.fromisoformat(future))
    _seed_issue(issue_db, status="OPEN", risk_level="HIGH", title="B 已超期", deadline_at=datetime.fromisoformat(past))
    _seed_issue(issue_db, status="OPEN", risk_level="LOW", title="C 低危无期限")

    result = risk_tools.triage_risks({"limit": 50}, _Ctx(), issue_db)
    titles = [p["title"] for p in result["priorities"]]

    assert titles[0] == "B 已超期", f"超期的高危应排在同级最前：{titles}"
    by_title = {p["title"]: p for p in result["priorities"]}
    assert by_title["B 已超期"]["overdue"] is True
    assert by_title["A 未超期"]["overdue"] is False
    assert "1 个已超期" in result["summary"], result["summary"]


def test_triage_still_excludes_closed_and_reports_status(issue_db):
    from app.services.tool_adapters import risk_tools

    _seed_issue(issue_db, status="PROCESSING", risk_level="MEDIUM", title="在办中危")
    result = risk_tools.triage_risks({"limit": 50}, _Ctx(), issue_db)
    item = result["priorities"][0]
    assert item["status"] == "PROCESSING"
    assert item["priority"] == "P1"
    assert item["reason"]
