"""Dovo 目标清单修正：剔除纯 Caddy 代理服务器（2026-09-03 运维确认）。

proxy 服务器只部署 Caddy 反代，无前后端业务代码：
- 不参与后端蓝绿（无 /data/bin/ata、无 pm2 进程——六段式会在①探测即失败）
- 不参与前端 www.sh 更新（无 /data/www 业务目录语义，业务前端在真实服务器上）

修正规则：服务名含 proxy 的服务器从所有 dovo-* 服务 targets 剔除。
幂等：按服务名逐个更新 servers 清单。
"""
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, ".")


def main() -> int:
    from app.db.base import SessionLocal
    from app.db.models import Server, Service

    db = SessionLocal()
    try:
        # 全库 proxy 服务器名单（确认剔除面）
        proxies = [s.name for s in db.query(Server).order_by(Server.name).all()
                   if "proxy" in (s.name or "").lower()]
        print(f"proxy 服务器（全部剔除）: {proxies}")

        changed = []
        for svc in db.query(Service).filter(Service.name.like("dovo-%")).all():
            servers = svc.servers or []
            if not isinstance(servers, list):
                continue
            kept = [s for s in servers if not (
                (isinstance(s, str) and "proxy" in s.lower())
                or (isinstance(s, dict) and "proxy" in str(s.get("id") or s.get("name") or "").lower())
            )]
            removed = len(servers) - len(kept)
            if removed:
                svc.servers = kept
                changed.append((svc.name, len(servers), len(kept), removed))
        db.commit()
        for name, before, after, rm in changed:
            print(f"  {name}: {before} -> {after}（剔除 {rm} 台 proxy）")
        if not changed:
            print("无改动（清单已干净）")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
