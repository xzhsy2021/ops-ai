"""巡检台账/报表的时间窗契约回归（2026-09-12 复盘第 5 轮）。

缺陷：``_period_bounds`` 未知周期分支引用未绑定的 ``end``
--------------------------------------------------------------------------------
``app/services/inspection_center.py::_period_bounds`` 结构：

    if date_from or date_to:
        ... return start, end, p
    if p in {"daily", "day"}:   ... return start, end, "daily"
    if p in {"weekly", "week"}: ... return start, end, "weekly"
    if p in {"monthly", "month"}: ... return start, end, "monthly"
    start = now.replace(hour=0, ...)
    return start, end, "daily"        # ← end 从未在该路径赋值

当 ``period`` 既不是 daily/weekly/monthly（含别名）又没给 ``date_from``/``date_to`` 时，
``end`` 是未绑定局部变量 → ``UnboundLocalError``。而 API 层 ``period: str = "daily"``
未做任何取值校验，所以

    GET  /api/v2/inspection/ledger?period=quarterly
    GET  /api/v2/inspection/reports/periodic-preview?period=7d
    POST /api/v2/inspection/reports/periodic   {"period": "quarterly"}
    DELETE /api/v2/inspection/ledger           {"period": "quarterly"}

在生产环境都返回 **HTTP 500 Internal server error**（实测已复现）。
排障时只有"未知周期 → 兜底为今日"的设计意图（返回值第三项就是 "daily"），却因漏赋值而崩溃。

另一处同族问题：``_parse_dt`` 对带非 UTC 偏移的 ISO 串直接 ``replace(tzinfo=None)``，
把 ``+08:00`` 当成 UTC，导致日期窗整体偏移 8 小时（与全仓"持久化时间统一为 naive UTC"
的约定及前端调用方行为不符）。

本文件锁定：任意 ``period`` 取值都不得让台账/报表/删除接口 500；
带偏移的时间串必须按 UTC 归一化。
"""
from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture()
def empty_db(tmp_path):
    from app.db.base import Base

    engine = create_engine(f"sqlite:///{tmp_path / 'inspection.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autocommit=False, autoflush=False)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


# ---------------------------------------------------------------------------
# 核心回归：未知 period 必须走"今日"兜底，而不是 UnboundLocalError
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("period", ["quarterly", "7d", "yearly", "custom", "unknown", "DAILY ", "Quarterly"])
def test_unknown_period_falls_back_to_daily(period):
    from app.services import inspection_center as ic

    start, end, normalized = ic._period_bounds(period)

    assert normalized == "daily", f"{period!r} 应兜底为 daily"
    assert end >= start
    now = ic._now()
    assert start.date() == now.date()
    assert start.hour == 0 and start.minute == 0 and start.second == 0
    assert end.date() == now.date()


@pytest.mark.parametrize("period,expected", [
    ("daily", "daily"), ("day", "daily"), ("", "daily"),
    ("weekly", "weekly"), ("week", "weekly"),
    ("monthly", "monthly"), ("month", "monthly"),
    ("WEEKLY", "weekly"),
])
def test_known_periods_normalize(period, expected):
    from app.services import inspection_center as ic

    start, end, normalized = ic._period_bounds(period)

    assert normalized == expected
    assert end >= start


def test_week_window_starts_on_monday():
    from app.services import inspection_center as ic

    start, end, _ = ic._period_bounds("weekly")

    assert start.weekday() == 0, "周窗口应从周一 00:00 开始"
    assert start.hour == 0 and start.minute == 0


def test_month_window_starts_on_first_day():
    from app.services import inspection_center as ic

    start, end, _ = ic._period_bounds("monthly")

    assert start.day == 1
    assert start.hour == 0 and start.minute == 0


def test_explicit_date_range_covers_whole_end_day():
    from app.services import inspection_center as ic

    start, end, _ = ic._period_bounds("daily", "2026-09-01", "2026-09-03")

    assert start == datetime(2026, 9, 1, 0, 0, 0)
    assert end.date() == datetime(2026, 9, 3).date()
    assert (end.hour, end.minute, end.second) == (23, 59, 59)


def test_explicit_date_range_with_unknown_period_still_works():
    from app.services import inspection_center as ic

    start, end, normalized = ic._period_bounds("quarterly", "2026-09-01", "2026-09-03")

    assert normalized == "quarterly", "显式区间应保留调用方传入的周期标签"
    assert start == datetime(2026, 9, 1, 0, 0, 0)
    assert end.date() == datetime(2026, 9, 3).date()


# ---------------------------------------------------------------------------
# 带时区偏移的时间串必须按 UTC 归一化（全仓 naive UTC 约定）
# ---------------------------------------------------------------------------

def test_parse_dt_normalizes_offset_to_utc():
    from app.services import inspection_center as ic

    assert ic._parse_dt("2026-09-10T08:00:00+08:00") == datetime(2026, 9, 10, 0, 0, 0)
    assert ic._parse_dt("2026-09-10T00:00:00Z") == datetime(2026, 9, 10, 0, 0, 0)
    assert ic._parse_dt("2026-09-10T00:00:00+00:00") == datetime(2026, 9, 10, 0, 0, 0)
    assert ic._parse_dt("2026-09-10") == datetime(2026, 9, 10, 0, 0, 0)


# ---------------------------------------------------------------------------
# 服务层：台账 / 报表预览 / 历史删除 都不得因未知 period 崩溃
# ---------------------------------------------------------------------------

def test_ledger_with_unknown_period_does_not_crash(empty_db):
    from app.services import inspection_center as ic

    result = ic.inspection_ledger(empty_db, period="quarterly")

    assert result["summary"]["period"] == "daily"
    assert result["total"] == 0
    assert result["items"] == []


def test_periodic_report_payload_with_unknown_period_does_not_crash(empty_db):
    from app.services import inspection_center as ic

    payload = ic.inspection_periodic_report_payload(empty_db, period="7d")

    assert payload["summary"]["report_kind"] == "daily"
    assert payload["summary"]["report_title"] == "每日巡检台账"


def test_delete_history_with_unknown_period_does_not_crash(empty_db):
    from app.services import inspection_center as ic

    result = ic.delete_inspection_history(empty_db, period="quarterly")

    assert result["deleted"] == 0
    assert result["period"] == "daily"
