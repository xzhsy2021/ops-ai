"""Dovo 蓝绿双目录发版执行器。

六段式流水线（每台服务器独立执行，一台失败不阻断其余）：
  ① 探测    cat global.yml → task_port → port_map 反查 → active/standby
  ② 上传    server.zip → {standby_dir}/server.zip（staging 后 binupdate 就地处理）
  ③ 更新    cd {standby_dir} && ./binupdate.sh（脚本内部 mv server→server.bak、
             unzip、pm2 restart）
  ④ 验证    pm2 jlist：standby 进程 online 且 pid 换代；
             out 日志 mtime 刷新；error 日志无新增
  ⑤ 切换    cd {standby_dir} && ./portupdate.sh（改 global.yml task_port +
             Caddyfile 反代 + systemctl reload caddy）
  ⑥ 复验    global.yml task_port == standby 端口；standby out 日志继续刷新
             ——失败→自动回滚：cd {active_dir} && ./portupdate.sh 切回旧端口

设计原则（2026-09-03 设计文档 docs/plans/2026-09-03-dovo-blue-green-release-design.md）：
- active/standby 是运行时瞬态，绝不预探测冻结进计划参数；每台执行时现场判定。
- 验证锚点全部取自环境真实信号（pm2 pid 换代 / 日志 mtime / task_port 值），
  不发明新探针。
- 旧 active 不做任何 stop/restart（pm2 继续在线，自然降级为下轮 standby）。
- 回滚 = 在旧 active 目录执行 portupdate.sh（脚本自带端口硬编码，零额外回滚逻辑）。
- binupdate.sh 内部用数字 pm2 id（restart 1/2），数字 id 跨机不稳定（pm2 resurrect
  顺序变化即漂移），执行器验证一律按进程名 {dir}.f 匹配，不受 id 漂移影响。
"""
from __future__ import annotations

import logging
import re
import shlex
import time
from dataclasses import dataclass, field
from typing import Any

from app.services.tool_adapters.server_tools import (
    _check_remote_dir_exists,
    _connect,
    get_service_config,
)

logger = logging.getLogger(__name__)

# 步骤间等待（秒）：binupdate 后进程重启、portupdate 后 caddy 重载生效
WAIT_AFTER_UPDATE = 5
WAIT_AFTER_SWITCH = 5
# 复验轮询（秒）：切换后日志刷新需要业务流量进来，给足窗口
VERIFY_RECHECK_DELAY = 8
VERIFY_POLL_TIMES = 3

_PORT_RE = re.compile(r"^task_port:\s*:?(\d+)\s*$", re.MULTILINE)


@dataclass
class BGConfig:
    """从服务 template_variables 读取的蓝绿静态配置。"""

    base_dir: str
    dirs: list[str]
    port_map: dict[str, int]          # {"pak1": 8001, "pak2": 8002}
    config_file: str
    update_script: str
    port_script: str
    pm2_pattern: str                  # "{dir}.f"
    package_name: str = "server.zip"  # binupdate.sh 就地处理的固定包名
    log_dir: str = "/root/.pm2/logs"

    @classmethod
    def from_service_config(cls, cfg: dict[str, Any]) -> "BGConfig":
        tv = cfg.get("template_variables") or {}
        base_dir = tv.get("bg_base_dir") or ""
        dirs = tv.get("bg_dirs") or []
        port_map_raw = tv.get("bg_port_map") or {}
        if not base_dir or len(dirs) != 2 or len(port_map_raw) != 2:
            raise ValueError(
                "蓝绿服务配置不完整：需要 bg_base_dir、bg_dirs（2 个目录）、"
                "bg_port_map（目录→端口映射）"
            )
        port_map = {d: int(str(p).lstrip(":")) for d, p in port_map_raw.items()}
        for d in dirs:
            if d not in port_map:
                raise ValueError(f"bg_port_map 缺少目录 {d} 的端口")
        return cls(
            base_dir=base_dir.rstrip("/"),
            dirs=dirs,
            port_map=port_map,
            config_file=tv.get("bg_config_file") or f"{base_dir}/config/global.yml",
            update_script=tv.get("bg_update_script") or "./binupdate.sh",
            port_script=tv.get("bg_port_script") or "./portupdate.sh",
            pm2_pattern=tv.get("bg_pm2_pattern") or "{dir}.f",
            package_name=tv.get("bg_package_name") or "server.zip",
            log_dir=tv.get("bg_log_dir") or "/root/.pm2/logs",
        )

    def dir_path(self, d: str) -> str:
        return f"{self.base_dir}/{d}"

    def pm2_name(self, d: str) -> str:
        return self.pm2_pattern.format(dir=d)

    def out_log(self, d: str) -> str:
        return f"{self.log_dir}/{self.pm2_name(d)}-out.log"

    def err_log(self, d: str) -> str:
        return f"{self.log_dir}/{self.pm2_name(d)}-error.log"


