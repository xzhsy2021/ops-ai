"""crypto-trader puller 服务配置对齐（2026-09-07）。

现实变化：test2（cc-test2 / 43.106.8.111）puller 已停；test1（ct-test /
47.84.58.154）新增 4 个 compose 容器：puller-kline / puller-query /
puller-snapshot / puller-trade（docker-compose.puller.yml + env.puller）。

变更内容：
1. 旧 `puller` 服务改造（A.1）：template → docker_compose，test 环境目标
   43.106.8.111 → 47.84.58.154，配置 compose_dir/compose_file/env_file
2. 新增 4 个 Service（puller-kline/query/snapshot/trade），每个
   compose_service 指向自己的 compose 服务名——独立管理单元
3. crypto-docker-compose 的陈旧 servers_by_env（量化测试服务器/2，已不
   存在于 servers 表）修正为真实服务器名

用法：python scripts/migrate_puller_services.py [--apply]（默认 dry-run）
"""
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\code\ops-ai")

import os

def _env(n):
    for line in open(r"D:\code\ops-ai\.env", encoding="utf-8"):
        line = line.strip()
        if line.startswith(n + "="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""

os.environ.setdefault("APPROVAL_SIGNING_KEY", _env("APPROVAL_SIGNING_KEY"))

import json

APPLY = "--apply" in sys.argv

from app.db.base import SessionLocal
from app.db.models import Service

TEST1 = "47.84.58.154-量化测试"
TEST2 = "43.106.8.111-量化测试 - 2"

COMPOSE_TV_BASE = {
    "compose_dir": "/data/crypto-trader",
    "compose_file": "docker-compose.puller.yml",
    "env_file": "env.puller",
    "wait_after_up": 10,
    "log_tail_lines": 30,
}

NEW_SERVICES = {
    "puller-kline": "puller-kline",
    "puller-query": "puller-query",
    "puller-snapshot": "puller-snapshot",
    "puller-trade": "puller-trade",
}


def _set_tv(svc: Service, updates: dict) -> None:
    tv = dict(svc.template_variables or {})
    tv.update(updates)
    svc.template_variables = tv


db = SessionLocal()
changes = []

# ── 1. 旧 puller 改造 ──
old = db.query(Service).filter_by(system_name="crypto-trader", name="puller").first()
if old:
    tv = dict(old.template_variables or {})
    sbe = dict(tv.get("servers_by_env") or {})
    prev_test = sbe.get("test")
    sbe["test"] = [TEST1]
    _set_tv(old, {
        **COMPOSE_TV_BASE,
        "servers_by_env": sbe,
    })
    old.template = "docker_compose"
    changes.append(
        f"[改造] puller: template {tv.get('template') or old.template}→docker_compose, "
        f"servers_by_env.test {prev_test}→[47.84.58.154-量化测试], +compose_file=docker-compose.puller.yml, +env_file=env.puller"
    )
else:
    changes.append("[跳过] puller 服务不存在")

# ── 2. 新增 4 个 puller 容器服务 ──
for name, compose_svc in NEW_SERVICES.items():
    exists = db.query(Service).filter_by(system_name="crypto-trader", name=name).first()
    if exists:
        changes.append(f"[跳过] {name} 已存在")
        continue
    db.add(Service(
        system_name="crypto-trader",
        name=name,
        display_name=f"crypto-trader {compose_svc} 拉取服务",
        template="docker_compose",
        template_variables={
            **COMPOSE_TV_BASE,
            "compose_service": compose_svc,
            "servers_by_env": {"test": [TEST1]},
        },
    ))
    changes.append(f"[新增] {name}: docker_compose, compose_service={compose_svc}, servers_by_env.test=[47.84.58.154-量化测试]")

# ── 3. crypto-docker-compose 陈旧服务器名修正 ──
cdc = db.query(Service).filter_by(system_name="crypto-trader", name="crypto-docker-compose").first()
if cdc:
    tv = dict(cdc.template_variables or {})
    sbe = dict(tv.get("servers_by_env") or {})
    if sbe.get("test") != [TEST1, TEST2]:
        prev = sbe.get("test")
        sbe["test"] = [TEST1, TEST2]
        _set_tv(cdc, {"servers_by_env": sbe})
        changes.append(f"[修正] crypto-docker-compose: servers_by_env.test {prev}→[量化测试, 量化测试-2]")
    else:
        changes.append("[跳过] crypto-docker-compose 已正确")
else:
    changes.append("[跳过] crypto-docker-compose 不存在")

print("=" * 60)
print(f"模式: {'APPLY（写库）' if APPLY else 'DRY-RUN（预览，不写库）'}")
print("=" * 60)
for c in changes:
    print("  " + c)

if APPLY:
    db.commit()
    print()
    print("✅ 已写库。核验：")
    for s in db.query(Service).filter_by(system_name="crypto-trader").order_by(Service.name).all():
        tv = s.template_variables or {}
        sbe = tv.get("servers_by_env") or {}
        cs = tv.get("compose_service") or "-"
        print(f"  {s.name:24s} {s.template:22s} compose_service={cs:16s} test={sbe.get('test')}")
else:
    db.rollback()
    print()
    print("预览无误后执行: python scripts/migrate_puller_services.py --apply")

db.close()
