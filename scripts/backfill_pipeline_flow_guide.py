"""回填 pipelines.flow_guide_id（流程 ↔ flow guide 关联）。

依据 2026-09-03 流程管理梳理的对应关系：
- 前端更新（crypto-trader, DIRECT）→ frontend-release（已跑通 8 次）
- 量化后台 updatebin（SCRIPTED_BACKEND）→ 无专属 flow（service-restart 同构
  的编排层，无 MATRIX_PULL 需求）→ service-restart
- 容器（DOCKER_COMPOSE）→ package-pull-release（本周 docker 镜像发版模式）
- Dovo 后台蓝绿更新（BLUE_GREEN）→ dovo-bg-release（本次新梳理）
幂等：仅回填 NULL 行。
"""
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, ".")

MAPPING = {
    "前端更新": "frontend-release",
    "量化后台 updatebin 发布二进制部署": "service-restart",
    "容器": "package-pull-release",
    "Dovo 后台蓝绿更新": "dovo-bg-release",
}


def main() -> int:
    from app.db.base import SessionLocal
    from app.db.models import Pipeline
    from app.services.agent_context import FLOW_GUIDES

    db = SessionLocal()
    try:
        for p in db.query(Pipeline).all():
            if p.flow_guide_id:
                continue
            target = MAPPING.get(p.name)
            if target and target in FLOW_GUIDES:
                p.flow_guide_id = target
                print(f"{p.name} -> {target}")
            else:
                print(f"{p.name}: 无映射（留空）")
        db.commit()
        print("\n终态:")
        for p in db.query(Pipeline).order_by(Pipeline.created_at).all():
            print(f"  {p.name:42s} flow_guide_id={p.flow_guide_id}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