@dataclass
class BGProbe:
    """①探测结果（每台现探，不信任计划前的快照）。"""

    task_port: int | None
    active: str
    standby: str
    raw: str = ""


@dataclass
class BGStepResult:
    """单台服务器的蓝绿执行结果。"""

    server: str
    ok: bool
    steps: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""
    switched_to: str = ""          # 切换成功的目录（回滚后为空）
    rolled_back: bool = False

    def add(self, name: str, **detail) -> dict[str, Any]:
        entry = {"step": name, **detail}
        self.steps.append(entry)
        return entry


def _remote_file_read(ssh, path: str) -> tuple[int, str]:
    code, out, err = ssh.exec(f"cat {shlex.quote(path)}", timeout=15)
    return code, out


def _probe_active(ssh, bg: BGConfig) -> BGProbe:
    """①探测：读 global.yml 的 task_port，反查 port_map 判定 active/standby。"""
    code, out = _remote_file_read(ssh, bg.config_file)
    m = _PORT_RE.search(out or "")
    if code != 0 or not m:
        raise RuntimeError(f"无法读取 {bg.config_file} 的 task_port（exit={code}）")
    port = int(m.group(1))
    active = next((d for d, p in bg.port_map.items() if p == port), None)
    if not active:
        raise RuntimeError(
            f"task_port=:{port} 不在 port_map {bg.port_map} 内——配置漂移需人工核对"
        )
    standby = next(d for d in bg.dirs if d != active)
    return BGProbe(task_port=port, active=active, standby=standby, raw=out)


def _pm2_state(ssh, bg: BGConfig, d: str) -> dict[str, Any]:
    """pm2 jlist 按进程名取状态（online/pid/重启次数）。"""
    name = bg.pm2_name(d)
    code, out, _ = ssh.exec("pm2 jlist", timeout=20)
    if code != 0:
        return {"online": False, "note": f"pm2 jlist exit={code}"}
    import json as _json
    try:
        procs = _json.loads(out or "[]")
    except ValueError:
        return {"online": False, "note": "pm2 jlist 非法 JSON"}
    for p in procs:
        if p.get("name") == name:
            return {
                "online": p.get("pm_id") is not None and (p.get("status") == "online"),
                "pid": p.get("pid"),
                "restarts": p.get("restart_time"),
                "status": p.get("status"),
            }
    return {"online": False, "note": f"进程 {name} 不在 pm2 列表中"}


def _log_mtime(ssh, bg: BGConfig, d: str, which: str = "out") -> int | None:
    log = bg.out_log(d) if which == "out" else bg.err_log(d)
    code, out, _ = ssh.exec(f"stat -c %Y {shlex.quote(log)}", timeout=15)
    if code != 0:
        return None
    try:
        return int(out.strip())
    except ValueError:
        return None


def _err_size(ssh, bg: BGConfig, d: str) -> int | None:
    code, out, _ = ssh.exec(f"stat -c %s {shlex.quote(bg.err_log(d))}", timeout=15)
    if code != 0:
        return None
    try:
        return int(out.strip())
    except ValueError:
        return None


def _verify_standby(ssh, bg: BGConfig, d: str, *, pid_before: Any = None) -> dict[str, Any]:
    """④验证：进程换代 + out 日志刷新 + 进程在线。"""
    st = _pm2_state(ssh, bg, d)
    if not st.get("online"):
        return {"ok": False, "reason": f"pm2 进程 {bg.pm2_name(d)} 不在线: {st}", "pm2": st}
    if pid_before is not None and st.get("pid") == pid_before:
        return {"ok": False, "reason": "pid 未换代（binupdate 的 pm2 restart 未生效）", "pm2": st}
    mtime = _log_mtime(ssh, bg, d, "out")
    if mtime is None:
        return {"ok": False, "reason": f"读不到 out 日志 mtime: {bg.out_log(d)}"}
    if time.time() - mtime > 300:
        return {"ok": False, "reason": "out 日志 5 分钟未刷新（程序可能未真正运行）", "mtime_age": int(time.time() - mtime)}
    return {"ok": True, "pm2": st, "out_log_mtime": mtime}


def _upload_package(ssh, bg: BGConfig, standby_dir: str, local_path: str) -> dict[str, Any]:
    """②上传：包 → {standby_dir}/server.zip（binupdate.sh 就地处理固定名）。"""
    remote = f"{bg.dir_path(standby_dir)}/{bg.package_name}"
    try:
        ssh.upload(local_path, remote)
        return {"ok": True, "remote": remote}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"上传失败 {local_path} -> {remote}: {e}"}


