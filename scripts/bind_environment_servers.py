"""闸1 数据录入：SystemEnvironment.servers 环境权威清单绑定。

环境归属判定（依据现有执行记录与服务器命名）：
- crypto-trader.test：47.84.58.154-量化测试、43.106.8.111-量化测试 - 2
  （14 条成功计划的实际 targets 全部这两台——历史事实）
- crypto-trader.prod：暂空（fail-closed：prod 环境绑定前任何 prod 计划都会被拒，
  等运维提供正式生产清单后录入）
- dovo.test：暂空（dovo 各区域均为生产服务，无测试机——先 fail-closed）
- dovo.prod：各区域生产服务器（dovo-pak/bgd/tha/idn/web 的 servers 并集）

幂等：仅更新 servers 为空的环境行。
"""
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, ".")


def main() -> int:
    from app.db.base import SessionLocal
    from app.db.models import Service, SystemEnvironment

    db = SessionLocal()
    try:
        # crypto-trader.test ← 历史执行事实
        test_env = db.query(SystemEnvironment).filter(
            SystemEnvironment.system_name == "crypto-trader",
            SystemEnvironment.name == "test",
        ).first()
        if test_env and not (test_env.servers or []):
            test_env.servers = [
                {"id": "47.84.58.154-量化测试"},
                {"id": "43.106.8.111-量化测试 - 2"},
            ]
            print("crypto-trader.test: 绑定 2 台测试服务器")

        # dovo.prod ← 各区域服务 servers 并集（proxy 已在建模时剔除）
        dovo_prod = db.query(SystemEnvironment).filter(
            SystemEnvironment.system_name == "dovo",
            SystemEnvironment.name == "prod",
        ).first()
        if dovo_prod is None:
            dovo_prod = SystemEnvironment(
                system_name="dovo", name="prod", category="prod",
                display_name="Dovo 生产",
            )
            db.add(dovo_prod)
            print("dovo.prod: 创建")
        if not (dovo_prod.servers or []):
            prod_servers: dict[str, dict] = {}
            for svc in db.query(Service).filter(Service.system_name == "dovo").all():
                for s in svc.servers or []:
                    sid = s if isinstance(s, str) else str(s.get("id") or "")
                    if sid and "web" not in svc.name:  # 前端并集同源，一并纳入
                        prod_servers.setdefault(sid, {"id": sid})
            dovo_prod.servers = list(prod_servers.values())
            print(f"dovo.prod: 绑定 {len(dovo_prod.servers)} 台生产服务器")

        # 汇总终态
        print("\n终态：")
        for row in db.query(SystemEnvironment).order_by(SystemEnvironment.system_name, SystemEnvironment.name).all():
            ids = [s.get("id") if isinstance(s, dict) else str(s) for s in (row.servers or [])]
            print(f"  {row.system_name}.{row.name} ({row.category}): {len(ids)} 台")
            for i in ids:
                print(f"      {i}")
        db.commit()
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
