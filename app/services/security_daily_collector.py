"""安全日报采集 worker 入口：支持一次性或循环采集。

用法：
    python -m app.services.security_daily_collector --once
    python -m app.services.security_daily_collector --loop --interval 3600
    python -m app.services.security_daily_collector --once --date 2026-08-19
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time

from app.db.base import SessionLocal
from app.services.security_daily import collect_all

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")


def main() -> int:
    parser = argparse.ArgumentParser(description="安全日报采集器")
    parser.add_argument("--once", action="store_true", help="执行一次采集后退出")
    parser.add_argument("--loop", action="store_true", help="循环采集")
    parser.add_argument("--interval", type=int, default=3600,
                        help="循环间隔（秒），默认 3600")
    parser.add_argument("--date", default=None, help="采集指定日期（YYYY-MM-DD）")
    args = parser.parse_args()

    if args.once:
        result = collect_all(SessionLocal(), report_date=args.date)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        # 有高危项时以非零退出码告警便于 cron/监控感知
        if result.get("summary", {}).get("high_count"):
            return 1
        return 0
    if args.loop:
        while True:
            try:
                collect_all(SessionLocal(), report_date=args.date)
            except Exception as exc:  # noqa: BLE001
                logging.exception("security_daily collect loop error: %s", exc)
            time.sleep(max(60, int(args.interval)))
        return 0
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())