#!/usr/bin/env python3
"""服务器迁移主控脚本（参数化，可复用）。

基于 bacteria 迁移实战提炼。配合 README.md 使用。

子命令:
  inventory      查 OPS DB 里旧机配置
  ssh-inventory  SSH 进旧机盘点服务/进程/目录
  register       登记新跳板机+新目标机到 OPS DB
  setup-proxy    生成 SSH 代理配置（ssh_config）
  sync-files     同步业务文件（旧机→新机）
  patch-config   改新机配置里的依赖地址
  install-deps   在新机装运行时依赖
  verify         核对新旧机一致性
  switch         执行切换（停旧机→等→启新机）

用法示例:
  python scripts/migration/migrate_server.py inventory --old-server "8.219.71.126-推广-bacteria"
  python scripts/migration/migrate_server.py register --jump-name tiaoban-new ...
  python scripts/migration/migrate_server.py setup-proxy --jump-name tiaoban-new --target-host 47.84.142.156
  python scripts/migration/migrate_server.py sync-files --old-server "..." --target-host 47.84.142.156 --paths "/data/bin/bactera /data/www/bacteria /etc/caddy/Caddyfile"
  python scripts/migration/migrate_server.py switch --old-server "..." --target-host 47.84.142.156 --ecosystem /root/ecosystem.config.js
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

# 让脚本能 import app.*
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir)))

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
SSH_CONFIG = os.path.join(PROJECT_ROOT, "data", "tmp", "ssh_proxy_config")
TMP_DIR = os.path.join(PROJECT_ROOT, "data", "tmp")


# ── 工具函数 ──────────────────────────────────────────────

def run_ssh(target: str, cmd: str, timeout: int = 120) -> tuple[int, str, str]:
    """经 SSH 代理配置执行远程命令。target 可以是 host 或 host:port。"""
    ssh_args = ["ssh", "-F", SSH_CONFIG, "-o", "ConnectTimeout=15", f"root@{target}", cmd]
    r = subprocess.run(ssh_args, capture_output=True, text=True, timeout=timeout)
    return r.returncode, r.stdout, r.stderr


def scp_to(target: str, local: str, remote: str) -> int:
    """本地 → 远程。"""
    return subprocess.run(
        ["scp", "-F", SSH_CONFIG, local, f"root@{target}:{remote}"],
        capture_output=True, text=True,
    ).returncode


def scp_from(target: str, remote: str, local: str) -> int:
    """远程 → 本地。"""
    return subprocess.run(
        ["scp", "-F", SSH_CONFIG, f"root@{target}:{remote}", local],
        capture_output=True, text=True,
    ).returncode


def scp_between(src_host: str, src_path: str, dst_host: str, dst_path: str) -> int:
    """远程 → 远程（经本地中转）。"""
    return subprocess.run(
        ["scp", "-F", SSH_CONFIG, "-3", f"root@{src_host}:{src_path}", f"root@{dst_host}:{dst_path}"],
        capture_output=True, text=True,
    ).returncode


def upload_script_and_run(target: str, script_content: str, run_args: str = "") -> tuple[int, str, str]:
    """把脚本内容写到本地临时文件，scp 上传，bash 执行，删除。"""
    os.makedirs(TMP_DIR, exist_ok=True)
    local_path = os.path.join(TMP_DIR, "_run.sh")
    with open(local_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(script_content)
    remote_path = "/tmp/_migrate_run.sh"
    rc = scp_to(target, local_path, remote_path)
    if rc != 0:
        return rc, "", "scp upload failed"
    rc, out, err = run_ssh(target, f"bash {remote_path} {run_args}; rm -f {remote_path}", timeout=300)
    return rc, out, err


# ── 子命令实现 ────────────────────────────────────────────

def cmd_inventory(args):
    """查 OPS DB 里旧机配置。"""
    from app.db.base import SessionLocal
    from app.config.servers import get_server_by_name

    db = SessionLocal()
    try:
        srv = get_server_by_name(args.old_server, db=db)
        if not srv:
            print(f"!! server not found: {args.old_server}")
            return 2
        print(json.dumps(srv, ensure_ascii=False, indent=2, default=str))
    finally:
        db.close()
    return 0


def cmd_ssh_inventory(args):
    """SSH 进旧机盘点。"""
    from app.db.base import SessionLocal
    from app.config.servers import get_server_by_name
    from ssh_client import create_ssh_client

    db = SessionLocal()
    try:
        srv = get_server_by_name(args.old_server, db=db)
        if not srv:
            print(f"!! server not found: {args.old_server}")
            return 2
    finally:
        db.close()

    ssh = create_ssh_client(srv)
    ssh.connect(max_retries=2, retry_delay=2)
    print(f"== SSH 连上 {srv['name']} ==")

    probes = [
        ("系统", "uname -a; cat /etc/os-release 2>/dev/null | head -3"),
        ("运行中服务", "systemctl list-units --type=service --state=running --no-pager --no-legend | head -40"),
        ("PM2 进程", "pm2 jlist 2>/dev/null | python3 -m json.tool 2>/dev/null | head -100 || pm2 list"),
        ("监听端口", "ss -tlnp 2>/dev/null | head -40"),
        ("部署目录", "ls -la /data /opt /srv /home /root 2>/dev/null"),
        ("业务进程", "ps -eo pid,user,etime,cmd --sort=-etime | grep -E 'java|node|python|go|core|module|checker' | grep -v grep | head -20"),
        ("依赖版本", "redis-server --version 2>/dev/null; node --version 2>/dev/null; pm2 --version 2>/dev/null; caddy version 2>/dev/null; etcd --version 2>/dev/null | head -1"),
        ("磁盘", "df -h / 2>/dev/null"),
        ("内存", "free -h 2>/dev/null"),
    ]
    for name, cmd in probes:
        rc, out, err = ssh.exec(cmd, timeout=60)
        print(f"\n== {name} ==")
        print(out)

    # 抓配置文件里的依赖地址
    rc, out, err = ssh.exec(
        r"grep -rEn 'redis|postgres|psql' /data/bin /opt /srv --include='*.yml' --include='*.yaml' "
        r"--include='*.conf' --include='*.env' --include='*.properties' -l 2>/dev/null | head -20",
        timeout=120,
    )
    print("\n== 含 redis/psql 的配置文件 ==")
    print(out)

    ssh.close()
    return 0


def cmd_register(args):
    """登记新跳板机+新目标机到 OPS DB。"""
    from app.db.base import SessionLocal
    from app.db.models import JumpHost
    from app.config.servers import save_server
    from app.config.keys import key_file_exists

    for k in (args.jump_key, args.target_key):
        if not key_file_exists(k):
            print(f"!! 密钥文件不存在: data/keys/{k}")
            return 2

    db = SessionLocal()
    try:
        # 1) jump_hosts 表
        jh = db.query(JumpHost).filter(JumpHost.name == args.jump_name).first()
        if jh:
            jh.host = args.jump_host
            jh.port = args.jump_port
            jh.user = args.jump_user
            jh.key = args.jump_key
            print(f"== jump_hosts 已更新 {args.jump_name} ==")
        else:
            jh = JumpHost(
                name=args.jump_name, host=args.jump_host, port=args.jump_port,
                user=args.jump_user, key=args.jump_key, status="online",
                description=f"跳板机（{args.target_name} 迁移用）", tags=["migration", "jump"],
            )
            db.add(jh)
            print(f"== jump_hosts 已登记 {args.jump_name} ==")

        # 2) servers 表 - 跳板机
        save_server({
            "name": args.jump_name, "host": args.jump_host, "port": args.jump_port,
            "user": args.jump_user, "auth_type": "key", "key": args.jump_key,
            "jump_host": None, "status": "online",
            "description": f"跳板机（{args.target_name} 迁移用）", "tags": ["migration", "jump"], "group": "other",
        }, db=db)

        # 3) servers 表 - 目标机
        save_server({
            "name": args.target_name, "host": args.target_host, "port": args.target_port,
            "user": args.target_user, "auth_type": "key", "key": args.target_key,
            "jump_host": args.jump_name, "status": "online",
            "description": f"新服务器（从 {args.target_name} 迁移）", "tags": ["migration"], "group": "other",
        }, db=db)
        print(f"== servers 已登记 {args.target_name}（经 {args.jump_name}）==")

        db.commit()
    finally:
        db.close()
    return 0


def cmd_setup_proxy(args):
    """生成 SSH 代理配置文件。"""
    from app.db.base import SessionLocal
    from app.config.servers import get_server_by_name

    db = SessionLocal()
    try:
        jump_srv = get_server_by_name(args.jump_name, db=db)
        if not jump_srv:
            print(f"!! jump server not found: {args.jump_name}")
            return 2
    finally:
        db.close()

    os.makedirs(TMP_DIR, exist_ok=True)

    # 修复 Windows 密钥权限
    for key_name in [jump_srv["key"], args.target_key or ""]:
        if not key_name:
            continue
        key_path = os.path.join(PROJECT_ROOT, "data", "keys", key_name)
        if os.path.exists(key_path):
            subprocess.run(
                ["icacls", key_path, "/inheritance:r", "/grant:r", f"{os.environ['USERNAME']}:F"],
                capture_output=True,
            )
            print(f"== 修复密钥权限: {key_path} ==")

    config = f"""Host {jump_srv['name']}
    HostName {jump_srv['host']}
    Port {jump_srv['port']}
    User {jump_srv['user']}
    IdentityFile data/keys/{jump_srv['key']}
    IdentitiesOnly yes
    StrictHostKeyChecking no
    UserKnownHostsFile /dev/null

