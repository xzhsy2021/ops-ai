"""MCP 工具描述覆盖契约（2026-09-12 复盘第 9 轮）。

背景：`MCP_TOOL_DESCRIPTION_OVERRIDES` 是 AI 客户端（MCP）看到的工具描述来源。
`english_tool_description()` 在"没有覆盖条目"时会**丢弃工具自身的描述**，
回退成样板句 ``OPS capability tool <name>. Category: … Risk: ….`` ——
工具"做什么、什么时候用"的信息全部丢失。本轮发现 12 个已注册工具（安全模块、
安全日报、集成接入、作业等待）就处于这种状态：注册表里明明有完整的中文描述，
AI 却只看得到样板句，无法按用户意图发现这些能力。

本文件锁定两条契约：
 1. 任何已注册工具，其 MCP 可见描述都不得退化为样板句；
 2. 没有覆盖条目但工具自带描述时，必须使用工具自带描述（而不是样板句）。
"""
from __future__ import annotations

import pytest

from app.services.mcp_capability_service import (
    MCP_TOOL_DESCRIPTION_OVERRIDES,
    english_tool_description,
    mcp_tool_payload,
    to_mcp_tool_name,
)
from app.services.tool_registry import register_builtin_tools, registry

BOILERPLATE_MARK = "OPS capability tool "


@pytest.fixture(scope="module", autouse=True)
def _registered():
    register_builtin_tools()


def _all_tools() -> dict:
    return dict(registry._tools)


def test_every_registered_tool_has_meaningful_mcp_description():
    """所有已注册工具都必须有语义化描述，不允许落到样板句。"""
    offenders = []
    for name, tool in _all_tools().items():
        payload = mcp_tool_payload(tool.to_dict() if hasattr(tool, "to_dict") else dict(getattr(tool, "__dict__", {})))
        desc = str(payload.get("description") or "")
        if not desc.strip() or desc.startswith(BOILERPLATE_MARK):
            offenders.append(name)
    assert offenders == [], (
        "以下已注册工具的 MCP 描述退化为样板句，AI 无法按意图发现它们：\n  "
        + "\n  ".join(sorted(offenders))
    )


def test_tools_with_own_description_keep_their_semantics_without_override():
    """无覆盖条目时使用工具自带描述（回归：此前被样板句覆盖）。"""
    tool = {
        "name": "ops.demo.some_tool",
        "category": "demo",
        "risk": "low",
        "description": "Trigger a demo action. Use when the user asks for a demo. 中文: 演示工具/示例操作.",
    }
    desc = english_tool_description(tool, "ops.demo.some_tool", to_mcp_tool_name("ops.demo.some_tool"), {})
    assert desc.startswith("Trigger a demo action")
    assert "演示工具" in desc
    assert BOILERPLATE_MARK not in desc


def test_boilerplate_is_last_resort_when_tool_has_no_description():
    """工具自身也没有描述时才退化为样板句（保证字段非空）。"""
    tool = {"name": "ops.demo.bare", "category": "demo", "risk": "low", "description": ""}
    desc = english_tool_description(tool, "ops.demo.bare", to_mcp_tool_name("ops.demo.bare"), {})
    assert desc.startswith(BOILERPLATE_MARK)
    assert "demo" in desc and "low" in desc


def test_override_entry_still_wins_when_present():
    """覆盖条目优先级最高（人工精选描述不被工具自带描述挤掉）。"""
    tool = {"name": "ops.demo.x", "description": "工具自带描述", "category": "demo", "risk": "low"}
    desc = english_tool_description(tool, "ops.demo.x", to_mcp_tool_name("ops.demo.x"), {"ops.demo.x": "Curated text. 中文: 精选."})
    assert desc.startswith("Curated text")


def test_security_and_integration_tools_have_curated_overrides():
    """本轮补齐的 12 条覆盖必须存在且包含中文关键词（便于中文意图匹配）。"""
    expected = [
        "ops.security_report.collect",
        "ops.security_report.get_daily_report",
        "ops.security_report.summarize",
        "ops.security_module.probe",
        "ops.security_module.install",
        "ops.security_module.setup",
        "ops.inspection.run_security_daily",
        "ops.wait_job",
        "ops.integration.get_context_pack",
        "ops.integration.get_flow_guide",
        "ops.integration.get_heartbeat_ops",
        "ops.integration.save_lesson",
    ]
    missing = [name for name in expected if name not in MCP_TOOL_DESCRIPTION_OVERRIDES]
    assert missing == [], f"缺少描述覆盖：{missing}"
    for name in expected:
        assert "中文" in MCP_TOOL_DESCRIPTION_OVERRIDES[name], f"{name} 的覆盖描述缺少中文关键词"
