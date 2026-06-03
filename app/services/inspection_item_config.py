"""巡检项目配置服务 - 可选/可编辑/可调整的规则引擎"""
import json
import uuid
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from app.db.models import InspectionItemConfig, InspectionItemRule, InspectionRule
from app.services.inspection_center import (
    SERVER_CATEGORIES,
    PROJECT_CATEGORIES,
    SERVER_RULE_COMMANDS,
    PROJECT_RULE_COMMANDS,
)


def list_item_configs(db: Session, scope_type: str = "SERVER", include_disabled: bool = True) -> List[Dict]:
    """获取巡检项目配置列表"""
    q = db.query(InspectionItemConfig).filter(InspectionItemConfig.scope_type == scope_type)
    if not include_disabled:
        q = q.filter(InspectionItemConfig.enabled == True)
    configs = q.order_by(InspectionItemConfig.sort_order).all()
    
    result = []
    for c in configs:
        rules = db.query(InspectionItemRule).filter(
            InspectionItemRule.item_config_id == c.id
        ).order_by(InspectionItemRule.sort_order).all()
        result.append({
            "id": c.id,
            "item_code": c.item_code,
            "item_name": c.item_name,
            "category": c.category,
            "scope_type": c.scope_type,
            "description": c.description,
            "enabled": c.enabled,
            "sort_order": c.sort_order,
            "is_builtin": c.is_builtin,
            "config_json": c.config_json or {},
            "rules": [
                {
                    "id": r.id,
                    "rule_code": r.rule_code,
                    "enabled": r.enabled,
                    "sort_order": r.sort_order,
                    "config_override": r.config_override or {},
                }
                for r in rules
            ],
            "created_at": c.created_at.isoformat() if c.created_at else None,
            "updated_at": c.updated_at.isoformat() if c.updated_at else None,
        })
    return result


def get_item_config(db: Session, item_id: str) -> Optional[Dict]:
    """获取单个项目配置"""
    c = db.query(InspectionItemConfig).filter(InspectionItemConfig.id == item_id).first()
    if not c:
        return None
    return next((x for x in list_item_configs(db, c.scope_type, include_disabled=True) if x["id"] == item_id), None)


def update_item_config(db: Session, item_id: str, payload: Dict) -> Optional[Dict]:
    """更新项目配置"""
    c = db.query(InspectionItemConfig).filter(InspectionItemConfig.id == item_id).first()
    if not c:
        return None
    
    if "item_name" in payload:
        c.item_name = payload["item_name"]
    if "description" in payload:
        c.description = payload["description"]
    if "enabled" in payload:
        c.enabled = bool(payload["enabled"])
    if "sort_order" in payload:
        c.sort_order = int(payload["sort_order"])
    if "config_json" in payload and isinstance(payload["config_json"], dict):
        # 合并配置
        current = dict(c.config_json or {})
        current.update(payload["config_json"])
        c.config_json = current
    
    db.commit()
    return get_item_config(db, item_id)


def toggle_item_config(db: Session, item_id: str) -> Optional[Dict]:
    """启用/禁用项目配置"""
    c = db.query(InspectionItemConfig).filter(InspectionItemConfig.id == item_id).first()
    if not c:
        return None
    c.enabled = not c.enabled
    db.commit()
    return get_item_config(db, item_id)


def reorder_item_configs(db: Session, scope_type: str, ordered_ids: List[str]) -> bool:
    """调整项目配置顺序"""
    for idx, item_id in enumerate(ordered_ids):
        c = db.query(InspectionItemConfig).filter(
            InspectionItemConfig.id == item_id,
            InspectionItemConfig.scope_type == scope_type,
        ).first()
        if c:
            c.sort_order = idx
    db.commit()
    return True


def update_item_rules(db: Session, item_id: str, rules: List[Dict]) -> Optional[Dict]:
    """更新项目关联的规则列表"""
    cfg = db.query(InspectionItemConfig).filter(InspectionItemConfig.id == item_id).first()
    if not cfg:
        return None
    
    # 删除现有关联
    db.query(InspectionItemRule).filter(InspectionItemRule.item_config_id == item_id).delete()
    
    # 创建新关联
    for idx, rule in enumerate(rules):
        ir = InspectionItemRule(
            id=uuid.uuid4().hex,
            item_config_id=item_id,
            rule_code=rule.get("rule_code", ""),
            enabled=bool(rule.get("enabled", True)),
            sort_order=rule.get("sort_order", idx),
            config_override=rule.get("config_override", {}),
        )
        db.add(ir)
    
    db.commit()
    return get_item_config(db, item_id)


def get_enabled_categories(db: Session, scope_type: str) -> List[Dict]:
    """获取启用的项目（兼容旧的SERVER_CATEGORIES/PROJECT_CATEGORIES）"""
    configs = list_item_configs(db, scope_type, include_disabled=False)
    return [
        {
            "code": c["item_code"],
            "key": c["category"],
            "name": c["item_name"],
            "description": c["description"],
            "command_template": c["config_json"].get("command_template", ""),
            "sort_order": c["sort_order"],
        }
        for c in configs
    ]


def get_run_raw_output(db: Session, run_id: str) -> List[Dict]:
    """获取巡检记录的原始输出数据"""
    from app.db.models import InspectionItemResult, InspectionEvidence
    
    results = db.query(InspectionItemResult).filter(
        InspectionItemResult.run_id == run_id
    ).order_by(InspectionItemResult.category).all()
    
    output_data = []
    for r in results:
        # 关联证据
        evidence_content = None
        if r.evidence_id:
            ev = db.query(InspectionEvidence).filter(InspectionEvidence.id == r.evidence_id).first()
            if ev:
                evidence_content = ev.content_snapshot
        
        output_data.append({
            "id": r.id,
            "category": r.category,
            "item_code": r.item_code,
            "item_name": r.item_name,
            "status": r.status,
            "risk_level": r.risk_level,
            "message": r.message,
            "suggestion": r.suggestion,
            "raw_output": r.raw_output,
            "evidence_content": evidence_content,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "finished_at": r.finished_at.isoformat() if r.finished_at else None,
        })
    return output_data
