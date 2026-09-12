#!/usr/bin/env python
"""一次性修复：把历史审计记录的时间戳从"服务器本地时间"迁移为 naive UTC。

背景
----
`app/config/audit.py::save_audit_log` 原先用 `datetime.now()`（服务器本地时间）写入
`audit_records.created_at`，而系统内其余写入（`app.db.models._utcnow` 及 69 处调用）
统一是 naive UTC。本机时区为 UTC+8，于是：

* 同一条操作在「审计日志」页显示 21:41、在「MCP 审计/任务中心」页显示 13:41；
* 审计链路把两种时基的记录合并按时间排序 → 跨源时间线错位 ±8h；
* 按日期过滤时，取字符串前 10 位得到的"业务日期"与其它页面不一致。

修复分两步：写入端已改为 naive UTC（本脚本所在提交）；历史数据由本脚本一次性回迁。

用法
----
    python scripts/fix_audit_timezone.py --check        # 只读体检（默认行为）
    python scripts/fix_audit_timezone.py --apply        # 备份数据库后回迁（-8h）

脚本只改 `audit_records.created_at` 一列，先备份整库（sqlite backup API，含 WAL），
并在同一事务内写入、回读校验；解析失败的行会被跳过并列出行号。
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def log(message: str = "") -> None:
    print(message, flush=True)


def load_env(path: Path | None = None) -> None:
    env_path = path or (ROOT / ".env")
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def database_path() -> Path:
    explicit = os.getenv("OPS_DB_PATH")
    if explicit:
        return Path(explicit).resolve()
    app_data = os.getenv("APP_DATA_DIR") or str(ROOT / "data")
    return (Path(app_data) / "ops.db").resolve()


def local_utc_offset_hours() -> float:
    offset = datetime.now().astimezone().utcoffset() or timedelta(0)
    return offset.total_seconds() / 3600.0


def backup_database(db_path: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = db_path.with_name(f"{db_path.name}.bak-tz-{stamp}")
    source = sqlite3.connect(str(db_path))
    target = sqlite3.connect(str(backup))
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    return backup


def collect_rows(connection: sqlite3.Connection) -> List[Tuple[int, str]]:
    return [(int(row[0]), str(row[1] or "")) for row in connection.execute("SELECT id, created_at FROM audit_records")]


def plan_shift(rows: List[Tuple[int, str]], offset_hours: float) -> Tuple[List[Tuple[int, str, str]], List[Tuple[int, str]]]:
    delta = timedelta(hours=offset_hours)
    updates: List[Tuple[int, str, str]] = []
    skipped: List[Tuple[int, str]] = []
    for row_id, value in rows:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00").replace(" ", "T"))
        except ValueError:
            skipped.append((row_id, value))
            continue
        shifted = (parsed - delta) if parsed.tzinfo is None else (parsed.astimezone(timezone.utc).replace(tzinfo=None) - delta)
        updates.append((row_id, value, shifted.isoformat()))
    return updates, skipped


def main() -> int:
    parser = argparse.ArgumentParser(description="把历史审计记录时间戳回迁为 naive UTC")
    parser.add_argument("--check", action="store_true", help="只读体检（默认）")
    parser.add_argument("--apply", action="store_true", help="真正写入（默认 dry-run）")
    parser.add_argument("--offset-hours", type=float, default=None,
                        help="本地时区相对 UTC 的小时数（默认取当前系统时区，本机为 8）")
    parser.add_argument("--env-file", default=None, help="指定 .env（默认 <repo>/.env）")
    args = parser.parse_args()

    load_env(Path(args.env_file) if args.env_file else None)
    db_path = database_path()
    if not db_path.exists():
        log(f"[错误] 数据库不存在: {db_path}")
        return 2

    offset = args.offset_hours if args.offset_hours is not None else local_utc_offset_hours()
    log(f"数据库: {db_path}")
    log(f"时区偏移: UTC{offset:+g}（将把历史时间戳减去 {offset:g} 小时 → naive UTC）")

    connection = sqlite3.connect(str(db_path))
    try:
        rows = collect_rows(connection)
        if not rows:
            log("[完成] audit_records 为空，无需迁移")
            return 0
        updates, skipped = plan_shift(rows, offset)
        stamps = sorted(value for _, value in rows if value)
        log(f"记录数: {len(rows)}；可迁移: {len(updates)}；无法解析: {len(skipped)}")
        log(f"当前最小/最大 created_at: {stamps[0]} / {stamps[-1]}")
        if updates:
            before = [u for u in updates if u[1]][0]
            after = updates[[i for i, u in enumerate(updates) if u[1]][0]]
            log(f"示例: id={after[0]}  {after[1]}  ->  {after[2]}")
        for row_id, value in skipped[:5]:
            log(f"[跳过] id={row_id} created_at={value!r} 无法解析")
        if offset == 0:
            log("[完成] 偏移为 0（服务器即 UTC），无需迁移")
            return 0
        if not args.apply:
            log("")
            log("[dry-run] 未写入。确认无误后执行：python scripts/fix_audit_timezone.py --apply")
            return 0

        backup = backup_database(db_path)
        log(f"[备份] {backup}")
        with connection:  # 单事务
            connection.executemany(
                "UPDATE audit_records SET created_at = ? WHERE id = ?",
                [(new, row_id) for row_id, _old, new in updates],
            )
        # 回读校验
        after_rows = dict(collect_rows(connection))
        mismatched = [(rid, old, new) for rid, old, new in updates if after_rows.get(rid) != new]
        if mismatched:
            log(f"[失败] 回读校验不一致 {len(mismatched)} 行，请从备份恢复：{backup}")
            return 1
        new_stamps = sorted(after_rows.values())
        log(f"[完成] 已迁移 {len(updates)} 行；新最小/最大: {new_stamps[0]} / {new_stamps[-1]}")
        log("提示：重启 OPS 后审计页时间将与其它的工具调用/任务页一致（均为本地时间显示）。")
        return 0
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