Host {args.target_host}
    HostName {args.target_host}
    Port {args.target_port or 22}
    User {args.target_user or 'root'}
    ProxyJump {jump_srv['name']}
    IdentityFile data/keys/{args.target_key}
    IdentitiesOnly yes
    StrictHostKeyChecking no
    UserKnownHostsFile /dev/null
"""
    with open(SSH_CONFIG, "w", encoding="utf-8") as f:
        f.write(config)
    print(f"== SSH 代理配置已生成: {SSH_CONFIG} ==")

    # 测连通性
    rc, out, err = run_ssh(args.target_host, "echo PROXY_OK; uname -a")
    if "PROXY_OK" in out:
        print(f"== 代理连通成功 ==\n{out}")
        return 0
    else:
        print(f"!! 代理连通失败: {err}")
        return 3


def cmd_sync_files(args):
    """同步业务文件：旧机 tar → 本地 → 新机。"""
    from app.db.base import SessionLocal
    from app.config.servers import get_server_by_name
    from ssh_client import create_ssh_client

    db = SessionLocal()
    try:
        srv = get_server_by_name(args.old_server, db=db)
        if not srv:
            print(f"!! server not found: {args.old_server}")
            return 2
    finally:
        db.close()

    paths = args.paths.split()
    exclude = args.exclude.split() if args.exclude else []
    exclude_opts = " ".join(f"--exclude='{p}'" for p in exclude)

    local_tar = os.path.join(TMP_DIR, "migration_sync.tar.gz")
    remote_tar = "/tmp/migration_sync.tar.gz"
    os.makedirs(TMP_DIR, exist_ok=True)

    # 1) 旧机打包
    ssh = create_ssh_client(srv)
    ssh.connect(max_retries=2, retry_delay=2)
    print(f"== SSH 连上旧机 {srv['name']} ==")

    pack_cmd = f"cd / && tar czf {remote_tar} {exclude_opts} {' '.join(paths)} 2>&1; echo ---rc=$?; ls -lh {remote_tar}"
    rc, out, err = ssh.exec(pack_cmd, timeout=600)
    print(f"== 打包完成 ==\n{out}")

    # 2) 下载到本地
    print(f"== 下载到本地 {local_tar} ==")
    ssh.download(remote_tar, local_tar)
    local_size = os.path.getsize(local_tar)
    print(f"本地文件: {local_size/1024/1024:.1f} MB")

    # 3) 清理旧机临时文件
    ssh.exec(f"rm -f {remote_tar}")
    ssh.close()

    # 4) 上传到新机
    print(f"== 上传到新机 {args.target_host} ==")
    scp_to(args.target_host, local_tar, remote_tar)

    # 5) 新机解压
    rc, out, err = run_ssh(args.target_host, f"cd / && tar xzf {remote_tar} 2>&1; echo ---rc=$?; rm -f {remote_tar}")
    print(f"== 解压完成 ==\n{out}")

    # 6) 清理本地
    os.remove(local_tar)
    print("== 同步完成 ==")
    return 0


def cmd_patch_config(args):
    """改新机配置里的依赖地址。"""
    replacements = []
    if args.old_redis and args.new_redis:
        replacements.append((args.old_redis, args.new_redis))
    if args.old_psql and args.new_psql:
        replacements.append((args.old_psql, args.new_psql))

    if not replacements:
        print("!! 未指定替换规则")
        return 2

    sed_expr = "; ".join(f"s/{old}/{new}/g" for old, new in replacements)
    find_cmd = f"find {args.config_dir} -name '{args.config_pattern}' -exec sed -i '{sed_expr}' {{}} +"
    verify_cmd = f"grep -rn '{args.new_redis}\\|{args.new_psql}' {args.config_dir}/*/{args.config_pattern} 2>/dev/null | head -20"

    rc, out, err = run_ssh(args.target_host, f"{find_cmd} && echo SED_DONE && {verify_cmd}")
    print(out)
    return 0 if "SED_DONE" in out else 1


def cmd_install_deps(args):
    """在新机装运行时依赖。"""
    deps = args.deps.split()

    install_script = "#!/bin/bash\nset -e\n"
    if "redis" in deps:
        install_script += """
