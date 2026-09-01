"""Agent Context Layer 契约测试（Phase 1）。

覆盖 docs/agent-integration-abstraction-layer.md 第 6 章 Phase 1 项：
pack revision 稳定性、facts 与 DB 一致性、flow guide 步骤工具名存在
于 registry、缓存协议、MCP annotations 映射、教训库初版（八轮故障）。
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def _sqlite_session(tmp_path):
    from app.db.models import Base

    engine = create_engine(
        f"sqlite:///{tmp_path / 'agent_context_layer.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    return engine, Session


@pytest.fixture()
def env(tmp_path):
    engine, Session = _sqlite_session(tmp_path)
    db = Session()
    try:
        from app.services.tool_registry import register_builtin_tools

        register_builtin_tools()
        yield db
    finally:
        db.close()
        engine.dispose()


@pytest.fixture()
def ctx():
    from app.services.tool_context import ToolContext

    return ToolContext(
        username="tester",
        auth_type="session",
        is_admin=True,
        scopes=["ops:read", "ops:write", "*"],
        allow_write=True,
    )


def _call(db, name, args, ctx):
    from app.services.tool_registry import registry

    return registry.call(db, name, args, ctx)["result"]


# ──────────────────────────────────────────────────────────────
# pack 结构与 revision 协议
# ──────────────────────────────────────────────────────────────


def test_pack_structure_and_revision_stability(env, ctx):
    """pack 六区块齐全；同库同 agent 两次组装 revision 一致。"""
    from app.services import agent_context

    p1 = agent_context.build_context_pack(env, ctx, agent_name="zeroclaw")
    p2 = agent_context.build_context_pack(env, ctx, agent_name="zeroclaw")
    assert p1["pack_revision"] == p2["pack_revision"]
    for key in ("capabilities", "facts", "flows", "lessons", "agent_name", "channel"):
        assert key in p1, key
    # 不同 agent_name → 不同 revision（视图区分）
    p3 = agent_context.build_context_pack(env, ctx, agent_name="openclaw")
    assert p3["pack_revision"] != p1["pack_revision"]


def test_pack_cache_protocol_via_tool(env, ctx):
    """cached_revision 命中 → unchanged 轻量响应；过期 → 全量。"""
    r1 = _call(env, "ops.integration.get_context_pack", {"agent_name": "zeroclaw"}, ctx)
    r2 = _call(
        env,
        "ops.integration.get_context_pack",
        {"agent_name": "zeroclaw", "cached_revision": r1["pack_revision"]},
        ctx,
    )
    assert r2.get("unchanged") is True
    assert "capabilities" not in r2  # 轻量响应不带全量

    r3 = _call(
        env,
        "ops.integration.get_context_pack",
        {"agent_name": "zeroclaw", "cached_revision": "0" * 64},
        ctx,
    )
    assert "capabilities" in r3 and r3["pack_revision"] == r1["pack_revision"]


def test_pack_include_tools_false_trims_enumeration(env, ctx):
    """include_tools=False 省略 120+ 工具明细但保留计数。"""
    r = _call(
        env,
        "ops.integration.get_context_pack",
        {"agent_name": "t", "include_tools": False},
        ctx,
    )
    assert "tools" not in r["capabilities"]
    assert r["capabilities"]["tool_count"] >= 100
    assert r["capabilities"]["tolerances"] and r["capabilities"]["forbidden"]


# ──────────────────────────────────────────────────────────────
# facts 与 DB 一致性（C 类根因的解：拿不到不存在的服务名）
# ──────────────────────────────────────────────────────────────


def test_facts_reflect_db_systems_services_rooms(env, ctx):
    """pack facts 从 DB 实时组装：注入系统/服务/房间后立即出现在 pack。"""
    from app.db.models import Service, System

    system = System(
        name="layer-test-sys",
        display_name="分层测试",
        message_routing={"rooms": [{"conversation_id": "!layer-room:hubtel.xyz"}]},
    )
    env.add(system)
    env.flush()
    env.add(Service(name="layer-test-svc", system_name="layer-test-sys", display_name="svc"))
    env.commit()

    r = _call(env, "ops.integration.get_context_pack", {"agent_name": "t"}, ctx)
    entry = [f for f in r["facts"]["systems"] if f["name"] == "layer-test-sys"]
    assert entry, "新增系统应出现在 facts"
    assert entry[0]["services"] == ["layer-test-svc"]
    assert entry[0]["rooms"] == ["!layer-room:hubtel.xyz"]

    env.rollback()


# ──────────────────────────────────────────────────────────────
# flow guide：工具名存在性 + 样例正确性（B/C 类根因的解）
# ──────────────────────────────────────────────────────────────


def test_flow_guide_tools_exist_in_registry(env, ctx):
    """flow guide 引用的每个工具名必须真实注册——消灭'按记忆调用不存在工具'。"""
    from app.services.tool_registry import registry

    r = _call(env, "ops.integration.get_flow_guide", {"flow_id": "frontend-release"}, ctx)
    assert r["atomic"] is True
    for step in r["steps"]:
        tool_name = step.get("tool", "")
        if tool_name.startswith("ops."):
            assert registry.get(tool_name), f"flow 引用未注册工具: {tool_name}"
    # 票据链路三件套必须都在
    listed = registry.list_tools(env, ctx, include_disabled=False, include_schema=False, limit=2000)
    names = {t["name"] for t in listed["tools"]}
    for must in (
        "ops.routing.resolve_message_target",
        "ops.approval.prepare_plan",
        "ops.approval.execute_plan",
    ):
        assert must in names


def test_flow_guide_steps_template_carries_real_service(env, ctx):
    """steps_template 的 SERVICE_CONTROL 带真实服务名（crypto-trader-web，
    不是历史错误的 crypto-frontend）——L007 教训的机制化落地。"""
    r = _call(env, "ops.integration.get_flow_guide", {"flow_id": "frontend-release"}, ctx)
    svc = r["steps_template"]["SERVICE_CONTROL"]["parameters"]["action_parameters"]
    assert svc["service_name"] == "crypto-trader-web"
    assert svc["system_name"] == "crypto-trader"
    assert svc["targets"] == ["203.0.113.10"]


def test_flow_guide_unknown_id_returns_index(env, ctx):
    r = _call(env, "ops.integration.get_flow_guide", {"flow_id": "no-such-flow"}, ctx)
    assert "error" in r and "flow_ids" in r
    listing = _call(env, "ops.integration.get_flow_guide", {"list_only": True}, ctx)
    assert set(listing["flow_ids"]) == {
        "frontend-release",
        "package-pull-release",
        "service-restart",
    }


def test_flow_guide_listings_match_titles():
    from app.services.agent_context import FLOW_GUIDES, list_flow_ids

    assert list_flow_ids() == sorted(FLOW_GUIDES)
    for flow_id, flow in FLOW_GUIDES.items():
        assert flow["steps"] and flow["hard_rules"], flow_id
        assert flow["steps"][0]["n"] == 1  # 步骤从 1 连续编号
        for i, step in enumerate(flow["steps"], start=1):
            assert step["n"] == i, (flow_id, step["n"])


def test_frontend_release_flow_verified_end_to_end(env, ctx):
    """前端发版流程经端到端验证（2026-09-01 zeroclaw 元指令版全链路成功），
    verified 字段随 flow 进入 revision——流程定义任何变更（含验证状态）
    都会改变 flow_revision，缓存协议自动传播。"""
    r = _call(env, "ops.integration.get_flow_guide", {"flow_id": "frontend-release"}, ctx)
    assert "2026-09-01" in r.get("verified", ""), "发版流程应标注端到端验证日期"
    assert r["atomic"] is True


# ──────────────────────────────────────────────────────────────
# MCP annotations 映射（补充①）
# ──────────────────────────────────────────────────────────────


def test_pack_tools_carry_mcp_annotations(env, ctx):
    """每个工具条目带 readOnly/destructiveHint/idempotentHint 映射。"""
    r = _call(env, "ops.integration.get_context_pack", {"agent_name": "t"}, ctx)
    tools = r["capabilities"]["tools"]
    assert tools, "默认应包含工具明细"
    for tool in tools:
        ann = tool.get("mcp_annotations", {})
        assert set(ann) >= {"readOnly", "destructiveHint", "idempotentHint"}, tool["name"]
        # 写工具必非只读
        if tool.get("write"):
            assert ann["readOnly"] is False, tool["name"]
    # 审批执行工具应在列且带注解
    by_name = {t["name"]: t for t in tools}
    assert "ops.approval.execute_plan" in by_name


# ──────────────────────────────────────────────────────────────
# 教训库初版（八轮故障；superseded 条目标注失效原因）
# ──────────────────────────────────────────────────────────────


def test_lessons_initial_set_covers_eight_rounds(env, ctx):
    """8 条教训、3 条 superseded（摘要系列 L001/L002 + 参数系列 L005）、
    superseded 必带失效说明——D 类根因的解。"""
    r = _call(env, "ops.integration.get_context_pack", {"agent_name": "t"}, ctx)
    lessons = r["lessons"]
    assert len(lessons) >= 8
    superseded = [l for l in lessons if l["status"] == "superseded"]
    active = [l for l in lessons if l["status"] == "active"]
    assert len(superseded) == 3
    assert all(l.get("superseded_note") for l in superseded)
    assert active, "至少保留活跃教训"
    # 关键教训在场：execute_plan 四参数 + 服务名以 facts 为准
    patterns = " ".join(l["pattern"] + l["guidance"] for l in lessons)
    assert "execute_plan 只需 4 个参数" in patterns
    assert "crypto-trader-web" in patterns or "服务名以 pack facts" in patterns
    ids = [l["id"] for l in lessons]
    assert len(ids) == len(set(ids)), "教训 ID 必须唯一"


def test_pack_next_step_present(env, ctx):
    """pack 返回自带 next_step 引导（agent 接到 pack 后知道下一步）。"""
    r = _call(env, "ops.integration.get_context_pack", {"agent_name": "t"}, ctx)
    assert "get_flow_guide" in r["next_step"]
    g = _call(env, "ops.integration.get_flow_guide", {"flow_id": "service-restart"}, ctx)
    assert "atomic" in g["next_step"]


# ──────────────────────────────────────────────────────────────
# Phase 2：教训回写（save_lesson）
# ──────────────────────────────────────────────────────────────


def test_save_lesson_pending_idempotent_and_pack_merge(env, ctx):
    """回写默认 pending + 同 pattern 幂等更新 + 管理端确认后入 pack 正文。

    pending 不入 pack 正文（防污染）；激活后出现且 DB 记录优先于内置同 pattern。
    """
    from app.db.models import AgentLesson

    r1 = _call(
        env,
        "ops.integration.save_lesson",
        {
            "pattern": "目标服务器磁盘只读导致上传失败",
            "guidance": "先检查挂载与磁盘余量再重试上传",
            "evidence": "plan=dbg, step-1-upload FAILED",
            "severity": "warning",
            "agent_name": "zeroclaw",
        },
        ctx,
    )
    assert r1["status"] == "pending" and not r1["updated"]
    lesson_id = r1["lesson_id"]

    # 幂等：同 pattern 再回写 → 更新而非新增
    r2 = _call(
        env,
        "ops.integration.save_lesson",
        {"pattern": "目标服务器磁盘只读导致上传失败", "guidance": "先修复磁盘再重试"},
        ctx,
    )
    assert r2["updated"] is True and r2["lesson_id"] == lesson_id
    assert (
        env.query(AgentLesson)
        .filter(AgentLesson.pattern == "目标服务器磁盘只读导致上传失败", AgentLesson.status == "pending")
        .count()
        == 1
    )

    # pending 不入 pack 正文
    pack = _call(env, "ops.integration.get_context_pack", {"agent_name": "t"}, ctx)
    assert not any("磁盘只读" in l["pattern"] for l in pack["lessons"])

    # 管理端确认 → active → 入 pack 正文且 revision 变化
    row = env.get(AgentLesson, lesson_id)
    row.status = "active"
    env.commit()
    pack2 = _call(env, "ops.integration.get_context_pack", {"agent_name": "t"}, ctx)
    merged = [l for l in pack2["lessons"] if "磁盘只读" in l["pattern"]]
    assert merged and merged[0]["origin"] == "agent" and merged[0]["status"] == "active"
    assert pack2["pack_revision"] != pack["pack_revision"]


def test_save_lesson_validation_and_severity_default(env, ctx):
    """缺 pattern/guidance 业务校验拒绝；非法 severity 落回 info。"""
    from app.services import agent_context as ac

    try:
        ac.save_lesson(env, pattern="   ", guidance="x")
        raised = False
    except ValueError:
        raised = True
    assert raised

    r = _call(
        env,
        "ops.integration.save_lesson",
        {"pattern": "验证 severity 兜底", "guidance": "ok", "severity": "bogus"},
        ctx,
    )
    assert r["status"] == "pending"
    from app.db.models import AgentLesson

    row = env.get(AgentLesson, r["lesson_id"])
    assert row.severity == "info"
    assert row.origin == "agent"


def test_db_superseded_overrides_builtin_lesson(env, ctx):
    """DB 中同 pattern 的 superseded 记录优先于内置教训——管理端可把内置
    条目置失效（Phase 2 supersede 工作流的机制验证）。"""
    from datetime import datetime, timezone

    from app.db.models import AgentLesson

    builtin = next(l for l in _builtin_lessons() if l["status"] == "active")
    env.add(
        AgentLesson(
            id="Mtest01",
            pattern=builtin["pattern"],
            guidance=builtin["guidance"],
            status="superseded",
            origin="manual",
            superseded_note="测试：人工置失效",
            created_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        )
    )
    env.commit()
    pack = _call(env, "ops.integration.get_context_pack", {"agent_name": "t"}, ctx)
    hits = [l for l in pack["lessons"] if l["pattern"] == builtin["pattern"]]
    assert len(hits) == 1, "同 pattern 不应出现内置+DB 双条"
    assert hits[0]["status"] == "superseded" and hits[0]["origin"] == "manual"


def _builtin_lessons():
    from app.services.agent_context import LESSONS

    return LESSONS


# ──────────────────────────────────────────────────────────────
# Phase 3：契约自动化（facts 全量 + args_hint/steps_template/annotations 校验）
# ──────────────────────────────────────────────────────────────


def test_facts_auto_assembly_full(env, ctx):
    """facts 全自动化：systems + approvers + environments 一次到位。"""
    from app.db.models import Service, System, SystemEnvironment

    env.add(
        System(
            name="layer-auto-sys",
            display_name="自动化",
            message_routing={
                "rooms": [{"channel": "matrix", "channel_account_id": "default", "conversation_id": "!auto:hubtel.xyz"}],
                "approvers": [{"channel": "matrix", "channel_account_id": "default", "sender_id": "@auto-approver:hubtel.xyz"}],
            },
        )
    )
    env.flush()
    env.add(Service(name="auto-svc", system_name="layer-auto-sys", display_name="s"))
    env.add(SystemEnvironment(system_name="layer-auto-sys", name="test"))
    env.commit()

    pack = _call(env, "ops.integration.get_context_pack", {"agent_name": "t", "include_tools": False}, ctx)
    facts = pack["facts"]
    assert set(facts) >= {"systems", "approvers", "environments"}
    entry = [f for f in facts["systems"] if f["name"] == "layer-auto-sys"][0]
    assert entry["services"] == ["auto-svc"]
    assert entry["rooms"] == ["!auto:hubtel.xyz"]
    assert entry["approvers"] == ["@auto-approver:hubtel.xyz"]
    assert "@auto-approver:hubtel.xyz" in [a["sender_id"] for a in facts["approvers"]]
    assert "test" in facts["environments"]


def test_flow_guide_contract_no_drift(env, ctx):
    """Phase 3 核心：args_hint 键 ∈ 工具 schema、steps_template 参数键 ∈ 执行器
    读取口径——任何漂移即测试失败（消灭 C 类根因的机制化）。"""
    from app.services.agent_context import validate_flow_guide_contract

    report = validate_flow_guide_contract(env, ctx)
    assert report["ok"], f"flow guide 契约漂移: {report['violations']}"


def test_flow_guide_contract_detects_drift(env, ctx):
    """校验器本身要能抓住漂移（反向验证：注入非法键必须被逮住）。"""
    from app.services import agent_context as ac

    original = ac.FLOW_GUIDES["frontend-release"]["steps"][2]["args_hint"]
    try:
        ac.FLOW_GUIDES["frontend-release"]["steps"][2]["args_hint"] = dict(original, bogus_key="x")
        report = ac.validate_flow_guide_contract(env, ctx)
        assert not report["ok"]
        assert any("bogus_key" in v for v in report["violations"])
    finally:
        ac.FLOW_GUIDES["frontend-release"]["steps"][2]["args_hint"] = original
    # 恢复后再校验必须干净
    assert ac.validate_flow_guide_contract(env, ctx)["ok"]


def test_capability_annotations_sync(env, ctx):
    """MCP annotations 与 registry risk/write 推导关系同步校验。"""
    from app.services.agent_context import validate_capability_annotations

    report = validate_capability_annotations(env, ctx)
    assert report["ok"], f"annotations 漂移: {report['violations']}"


def test_execute_plan_next_step_guides_by_status():
    """execute_plan 结果引导按状态分派（SUCCEEDED/PARTIAL_FAILED/FAILED）。"""
    import inspect
    import sys as _sys

    code = _sys.modules["app.services.tool_adapters.approval_tools"].__dict__
    src = inspect.getsource(code["approval_execute_plan"])
    assert "PARTIAL_FAILED" in src and "save_lesson" in src
    assert "失败计划是终态" in src
    assert "不要重新执行" in src or "不能重试 execute_plan" in src


# ──────────────────────────────────────────────────────────────
# Phase 4：heartbeat_ops（审批催办闭环）
# ──────────────────────────────────────────────────────────────


def _mk_pending_plan(db, *, expires_in_minutes, age_minutes=0):
    from datetime import datetime, timedelta, timezone

    from app.db.models import ExecutionPlan

    now = datetime.now(timezone.utc)
    plan = ExecutionPlan(
        id=f"hb-{expires_in_minutes}-{age_minutes}",
        plan_digest=f"digest-{expires_in_minutes}-{age_minutes}",
        system_name="crypto-trader",
        service_name="crypto-trader-web",
        environment="test",
        channel="matrix",
        channel_account_id="default",
        room_id="!hb-room:hubtel.xyz",
        request_event_id="$hb-evt",
        request_sender_id="@requester:hubtel.xyz",
        status="PENDING_APPROVAL",
        created_at=now - timedelta(minutes=age_minutes),
        expires_at=now + timedelta(minutes=expires_in_minutes),
    )
    db.add(plan)
    db.commit()
    return plan


def test_heartbeat_ops_state_machine(env, ctx):
    """待审批计划按剩余时间分派 pending/expiring/expired 三态。"""
    _mk_pending_plan(env, expires_in_minutes=30, age_minutes=5)   # pending
    _mk_pending_plan(env, expires_in_minutes=3, age_minutes=12)    # expiring
    _mk_pending_plan(env, expires_in_minutes=-2, age_minutes=17)   # expired

    r = _call(env, "ops.integration.get_heartbeat_ops", {}, ctx)
    assert r["has_work"] is True
    rem = r["approval_reminders"]
    assert len(rem["pending"]) == 1 and rem["pending"][0]["state"] == "pending"
    assert len(rem["expiring_soon"]) == 1 and rem["expiring_soon"][0]["state"] == "expiring"
    assert len(rem["expired"]) == 1 and rem["expired"][0]["state"] == "expired"
    plan = rem["expiring_soon"][0]
    assert plan["room_id"] == "!hb-room:hubtel.xyz"
    assert plan["minutes_left"] <= 5
    assert "审批人" in r["instructions"] and "沉默" in r["instructions"]
    assert r["next_step"] == r["instructions"]


def test_heartbeat_ops_silent_when_clean(env, ctx):
    """无待审批、无异常 → has_work=False + 明确沉默指令（不发空消息）。"""
    r = _call(env, "ops.integration.get_heartbeat_ops", {}, ctx)
    assert r["has_work"] is False
    assert "沉默" in r["next_step"]


def test_pack_heartbeat_channel_carries_ops(env, ctx):
    """channel=heartbeat 的 pack 附带 heartbeat_ops 区块；普通渠道不带
    （控制 pack 体积）。"""
    _mk_pending_plan(env, expires_in_minutes=10, age_minutes=5)
    hb_pack = _call(
        env, "ops.integration.get_context_pack", {"agent_name": "t", "channel": "heartbeat"}, ctx
    )
    assert "heartbeat_ops" in hb_pack
    assert hb_pack["heartbeat_ops"]["approval_reminders"]["pending"], "待审批计划应出现"
    plain = _call(env, "ops.integration.get_context_pack", {"agent_name": "t2", "channel": "matrix"}, ctx)
    assert "heartbeat_ops" not in plain
    # 渠道不同 → revision 不同
    assert hb_pack["pack_revision"] != plain["pack_revision"]
