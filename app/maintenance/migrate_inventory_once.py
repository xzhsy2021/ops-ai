"""
一次性把 config_kv 里的资产数据迁到 SQLAlchemy 资源表(servers / services /
environments / server_groups),迁完即可废弃对 config_kv 资产键的写入。

调用链路:
    config_kv["servers"/"systems"/...]  (JSON blob,旧存储)
            │
            ▼  (本脚本)
    sync_config_assets_to_db()  [在 app.maintenance.config_asset_sync]
            │
            ▼
    servers / services / environments / server_groups  (SQLAlchemy,新 SSOT)

特性:
- 幂等可重入:同一脚本可多次执行,不会破坏已有数据。
- 安全默认:--no-apply 走事务回滚,只看不写。--apply 才会真正落库。
- 借力已有实现:不重新实现 upsert,直接复用 _upsert_* 与 sync_* 函数。

用法:
    # 1) 仅观察:不写库,所有改动会在事务末尾回滚
    python -m app.maintenance.migrate_inventory_once --no-apply

    # 2) 真迁移:把所有 config_kv 资产键补回 SQLAlchemy 表
    python -m app.maintenance.migrate_inventory_once --apply

    # 3) 强覆盖:连 UI 已填的字段也覆盖(config_kv 视为唯一真相)
    python -m app.maintenance.migrate_inventory_once --apply --overwrite

    # 4) 删 config_kv 资产键(必须在 --apply 后)
    python -m app.maintenance.migrate_inventory_once --apply --drop-legacy

注意:
- --drop-legacy 不可逆。务必先 --apply 跑通,核对 summary 之后再用。
- 跑此脚本前请先做一次 ops.create_backup(在 MCP / 前端触发)。
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Any, Dict

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(message)s",
)
log = logging.getLogger("migrate_inventory_once")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="一次性把 config_kv 资产数据迁到 SQLAlchemy 资源表(SSOT)。",
    )
    mode = p.add_mutually_exclusive_group()
    mode.add_argument(
        "--no-apply",
        action="store_true",
        help="事务回滚模式,只看不写(默认)。",
    )
    mode.add_argument(
        "--apply",
        action="store_true",
        help="真迁移,把 config_kv 资产数据写进 SQLAlchemy 表。",
    )
    p.add_argument(
        "--overwrite",
        action="store_true",
        help="覆盖 UI 已填字段(config_kv 当唯一真相)。默认只补缺。",
    )
    p.add_argument(
        "--drop-legacy",
        action="store_true",
        help="迁移完成后删除 config_kv 中的资产键(servers/systems/jump_hosts/groups/environments/services)。不可逆。",
    )
    p.add_argument(
        "--yes",
        action="store_true",
        help="跳过交互确认。",
    )
    return p.parse_args()


def _table_counts(db) -> Dict[str, int]:
    from app.db.models import Server, Service, Environment, ServerGroup
    return {
        "servers":       db.query(Server).count(),
        "services":      db.query(Service).count(),
        "environments":  db.query(Environment).count(),
        "server_groups": db.query(ServerGroup).count(),
    }


def _config_kv_inventory(db) -> Dict[str, Any]:
    """返回 config_kv 中与资产相关的键与体量,供人工判断。"""
    from app.db.models import ConfigKV
    out: Dict[str, Any] = {}
    legacy_keys = {"servers", "systems", "jump_hosts", "groups", "environments", "services"}
    rows = db.query(ConfigKV).filter(ConfigKV.key.in_(legacy_keys)).all()
    for r in rows:
        try:
            parsed = json.loads(r.value) if r.value else None
        except Exception as exc:  # noqa: BLE001
            parsed = f"<parse error: {exc}>"
        if isinstance(parsed, list):
            out[r.key] = {"type": "list", "count": len(parsed), "preview": parsed[:3]}
        elif isinstance(parsed, dict):
            out[r.key] = {"type": "dict", "top_keys": list(parsed.keys())[:10]}
        else:
            out[r.key] = {"type": type(parsed).__name__, "value": parsed}
    return out


def _drop_legacy_kv_keys(db, *, commit: bool) -> list[str]:
    """删除 config_kv 中的资产键。非资产键(workflow_templates 等)不动。"""
    from app.db.models import ConfigKV
    legacy_keys = {"servers", "systems", "jump_hosts", "groups", "environments", "services"}
    rows = db.query(ConfigKV).filter(ConfigKV.key.in_(legacy_keys)).all()
    removed: list[str] = []
    for r in rows:
        removed.append(r.key)
        db.delete(r)
    if commit:
        db.commit()
    return removed


def main() -> int:
    args = _parse_args()
    is_apply = bool(args.apply)
    is_dryrun = not is_apply

    if args.drop_legacy and not is_apply:
        log.error("--drop-legacy 必须配合 --apply 使用(否则没有意义)")
        return 2

    if args.drop_legacy and not args.yes:
        log.warning("你即将删除 config_kv 中的资产键,该操作不可逆。")
        log.warning("重新执行时加上 --yes 以跳过此提示。")
        return 3

    from app.db import SessionLocal
    from app.config.repository import load_config
    from app.maintenance.config_asset_sync import sync_config_assets_to_db

    cfg = load_config() or {}
    log.info("config_kv 顶层键: %s", sorted(list((cfg or {}).keys())))

    with SessionLocal() as db:
        # 1) 资产键现状(给人看)
        kv_inventory = _config_kv_inventory(db)
        log.info("config_kv 资产键清单:")
        for k, v in kv_inventory.items():
            log.info("  - %s: %s", k, json.dumps(v, ensure_ascii=False))

        # 2) 迁移前 DB 计数
        before = _table_counts(db)
        log.info("迁移前 DB 行数: %s", before)

        # 3) 真正跑同步
        if is_dryrun:
            log.info("[DRY-RUN] 开启事务,执行 sync_config_assets_to_db(overwrite=%s) 后回滚", args.overwrite)
            # 用 savepoint 风格:open nested transaction,最后 rollback
            db.begin_nested()
            summary = sync_config_assets_to_db(db, config=cfg, overwrite=args.overwrite)
            db.rollback()
            log.info("[DRY-RUN] 已回滚,无任何数据落库。")
        else:
            log.info("[APPLY] 正在执行 sync_config_assets_to_db(overwrite=%s) ...", args.overwrite)
            summary = sync_config_assets_to_db(db, config=cfg, overwrite=args.overwrite)
            db.commit()
            log.info("[APPLY] 已 commit。")

        # 4) 迁移后 DB 计数(回滚后应与 before 相同)
        after = _table_counts(db)
        log.info("迁移后 DB 行数: %s", after)
        log.info("DB 行数变化:    %s", {k: after[k] - before[k] for k in before})

        # 5) 输出 summary
        log.info("同步汇总 summary:")
        print(json.dumps(summary, ensure_ascii=False, indent=2))

        # 6) 删除 config_kv 资产键(仅在 --apply + --drop-legacy)
        if args.drop_legacy and is_apply:
            removed = _drop_legacy_kv_keys(db, commit=True)
            log.info("已从 config_kv 删除的资产键: %s", removed)

    log.info("完成。模式: %s", "APPLIED" if is_apply else "DRY-RUN")
    return 0


if __name__ == "__main__":
    sys.exit(main())