echo '== 安装 redis-server =='
DEBIAN_FRONTEND=noninteractive apt-get install -y redis-server
systemctl stop redis-server; systemctl disable redis-server
redis-server --version
"""
    if "node" in deps:
        install_script += """
echo '== 安装 node v22 =='
curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
DEBIAN_FRONTEND=noninteractive apt-get install -y nodejs
node --version
"""
    if "pm2" in deps:
        install_script += """
echo '== 安装 pm2 =='
npm install -g pm2
pm2 --version
pm2 startup systemd -u root --hp /root 2>&1 | tail -3
systemctl disable pm2-root 2>/dev/null
pm2 kill 2>/dev/null
"""
    if "caddy" in deps:
        install_script += """
echo '== 安装 caddy =='
curl -fsSL https://github.com/caddyserver/caddy/releases/download/v2.10.0/caddy_2.10.0_linux_amd64.tar.gz -o /tmp/caddy.tgz
tar xzf /tmp/caddy.tgz -C /usr/bin caddy
chmod +x /usr/bin/caddy
caddy version
# systemd unit 由后续步骤创建
"""
    if "etcd" in deps:
        install_script += """
echo '== etcd: 从旧机同步（见 sync-files）或手动安装 =='
echo '!! etcd 无 apt 源，需从旧机拷贝二进制或源码编译'
"""
    if "pm2-logrotate" in deps:
        install_script += """
