"""前端时间渲染契约（2026-09-12 复盘修复）。

后端落库时间戳统一 naive UTC，浏览器把这种串当本地时间解析 → UTC+8 下所有页面
比真实时间少 8 小时（相对时间把刚发生的事说成"8 小时前"）。修复方式：

* ``frontend/src/utils/datetime.js``（+ ``.d.ts``）统一补 ``Z`` 后解析；
* 审计/工具/任务/文件/巡检等展示后端时间戳的页面改用该工具；
* 文件系统来源的时间（SFTP mtime、备份文件时间、epoch 秒）保持原样。

这里跑 node 单测作为行为证据（node 不可用时跳过），并做最小源码级防回归。
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
NODE = shutil.which("node")

# 展示数据库时间戳、必须走统一解析的页面/组件
SHARED_UTIL_CONSUMERS = (
    "src/pages/AuditLogPage.tsx",
    "src/pages/McpAuditPage.tsx",
    "src/pages/TaskCenterPage.tsx",
    "src/pages/FileCenterPage.tsx",
    "src/pages/inspection/inspectionHelpers.ts",
    "src/pages/tools/ToolAuditTimeline.tsx",
    "src/components/workspace/GanttStrip.tsx",
)


def test_shared_datetime_util_treats_naive_as_utc():
    source = (FRONTEND / "src" / "utils" / "datetime.js").read_text(encoding="utf-8")
    assert "export function parseBackendTime" in source
    assert "Z`" in source or "}Z`" in source, "必须给无时区的 naive 串补 Z"
    assert (FRONTEND / "src" / "utils" / "datetime.d.ts").exists()


def test_timestamp_rendering_pages_use_shared_util():
    missing = []
    for rel in SHARED_UTIL_CONSUMERS:
        text = (FRONTEND / rel).read_text(encoding="utf-8")
        if "utils/datetime.js" not in text:
            missing.append(rel)
    assert not missing, f"这些文件未使用统一时间工具: {missing}"


def test_no_raw_backend_timestamp_parsing_in_pages():
    """审计/工具/任务三页不得再出现裸 new Date(后端字段)。"""
    offenders = []
    for rel in ("src/pages/AuditLogPage.tsx", "src/pages/McpAuditPage.tsx",
                "src/pages/TaskCenterPage.tsx", "src/pages/tools/ToolAuditTimeline.tsx"):
        text = (FRONTEND / rel).read_text(encoding="utf-8")
        if "new Date(" in text:
            offenders.append(rel)
    assert not offenders, f"仍存在裸 new Date 解析后端时间戳: {offenders}"


@pytest.mark.skipif(NODE is None, reason="node 不可用，跳过前端单测")
def test_frontend_datetime_unit_tests_pass():
    result = subprocess.run(
        [NODE, "--test", "./tests/datetime.test.js"],
        cwd=str(FRONTEND), capture_output=True, text=True, timeout=300,
    )
    assert result.returncode == 0, f"前端时间工具单测失败:\n{result.stdout}\n{result.stderr}"
