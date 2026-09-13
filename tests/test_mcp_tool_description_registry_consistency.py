"""MCP 工具名一致性守卫（2026-09-12 复盘第 13 轮）。

背景：MCP 工具面收窄（见 705f15f "slim default MCP tool catalog"）之后，被移除/改名的工具
仍然散落在**数据与文案**里，形成一批"幽灵引用"——它们不会报错，只会静默失效或误导 AI：

  - 描述覆盖表 `MCP_TOOL_DESCRIPTION_OVERRIDES` 180 条里有 54 条指向不存在的工具（30%），
    其中 `ops.generate_report` 那条还被 `test_ai_analysis_retirement.py` 当作契约锁着，
    于是"报告描述不得提及已退役的 AI 分析"这条契约实际上锁在死数据上；
  - 巡检工具返回的 `next_actions` 与工具定义里的 `related_tools` 建议 AI 调用不存在的工具
    （`ops.inspection.get_run` / `ops.inspection.summarize_run` / `ops.inspection.list_issues` …），
    AI 按建议调用必然失败；
  - MCP prompts（`ops_db_export_request`、`ops_backup_workflow`）与 `app/agent/prompts/*.md`
    把 AI 引向不存在的 DML / 备份工具；
  - `GET /api/v2/mcp/tools/recommend?scenario=inspection` 的推荐列表里含不存在的工具。

本文件把"工具名引用必须指向真实注册工具"变成可执行契约，防止再次漂移。

豁免规则：形如 `ops.inspection.*` 的通配写法不是具体工具名，跳过；
`app/services/risk_policy.py` 里**明确登记**的兼容工具名见 `UNREGISTERED_TOOL_COMPATIBILITY`。
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from app.services.mcp_capability_service import (
    MCP_TOOL_DESCRIPTION_OVERRIDES,
    mcp_prompt_get,
    mcp_prompt_items,
)
from app.services.tool_registry import register_builtin_tools, registry

REPO_ROOT = Path(__file__).resolve().parents[1]

TOOL_REF_RE = re.compile(r"\bops(?:\.[A-Za-z0-9_*]+)+")
# 真实工具名的形状（全部小写）。用于排除形如 annotations["ops.originalToolName"] 的
# 命名空间注解键，以及 `ops.inspection.*` 这类通配写法。
TOOL_NAME_SHAPE_RE = re.compile(r"^ops(?:\.[a-z0-9_]+)+$")

# 这些源文件/目录里出现的工具名都必须是真实注册工具（risk_policy 有独立的兼容清单测试）。
SCANNED_SOURCES = (
    "app/services/mcp_capability_service.py",
    "app/services/tool_adapters",
    "app/api/tools.py",
)


@pytest.fixture(scope="module", autouse=True)
def _registered():
    register_builtin_tools()


def _registered_tools() -> set[str]:
    return set(getattr(registry, "_tools", {}) or {})


def _refs_in_text(text: str) -> set[str]:
    """从任意文本里抽出具体的工具名引用（跳过通配写法与非工具名的注解键）。"""
    found: set[str] = set()
    for match in TOOL_REF_RE.finditer(text or ""):
        name = match.group(0)
        if "*" in name or not TOOL_NAME_SHAPE_RE.match(name):
            continue
        found.add(name)
    return found


def _string_constants(path: Path) -> list[str]:
    """AST 取一个 .py 文件里的全部字符串常量（注释与文档字符串以外的字面量也算）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    values: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            values.append(node.value)
    return values


def _iter_python_sources() -> list[Path]:
    paths: list[Path] = []
    for rel in SCANNED_SOURCES:
        target = REPO_ROOT / rel
        if target.is_dir():
            paths.extend(sorted(p for p in target.rglob("*.py") if "__pycache__" not in p.parts))
        elif target.exists():
            paths.append(target)
    return paths


# --------------------------------------------------------------------------- 描述覆盖表

def test_registered_tool_names_follow_lowercase_shape():
    """注册工具名必须是小写点分名 —— 本文件的工具名识别依赖这个不变量。"""
    offenders = sorted(name for name in _registered_tools() if not TOOL_NAME_SHAPE_RE.match(name))
    assert offenders == [], (
        "以下工具名不符合 `ops.a.b` 小写形状：\n  " + "\n  ".join(offenders)
    )


def test_override_table_has_no_orphan_entries():
    """覆盖表里不允许存在"没有对应注册工具"的条目（第 13 轮清理了 54 条）。"""
    registered = _registered_tools()
    orphans = sorted(name for name in MCP_TOOL_DESCRIPTION_OVERRIDES if name not in registered)
    assert orphans == [], (
        "以下描述覆盖条目指向未注册的工具（永远不会被 AI 看到，属死数据）：\n  "
        + "\n  ".join(orphans)
    )