echo '== 安装 pm2-logrotate =='
pm2 install pm2-logrotate
pm2 set pm2-logrotate:max_size 100M
pm2 set pm2-logrotate:retain 30
pm2 set pm2-logrotate:compress false
pm2 set pm2-logrotate:dateFormat YYYY-MM-DD_HH-mm-ss
pm2 set pm2-logrotate:workerInterval 30
pm2 set pm2-logrotate:rotateInterval '0 0 * * *'
pm2 set pm2-logrotate:rotateModule true
pm2 kill 2>/dev/null
pkill -9 -f PM2 2>/dev/null
"""

    install_script += 'echo "INSTALL_DONE"\n'

    rc, out, err = upload_script_and_run(args.target_host, install_script)
    print(out)
    return 0 if "INSTALL_DONE" in out else 1


def cmd_verify(args):
    """核对新旧机一致性。"""
    verify_script = """#!/bin/bash
echo "=== 业务二进制 md5 ==="
find /data/bin -type f -executable -name '*-service' -o -name '*.sh' | head -20 | while read f; do
  md5sum "$f"
done

echo "=== config.yaml 依赖地址 ==="
find /data/bin -name 'config.yaml' -exec grep -l 'redis\\|postgres\\|psql' {} \\; | while read f; do
  echo "--- $f ---"
  grep -E 'addr:|source:|redis|psql|postgres' "$f" | head -5
