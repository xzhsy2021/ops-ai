"""发版执行不阻塞主事件循环的回归测试。

背景：部署 worker 通过 AsyncWorkerHandle.create_task 挂在 uvicorn 主事件循环上，
任何同步阻塞调用（SFTP 整包上传、SSH 探测）都会冻结整个站点直到完成。
本测试锁住两处修复：
1. _run_pipeline_task_one_server 中 distribute_package 必须走 run_in_executor；
2. 行为验证：分发执行期间事件循环保持调度（watchdog 持续跳动）。
"""
import asyncio
import inspect
import re
import time
from types import SimpleNamespace

import pytest


# ── 源码守卫 ──

def test_distribute_package_call_is_offloaded():
    """守卫：distribute_package 不得在事件循环上直接同步调用（历史故障源）。"""
    from app.api.deploy import _shared

    src = inspect.getsource(_shared._run_pipeline_task_one_server)
    assert "await loop.run_in_executor" in src, "必须通过 run_in_executor 卸载阻塞分发"
    assert not re.search(r"^\s+distribute_package\(task_id", src, re.M), (
        "检测到 distribute_package 被直接同步调用——这会冻结整个站点"
    )


# ── 行为验证：分发期间事件循环保持响应 ──

@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_event_loop_stays_responsive_during_distribution(monkeypatch):
    import app.api.deploy._shared as shared
    import app.api.deploy_v2 as deploy_v2
    import config_manager

    state = {"distributed": 0}

    def fake_distribute(*a, **k):
        # 模拟长阻塞 SFTP 传输；若被直接放在循环上，watchdog 会停跳
        time.sleep(0.45)
        state["distributed"] += 1
        return None

    monkeypatch.setattr(
        deploy_v2, "_distribute_package_to_server", fake_distribute, raising=False
    )
    monkeypatch.setattr(deploy_v2, "_connect_ssh", lambda srv: SimpleNamespace(close=lambda: None), raising=False)
    monkeypatch.setattr(deploy_v2, "_merge_release_variables", lambda req, db: {}, raising=False)

    class FakeRepo:
        def __init__(self, *a, **k):
            pass

        def create_server_task(self, *a, **k):
            return SimpleNamespace(id=1)

        def update_server_task(self, *a, **k):
            pass

    monkeypatch.setattr(deploy_v2, "DeploymentRuntimeRepository", FakeRepo, raising=False)

    class FakeEngine:
        def __init__(self, *a, **k):
            pass

        async def run(self, *a, **k):
            return {"success": True}

    monkeypatch.setattr(deploy_v2, "PipelineEngine", FakeEngine, raising=False)
    monkeypatch.setattr(shared, "_log_to_db", lambda *a, **k: None)
    monkeypatch.setattr(shared, "_task_cancel_requested", lambda db, tid: False)
    monkeypatch.setattr(config_manager, "get_server_by_name", lambda name: {"name": name})

    req = SimpleNamespace(
        system="s", service="api", environment="test",
        servers=["node-1"], file_name="pkg.tar.gz", version="v1", variables={},
        parallelism=1, fail_fast=True, wave_size=None,
    )

    ticks = []

    async def watchdog():
        while True:
            ticks.append(time.monotonic())
            await asyncio.sleep(0.05)

    watcher = asyncio.ensure_future(watchdog())
    try:
        result = await shared._run_pipeline_task_one_server(
            "node-1", 1, 1, "task-x", "dep-x", req,
            [{"name": "release", "type": "command"}],
            None, None, None,
        )
    finally:
        watcher.cancel()

    assert state["distributed"] == 1
    assert result[1] is True, f"server step should succeed: {result}"
    # 分发的 0.45s 里循环若被阻塞，watchdog 只能跳 1 次；
    # 卸载到线程池后应持续跳动（≥5 次）
    assert len(ticks) >= 5, (
        f"事件循环在分发期间被阻塞（仅 {len(ticks)} 次 tick）——"
        "distribute_package 必须保持在 run_in_executor 内"
    )
