"""复盘修复回归：retention 0 天护栏 / cap 扫尾豁免 / ToolPlan 引用清理 / _resolve LIKE 转义。

2026-09-07 复盘产物。历史事故：09-04 审计页保存策略把 UI 空值 0 写入
audit_keep_days=0 + dry_run=false，夜间自动清理三天把 audit_records 清到 131 行。
"""
from __future__ import annotations

import pytest
from datetime import datetime, timedelta, timezone

pytestmark = pytest.mark.usefixtures("_isolated_test_db")


@pytest.fixture()
def db():
    from app.db.base import Base, SessionLocal, engine
    from app.db.migrations.runner import run_schema_migrations

    Base.metadata.create_all(engine)
    run_schema_migrations(engine)
    session = SessionLocal()
    yield session
    session.rollback()
    session.close()


def _now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def test_zero_keep_days_clamped_to_min_keep():
    """0 天策略必须被钳到 min_keep_days（历史上 0=立即全删造成清库）。"""
    from app.services.release_retention import _normalize_policy

    policy = _normalize_policy({
        "audit_keep_days": 0,
        "audit_high_risk_keep_days": 0,
        "tool_call_keep_days": 0,
        "deploy_keep_days": 0,
        "min_keep_days": 7,
    })
    assert policy["audit_keep_days"] == 7
    assert policy["audit_high_risk_keep_days"] == 7
    assert policy["tool_call_keep_days"] == 7
    assert policy["deploy_keep_days"] == 7
    # 显式设置的长周期不受钳制影响
    policy2 = _normalize_policy({"audit_keep_days": 365, "min_keep_days": 7})
    assert policy2["audit_keep_days"] == 365


def test_deploy_keep_max_sweep_respects_prod_failed_protection(db):
    """cap 扫尾不得绕过生产失败部署 keep_prod_failed_days 豁免。"""
    from app.db.models import Deployment
    from app.services.release_retention import _deployment_candidates, _normalize_policy

    now = _now_naive()
    # 1201 条部署：前 1200 新（8 天前，超 keep_max=1000 的尾部全在此），1 条最老
    old = Deployment(
        id="d-old", system="sys", service="svc", environment="prod", status="failed",
        started_at=now - timedelta(days=8),
    )
    db.add(old)
    for i in range(1200):
        db.add(Deployment(
            id=f"d-new-{i}", system="sys", service="svc", environment="prod", status="success",
            started_at=now - timedelta(days=8),
        ))
    db.commit()

    policy = _normalize_policy({
        "deploy_keep_max": 1000,
        "deploy_success_keep_days": 90,
        "keep_prod_failed_days": 365,
        "min_keep_days": 7,
    })
    ids, summary = _deployment_candidates(db, policy)
    # d-old 排序在尾部（8 天前的 success 之后），是 cap 扫尾目标——但它是
    # prod failed 且保 365 天，不得进候选
    assert "d-old" not in ids
    # 全部行都 8 天 < 90 天成功保留 → age 路径也不删
    assert summary["count"] == 0


def test_tool_plan_deletion_clears_related_call_refs(db):
    """删 ToolPlan 前必须置空 ToolCallLog.related_plan_id（防回放图悬空边）。"""
    from app.db.models import ToolCallLog, ToolPlan, ToolPlanEvent
    from app.services.release_retention import cleanup_release_history, save_retention_policy

    old = _now_naive() - timedelta(days=400)
    plan = ToolPlan(id="plan-x", plan_type="generic", risk_level="low", status="done", created_at=old)
    db.add(plan)
    db.add(ToolCallLog(
        id="call-x", tool_name="t", status="success", risk_level="low",
        created_at=old, related_plan_id="plan-x",
    ))
    db.commit()

    save_retention_policy(db, {
        "tool_plan_keep_days": 180,
        "tool_call_keep_days": 1800,  # call-x（400 天前）不在调用日志删除范围内，必须保留以验证引用置空
        "min_keep_days": 7,
        "dry_run": False,
    })
    result = cleanup_release_history(db, dry_run=False)
    assert result["deleted"].get("tool_plans") == 1
    call = db.query(ToolCallLog).filter_by(id="call-x").first()
    assert call is not None, "调用日志必须保留"
    assert call.related_plan_id is None, "related_plan_id 必须已置空"


def test_resolve_short_prefix_escapes_like_wildcards(db):
    """短前缀解析：用户输入 _ 不得当单字符通配符（会静默构建错误链路）。"""
    from app.db.models import OperationJob
    from app.services.audit_chain import build_operation_chain

    real = OperationJob(id="abcdef1234567890abcdef1234567890", job_type="mcp_tool", title="real", status="success")
    decoy = OperationJob(id="abcd1f1234567890abcdef1234567890", job_type="mcp_tool", title="decoy", status="success")
    db.add_all([real, decoy])
    db.commit()

    # "abcd_f" 含下划线：转义后只能匹配字面 _ ——dec oy 的 abcd1f 不得命中
    # （若无转义，abcd_f 会 LIKE 匹配 abcd1f...）
    chain = build_operation_chain(db, chain_id="job:abcd_f")
    # 两个都匹配不到字面前缀 → roots 为空 → nodes 为 0（而不是解析到 decoy）
    nodes = chain.get("nodes") or []
    matched_ids = {n.get("id") for n in nodes}
    assert "abcd1f1234567890abcdef1234567890" not in matched_ids, "下划线不得作通配符匹配到 abcd1f"

    # 真前缀正常解析
    chain2 = build_operation_chain(db, chain_id="job:abcdef12")
    nodes2 = chain2.get("nodes") or []
    assert any("abcdef1234567890abcdef1234567890" in str(n.get("id")) for n in nodes2), "真实前缀必须命中 real"


def test_blank_step_result_text_no_indexerror(db):
    """步骤 result 为纯空白（" " / "\\n"）时回执模板不得 IndexError。"""
    from app.db.models import ExecutionPlan, ExecutionPlanStep
    from app.services.tool_adapters.approval_tools import _execution_reply_template

    plan = ExecutionPlan(
        id="p" * 32, system_name="sys", service_name="svc", environment="test",
        room_id="!room:x", request_event_id="$evt:x", channel="matrix",
        status="SUCCEEDED", approved_by="matrix:default:@jun:hubtel.xyz",
        plan_digest="d" * 64,
    )
    db.add(plan)
    db.add(ExecutionPlanStep(
        plan_id=plan.id, step_key="step-1", action_type="upload",
        status="SUCCEEDED", result={"summary": "  \n"}, step_order=0,
    ))
    db.commit()
    tpl = _execution_reply_template(plan)
    assert "计划" in tpl  # 正常渲染，不崩