done

echo "=== 监听端口 ==="
ss -tlnp | grep -E '80|443|8001|8002|9001|9002|9006|9007' || echo '(无业务端口)'

echo "=== 服务状态 ==="
for s in redis-server caddy etcd pm2-root; do
  echo "$s: $(systemctl is-active $s 2>/dev/null)/$(systemctl is-enabled $s 2>/dev/null)"
done
echo "pm2: $(pgrep -af PM2 | grep -v pgrep || echo '未运行')"
echo "VERIFY_DONE"
"""
    rc, out, err = upload_script_and_run(args.target_host, verify_script)
    print(out)
    return 0 if "VERIFY_DONE" in out else 1


def cmd_switch(args):
    """执行切换：停旧机 → 等 → 启新机。"""
    from app.db.base import SessionLocal
    from app.config.servers import get_server_by_name
    from ssh_client import create_ssh_client

    # 1) 停旧机
    print(f"=== 停止旧机 {args.old_server} ===")
    db = SessionLocal()
    try:
        srv = get_server_by_name(args.old_server, db=db)
        if not srv:
            print(f"!! server not found: {args.old_server}")
            return 2
    finally:
        db.close()

    stop_script = """#!/bin/bash
echo "=== 停止旧机服务 ==="
pm2 kill 2>&1 | tail -3
"""
    for svc in (args.systemd_services or "").split():
        stop_script += f"systemctl stop {svc} 2>&1\n"
    for svc in (args.skip_services or "").split():
        stop_script += f"systemctl stop {svc} 2>&1\n"
    stop_script += """echo "业务端口: $(ss -tlnp | grep -E '80|443|8001|8002|9001|9002|9006|9007' || echo '无')"
echo "OLD_STOPPED"
"""

    ssh = create_ssh_client(srv)
    ssh.connect(max_retries=2, retry_delay=2)
    rc, out, err = ssh.exec(stop_script, timeout=60)
    print(out)
    ssh.close()

    if "OLD_STOPPED" not in out:
        print("!! 旧机停止失败")
        return 3

    # 2) 等待
    print(f"=== 等待 {args.wait_seconds} 秒 ===")
    time.sleep(args.wait_seconds)

    # 3) 启新机
    print(f"=== 启动新机 {args.target_host} ===")
    start_script = f"""#!/bin/bash
echo "=== 启动 PM2 进程 ==="
pm2 start {args.ecosystem} 2>&1
sleep 3
"""
    for svc in (args.systemd_services or "").split():
        start_script += f"systemctl start {svc} 2>&1\n"
    start_script += """echo "=== 服务状态 ==="
pm2 list
for s in """ + " ".join((args.systemd_services or "").split()) + """ """ + " ".join((args.skip_services or "").split()) + """; do
  echo "$s: $(systemctl is-active $s)"
