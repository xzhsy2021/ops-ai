"""回滚后健康检查与"回滚成功"判定口径（2026-09-12 复盘第 10 轮）。

本轮修出的三个真实缺陷（都在回滚链路上）：

 1. **进程探针假通过**：`build_rollback_health_commands` 生成的进程检查是
    ``pgrep -af <kw> | head -3 >/dev/null``。管道退出码取最后一个命令（head 恒为 0），
    因此**进程不存在时探针也返回 0**——进程存活检查形同虚设，服务已死仍显示"健康检查通过"。
 2. **回滚健康检查结果被丢弃**：`RollbackRuntime.run` 调用了 `run_health_checks(...)`
    但不看返回值（类型声明却是 `-> bool`）。于是"回滚命令执行成功、但服务没起来"
    仍被记为 `success`，任务与发布单都是成功，还发出 `rollback.success` 通知；
    而正向发布的 `HealthCheckStep` 在健康检查失败时是直接让发布失败的（口径不一致）。
 3. **回滚健康检查没有重试**：正向发布默认重试 3 次 / 间隔 5s，回滚只探测一次，
    服务启动稍慢就会被判为不健康（这也是此前不敢把探测结果纳入判定的原因之一）。

本文件同时补齐 `scripts/ci_local.sh` 聚焦清单里早就列出、但仓库中一直缺失的
`tests/test_rollback_health.py`。
"""
from __future__ import annotations

import asyncio
import os
import shutil
import subprocess

import pytest

from app.deploy.rollback import RollbackRuntime
from app.deploy.rollback_health import build_rollback_health_commands


# ---------------------------------------------------------------------------
# 1. 探针命令本身：必须"服务不健康时返回非 0"
# ---------------------------------------------------------------------------

def _cmd(commands, label):
    for name, command in commands:
        if name == label:
            return command
    return ""


def test_process_probe_does_not_mask_exit_code_with_a_pipe():
    """进程探针不能把 pgrep 的退出码交给管道末端的 head（恒为 0）。"""
    commands = build_rollback_health_commands({"process_keyword": "crypto-trader"})
    probe = _cmd(commands, "process")
    assert probe, "应生成进程探针"
    assert probe.startswith("pgrep "), probe
    assert "|" not in probe, f"进程探针不得包含管道（退出码会被 head 遮蔽）：{probe}"
    assert "head" not in probe, probe


def test_http_and_path_probes_keep_direct_exit_codes():
    commands = build_rollback_health_commands({"health_url": "http://127.0.0.1:8000/health"})
    assert _cmd(commands, "http") == "curl -fsS --max-time 10 http://127.0.0.1:8000/health >/dev/null"
    assert "|" not in _cmd(commands, "http")

    commands = build_rollback_health_commands({"deploy_path": "/opt/app"})
    assert _cmd(commands, "path") == "test -d /opt/app"

    # health_cmd 优先且独占
    commands = build_rollback_health_commands({"health_cmd": "systemctl is-active app", "health_url": "http://x/health"})
    assert commands == [("custom", "systemctl is-active app")]


def test_no_topology_fields_means_no_checks():
    assert build_rollback_health_commands({}) == []


_BASH_CANDIDATES = (
    "bash",
    "sh",
    r"C:\Program Files\Git\bin\bash.exe",
    r"C:\Program Files\Git\usr\bin\bash.exe",
    "/bin/bash",
    "/bin/sh",
)


def _find_posix_shell():
    for candidate in _BASH_CANDIDATES:
        found = shutil.which(candidate) or (candidate if os.path.exists(candidate) else None)
        if found:
            return found
    return None


_BASH = _find_posix_shell()


@pytest.mark.skipif(not _BASH, reason="需要 POSIX shell 验证管道退出码语义")
def test_pipe_masking_is_real_and_unpiped_probe_detects_failure():
    """行为层证据：管道会把退出码换成末端命令的；去掉管道后失败才能传播。"""
    masked = subprocess.run([_BASH, "-c", "false | head -3 >/dev/null"], capture_output=True)
    assert masked.returncode == 0, "管道末端 head 的退出码恒为 0——这正是原探针假通过的原因"

    propagated = subprocess.run([_BASH, "-c", "false >/dev/null"], capture_output=True)
    assert propagated.returncode != 0, "去掉管道后失败退出码必须传播（修复后的探针据此判定失败）"

    # 进程不存在：pgrep 返回 1（无匹配），若环境没有 pgrep 则 127（命令不存在）——
    # 两种都是非 0，对健康探针而言都应判为不健康（保守正确）。
    missing = subprocess.run([_BASH, "-c", "pgrep -af ops-no-such-process-xyz >/dev/null 2>&1"], capture_output=True)
    assert missing.returncode != 0


