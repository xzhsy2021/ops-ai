"""Dovo 服务建模录入（2026-09-03 设计落地）。

- 5 个区域蓝绿服务（dovo-pak/bgd/tha/idn/ind）+ dovo-web 前端
- 路由占位：参照 crypto-trader（房间/审批人待创建后回填）
- 幂等：按 name 判断存在即更新 template_variables
"""
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, ".")

REGIONS = {
    # service: (bg_dirs 生成用区域前缀, 服务器 name 匹配词列表)
    "dovo-pak": (["pak"], ["pak"]),
    "dovo-bgd": (["bgd"], ["bgd"]),
    "dovo-tha": (["tha"], ["tha"]),
    "dovo-idn": (["idn"], ["idn"]),
    "dovo-ind": (["ind"], ["idno", "印度"]),
}


def main() -> int:
    from app.db.base import SessionLocal
    from app.db.models import Server, Service, System

    db = SessionLocal()
    try:
        system = db.query(System).filter(System.name == "dovo").first()
        if not system:
            print("dovo 系统不存在")
            return 1

        # 路由占位（参照 crypto-trader；房间/审批人创建后回填）
        if not system.message_routing:
            system.message_routing = {
                "enabled": True,
                "aliases": ["dovo"],
                "keywords": ["dovo", "ata", "后台"],
                "rooms": [],
                "approvers": [],
            }
            print("dovo 路由占位已写入（rooms/approvers 空，待 Matrix 房间创建后回填）")

        created, updated = [], []

        for svc_name, (prefixes, matchers) in REGIONS.items():
            d1, d2 = prefixes[0] + "1", prefixes[0] + "2"
            servers = [
                s.name for s in db.query(Server).order_by(Server.name).all()
                if any(m.lower() in (s.name or "").lower() for m in matchers)
                and "bak" not in (s.name or "").lower()  # 默认不含 bak（用户确认参与则另行加入）
                and s.status == "online"
            ]
            tv = {
                "template": "blue_green",
                "bg_base_dir": "/data/bin/ata",
                "bg_dirs": [d1, d2],
                "bg_port_map": {d1: ":8001", d2: ":8002"},
                "bg_config_file": "/data/bin/ata/config/global.yml",
                "bg_update_script": "./binupdate.sh",
                "bg_port_script": "./portupdate.sh",
                "bg_pm2_pattern": "{dir}.f",
                "bg_package_name": "server.zip",
                "bg_log_dir": "/root/.pm2/logs",
            }
            svc = db.query(Service).filter(Service.name == svc_name).first()
            if svc:
                svc.template_variables = tv
                svc.servers = servers
                updated.append(svc_name)
            else:
                db.add(Service(
                    name=svc_name, system_name="dovo",
                    display_name=f"Dovo {prefixes[0].upper()} 后台",
                    template_variables=tv, servers=servers,
                ))
                created.append(svc_name)
            print(f"{svc_name}: dirs={d1}/{d2} servers={len(servers)}")

        # dovo-web 前端
        web_tv = {
            "deploy_path": "/data/www",
            "update_script": "./www.sh",
        }
        web_servers = [
            s.name for s in db.query(Server).order_by(Server.name).all()
            if s.status == "online"
            and any(m.lower() in (s.name or "").lower() for m in ("pak", "bgd", "tha", "idn"))
            and "bak" not in (s.name or "").lower()
        ]
        svc = db.query(Service).filter(Service.name == "dovo-web").first()
        if svc:
            svc.template_variables = web_tv
            svc.servers = web_servers
            updated.append("dovo-web")
        else:
            db.add(Service(
                name="dovo-web", system_name="dovo",
                display_name="Dovo 前端",
                template_variables=web_tv, servers=web_servers,
            ))
            created.append("dovo-web")
        print(f"dovo-web: servers={len(web_servers)}")

        db.commit()
        print(f"\n完成: 新建 {created} 更新 {updated}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
