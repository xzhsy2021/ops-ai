"""清理历史遗留的默认服务器数据审计 + 迁移。

目标：service.servers（旧默认服务器清单）退役——
1. 审计：哪些服务有 servers 存量、是否已有按环境分配（servers_by_env）覆盖
2. 迁移：servers → servers_by_env 兜底环境（prod），然后清空 servers
   （若服务无 servers_by_env 且无其他回退源，保留 servers 不动并报告）

安全原则：
- 已有 servers_by_env 的服务：servers 直接清空（按环境分配已是唯一事实源）
- 无 servers_by_env 但发布流程依赖 servers 回退的：迁移到 prod 环境
  后清空——先展示后执行（--apply 才落库）
"""
import sys
import json

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\code\ops-ai")

from app.db.base import SessionLocal
from app.db.models import Service, SystemEnvironment


def main() -> int:
    apply = "--apply" in sys.argv
    db = SessionLocal()
    try:
        services = db.query(Service).order_by(Service.system_name, Service.name).all()
        print(f"{'服务':40s} {'servers':>7s} {'by_env':>7s} 动作")
        print("-" * 80)
        to_migrate = []
        to_clear = []
        untouched = []
        for svc in services:
            servers = svc.servers or []
            tv = svc.template_variables or {}
            sbe = tv.get("servers_by_env") or {}
            has_servers = len(servers) > 0
            has_sbe = any(len(v or []) > 0 for v in sbe.values() if isinstance(v, (list, str)))
            if not has_servers:
                continue  # 已干净
            if has_sbe:
                to_clear.append(svc)
                action = "清空 servers（已有按环境分配）"
            else:
                to_migrate.append(svc)
                action = "迁移 servers→prod 后清空"
            print(f"{svc.system_name}/{svc.name:32s} {len(servers):>4d}台  {len(sbe):>4d}env  {action}")
        print()
        print(f"待清空: {len(to_clear)}  待迁移+清空: {len(to_migrate)}  干净: {len(untouched)}")
        if not to_clear and not to_migrate:
            print("无遗留数据。")
            return 0
        if not apply:
            print("\n[dry-run] 加 --apply 执行落库")
            return 0
        # 执行
        for svc in to_migrate:
            tv = dict(svc.template_variables or {})
            sbe = dict(tv.get("servers_by_env") or {})
            servers = [str(x) for x in svc.servers or []]
            # 目标环境判定：服务器名含"测试/test"等标记 → test，否则 prod
            # （crypto-trader-web 的 servers 是测试机，盲迁 prod 会错位）
            looks_test = any(
                any(t in s.lower() for t in ("测试", "test", "testing", "qa", "stage", "staging", "uat", "dev"))
                for s in servers
            )
            target_env = "test" if looks_test else "prod"
            env_row = db.query(SystemEnvironment).filter(
                SystemEnvironment.system_name == svc.system_name,
                SystemEnvironment.name == target_env,
            ).first()
            if env_row is not None and not sbe.get(target_env):
                sbe[target_env] = servers
                tv["servers_by_env"] = sbe
                svc.template_variables = tv
                svc.servers = []
                print(f"  迁移 {svc.system_name}/{svc.name}: servers({len(servers)}台) -> servers_by_env.{target_env}")
            elif sbe.get(target_env):
                svc.servers = []
                print(f"  清空 {svc.system_name}/{svc.name}.servers（{target_env} 已有分配）")
            else:
                print(f"  跳过 {svc.system_name}/{svc.name}: 无 {target_env} 环境行，保留 servers")
        for svc in to_clear:
            print(f"  清空 {svc.system_name}/{svc.name}.servers")
            svc.servers = []
        db.commit()
        print("\n落库完成。")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
