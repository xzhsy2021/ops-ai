"""Dovo 蓝绿执行器契约测试。

Mock SSH 层验证六段式序列：
- 探测反查 active/standby（瞬态判定）
- binupdate 失败 → 中止（端口不动）
- 验证失败 → 中止
- 切换复验失败 → 自动回滚（旧 active 目录 portupdate）
- 全链成功路径
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.blue_green import (  # noqa: E402
    BGConfig,
    BGStepResult,
    execute_blue_green_update,
    _probe_active,
)

CFG = {
    "found": True,
    "template_variables": {
        "template": "blue_green",
        "bg_base_dir": "/data/bin/ata",
        "bg_dirs": ["pak1", "pak2"],
        "bg_port_map": {"pak1": ":8001", "pak2": ":8002"},
        "bg_config_file": "/data/bin/ata/config/global.yml",
        "bg_update_script": "./binupdate.sh",
        "bg_port_script": "./portupdate.sh",
        "bg_pm2_pattern": "{dir}.f",
        "bg_package_name": "server.zip",
        "bg_log_dir": "/root/.pm2/logs",
    },
}


class FakeSSH:
    """脚本化 SSH：按命令返回预设输出，记录调用序列。

    exec_handler 可被整体替换（测试内赋值 ssh.exec_handler = fn），
    记录逻辑恒生效。
    """

    def __init__(self, script: dict):
        self.script = script
        self.calls = []
        self.exec_handler = self._default_exec

    def _default_exec(self, cmd):
        for pattern, ret in self.script.items():
            if pattern in cmd:
                return ret
        return (0, "", "")

    def exec(self, cmd, timeout=120, **_):
        self.calls.append(cmd)
        return self.exec_handler(cmd)

    def upload(self, local, remote):
        self.calls.append(f"UPLOAD {local} -> {remote}")
        return True

    def close(self):
        pass


def _config_from_cfg():
    return BGConfig.from_service_config(CFG)


def test_bg_config_parses_port_map():
    bg = _config_from_cfg()
    assert bg.port_map == {"pak1": 8001, "pak2": 8002}
    assert bg.dir_path("pak2") == "/data/bin/ata/pak2"
    assert bg.pm2_name("pak1") == "pak1.f"
    assert bg.out_log("pak1") == "/root/.pm2/logs/pak1.f-out.log"


def test_bg_config_rejects_incomplete():
    bad = {"found": True, "template_variables": {"bg_base_dir": "/data/bin/ata"}}
    try:
        BGConfig.from_service_config(bad)
        raise AssertionError("应拒绝不完整配置")
    except ValueError as e:
        assert "bg_dirs" in str(e) or "不完整" in str(e)


def test_probe_active_reverse_lookup():
    ssh = FakeSSH({
        "cat /data/bin/ata/config/global.yml": (
            0, "task_port: :8001\nfront_version: 20260901160205\n", ""),
    })
    probe = _probe_active(ssh, _config_from_cfg())
    assert probe.active == "pak1"
    assert probe.standby == "pak2"
    assert probe.task_port == 8001


def test_probe_rejects_port_out_of_map():
    ssh = FakeSSH({
        "cat": (0, "task_port: :9999\n", ""),
    })
    try:
        _probe_active(ssh, _config_from_cfg())
        raise AssertionError("端口不在映射内应报配置漂移")
    except RuntimeError as e:
        assert "9999" in str(e)


class _Ctx:
    username = "test"
    token_owner = "test"


def _patch_service_config(monkeypatch, cfg=CFG):
    from app.services import blue_green as bg_mod
    monkeypatch.setattr(
        bg_mod, "get_service_config", lambda params, ctx, db: cfg)


def _patch_connect(monkeypatch, ssh):
    from app.services import blue_green as bg_mod
    monkeypatch.setattr(bg_mod, "_connect", lambda key: (ssh, {"name": key}))
    monkeypatch.setattr(
        bg_mod, "_check_remote_dir_exists",
        lambda ssh_, path: (True, ""))


def _jlist(dir_name, pid, status="online"):
    import json
    return (0, json.dumps([
        {"name": f"{dir_name}.f", "pm_id": 1, "pid": pid,
         "status": status, "restart_time": 10},
        {"name": "pm2-logrotate", "pm_id": 0, "pid": 999, "status": "online"},
    ]), "")


def test_full_success_path(monkeypatch):
    """全链成功：探测→上传→binupdate→验证→portupdate→复验通过。"""
    state = {"task_port": ":8001", "pak2_pid": 100, "err_size": 100}
    import time as _t
    now = int(_t.time())

    def cat_global(cmd, timeout=15, **_):
        return (0, f"task_port: {state['task_port']}\n", "")

    ssh = FakeSSH({})
    def _e(cmd, timeout=15, **_):  # noqa: E731
        if "cat" in cmd and "global.yml" in cmd:
            return (0, f"task_port: {state['task_port']}\n", "")
        if "pm2 jlist" in cmd:
            return _jlist("pak2", state["pak2_pid"])
        if "stat -c %Y" in cmd and "-out.log" in cmd:
            return (0, str(now), "")
        if "stat -c %s" in cmd:
            return (0, str(state["err_size"]), "")
        if "binupdate.sh" in cmd:
            state["pak2_pid"] += 500  # pm2 restart → pid 换代
            return (0, "binupdate ok", "")
        if "portupdate.sh" in cmd:
            state["task_port"] = ":8002"
            return (0, "portupdate ok", "")
        return (0, "", "")
    ssh.exec_handler = _e
    _patch_service_config(monkeypatch)
    _patch_connect(monkeypatch, ssh)

    import app.services.blue_green as bg_mod
    monkeypatch.setattr(bg_mod, "WAIT_AFTER_UPDATE", 0)
    monkeypatch.setattr(bg_mod, "WAIT_AFTER_SWITCH", 0)
    monkeypatch.setattr(bg_mod, "VERIFY_RECHECK_DELAY", 0)

    res = execute_blue_green_update(
        None, "pak-f", "dovo", "dovo-pak", "C:/pkg/server.zip", _Ctx())
    assert res.ok, f"error={res.error} steps={res.steps}"
    assert res.switched_to == "pak2"
    assert res.rolled_back is False
    # 序列断言：binupdate 在 pak2 目录执行、portupdate 也在 pak2
    assert any("cd /data/bin/ata/pak2 && ./binupdate.sh" in c for c in ssh.calls)
    assert any("cd /data/bin/ata/pak2 && ./portupdate.sh" in c for c in ssh.calls)
    # 上传到了 standby 目录
    assert any(c.startswith("UPLOAD") and "/pak2/server.zip" in c for c in ssh.calls)


def test_binupdate_failure_aborts_without_port_change(monkeypatch):
    """binupdate 失败 → 立即中止，portupdate 从未执行。"""
    import time as _t
    now = int(_t.time())
    ssh = FakeSSH({})
    def _e(cmd, timeout=15, **_):  # noqa: E731
        if "cat" in cmd and "global.yml" in cmd:
            return (0, "task_port: :8001\n", "")
        if "binupdate.sh" in cmd:
            return (1, "", "unzip: cannot find server.zip")
        return (0, "", "")
    ssh.exec_handler = _e
    _patch_service_config(monkeypatch)
    _patch_connect(monkeypatch, ssh)
    import app.services.blue_green as bg_mod
    monkeypatch.setattr(bg_mod, "WAIT_AFTER_UPDATE", 0)

    res = execute_blue_green_update(
        None, "pak-f", "dovo", "dovo-pak", None, _Ctx(), skip_upload=True)
    assert res.ok is False
    assert "binupdate" in res.error or "unzip" in res.error
    # 关键：端口脚本从未执行
    assert not any("portupdate.sh" in c for c in ssh.calls)


def test_verify_pid_not_rotated_aborts(monkeypatch):
    """pm2 pid 未换代（binupdate 内 restart 未生效）→ 验证失败中止。"""
    import time as _t
    now = int(_t.time())
    same_pid = 12345
    def _e(cmd, timeout=15, **_):  # noqa: E731
        if "cat" in cmd and "global.yml" in cmd:
            return (0, "task_port: :8001\n", "")
        if "pm2 jlist" in cmd:
            return _jlist("pak2", same_pid)
        if "binupdate.sh" in cmd:
            return (0, "ok", "")
        return (0, "", "")
    ssh = FakeSSH({})
    ssh.exec_handler = _e
    _patch_service_config(monkeypatch)
    _patch_connect(monkeypatch, ssh)
    import app.services.blue_green as bg_mod
    monkeypatch.setattr(bg_mod, "WAIT_AFTER_UPDATE", 0)

    res = execute_blue_green_update(
        None, "pak-f", "dovo", "dovo-pak", None, _Ctx(), skip_upload=True)
    assert res.ok is False
    assert "pid" in res.error or "换代" in res.error
    assert not any("portupdate.sh" in c for c in ssh.calls)


def test_recheck_failure_triggers_auto_rollback(monkeypatch):
    """切换后复验失败（task_port 没变）→ 自动回滚：旧 active 目录 portupdate。"""
    state = {"task_port": ":8001", "err_size": 100, "pak2_pid": 100}
    import time as _t
    now = int(_t.time())
    def _e(cmd, timeout=15, **_):  # noqa: E731
        if "cat" in cmd and "global.yml" in cmd:
            return (0, f"task_port: {state['task_port']}\n", "")
        if "pm2 jlist" in cmd:
            return _jlist("pak2", state["pak2_pid"])
        if "stat -c %Y" in cmd and "-out.log" in cmd:
            return (0, str(now), "")   # 日志一直在刷（mtime 新）
        if "stat -c %s" in cmd:
            return (0, str(state["err_size"]), "")
        if "binupdate.sh" in cmd:
            state["pak2_pid"] += 500
            return (0, "ok", "")
        if "portupdate.sh" in cmd:
            # pak2 的 portupdate "执行成功"但 task_port 实际没变（异常场景）
            # pak1 的 portupdate（回滚）正常生效
            if "/pak1/" in cmd:
                state["task_port"] = ":8001"
            return (0, "ok", "")
        return (0, "", "")
    ssh = FakeSSH({})
    ssh.exec_handler = _e
    _patch_service_config(monkeypatch)
    _patch_connect(monkeypatch, ssh)
    import app.services.blue_green as bg_mod
    monkeypatch.setattr(bg_mod, "WAIT_AFTER_UPDATE", 0)
    monkeypatch.setattr(bg_mod, "WAIT_AFTER_SWITCH", 0)
    monkeypatch.setattr(bg_mod, "VERIFY_RECHECK_DELAY", 0)
    monkeypatch.setattr(bg_mod, "VERIFY_POLL_TIMES", 1)

    res = execute_blue_green_update(
        None, "pak-f", "dovo", "dovo-pak", None, _Ctx(), skip_upload=True)
    assert res.ok is False
    assert res.rolled_back is True
    # 回滚 = 在 pak1 目录执行 portupdate
    assert any("cd /data/bin/ata/pak1 && ./portupdate.sh" in c for c in ssh.calls)
    # 回滚步骤记录存在且标记 ok
    rb = [s for s in res.steps if s.get("step") == "rollback"]
    assert rb and rb[0].get("rollback_ok") is True


def test_upload_routes_to_standby_only(monkeypatch):
    """上传只进 standby 目录（瞬态路由）——active 目录永不被写。"""
    import time as _t
    now = int(_t.time())
    state = {"task_port": ":8002", "pak1_pid": 300}  # 这次 pak2 是 active → 应传 pak1
    import time as _t
    now = int(_t.time())
    def _e(cmd, timeout=15, **_):  # noqa: E731
        if "cat" in cmd and "global.yml" in cmd:
            return (0, f"task_port: {state['task_port']}\n", "")
        if "pm2 jlist" in cmd:
            return _jlist("pak1", state["pak1_pid"])
        if "stat -c %Y" in cmd and "-out.log" in cmd:
            return (0, str(now), "")
        if "stat -c %s" in cmd:
            return (0, "100", "")
        if "binupdate.sh" in cmd:
            state["pak1_pid"] += 500
            return (0, "ok", "")
        if "portupdate.sh" in cmd:
            state["task_port"] = ":8001"
            return (0, "ok", "")
        return (0, "", "")
    ssh = FakeSSH({})
    ssh.exec_handler = _e
    _patch_service_config(monkeypatch)
    _patch_connect(monkeypatch, ssh)
    import app.services.blue_green as bg_mod
    monkeypatch.setattr(bg_mod, "WAIT_AFTER_UPDATE", 0)
    monkeypatch.setattr(bg_mod, "WAIT_AFTER_SWITCH", 0)
    monkeypatch.setattr(bg_mod, "VERIFY_RECHECK_DELAY", 0)

    res = execute_blue_green_update(
        None, "pak-f", "dovo", "dovo-pak", "C:/pkg/server.zip", _Ctx())
    assert res.ok, res.error
    assert res.switched_to == "pak1"
    assert any(c.startswith("UPLOAD") and "/pak1/server.zip" in c for c in ssh.calls)
    assert not any(c.startswith("UPLOAD") and "/pak2/" in c for c in ssh.calls)
