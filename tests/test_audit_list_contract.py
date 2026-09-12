"""审计记录列表/导出与列表 ETag 契约回归（2026-09-12 复盘修复）。

历史缺陷一（app/api/task_center.py 的 /api/v2/audit 与 /audit/export）：
先 ``load_audit_logs(5000)`` 把最新 5000 条读进内存，再过滤 action、按 offset 切页，
并令 ``total = len(rows)``。后果：
  - 审计记录超过 5000 条后 total 永远显示 5000，更早的记录 offset 翻页不可达；
  - 检索较久远才出现的 action 会静默返回空列表（库里明明有匹配记录）；
  - 导出 CSV 同样只在这 5000 条里过滤，静默丢数据。
修复：过滤/计数/分页全部下推到 SQL（app.config.audit.list_audit_records）。

历史缺陷二（app/api/helpers.py compute_list_etag）：
``getattr(item, "id", id(item))`` 对 dict 条目退化为 CPython 内存地址，同一份数据
每次请求算出的 ETag 都不同，If-None-Match 永远命中不了，304 短路实际失效
（审计、工具调用、令牌、报表列表传的都是 dict）。
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, insert
from sqlalchemy.orm import sessionmaker

ROOT_TS = datetime(2026, 1, 1)
LEGACY_WINDOW = 5000  # 旧实现固定的内存窗口


@pytest.fixture()
def db(tmp_path):
    from app.db.base import Base

    engine = create_engine(f"sqlite:///{tmp_path / 'audit.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autocommit=False, autoflush=False)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _seed(db, count: int, *, oldest_action: str = "rare-old-action"):
    """写入 count 条审计记录；i=0 是**最旧**的一条，用唯一 action 便于检索。"""
    from app.db.models import AuditRecord

    rows = []
    for i in range(count):
        rows.append({
            "id": i + 1,
            "action": oldest_action if i == 0 else f"act-{i % 7}",
            "target_type": "server",
            "target_name": f"node-{i}",
            "details": "{}",
            "created_at": (ROOT_TS + timedelta(seconds=i)).isoformat(),
        })
    db.execute(insert(AuditRecord), rows)
    db.commit()


def _admin_request(db, username: str = "auditor"):
    from app.core.auth_v2 import create_session_token, hash_password
    from app.db.models import User

    db.add(User(username=username, password_hash=hash_password("pw"), role="admin", is_admin=True, session_version=1))
    db.commit()
    return SimpleNamespace(cookies={"ops_session_v2": create_session_token(username, 1)}, headers={})


# ────────────────────── list_audit_records ──────────────────────


def test_total_is_real_count_beyond_legacy_window(db):
    from app.config.audit import list_audit_records

    _seed(db, LEGACY_WINDOW + 1)
    out = list_audit_records(db, limit=10)
    assert out["total"] == LEGACY_WINDOW + 1, "total 必须是真实总数，不能被 5000 内存窗口截断"
    assert len(out["items"]) == 10
    assert out["limit"] == 10 and out["offset"] == 0


def test_oldest_records_stay_reachable_by_offset(db):
    from app.config.audit import list_audit_records

    _seed(db, LEGACY_WINDOW + 1)
    out = list_audit_records(db, limit=10, offset=LEGACY_WINDOW)
    assert out["total"] == LEGACY_WINDOW + 1
    assert len(out["items"]) == 1, "最旧的第 5001 条必须还能翻页到达"
    assert out["items"][0]["id"] == 1


def test_action_filter_reaches_records_outside_legacy_window(db):
    from app.config.audit import list_audit_records

    _seed(db, LEGACY_WINDOW + 1)
    out = list_audit_records(db, limit=10, action="rare-old")
    assert out["total"] == 1, "较久远的 action 不能被静默过滤成空结果"
    assert [row["id"] for row in out["items"]] == [1]


def test_action_filter_is_case_insensitive_and_escapes_like_wildcards(db):
    from app.config.audit import list_audit_records

    _seed(db, 20)
    assert list_audit_records(db, limit=5, action="RARE-OLD")["total"] == 1
    # 库中没有 action 含字面量 % —— 若未转义 LIKE 通配符，这里会错误地匹配全部
    assert list_audit_records(db, limit=5, action="%")["total"] == 0
    assert list_audit_records(db, limit=5, action="act-1")["total"] > 0


def test_limit_is_clamped_and_offset_normalised(db):
    from app.config.audit import AUDIT_LIST_MAX_LIMIT, list_audit_records

    _seed(db, 5)
    assert list_audit_records(db, limit=10 ** 6)["limit"] == AUDIT_LIST_MAX_LIMIT
    assert list_audit_records(db, limit=0)["limit"] == 1
    assert list_audit_records(db, limit=5, offset=-3)["offset"] == 0


def test_since_id_filter_counts_in_sql(db):
    from app.config.audit import list_audit_records

    _seed(db, 30)
    out = list_audit_records(db, limit=5, since_id="11")
    assert out["total"] == 10
    assert max(row["id"] for row in out["items"]) <= 10
    # 非法 since_id 不应报错，只是忽略过滤
    assert list_audit_records(db, limit=5, since_id="not-an-int")["total"] == 30


def test_ordering_is_newest_first(db):
    from app.config.audit import list_audit_records

    _seed(db, 5)
    ids = [row["id"] for row in list_audit_records(db, limit=5)["items"]]
    assert ids == [5, 4, 3, 2, 1]


# ────────────────────── 端点级契约 ──────────────────────


def test_audit_endpoint_reports_real_total(db):
    from app.api.task_center import list_audit
    from fastapi import Response

    _seed(db, LEGACY_WINDOW + 1)
    request = _admin_request(db)
    payload = asyncio.run(list_audit(request, Response(), limit=20, offset=0, action="", since_id="", db=db))
    assert payload["data"]["total"] == LEGACY_WINDOW + 1
    assert len(payload["data"]["items"]) == 20
    assert payload["data"]["limit"] == 20


def test_audit_endpoint_etag_is_stable_and_short_circuits(db):
    """dict 条目的 ETag 必须稳定，否则 If-None-Match / 304 永远不生效。"""
    from app.api.task_center import list_audit
    from fastapi import Response

    _seed(db, 30)
    request = _admin_request(db)
    response = Response()
    asyncio.run(list_audit(request, Response(), limit=10, offset=0, action="", since_id="", db=db))
    first = asyncio.run(list_audit(request, response, limit=10, offset=0, action="", since_id="", db=db))
    etag = response.headers["ETag"]
    assert etag.startswith('W/"')

    again = Response()
    asyncio.run(list_audit(request, again, limit=10, offset=0, action="", since_id="", db=db))
    assert again.headers["ETag"] == etag, "同一份数据两次请求的 ETag 必须一致"

    request.headers = {"If-None-Match": etag}
    not_modified = asyncio.run(list_audit(request, Response(), limit=10, offset=0, action="", since_id="", db=db))
    assert getattr(not_modified, "status_code", None) == 304
    assert first["data"]["total"] == 30


def test_compute_list_etag_is_stable_for_dicts_and_objects():
    from app.api.helpers import compute_list_etag

    page = [{"id": 1, "created_at": "2026-01-01T00:00:00"}, {"id": 2, "created_at": "2026-01-01T00:00:01"}]
    assert compute_list_etag(page, "audit") == compute_list_etag(list(page), "audit")
    # 内容不同 → 指纹不同；前缀不同 → 指纹不同
    assert compute_list_etag(page, "audit") != compute_list_etag(page[:1], "audit")
    assert compute_list_etag(page, "audit") != compute_list_etag(page, "calls")

    class Row:
        def __init__(self, ident, created):
            self.id = ident
            self.created_at = created

    assert compute_list_etag([Row(1, "a"), Row(2, "b")]) == compute_list_etag([Row(1, "a"), Row(2, "b")])


def test_audit_export_filters_in_sql_and_reports_counts(db, monkeypatch):
    """导出必须在 SQL 层过滤（旧实现在最新 5000 条里过滤，静默丢历史数据）。"""
    import app.config.audit as audit_config
    from app.api.task_center import export_audit_csv

    captured = {}

    def fake_list(db_, *, limit=200, offset=0, action="", since_id=""):
        captured.update(limit=limit, offset=offset, action=action, since_id=since_id)
        return {
            "items": [{"id": 1, "created_at": "2026-01-01T00:00:00", "action": "rare-old-action",
                       "target_type": "server", "target_name": "node-0", "details": "{}"}],
            "total": 1, "limit": limit, "offset": offset,
        }

    monkeypatch.setattr(audit_config, "list_audit_records", fake_list)
    request = _admin_request(db)
    resp = asyncio.run(export_audit_csv(request, limit=10 ** 6, action="rare-old-action", db=db))

    assert captured["action"] == "rare-old-action"
    assert captured["limit"] == 5000, "导出上限保持 5000 行"
    assert resp.headers["X-Total-Count"] == "1"
    assert resp.headers["X-Exported-Count"] == "1"