done
echo "=== 监听端口 ==="
ss -tlnp | grep -E '80|443|8001|8002|9001|9002|9006|9007'
echo "NEW_STARTED"
"""

    rc, out, err = upload_script_and_run(args.target_host, start_script, timeout=120)
    print(out)

    if "NEW_STARTED" not in out:
        print("!! 新机启动失败")
        return 4

    # 4) 保存 PM2 配置 + 开机自启
    enable_svcs = " ".join((args.systemd_services or "").split())
    finalize_cmd = f"pm2 save 2>&1 | tail -2; systemctl enable {enable_svcs} pm2-root 2>&1; echo DONE"
    rc, out, err = run_ssh(args.target_host, finalize_cmd)
    print(out)

    print("=== 切换完成 ===")
    return 0


# ── CLI ──────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="服务器迁移主控脚本")
    sub = parser.add_subparsers(dest="command", required=True)

    # inventory
    p = sub.add_parser("inventory", help="查 OPS DB 里旧机配置")
    p.add_argument("--old-server", required=True)
    p.set_defaults(func=cmd_inventory)

    # ssh-inventory
    p = sub.add_parser("ssh-inventory", help="SSH 进旧机盘点")
    p.add_argument("--old-server", required=True)
    p.set_defaults(func=cmd_ssh_inventory)

    # register
    p = sub.add_parser("register", help="登记新跳板机+新目标机到 OPS DB")
    p.add_argument("--jump-name", required=True)
    p.add_argument("--jump-host", required=True)
    p.add_argument("--jump-port", type=int, default=22)
    p.add_argument("--jump-user", default="root")
    p.add_argument("--jump-key", required=True)
    p.add_argument("--target-name", required=True)
    p.add_argument("--target-host", required=True)
    p.add_argument("--target-port", type=int, default=22)
    p.add_argument("--target-user", default="root")
    p.add_argument("--target-key", required=True)
    p.set_defaults(func=cmd_register)

    # setup-proxy
    p = sub.add_parser("setup-proxy", help="生成 SSH 代理配置")
    p.add_argument("--jump-name", required=True)
    p.add_argument("--target-host", required=True)
    p.add_argument("--target-port", type=int, default=22)
    p.add_argument("--target-user", default="root")
    p.add_argument("--target-key", required=True)
    p.set_defaults(func=cmd_setup_proxy)

    # sync-files
    p = sub.add_parser("sync-files", help="同步业务文件")
    p.add_argument("--old-server", required=True)
    p.add_argument("--target-host", required=True)
    p.add_argument("--paths", required=True, help="空格分隔的路径列表")
    p.add_argument("--exclude", default="*.log", help="空格分隔的排除模式")
    p.set_defaults(func=cmd_sync_files)

    # patch-config
    p = sub.add_parser("patch-config", help="改新机配置里的依赖地址")
    p.add_argument("--target-host", required=True)
    p.add_argument("--config-dir", default="/data/bin")
    p.add_argument("--config-pattern", default="config.yaml")
    p.add_argument("--old-redis")
    p.add_argument("--new-redis")
    p.add_argument("--old-psql")
    p.add_argument("--new-psql")
    p.set_defaults(func=cmd_patch_config)

    # install-deps
    p = sub.add_parser("install-deps", help="在新机装运行时依赖")
    p.add_argument("--target-host", required=True)
    p.add_argument("--deps", default="redis node pm2 caddy", help="空格分隔的依赖列表")
    p.set_defaults(func=cmd_install_deps)

    # verify
    p = sub.add_parser("verify", help="核对新旧机一致性")
    p.add_argument("--target-host", required=True)
    p.set_defaults(func=cmd_verify)

    # switch
    p = sub.add_parser("switch", help="执行切换")
    p.add_argument("--old-server", required=True)
    p.add_argument("--target-host", required=True)
    p.add_argument("--ecosystem", default="/root/ecosystem.config.js")
    p.add_argument("--wait-seconds", type=int, default=60)
    p.add_argument("--systemd-services", default="caddy")
    p.add_argument("--skip-services", default="")
    p.set_defaults(func=cmd_switch)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