def execute_blue_green_update(
    db, server_name: str, system: str, service: str, local_package: str | None,
    ctx, *, skip_upload: bool = False,
) -> BGStepResult:
    """单台服务器蓝绿更新六段式。local_package 为文件中心包的本地绝对路径；
    skip_upload=True 时假定包已在 standby 目录（复跑/人工放置场景）。"""
    cfg = get_service_config({"system": system, "service": service}, ctx, db)
    if not cfg.get("found"):
        raise ValueError(f"服务配置未找到: {system}/{service}")
    bg = BGConfig.from_service_config(cfg)

    res = BGStepResult(server=server_name, ok=False)
    ssh, _ = _connect(server_name)
    try:
        # 基目录校验（复用现有路径校验语义）
        ok, err_msg = _check_remote_dir_exists(ssh, bg.base_dir)
        if not ok:
            res.error = err_msg
            return res

        # ① 探测（现探，瞬态判定）
        probe = _probe_active(ssh, bg)
        res.add("probe", active=probe.active, standby=probe.standby,
                task_port=probe.task_port)
        sdir = bg.dir_path(probe.standby)
        adir = bg.dir_path(probe.active)

        # ② 上传（瞬态路由：只发给本轮 standby）
        if local_package and not skip_upload:
            up = _upload_package(ssh, bg, probe.standby, local_package)
            res.add("upload", **up)
            if not up.get("ok"):
                res.error = up.get("error") or "上传失败"
                return res
        else:
            res.add("upload", skipped=True,
                    note="无本地包或 skip_upload（假定包已在位）")

        # ③ 更新 standby（先取更新前 pid 快照用于换代验证）
        pid_before = None
        try:
            pid_before = _pm2_state(ssh, bg, probe.standby).get("pid")
        except Exception:  # noqa: BLE001
            pid_before = None
        code, out, err = ssh.exec(
            f"cd {shlex.quote(sdir)} && {bg.update_script}", timeout=180)
        res.add("update", exit_code=code, stdout=(out or "")[:300],
                pid_before=pid_before)
        if code != 0:
            res.error = f"binupdate 失败 exit={code}: {(err or '')[:200]}"
            return res

        time.sleep(WAIT_AFTER_UPDATE)

        # ④ 验证 standby：pid 换代（对比 binupdate 前快照）+ 日志刷新
        verify = _verify_standby(ssh, bg, probe.standby, pid_before=pid_before)
        res.add("verify", **verify)
        if not verify.get("ok"):
            res.error = f"standby 验证失败: {verify.get('reason')}"
            return res

        # ⑤ 切换
        err_before = _err_size(ssh, bg, probe.standby)
        code, out, err = ssh.exec(
            f"cd {shlex.quote(sdir)} && {bg.port_script}", timeout=120)
        res.add("switch", exit_code=code, stdout=(out or "")[:300])
        if code != 0:
            res.error = f"portupdate 失败 exit={code}: {(err or '')[:200]}"
            return res
        time.sleep(WAIT_AFTER_SWITCH)

        # ⑥ 复验 + 失败自动回滚
        for attempt in range(VERIFY_POLL_TIMES):
            time.sleep(VERIFY_RECHECK_DELAY if attempt else 0)
            code2, raw = _remote_file_read(ssh, bg.config_file)
            m = _PORT_RE.search(raw or "")
            new_port = int(m.group(1)) if m else None
            mtime = _log_mtime(ssh, bg, probe.standby, "out")
            mtime_age = int(time.time() - mtime) if mtime else None
            err_now = _err_size(ssh, bg, probe.standby)
            good = (
                new_port == bg.port_map[probe.standby]
                and mtime is not None and mtime_age is not None and mtime_age <= 120
                and err_before is not None and err_now is not None
                and err_now <= err_before + 512  # 切换窗口容忍少量新增
            )
            if good:
                res.ok = True
                res.switched_to = probe.standby
                res.add("recheck", task_port=new_port, out_log_age=mtime_age,
                        switched=True)
                return res

        # 复验失败 → 自动回滚（旧 active 目录执行 portupdate.sh）
        rb = _rollback_to_active(ssh, bg, probe.active)
        res.add("rollback", **rb)
        res.rolled_back = True
        res.error = (
            f"切换后复验未通过（task_port={new_port} 期望 :{bg.port_map[probe.standby]}"
            f" 或日志未刷新），已回滚到 {probe.active}"
        )
        if not rb.get("ok"):
            res.error += f"；⚠️ 回滚本身失败，需人工介入: {rb}"
        return res
    finally:
        ssh.close()


def _rollback_to_active(ssh, bg: BGConfig, active_dir: str) -> dict[str, Any]:
    """⑥失败分支：旧 active 目录执行 portupdate.sh 切回旧端口。"""
    adir = bg.dir_path(active_dir)
    code, out, err = ssh.exec(
        f"cd {shlex.quote(adir)} && {bg.port_script}", timeout=120)
    ok = code == 0
    detail = {"ok": ok, "dir": active_dir, "exit_code": code, "stdout": (out or "")[:200]}
    if ok:
        time.sleep(WAIT_AFTER_SWITCH)
        code2, raw = _remote_file_read(ssh, bg.config_file)
        m = _PORT_RE.search(raw or "")
        back = int(m.group(1)) if m else None
        detail["task_port_after"] = back
        detail["rollback_ok"] = back == bg.port_map[active_dir]
    return detail
