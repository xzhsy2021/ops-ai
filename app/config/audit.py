"""审计日志 — SSOT: ORM audit_records 表。

原 audit_logs 原生表已废弃，由 schema migration 081_002 DROP。
所有读写统一走 ORM AuditRecord 模型。

**时间约定**：所有落库时间戳一律 naive UTC（与 `app.db.models._utcnow` 相同约定，
系统内 69 处写入都遵循它）。审计记录历史上的写出者用 `datetime.now()`（服务器本地时间，
本机 UTC+8），导致同一时刻的审计记录比工具调用/任务时间戳"早 8 小时"显示、审计链路
跨源时间线排序错位、按日期过滤的边界偏移 ±8h；本文件已修正，历史数据由
`scripts/fix_audit_timezone.py` 一次性迁移。
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


def utcnow_naive() -> datetime:
    """当前 naive UTC（落库约定，勿改成 datetime.now()）。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def save_audit_log(action: str, target_type: str = None, target_name: str = None, details: str = None) -> bool:
    try:
        from app.db.base import SessionLocal
        from app.db.models import AuditRecord
        with SessionLocal() as db:
            record = AuditRecord(
                action=action,
                target_type=target_type,
                target_name=target_name,
                details=details,
                created_at=utcnow_naive().isoformat(),
            )
            db.add(record)
            db.commit()
            return True
    except Exception as e:
        logger.error(f"Failed to save audit log: {e}")
        return False


def load_audit_logs(limit: int = 1000) -> List[Dict[str, Any]]:
    try:
        from app.db.base import SessionLocal
        from app.db.models import AuditRecord
        with SessionLocal() as db:
            rows = db.query(AuditRecord).order_by(AuditRecord.created_at.desc()).limit(limit).all()
            return [
                {
                    "id": row.id,
                    "action": row.action,
                    "target_type": row.target_type,
                    "target_name": row.target_name,
                    "details": row.details,
                    "created_at": row.created_at,
                }
                for row in rows
            ]
    except Exception as e:
        logger.error(f"Failed to load audit logs: {e}")
        return []


AUDIT_LIST_MAX_LIMIT = 1000


def list_audit_records(
    db,
    *,
    limit: int = 200,
    offset: int = 0,
    action: str = "",
    since_id: str = "",
) -> Dict[str, Any]:
    """分页查询审计记录：过滤与计数都下推到 SQL，``total`` 是匹配的真实总数。

    历史缺陷（``/api/v2/audit``）：先 ``load_audit_logs(5000)`` 取最新 5000 条，
    再在内存里过滤 action、切页，并令 ``total = len(rows)``。后果：
      - 审计记录超过 5000 条后 total 被截断成 5000，更早的记录永远无法翻页到达；
      - 检索一个较久远才出现的 action 会静默返回空列表（库里明明有匹配记录）；
      - 导出 CSV 同样只在这 5000 条里过滤，静默丢数据。
    """
    from sqlalchemy import func

    from app.db.models import AuditRecord

    limit = max(1, min(200 if limit is None else int(limit), AUDIT_LIST_MAX_LIMIT))
    offset = max(0, int(offset or 0))
    query = db.query(AuditRecord)
    if action:
        # 与旧内存实现一致：大小写不敏感的子串匹配；转义 LIKE 通配符，避免 % / _ 被当模式
        needle = action.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        query = query.filter(func.lower(AuditRecord.action).like(f"%{needle}%", escape="\\"))
    if since_id:
        try:
            query = query.filter(AuditRecord.id < int(since_id))
        except (TypeError, ValueError):
            pass
    total = int(query.count() or 0)
    rows = (
        query.order_by(AuditRecord.created_at.desc(), AuditRecord.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return {
        "items": [
            {
                "id": row.id,
                "action": row.action,
                "target_type": row.target_type,
                "target_name": row.target_name,
                "details": row.details,
                "created_at": row.created_at,
            }
            for row in rows
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def cleanup_audit_logs(max_age_hours: int = 720):
    try:
        from app.db.base import SessionLocal
        from app.db.models import AuditRecord
        # 必须与写入端同一时基（naive UTC），否则按本地时间算截止点会多删 8 小时的记录
        cutoff = (utcnow_naive() - timedelta(hours=max_age_hours)).isoformat()
        with SessionLocal() as db:
            db.query(AuditRecord).filter(AuditRecord.created_at < cutoff).delete(synchronize_session=False)
            db.commit()
    except Exception as e:
        logger.error(f"Failed to cleanup audit logs: {e}")