# ---------------------------------------------------------------------------
# 真实实现：_log_rollback_health_commands 的重试与返回口径
# ---------------------------------------------------------------------------

class _ProbeSSH:
    """按脚本返回退出码的假 SSH：exits 逐次弹出，用尽后沿用最后一个。"""

    def __init__(self, exits):
        self.exits = list(exits)
        self.calls = []

    def exec(self, command, timeout=None):
        self.calls.append(command)
        code = self.exits.pop(0) if len(self.exits) > 1 else self.exits[0]
        return code, "", f"exit={code}"


def _health_module(monkeypatch):
    from app.api.deploy import _shared

    logs = []
    monkeypatch.setattr(_shared, "_log_to_db", lambda task_id, level, message, scope, deployment_id: logs.append((level, message)))
    return _shared, logs


def test_real_checker_retries_until_probe_passes(monkeypatch):
    """服务启动稍慢：前两次探测失败、第三次通过 → 判定健康（不误报失败）。"""
    shared, logs = _health_module(monkeypatch)
    ssh = _ProbeSSH([1, 1, 0])
    ok = asyncio.run(shared._log_rollback_health_commands(
        "task-1", "dep-1", "srv-a", ssh, {"process_keyword": "app"}, attempts=3, interval=0,
    ))
    assert ok is True
    assert len(ssh.calls) == 3, ssh.calls
    assert any("健康检查重试" in msg for _, msg in logs), logs


def test_real_checker_fails_after_exhausting_retries(monkeypatch):
    """重试耗尽仍失败 → 返回 False 并留下 warning 日志。"""
    shared, logs = _health_module(monkeypatch)
    ssh = _ProbeSSH([1])
    ok = asyncio.run(shared._log_rollback_health_commands(
        "task-1", "dep-1", "srv-a", ssh, {"process_keyword": "app"}, attempts=2, interval=0,
    ))
    assert ok is False
    assert len(ssh.calls) == 2, "应按 attempts 次数探测"
    assert any(level == "warning" and "Rollback health check failed" in msg for level, msg in logs), logs


def test_real_checker_without_probes_is_informational_only(monkeypatch):
    """未配置探针：返回 True（不影响成功口径），但留下建议配置的 warning。"""
    shared, logs = _health_module(monkeypatch)
    ssh = _ProbeSSH([1])
    ok = asyncio.run(shared._log_rollback_health_commands("task-1", "dep-1", "srv-a", ssh, {}))
    assert ok is True
    assert ssh.calls == [], "没有探针就不该执行任何命令"
    assert any(level == "warning" and "未配置回滚后健康检查" in msg for level, msg in logs), logs


# ---------------------------------------------------------------------------
# 2/3. RollbackRuntime：健康检查结果必须参与"回滚成功"的判定
# ---------------------------------------------------------------------------

class _TaskRepo:
    def __init__(self, db):
        self.db = db

    def update_status(self, task_id, status, **kwargs):
        self.db.task_status.append((task_id, status, kwargs.get("result")))


class _DeployRepo:
    def __init__(self, db):
        self.db = db

    def get_by_id(self, deployment_id):
        return self.db.deployment

    def update_status(self, deployment_id, status, message=""):
        self.db.deployment_status.append((deployment_id, status, message))
        if self.db.deployment is not None:
            self.db.deployment.status = status


class _Deployment:
    id = "dep-1"
    system = "crypto-trader"
    service = "web"
    environment = "prod"
    version = "v1.2.3"
    servers = "srv-a, srv-b"
    status = "success"


class _FakeSSH:
    def __init__(self, deploy_exit=0, health_exit=0):
        self.deploy_exit = deploy_exit
        self.health_exit = health_exit
        self.commands = []
        self.closed = False

    def exec(self, command, timeout=None):
        self.commands.append(command)
        if "rollback-command" in command:
            return self.deploy_exit, "rolled back", ""
        return self.health_exit, "", "unhealthy"

    def close(self):
        self.closed = True


