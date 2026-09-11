"""流程 ↔ MCP 能力序关联契约测试（设计 ①②）。

- build_capability_sequence：工具属性从 tool_map 实时 join（不手工标注）
- 审批门自动识别：(matrix 步骤 → approval_gate=true
- steps_template → sub_actions 计划动作层
- 全部 FLOW_GUIDES 步骤工具名必须在 registry（漂移检测，用真 registry）
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.agent_context import (  # noqa: E402
    FLOW_GUIDES,
    build_capability_sequence,
)


def _env(name: str) -> str:
    """从仓库 .env 读一个键的值（只解析文本，不执行文件内容）。"""
    for line in open(Path(__file__).resolve().parent.parent / ".env", encoding="utf-8"):
        line = line.strip()
        if line.startswith(name + "="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def _bind_signing_key(monkeypatch) -> None:
    """注入 APPROVAL_SIGNING_KEY，测试结束后由 monkeypatch 自动还原。

    此前这里用 os.environ.setdefault 直接改写进程环境且从不回收，会把该键泄漏
    给同进程后续测试：test_task5_final_review 的 dotenv 用例依赖“未导出的键才
    回填”语义，被泄漏的键会让它误判失败。
    """
    monkeypatch.setenv("APPROVAL_SIGNING_KEY", _env("APPROVAL_SIGNING_KEY"))


def _tool_map(**overrides):
    """构造受控 tool_map。"""
    base = {
        "ops.matrix.scan_media_events": {"name": "ops.matrix.scan_media_events", "write": False, "risk": "low", "scopes": ["ops:read"]},
        "ops.routing.resolve_message_target": {"name": "ops.routing.resolve_message_target", "write": False, "risk": "low", "scopes": ["ops:read"]},
        "ops.approval.prepare_plan": {"name": "ops.approval.prepare_plan", "write": False, "risk": "low", "scopes": ["ops:read"]},
        "ops.approval.execute_plan": {"name": "ops.approval.execute_plan", "write": False, "risk": "low", "scopes": ["ops:read"]},
    }
    base.update(overrides)
    return base


def test_sequence_joins_tool_capabilities():
    flow = FLOW_GUIDES["frontend-release"]
    seq = build_capability_sequence(flow, _tool_map())
    # 第一步 scan_media_events 应带上 registry 属性
    s1 = seq[0]
    assert s1["tool"] == "ops.matrix.scan_media_events"
    assert s1["write"] is False
    assert s1["risk"] == "low"
    assert "ops:read" in s1["scopes"]


def test_approval_gate_detected():
    flow = FLOW_GUIDES["frontend-release"]
    seq = build_capability_sequence(flow, _tool_map())
    gates = [s for s in seq if s.get("approval_gate")]
    assert len(gates) == 1
    assert gates[0]["tool"].startswith("(matrix")
    assert "人工审批门" in gates[0]["note"]


def test_plan_action_layer_included():
    flow = FLOW_GUIDES["frontend-release"]
    seq = build_capability_sequence(flow, _tool_map())
    plan = [s for s in seq if s.get("tool") == "(execution_plan)"]
    assert len(plan) == 1
    action_types = {a["action_type"] for a in plan[0]["sub_actions"]}
    assert action_types == {"FILE_UPLOAD", "SERVICE_CONTROL"}
    # 依赖链：SERVICE_CONTROL 依赖 FILE_UPLOAD（step key 以 flow 实际定义为准）
    deps = {a["action_type"]: a["depends_on"] for a in plan[0]["sub_actions"]}
    upload_key = next(k["step_key"] for k in plan[0]["sub_actions"] if k["action_type"] == "FILE_UPLOAD")
    assert upload_key in deps["SERVICE_CONTROL"]


def test_dovo_bg_flow_action_layer():
    flow = FLOW_GUIDES["dovo-bg-release"]
    seq = build_capability_sequence(flow, _tool_map())
    plan = [s for s in seq if s.get("tool") == "(execution_plan)"]
    assert plan
    action_types = {a["action_type"] for a in plan[0]["sub_actions"]}
    assert action_types == {"MATRIX_PULL", "SERVICE_CONTROL"}


def test_drift_marked_when_tool_missing():
    flow = FLOW_GUIDES["frontend-release"]
    seq = build_capability_sequence(flow, {})  # 空 tool_map
    drifts = [s for s in seq if s.get("drift")]
    assert drifts, "工具不在 tool_map 应标 drift"


def test_all_flow_tools_in_real_registry(monkeypatch):
    """CI 漂移检测：全部流程步骤工具名必须在真 registry。

    与 validate_capability_annotations 同思路——名称 join 的地基。
    """
    _bind_signing_key(monkeypatch)
    from app.db.base import SessionLocal
    from app.services.tool_context import ToolContext
    from app.services.tool_registry import register_builtin_tools, registry

    register_builtin_tools()
    db = SessionLocal()
    try:
        ctx = ToolContext(username="probe", auth_type="session", is_admin=True, scopes=["*"], allow_write=True)
        listed = registry.list_tools(db, ctx, include_disabled=False, include_schema=False, limit=2000)
        tool_map = {t["name"]: t for t in listed["tools"]}
        drifts = []
        for fid, flow in FLOW_GUIDES.items():
            seq = build_capability_sequence(flow, tool_map)
            for s in seq:
                if s.get("drift"):
                    drifts.append(f"{fid}#{s.get('n')}: {s['tool']}")
        assert not drifts, f"流程步骤工具名漂移: {drifts}"
    finally:
        db.close()


def test_capability_sequence_in_pack(monkeypatch):
    """pack.flows[] 携带 capability_sequence（生产组装验证）。"""
    _bind_signing_key(monkeypatch)
    from app.db.base import SessionLocal
    from app.services.tool_context import ToolContext
    from app.services.agent_context import build_context_pack

    db = SessionLocal()
    try:
        ctx = ToolContext(username="probe", auth_type="session", is_admin=True, scopes=["*"], allow_write=True)
        pack = build_context_pack(db, ctx, agent_name="probe", channel="matrix")
        for f in pack["flows"]:
            assert "capability_sequence" in f, f"{f['flow_id']} 缺 capability_sequence"
            assert len(f["capability_sequence"]) >= 3
        # dovo flow 的能力序应含计划动作层
        dovo = next(f for f in pack["flows"] if f["flow_id"] == "dovo-bg-release")
        plan = [s for s in dovo["capability_sequence"] if s.get("tool") == "(execution_plan)"]
        assert plan and plan[0]["sub_actions"]
    finally:
        db.close()


# ──────────────────────────────────────────────────────────────
# 设计 ③：validate_capability_sequence 四层校验
# ──────────────────────────────────────────────────────────────


def _live_registry_env(monkeypatch):
    _bind_signing_key(monkeypatch)
    from app.db.base import SessionLocal
    from app.services.tool_context import ToolContext
    from app.services.tool_registry import register_builtin_tools

    register_builtin_tools()
    db = SessionLocal()
    ctx = ToolContext(username="probe", auth_type="session", is_admin=True, scopes=["*"], allow_write=True)
    return db, ctx


def test_validate_sequence_ok_on_production(monkeypatch):
    """生产四层校验全绿（flow 数随 FLOW_GUIDES 增长，与定义同步）。"""
    from app.services.agent_context import FLOW_GUIDES, validate_capability_sequence

    db, ctx = _live_registry_env(monkeypatch)
    try:
        report = validate_capability_sequence(db, ctx)
        assert report["ok"], report["violations"]
        assert report["flows_checked"] == len(FLOW_GUIDES)
    finally:
        db.close()


def test_validate_sequence_catches_drift(monkeypatch):
    """层1：流程引用不存在的工具名 → 违规暴露。"""
    from app.services import agent_context as ac

    flow = {
        "steps": [{"n": 1, "tool": "ops.approval.prepare_plan"},
                  {"n": 2, "tool": "(matrix 回复)"},
                  {"n": 3, "tool": "ops.approval.execute_plan"}],
    }
    monkeypatch.setattr(ac, "FLOW_GUIDES", {"x-flow": flow})
    db, ctx = _live_registry_env(monkeypatch)
    try:
        seq = ac.build_capability_sequence(flow, {})  # 空 map → drift 标记
        assert any(s.get("drift") for s in seq)
    finally:
        db.close()


def test_validate_sequence_gate_uniqueness(monkeypatch):
    """层3：审批门数量≠1 → 违规（零门/多门都报）。"""
    from app.services import agent_context as ac

    flow = {
        "steps": [{"n": 1, "tool": "ops.approval.prepare_plan"},
                  {"n": 2, "tool": "ops.approval.execute_plan"}],  # 零审批门
    }
    monkeypatch.setattr(ac, "FLOW_GUIDES", {"x-flow": flow})
    db, ctx = _live_registry_env(monkeypatch)
    try:
        report = ac.validate_capability_sequence(db, ctx)
        assert not report["ok"]
        assert any("审批门数量 0" in v for v in report["violations"]), report["violations"]
    finally:
        db.close()


def test_validate_sequence_action_type_illegal(monkeypatch):
    """层4：action_type 不在 STEP_HANDLERS → 违规。"""
    from app.services import agent_context as ac

    flow = {
        "steps": [{"n": 1, "tool": "ops.approval.prepare_plan"},
                  {"n": 2, "tool": "(matrix 回复)"},
                  {"n": 3, "tool": "ops.approval.execute_plan"}],
        "steps_template": {
            "BOGUS_ACTION": {"step_key": "s1", "action_type": "BOGUS_ACTION"},
        },
    }
    monkeypatch.setattr(ac, "FLOW_GUIDES", {"x-flow": flow})
    db, ctx = _live_registry_env(monkeypatch)
    try:
        report = ac.validate_capability_sequence(db, ctx)
        assert not report["ok"]
        assert any("BOGUS_ACTION" in v and "STEP_HANDLERS" in v for v in report["violations"])
    finally:
        db.close()


def test_validate_sequence_dependency_dangling(monkeypatch):
    """层4：依赖引用不存在的 step_key → 违规。"""
    from app.services import agent_context as ac

    flow = {
        "steps": [{"n": 1, "tool": "ops.approval.prepare_plan"},
                  {"n": 2, "tool": "(matrix 回复)"},
                  {"n": 3, "tool": "ops.approval.execute_plan"}],
        "steps_template": {
            "SERVICE_CONTROL": {"step_key": "s2", "action_type": "SERVICE_CONTROL",
                                "dependencies": ["no-such-step"]},
        },
    }
    monkeypatch.setattr(ac, "FLOW_GUIDES", {"x-flow": flow})
    db, ctx = _live_registry_env(monkeypatch)
    try:
        report = ac.validate_capability_sequence(db, ctx)
        assert not report["ok"]
        assert any("no-such-step" in v for v in report["violations"])
    finally:
        db.close()


def test_pipeline_flow_guide_ids_valid(monkeypatch):
    """层5：pipelines.flow_guide_id 合法性（fixture 库内自建数据验证）。

    生产库回填验证见 scripts/backfill_pipeline_flow_guide.py 的 live probe；
    契约层只测校验逻辑本身。
    """
    from app.db.base import SessionLocal
    from app.db.models import Pipeline
    from app.services.agent_context import FLOW_GUIDES, validate_capability_sequence

    db, ctx = _live_registry_env(monkeypatch)  # registry ctx；db 用 fixture 库
    # _live_registry_env 返回的 db 也是 fixture 库（同 DATABASE_URL）——直接用
    try:
        # 干净库：无 pipeline → 层5 不违规
        report = validate_capability_sequence(db, ctx)
        assert report["ok"], report["violations"]
        # 插入合法关联 → 仍绿
        db.add(Pipeline(id="t-pipe-1", name="t-前端", system_name="crypto-trader",
                        flow_guide_id="frontend-release"))
        db.commit()
        report = validate_capability_sequence(db, ctx)
        assert report["ok"], report["violations"]
        # 乱指 → 层5 违规
        p = db.query(Pipeline).filter(Pipeline.id == "t-pipe-1").first()
        p.flow_guide_id = "no-such-flow"
        db.commit()
        report = validate_capability_sequence(db, ctx)
        assert not report["ok"]
        assert any("no-such-flow" in v for v in report["violations"])
    finally:
        db.query(Pipeline).filter(Pipeline.id == "t-pipe-1").delete()
        db.commit()
        db.close()


def test_pipeline_flow_guide_id_dangling_detected(monkeypatch):
    """层5：flow_guide_id 指向不存在的 flow → 违规暴露（软引用完整性）。"""
    from app.services import agent_context as ac

    class _FakePipeline:
        name = "dangling-pipeline"
        flow_guide_id = "no-such-flow"

    class _FakeQuery:
        def all(self):
            return [_FakePipeline()]

    class _FakeDB:
        def query(self, model):
            return _FakeQuery()

    flow = {
        "steps": [{"n": 1, "tool": "ops.approval.prepare_plan"},
                  {"n": 2, "tool": "(matrix 回复)"},
                  {"n": 3, "tool": "ops.approval.execute_plan"}],
    }
    monkeypatch.setattr(ac, "FLOW_GUIDES", {"x-flow": flow})
    db, ctx = _live_registry_env(monkeypatch)
    try:
        report = ac.validate_capability_sequence(_FakeDB(), ctx)
        assert not report["ok"]
        assert any("no-such-flow" in v for v in report["violations"])
    finally:
        db.close()
