"""数据迁移脚本：将硬编码的SERVER_CATEGORIES/PROJECT_CATEGORIES同步到数据库"""
import sys
import uuid
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import inspect, text

from app.db.base import Base, engine, SessionLocal
from app.db.models import InspectionItemConfig, InspectionItemRule
from app.services.inspection_center import (
    SERVER_CATEGORIES,
    PROJECT_CATEGORIES,
    SERVER_RULE_COMMANDS,
    PROJECT_RULE_COMMANDS,
)


def _ensure_column():
    """确保 inspection_runs 表有 item_config_snapshot 列（兼容老库）"""
    inspector = inspect(engine)
    if "inspection_runs" not in inspector.get_table_names():
        return
    columns = [c["name"] for c in inspector.get_columns("inspection_runs")]
    if "item_config_snapshot" in columns:
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE inspection_runs ADD COLUMN item_config_snapshot JSON"))
    print("已添加 inspection_runs.item_config_snapshot 列")


def migrate():
    # 确保表已创建
    Base.metadata.create_all(engine)
    _ensure_column()

    db = SessionLocal()
    try:
        migrated = 0
        linked = 0
        
        # 迁移SERVER_CATEGORIES
        for sort_idx, category in enumerate(SERVER_CATEGORIES):
            item_code = category["code"]
            item_name = category["name"]
            desc = category.get("description", "")
            category_key = category["code"]  # 与 SERVER_RULE_COMMANDS key 一致
            
            existing = db.query(InspectionItemConfig).filter_by(item_code=item_code).first()
            if existing:
                continue
            
            cfg = InspectionItemConfig(
                id=uuid.uuid4().hex,
                item_code=item_code,
                item_name=item_name,
                category=category_key,
                scope_type="SERVER",
                description=desc,
                enabled=True,
                sort_order=sort_idx,
                config_json={"command_template": SERVER_RULE_COMMANDS.get(category_key, "")},
                is_builtin=True,
            )
            db.add(cfg)
            db.flush()
            migrated += 1
            
            # 创建项目与规则的关联（单一规则）
            item_rule = InspectionItemRule(
                id=uuid.uuid4().hex,
                item_config_id=cfg.id,
                rule_code=item_code,  # 规则 code 与 item_code 相同（内置）
                enabled=True,
                sort_order=0,
            )
            db.add(item_rule)
            linked += 1
        
        # 迁移PROJECT_CATEGORIES
        for sort_idx, category in enumerate(PROJECT_CATEGORIES):
            item_code = category["code"]
            item_name = category["name"]
            desc = category.get("description", "")
            category_key = category["code"]
            
            existing = db.query(InspectionItemConfig).filter_by(item_code=item_code).first()
            if existing:
                continue
            
            cfg = InspectionItemConfig(
                id=uuid.uuid4().hex,
                item_code=item_code,
                item_name=item_name,
                category=category_key,
                scope_type="PROJECT",
                description=desc,
                enabled=True,
                sort_order=100 + sort_idx,
                config_json={"command_template": PROJECT_RULE_COMMANDS.get(category_key, "")},
                is_builtin=True,
            )
            db.add(cfg)
            db.flush()
            migrated += 1
            
            item_rule = InspectionItemRule(
                id=uuid.uuid4().hex,
                item_config_id=cfg.id,
                rule_code=item_code,
                enabled=True,
                sort_order=0,
            )
            db.add(item_rule)
            linked += 1
        
        db.commit()
        print(f"迁移完成: 新增 {migrated} 个项目配置, {linked} 个规则关联")
        
        # 统计
        total = db.query(InspectionItemConfig).count()
        print(f"当前 inspection_item_configs 总数: {total}")
    except Exception as e:
        db.rollback()
        print(f"迁移失败: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    migrate()
