"""Phase 3d 一次性脚本: 将 ServerGroup.name 'crypto-trader-crypto' 改名为 'crypto'。

前置条件: 部署/管道/任何业务表都未硬编码 'crypto-trader-crypto' 字面量
(已在 _check_crypto_group_refs.py 中确认零引用)。

用法:
  python -m app.maintenance.rename_crypto_group         # 默认 dry-run
  python -m app.maintenance.rename_crypto_group --apply # 实际写入
"""
from __future__ import annotations

import argparse
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(message)s",
)
log = logging.getLogger("rename_crypto_group")

OLD_NAME = "crypto-trader-crypto"
NEW_NAME = "crypto"


def _check_safety() -> int:
    """全表扫描: 任何 TEXT/JSON 列是否含 OLD_NAME。返回命中行数。"""
    import sqlite3
    conn = sqlite3.connect("data/ops.db")
    try:
        cur = conn.cursor()
        hits = 0
        for (table,) in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
            try:
                cols = cur.execute(f"PRAGMA table_info({table})").fetchall()
            except Exception:
                continue
            for col in cols:
                col_name = col[1]
                col_type = (col[2] or "").upper()
                if col_type not in ("TEXT", "JSON"):
                    continue
                try:
                    n = cur.execute(
                        f"SELECT COUNT(*) FROM {table} WHERE {col_name} LIKE ?",
                        (f"%{OLD_NAME}%",),
                    ).fetchone()[0]
                    if n > 0:
                        log.warning("REF: %s.%s has %d row(s) referencing %r",
                                    table, col_name, n, OLD_NAME)
                        hits += n
                except Exception:
                    continue
        return hits
    finally:
        conn.close()


def _rename(apply: bool) -> None:
    import sqlite3
    from datetime import datetime, timezone
    conn = sqlite3.connect("data/ops.db")
    try:
        cur = conn.cursor()
        cur.execute("SELECT id, name, display_name, server_names FROM server_groups WHERE name=?",
                    (OLD_NAME,))
        row = cur.fetchone()
        if not row:
            log.info("No row with name=%r; nothing to do.", OLD_NAME)
            return
        gid, name, display_name, server_names = row
        log.info("Found: id=%s name=%r display_name=%r server_names=%s",
                 gid, name, display_name, server_names)
        if not apply:
            log.info("[DRY-RUN] Would rename to name=%r. Re-run with --apply to commit.", NEW_NAME)
            return
        # Check for collision
        cur.execute("SELECT 1 FROM server_groups WHERE name=?", (NEW_NAME,))
        if cur.fetchone():
            log.error("ABORT: target name %r already exists.", NEW_NAME)
            sys.exit(2)
        cur.execute(
            "UPDATE server_groups SET name=?, updated_at=? WHERE id=?",
            (NEW_NAME, datetime.now(timezone.utc).replace(tzinfo=None), gid),
        )
        conn.commit()
        log.info("Renamed: %r -> %r (id=%s)", OLD_NAME, NEW_NAME, gid)
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="Rename ServerGroup 'crypto-trader-crypto' -> 'crypto'")
    parser.add_argument("--apply", action="store_true", help="commit the change (default: dry-run)")
    args = parser.parse_args()

    log.info("=" * 60)
    log.info("Phase 3d rename: %r -> %r", OLD_NAME, NEW_NAME)
    log.info("=" * 60)

    log.info("[1/2] Safety check: scanning all tables for refs to %r ...", OLD_NAME)
    hits = _check_safety()
    if hits > 0:
        log.error("Found %d reference(s) in other tables. Rename would break those refs. Aborting.", hits)
        log.error("Run scripts/_check_crypto_group_refs.py to see details.")
        sys.exit(1)
    log.info("No refs found. Safe to rename.")

    log.info("[2/2] Performing rename (apply=%s) ...", args.apply)
    _rename(apply=args.apply)

    log.info("Done.")


if __name__ == "__main__":
    main()
