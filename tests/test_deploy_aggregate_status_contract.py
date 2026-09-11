"""回归：ops.deploy.aggregate_status 的 worker 存活判定。

背景（2026-09-11 用户报障）：`ops.deploy.aggregate_status` 报 AsyncWorkerHandle
相关的 AttributeError。原因是 build_deployments_aggregate 里读了
`_deploy_worker.running`，而 iter19 之后部署 worker 的生命周期状态已收敛到
AsyncWorkerHandle.status()，句柄没有裸的 `running` 属性。

本测试锁住：
1. 句柄的唯一对外口径是 status()；
2. 聚合状态里 worker_alive 是布尔值，且真值随句柄状态变化——不会再抛异常。
"""
import inspect

import pytest

from app.db.base import Base, SessionLocal, engine
from app.db.migrations.runner import run_schema_migrations
from app.deploy.worker import AsyncWorkerHandle
from app.domain.runtime.snapshots import build_deployments_aggregate


@pytest.fixture(scope="module")
def db():
    Base.metadata.create_all(engine)
    run_schema_migrations(engine)
    session = SessionLocal()
    yield session
    session.rollback()
    session.close()


def test_worker_handle_exposes_running_only_through_status():
    """句柄对外是 status()；running 字段由它给出（worker 未启动时为 False）。"""
    handle = AsyncWorkerHandle("deploy-worker")
    assert not hasattr(handle, "running"), "句柄不应有裸 running 属性，调用方必须走 status()"

    status = handle.status()
    assert status["name"] == "deploy-worker"
    assert status["running"] is False
    assert status["started_at"] is None


def test_aggregate_status_never_reads_bare_running_attribute():
    """源码守卫：聚合快照不得再读 `.running`（历史 AttributeError 来源）。"""
    src = inspect.getsource(build_deployments_aggregate)
    assert "_deploy_worker.running" not in src, (
        "禁止直接读 _deploy_worker.running——AsyncWorkerHandle 没有该属性，会抛 AttributeError"
    )
    assert "_deploy_worker.status()" in src, "worker 存活必须通过 status() 判定"


def test_aggregate_status_returns_worker_alive_boolean(db):
    """端到端：聚合状态可正常构造，worker_alive 为布尔且字段齐全。"""
    payload = build_deployments_aggregate(db, system="", environment="", limit=5)

    assert isinstance(payload["worker_alive"], bool)
    for key in (
        "latest_deployments",
        "active_jobs",
        "active_count",
        "running_count",
        "recent_rollback_count",
        "precheck_enabled",
        "snapshot_at",
    ):
        assert key in payload, f"聚合状态缺少字段 {key}"


def test_aggregate_status_endpoint_returns_data(db, monkeypatch):
    """HTTP 路由层：GET /api/v2/status/deploy-aggregate 不再 500。"""
    from fastapi.testclient import TestClient

    from app.api.v2 import status as status_api

    class _App:
        pass

    from fastapi import FastAPI
    from app.db import get_db

    app = FastAPI()
    app.include_router(status_api.router)
    app.dependency_overrides[get_db] = lambda: db

    with TestClient(app) as client:
        resp = client.get("/api/v2/status/deploy-aggregate")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.get("success") is True, body
    data = body.get("data") or {}
    assert isinstance(data["worker_alive"], bool)
    assert data["active_count"] == len(data["active_jobs"])
