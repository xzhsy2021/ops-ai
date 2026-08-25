"""security_daily 落库锁竞争修复的回归测试。

背景：批量采集默认 16 并发 worker 各自写同一 SQLite，WAL 单写者下
竞争超过 busy_timeout 即报 database is locked（真实故障：某台服务器
DELETE security_risks 报 locked，服务器本身无恙）。

修复：进程内写段串行化（_PERSIST_LOCK）+ 锁冲突指数退避重试 +
busy_timeout 提升。本文件验证重试语义。
"""
import pytest
from sqlalchemy.exc import OperationalError

from app.services.security_daily import _run_db_write


class _FakeSession:
    def __init__(self):
        self.rollbacks = 0

    def rollback(self):
        self.rollbacks += 1


def test_retry_succeeds_after_transient_lock(monkeypatch):
    """前两次锁冲突、第三次成功：应重试并返回结果。"""
    monkeypatch.setattr("app.services.security_daily.time.sleep", lambda s: None)
    db = _FakeSession()
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise OperationalError(
                "UPDATE", {}, Exception("(sqlite3.OperationalError) database is locked")
            )
        return "ok"

    assert _run_db_write(flaky, db) == "ok"
    assert calls["n"] == 3
    assert db.rollbacks == 2  # 每次冲突后回滚再重试


def test_non_locked_operational_error_raises_immediately(monkeypatch):
    """非锁类 OperationalError 不重试，立即抛出真实原因。"""
    monkeypatch.setattr("app.services.security_daily.time.sleep", lambda s: None)
    db = _FakeSession()
    calls = {"n": 0}

    def broken():
        calls["n"] += 1
        raise OperationalError("INSERT", {}, Exception("NOT NULL constraint failed"))

    with pytest.raises(OperationalError):
        _run_db_write(broken, db)
    assert calls["n"] == 1


def test_persistent_lock_exhausts_attempts_and_raises(monkeypatch):
    """持续锁冲突：重试耗尽后向上抛出，由调用方标记该台 error。"""
    monkeypatch.setattr("app.services.security_daily.time.sleep", lambda s: None)
    db = _FakeSession()
    calls = {"n": 0}

    def always_locked():
        calls["n"] += 1
        raise OperationalError(
            "DELETE", {}, Exception("(sqlite3.OperationalError) database is locked")
        )

    with pytest.raises(OperationalError):
        _run_db_write(always_locked, db, attempts=3)
    assert calls["n"] == 3
