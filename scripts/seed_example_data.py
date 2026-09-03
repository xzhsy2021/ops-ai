"""示例数据种子：为全新 OPS 部署生成演示环境（全 example 值，无任何真实信息）。

用法：
    python scripts/seed_example_data.py            # 写入示例数据（幂等，仅限空库）
    python scripts/seed_example_data.py --status   # 查看当前库内容概况

安全防护：
    库中已有真实系统（非 demo-*）时默认拒绝写入——demo 服务器混入生产库
    会污染巡检目标面（2026-09-03 事故）。确认混入需显式 --force。

生成内容：
- 2 个系统：demo-system（带 Matrix 路由）/ demo-monitor
- 3 台服务器：demo-web / demo-api / demo-db（TEST-NET-3 保留段 IP，仅凭据占位）
- 2 个服务：demo-web（前端）/ demo-api（后端）
- 测试环境 + 示例审批人路由（example.com 域）
"""
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, ".")

DEMO_IPS = {"web": "203.0.113.10", "api": "203.0.113.20", "db": "203.0.113.30"}


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--status", action="store_true", help="只查看概况")
    parser.add_argument("--force", action="store_true", help="明知库含真实系统仍写入（生产库勿用）")
    args = parser.parse_args()

    from app.db.base import SessionLocal
    from app.db.models import (
        Server,
        Service,
        System,
        SystemEnvironment,
    )

    db = SessionLocal()
    try:
        if args.status:
            for cls in (System, Service, Server, SystemEnvironment):
                print(f"{cls.__tablename__}: {db.query(cls).count()} 行")
            return 0

        # 生产防护：库中已有真实系统（非 demo）时拒绝写入——demo 服务器进
        # 生产库会污染巡检目标面（2026-09-03 事故：3 台 demo 服务器 SSH
        # 失败混入安全日报巡检）。示例数据只允许写入空库/演示库。
        real_systems = (
            db.query(System)
            .filter(~System.name.like("demo-%"))
            .count()
        )
        if real_systems > 0 and not args.force:
            print(
                f"拒绝写入：当前库已含 {real_systems} 个真实系统（生产库）。"
                "示例数据仅限空库使用；确认要混入请加 --force。"
            )
            return 1

        if db.query(System).filter(System.name == "demo-system").first():
            print("demo-system 已存在，跳过（幂等）")
            return 0

        db.add(System(
            name="demo-system",
            display_name="演示系统",
            message_routing={
                "enabled": True,
                "keywords": ["demo", "演示"],
                "rooms": [{
                    "channel": "matrix",
                    "channel_account_id": "default",
                    "conversation_id": "!demo-room:example.com",
                }],
                "approvers": [{
                    "channel": "matrix",
                    "channel_account_id": "default",
                    "sender_id": "@demo-approver:example.com",
                }],
            },
        ))
        db.add(System(name="demo-monitor", display_name="演示监控"))
        db.flush()

        for role, ip in DEMO_IPS.items():
            db.add(Server(
                name=f"demo-{role}",
                host=ip,
                port=22,
                user="deploy",
                status="online",
            ))
        db.add(SystemEnvironment(system_name="demo-system", name="test", category="test",
                                 servers=[{"id": "demo-web"}, {"id": "demo-api"}]))
        db.add(SystemEnvironment(system_name="demo-system", name="prod", category="prod",
                                 servers=[{"id": "demo-db"}]))
        db.flush()

        db.add(Service(
            name="demo-web",
            system_name="demo-system",
            display_name="前端",
            template_variables={"deploy_path": "/opt/demo/web", "update_script": "./deploy.sh"},
            servers=["demo-web"],
        ))
        db.add(Service(
            name="demo-api",
            system_name="demo-system",
            display_name="后端",
            template_variables={"service_dir": "/opt/demo/api"},
            servers=["demo-api"],
        ))
        db.commit()
        print("示例数据已写入：demo-system / demo-monitor，3 台服务器，2 个服务")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
