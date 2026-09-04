"""收敛迁移：service_overrides 剥离历史 servers 覆盖键。

2026-09-04 收敛后 servers 不再属于环境级覆盖面（统一走服务编辑页），
历史数据残留的 servers 覆盖在此清除，避免双路径。
幂等：无 servers 键的行不动。
"""
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\code\ops-ai")

from app.db.base import SessionLocal
from app.db.models import SystemEnvironment


def main() -> int:
    db = SessionLocal()
    try:
        changed = 0
        for env in db.query(SystemEnvironment).all():
            ov = env.service_overrides or {}
            dirty = False
            for svc, o in list(ov.items()):
                if isinstance(o, dict) and "servers" in o:
                    print(f"清理 {env.system_name}.{env.name}.{svc}: servers 覆盖 -> {o['servers']}")
                    o.pop("servers")
                    if not o:
                        ov.pop(svc)
                    dirty = True
            if dirty:
                env.service_overrides = dict(ov)
                changed += 1
        db.commit()
        print(f"修改 {changed} 个环境行")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