def test_override_table_covers_every_registered_tool():
    """每个注册工具都要有精选描述，否则 AI 只能看到工具自带描述甚至样板句。"""
    registered = _registered_tools()
    missing = sorted(name for name in registered if name not in MCP_TOOL_DESCRIPTION_OVERRIDES)
    assert missing == [], (
        "以下已注册工具缺少 MCP 描述覆盖：\n  " + "\n  ".join(missing)
    )


# --------------------------------------------------------------------------- 工具定义 / 适配器源码

def test_related_tools_of_registered_tools_are_registered():
    """工具定义里的 `related_tools` 是给 AI 看的"相关工具"提示，必须真实存在。"""
    registered = _registered_tools()
    offenders: list[str] = []
    for name, tool in sorted(getattr(registry, "_tools", {}).items()):
        payload = tool.to_dict() if hasattr(tool, "to_dict") else dict(getattr(tool, "__dict__", {}))
        for related in payload.get("related_tools") or []:
            if related not in registered:
                offenders.append(f"{name} → {related}")
    assert offenders == [], (
        "以下 related_tools 指向未注册的工具：\n  " + "\n  ".join(offenders)
    )


def test_tool_sources_only_reference_registered_tools():
    """工具适配器与 MCP 能力层源码里的工具名引用必须指向真实注册工具。"""
    registered = _registered_tools()
    offenders: list[str] = []
    for path in _iter_python_sources():
        for text in _string_constants(path):
            for ref in _refs_in_text(text):
                if ref not in registered:
                    offenders.append(f"{path.relative_to(REPO_ROOT).as_posix()} → {ref}")
    assert offenders == [], (
        "以下源码引用了未注册的工具名（AI 会被引向不存在的工具）：\n  " + "\n  ".join(sorted(set(offenders)))
    )


# --------------------------------------------------------------------------- MCP prompts / agent prompts

def test_mcp_prompt_texts_only_reference_registered_tools():
    """MCP prompts 是 AI 的行动指南，里面出现的工具名必须真实存在。"""
    registered = _registered_tools()
    offenders: list[str] = []
    for item in mcp_prompt_items():
        prompt_name = item.get("name") or ""
        args = {a.get("name"): "sample" for a in (item.get("arguments") or []) if a.get("name")}
        payload = mcp_prompt_get({"name": prompt_name, "arguments": args})
        texts = [
            (message.get("content") or {}).get("text") or ""
            for message in (payload.get("messages") or [])
        ]
        for ref in _refs_in_text(" ".join(texts)):
            if ref not in registered:
                offenders.append(f"{prompt_name} → {ref}")
    assert offenders == [], (
        "以下 MCP prompt 引用了未注册的工具：\n  " + "\n  ".join(sorted(set(offenders)))
    )


def test_agent_prompt_markdown_only_references_registered_tools():
    """`app/agent/prompts/*.md` 是宿主机 agent 的工作流提示，工具名同样必须真实存在。"""
    registered = _registered_tools()
    prompt_dir = REPO_ROOT / "app" / "agent" / "prompts"
    offenders: list[str] = []
    for path in sorted(prompt_dir.glob("*.md")):
        for ref in _refs_in_text(path.read_text(encoding="utf-8")):
            if ref not in registered:
                offenders.append(f"{path.name} → {ref}")
    assert offenders == [], (
        "以下 agent prompt 文档引用了未注册的工具：\n  " + "\n  ".join(sorted(set(offenders)))
    )


# --------------------------------------------------------------------------- 风险策略兼容清单

def test_risk_policy_unregistered_references_are_explicitly_registered():
    """risk_policy 允许保留未注册工具的确认短语，但必须显式登记在兼容清单里。"""
    from app.services import risk_policy

    registered = _registered_tools()
    allowlist = getattr(risk_policy, "UNREGISTERED_TOOL_COMPATIBILITY", None)
    assert allowlist is not None, (
        "risk_policy 缺少 UNREGISTERED_TOOL_COMPATIBILITY 兼容清单："
        "未注册工具名必须显式登记，避免再次出现来历不明的幽灵工具名。"
    )
    allowlist = set(allowlist)

    source = REPO_ROOT / "app" / "services" / "risk_policy.py"
    ghosts = {ref for ref in _refs_in_text(" ".join(_string_constants(source))) if ref not in registered}
    undeclared = sorted(ghosts - allowlist)
    assert undeclared == [], (
        "risk_policy 引用了未注册且未登记的工具名：\n  " + "\n  ".join(undeclared)
    )

    stale = sorted(name for name in allowlist if name in registered)
    assert stale == [], (
        "以下工具已经重新注册，应从兼容清单里移除：\n  " + "\n  ".join(stale)
    )
