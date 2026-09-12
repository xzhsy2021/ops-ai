"""Small, low-risk rollback health helpers.

The deployment API still owns orchestration, locks and audit.  This module keeps
post-rollback health probing in one place so deploy_v2.py can stay focused on
request/worker flow.
"""
from __future__ import annotations

import shlex
from typing import Any, Dict, List, Tuple


def build_rollback_health_commands(topology: Dict[str, Any]) -> List[Tuple[str, str]]:
    """Return labelled shell probes to run after a rollback.

    The commands are intentionally best-effort and read-only.  A failed probe
    should be logged clearly, but should not by itself mutate remote state.

    All probes must **fail (non-zero) when the service is unhealthy** — that is
    the whole point of the probe. See the process probe below for a pitfall that
    made it always pass.
    """
    commands: List[Tuple[str, str]] = []
    health_cmd = str(topology.get("health_cmd") or topology.get("health_command") or "").strip()
    if health_cmd:
        commands.append(("custom", health_cmd))
        return commands

    health_url = str(topology.get("health_url") or "").strip()
    if health_url:
        commands.append(("http", f"curl -fsS --max-time 10 {shlex.quote(health_url)} >/dev/null"))

    process_keyword = str(topology.get("process_keyword") or "").strip()
    if process_keyword:
        quoted = shlex.quote(process_keyword)
        # 2026-09-12 复盘第 10 轮：原命令是
        #   `pgrep -af <kw> | head -3 >/dev/null`
        # 管道退出码取最后一个命令（head 恒为 0），因此**进程不存在时探针也返回 0**，
        # 进程存活检查形同虚设（服务已死仍显示"健康检查通过"）。
        # 这里去掉管道，直接用 pgrep 自身的退出码（无匹配时返回 1）。
        commands.append(("process", f"pgrep -af {quoted} >/dev/null 2>&1"))

    service_dir = str(topology.get("service_dir") or topology.get("deploy_path") or "").strip()
    if service_dir:
        quoted_dir = shlex.quote(service_dir)
        commands.append(("path", f"test -d {quoted_dir}"))

    return commands
