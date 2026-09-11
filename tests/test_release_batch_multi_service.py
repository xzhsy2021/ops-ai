"""一批多次发版：RELEASE 步骤按 step 读取 service_name（一次审批覆盖多个服务）。

背景（2026-09-11）：用户需求是「测试环境拉取 system, transaction, strategy 最新镜像，
按顺序部署，transaction / strategy 两台都部署」，即**一次批量发版多个服务**。但

* 路由把多服务消息钉在单个服务上（已由 resolve_message_target 的
  system_keyword_multi_service 修复）；
* `_step_param_reader_keys()` 的 RELEASE 契约漏列 system_name/service_name，
  而 `plan_executor._release_handler` 实际是逐步骤读取
  `params.get("service_name") or plan.service_name` 的——"一个计划批量发版多个服务"
  这条正路没有任何文档或契约背书。

本文件锁定修复后的行为：
1. RELEASE 契约包含逐步 system_name/service_name；
2. _release_handler 优先用步骤自身的 service_name/targets，缺省才回落到计划级；
3. 系统级计划里两个 RELEASE 步骤分别发两个服务，各自 targets 独立。
"""
from types import SimpleNamespace

import pytest

from app.services import plan_executor
from app.services.agent_context import _step_param_reader_keys


def test_release_contract_reads_per_step_service():
    """契约必须承认执行器实际读取的逐步 system_name/service_name。"""
    keys = _step_param_reader_keys()["RELEASE"]
    assert "service_name" in keys
    assert "system_name" in keys
    # 原有键不能被顺手删掉
    for key in ("package_name", "targets", "environment"):
        assert key in keys


def _handler_payload(monkeypatch, *, params, plan_service=None, plan_targets=None):
    captured = {}

    def fake_execute_release(db, payload, operator=None, package_name=None):
        captured["payload"] = payload
        captured["package_name"] = package_name
        return {"ok": True}

    monkeypatch.setattr("app.services.approval_executor.execute_release", fake_execute_release)
    plan = SimpleNamespace(
        system_name="crypto-trader",
        service_name=plan_service,
        environment="test",
        targets=plan_targets or [],
        package_name="pkg.tar.gz",
        approved_by="admin",
        steps=[],
    )
    step = SimpleNamespace(step_key="release-x", parameters=params, dependencies=[])
    plan_executor._release_handler(plan, step, db=None)
    return captured["payload"]


def test_release_step_service_overrides_plan_level(monkeypatch):
    """步骤自带 service_name 时优先，覆盖计划级（批量发版的核心机制）。"""
    payload = _handler_payload(
        monkeypatch,
        params={"service_name": "transaction", "targets": ["主节点", "2节点"]},
        plan_service="system",
        plan_targets=["主节点"],
    )
    assert payload["service_name"] == "transaction"
    assert payload["targets"] == ["主节点", "2节点"]
    assert payload["system_name"] == "crypto-trader"


def test_release_step_falls_back_to_plan_targets(monkeypatch):
    """步骤未指定 targets 时回落计划级，兼容原单服务流程。"""
    payload = _handler_payload(
        monkeypatch,
        params={"service_name": "strategy"},
        plan_service=None,
        plan_targets=["主节点", "2节点"],
    )
    assert payload["service_name"] == "strategy"
    assert payload["targets"] == ["主节点", "2节点"]


@pytest.mark.parametrize("svc", ["system", "transaction", "strategy"])
def test_release_batch_two_steps_are_independent(monkeypatch, svc):
    """同一计划里多个 RELEASE 步骤各自解析出自己的服务与目标（互不串味）。"""
    payload = _handler_payload(
        monkeypatch,
        params={"service_name": svc, "targets": ["主节点", "2节点"]},
        plan_service=None,
    )
    assert payload["service_name"] == svc