class _Env:
    def __init__(self, *, deploy_exit=0, health_result=True, health_awaitable=False):
        self.task_status = []
        self.deployment_status = []
        self.deployment = _Deployment()
        self.notifications = []
        self.released_locks = []
        self.logs = []
        self.ssh = _FakeSSH(deploy_exit=deploy_exit)
        self._health_result = health_result
        self._health_awaitable = health_awaitable

    # --- rollback runtime deps ---
    def session_factory(self):
        return self

    def task_repo_cls(self, db):
        return _TaskRepo(db)

    def deployment_repo_cls(self, db):
        return _DeployRepo(db)

    def deploy_request_cls(self, **kwargs):
        return type("Req", (), kwargs)()

    def get_server_by_name(self, name):
        return {"name": name}

    def connect_ssh(self, srv):
        return self.ssh

    def log_to_db(self, task_id, level, message, scope, deployment_id):
        self.logs.append((level, message, scope))

    def merge_release_variables(self, req, db):
        return {}

    def rollback_plan_for(self, *args):
        return {"mode": "binary_bak", "safe": True, "command": "rollback-command", "description": "还原上一版本"}

    def service_topology(self, *args):
        return {"process_keyword": "crypto-trader", "service_dir": "/opt/app"}

    def run_health_checks(self, task_id, deployment_id, server_name, ssh, topology):
        if self._health_awaitable:
            async def _run():
                return self._health_result
            return _run()
        return self._health_result

    def send_release_notification(self, event_type, deployment, payload, db):
        self.notifications.append((event_type, dict(payload)))

    def release_deployment_locks(self, lock_keys, db):
        self.released_locks.append(list(lock_keys))

    def close(self):
        pass

    # --- helpers ---
    def runtime(self, **overrides):
        return RollbackRuntime(
            session_factory=self.session_factory,
            task_repo_cls=self.task_repo_cls,
            deployment_repo_cls=self.deployment_repo_cls,
            deploy_request_cls=self.deploy_request_cls,
            get_server_by_name=self.get_server_by_name,
            connect_ssh=self.connect_ssh,
            log_to_db=self.log_to_db,
            merge_release_variables=self.merge_release_variables,
            rollback_plan_for=self.rollback_plan_for,
            service_topology=self.service_topology,
            run_health_checks=overrides.get("run_health_checks", self.run_health_checks),
            send_release_notification=self.send_release_notification,
            release_deployment_locks=self.release_deployment_locks,
        )


def _run(env, **kwargs):
    return asyncio.run(env.runtime(**kwargs).run("task-1", "dep-1", lock_keys=["deploy:crypto-trader"], notification_context={"via": "web"}))


def test_health_failure_means_rollback_is_not_reported_as_success():
    """核心回归：回滚命令成功但健康检查未通过 → 不得记为 success/rollback.success。"""
    env = _Env(health_result=False)
    ok = _run(env)

    assert ok is False, "健康检查未通过时 run() 必须返回 False"
    assert env.task_status[-1][1] == "failed", env.task_status
    assert env.deployment_status[-1][1] == "failed", env.deployment_status
    assert "健康检查未通过" in env.task_status[-1][2]
    assert env.notifications[-1][0] == "rollback.failed", env.notifications
    assert env.notifications[-1][1]["health_failed_servers"] == ["srv-a", "srv-b"]
    assert env.released_locks == [["deploy:crypto-trader"]], "锁必须释放"


def test_health_success_keeps_rollback_success():
    env = _Env(health_result=True)
    ok = _run(env)

    assert ok is True
    assert env.task_status[-1][1] == "success"
    assert env.deployment_status[-1][1] == "success"
    assert env.notifications[-1][0] == "rollback.success"
    assert env.notifications[-1][1]["health_checked_servers"] == ["srv-a", "srv-b"]
    assert env.notifications[-1][1]["health_failed_servers"] == []
    assert env.notifications[-1][1]["via"] == "web", "调用方上下文必须保留"


def test_awaitable_health_checker_is_supported():
    """async 实现（带重试的真实实现）必须被 await。"""
    env = _Env(health_result=False, health_awaitable=True)
    ok = _run(env)
    assert ok is False
    assert env.notifications[-1][1]["health_failed_servers"] == ["srv-a", "srv-b"]


def test_no_health_configuration_still_counts_as_success():
    """未配置任何健康探针（返回 True）时不得影响原有成功口径。"""
    env = _Env()
    env.service_topology = lambda *a: {}
    ok = _run(env)
    assert ok is True
    assert env.notifications[-1][0] == "rollback.success"


def test_command_failure_still_reported_as_failed_without_health_noise():
    env = _Env(deploy_exit=1, health_result=True)
    ok = _run(env)
    assert ok is False
    assert env.task_status[-1][1] == "failed"
    assert env.task_status[-1][2] == "Rollback failed"
    assert env.notifications[-1][1]["health_failed_servers"] == []
