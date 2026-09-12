"""审计时间基契约回归（2026-09-12 复盘修复）。

历史缺陷：``app/config/audit.py::save_audit_log`` 用 ``datetime.now()``（服务器本地时间，
生产机为 UTC+8）写 ``audit_records.created_at``，而系统内其余写入
（``app.db.models._utcnow`` 及 69 处调用）统一是 naive UTC。实测同一时刻：

    audit_records.created_at = 2026-09-12T21:41:21   （本地）
    tool_call_logs.created_at = 2026-09-12 13:41:21  （UTC）

后果：审计页显示的时间比工具调用/任务页"早 8 小时"；审计链路跨源时间线合并排序错位；
按日期过滤的边界偏移；``cleanup_audit_logs`` 若继续用本地截止点会多删 8 小时的记录。

修复：写入端与清理端统一 naive UTC（``utcnow_naive()``），历史数据由
``scripts/fix_audit_timezone.py`` 一次性回迁（本机 628 行 -8h）。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]


def _naive_utc() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


@pytest.fixture()
def patched_session(monkeypatch, tmp_path):
    """把 app.db.base.SessionLocal 指向临时库，让 save_audit_log/cleanup 落到临时库。"""
    from app.db.base import Base
    import app.db.base as db_base

    engine = create_engine(f"sqlite:///{tmp_path / 'audit_tz.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    monkeypatch.setattr(db_base, "SessionLocal", factory)
    try:
        yield factory
    finally:
        engine.dispose()


def test_save_audit_log_writes_naive_utc(patched_session):
    """审计时间戳必须是 naive UTC（无时区标记），且与 UTC 现在时刻一致。"""
    from app.config.audit import save_audit_log
    from app.db.models import AuditRecord

    before = _naive_utc()
    assert save_audit_log("tz.contract.probe", "system", "tz", "{}") is True

    with patched_session() as db:
        row = db.query(AuditRecord).order_by(AuditRecord.id.desc()).first()
    assert row is not None and row.action == "tz.contract.probe"

    raw = str(row.created_at)
    assert not raw.endswith("Z") and "+" not in raw, f"不应带时区标记: {raw}"

    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    assert parsed.tzinfo is None, "必须是无时区的 naive 时间戳"
    skew = abs((parsed - before).total_seconds())
    assert skew < 60, f"审计时间戳与 UTC 相差 {skew:.0f}s（本地时间会差 8 小时）"

    # 非 UTC 机器上必须与"本地时间"明显不同，否则说明又退回 datetime.now()
    local_offset = datetime.now().astimezone().utcoffset() or timedelta(0)
    if abs(local_offset) >= timedelta(hours=1):
        assert abs((parsed - datetime.now()).total_seconds()) > 1800


def test_audit_writer_has_no_local_time_call():
    """源码级防回归：审计模块里不允许再出现无时区的 datetime.now() 调用。"""
    import ast

    source = (ROOT / "app" / "config" / "audit.py").read_text(encoding="utf-8")
    calls = [
        node for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "now"
        and getattr(node.func.value, "id", "") == "datetime"
        and not node.args and not node.keywords  # 只禁无参的 datetime.now()（无时区）
    ]
    assert calls == [], f"审计模块又出现了本地时间调用（{len(calls)} 处）"
    assert "utcnow_naive()" in source


def test_cleanup_audit_logs_uses_utc_cutoff(patched_session):
    """清理截止点必须与写入端同一时基：71 小时前的记录不能在 72 小时策略下被删。"""
    from app.config.audit import cleanup_audit_logs, utcnow_naive
    from app.db.models import AuditRecord

    now = utcnow_naive()
    with patched_session() as db:
        db.add_all([
            AuditRecord(action="tz.cleanup.old", created_at=(now - timedelta(hours=100)).isoformat()),
            AuditRecord(action="tz.cleanup.edge", created_at=(now - timedelta(hours=71)).isoformat()),
            AuditRecord(action="tz.cleanup.fresh", created_at=(now - timedelta(hours=1)).isoformat()),
        ])
        db.commit()

    cleanup_audit_logs(max_age_hours=72)

    with patched_session() as db:
        kept = {row.action for row in db.query(AuditRecord).all()}
    assert "tz.cleanup.old" not in kept
    assert {"tz.cleanup.edge", "tz.cleanup.fresh"} <= kept


def test_migration_plan_shift_is_exact_and_skips_garbage():
    """迁移脚本：按本地时区偏移回迁，无法解析的行跳过并单列。"""
    from scripts.fix_audit_timezone import plan_shift

    rows = [(1, "2026-09-12T21:41:21.054406"), (2, "2026-09-04 10:08:10"), (3, "not-a-timestamp")]
    updates, skipped = plan_shift(rows, 8)
    assert [u[0] for u in updates] == [1, 2]
    assert updates[0][2] == "2026-09-12T13:41:21.054406"
    assert updates[1][2] == "2026-09-04T02:08:10"
    assert skipped == [(3, "not-a-timestamp")]


def test_migration_backup_copies_rows(tmp_path):
    """迁移脚本先整库备份：备份文件必须含原数据（可回滚）。"""
    from scripts.fix_audit_timezone import backup_database

    db_path = tmp_path / "ops.db"
    connection = sqlite3.connect(str(db_path))
    try:
        connection.execute("CREATE TABLE audit_records (id INTEGER PRIMARY KEY, created_at TEXT)")
        connection.execute("INSERT INTO audit_records (id, created_at) VALUES (1, '2026-09-12T21:41:21')")
        connection.commit()
    finally:
        connection.close()

    backup = backup_database(db_path)
    assert backup.exists() and backup != db_path
    check = sqlite3.connect(str(backup))
    try:
        rows = check.execute("SELECT id, created_at FROM audit_records").fetchall()
    finally:
        check.close()
    assert rows == [(1, "2026-09-12T21:41:21")]
