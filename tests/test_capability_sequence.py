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


def test_all_flow_tools_in_real_registry():
    """CI 漂移检测：全部流程步骤工具名必须在真 registry。

    与 validate_capability_annotations 同思路——名称 join 的地基。
    """
    import os

    def _env(n):
        for line in open(Path(__file__).resolve().parent.parent / ".env", encoding="utf-8"):
            line = line.strip()
            if line.startswith(n + "="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
        return ""

    os.environ.setdefault("APPROVAL_SIGNING_KEY", _env("APPROVAL_SIGNING_KEY"))
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


def test_capability_sequence_in_pack():
    """pack.flows[] 携带 capability_sequence（生产组装验证）。"""
    import os

    def _env(n):
        for line in open(Path(__file__).resolve().parent.parent / ".env", encoding="utf-8"):
            line = line.strip()
            if line.startswith(n + "="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
        return ""

    os.environ.setdefault("APPROVAL_SIGNING_KEY", _env("APPROVAL_SIGNING_KEY"))
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
