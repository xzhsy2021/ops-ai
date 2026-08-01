"""Inspection tier dispatcher (DB-driven, 3-tier DAILY/WEEKLY/MONTHLY).

设计要点：
- 目标统一为全量在线服务器（按 Server.status != "deleted" + ACTIVE 状态过滤），
  不再使用 target_filter 字段。
- 真正区分三级的是 `categories`（巡检项组合）。
- 月巡检 (MONTHLY) 默认 `require_approval=True`，首跑前需手动审批解锁。
- 配置文件不再依赖 yaml；DB 中的 3 行 tier 走 seed 脚本初始化。

使用方式（worker）：
    python -m app.services.inspection_tier_dispatcher --loop --interval 30
单次扫描：
    python -m app.services.inspection_tier_dispatcher --once
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

try:
    from croniter import croniter  # type: ignore
except ImportError:  # fallback: simple stub
    croniter = None

from sqlalchemy import select

from app.db import SessionLocal
from app.db.models import (
    InspectionTierSchedule,
    Server,
    _utcnow,
)

log = logging.getLogger("inspection.dispatcher")


# ──────────────────────────────────────────────────────────────────────────
# 工具函数
# ──────────────────────────────────────────────────────────────────────────

def compute_next_run_at(cron_expr: str, base: Optional[datetime] = None) -> Optional[datetime]:
    """从 cron 表达式计算下一次执行时间（naive UTC）。"""
    base = base or _utcnow()
    if croniter is not None:
        try:
            itr = croniter(cron_expr, base)
            return itr.get_next(datetime)
        except Exception as exc:  # noqa
            log.warning("croniter failed for %r: %s", cron_expr, exc)
            return None
    # fallback: 简单按小时整点对齐
    return base.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)


def resolve_target_servers_all() -> List[Server]:
    """拉取全量"可巡检"服务器（status != deleted 且 != disabled/offline）。

    注：与 inspection_center.ACTIVE_SERVER_STATUSES 保持一致语义。
    """
    db = SessionLocal()
    try:
        rows = db.execute(
            select(Server).where(
                Server.status.notin_(["deleted", "disabled", "offline", "inactive", "decommissioned"])
            )
        ).scalars().all()
        return list(rows)
    finally:
        db.close()


def build_category_args(categories: Any) -> List[str]:
    """把 tier.categories 翻译成 inspection.run 的 categories 参数（code 列表）。"""
    if not categories:
        return []
    if isinstance(categories, str):
        try:
            categories = json.loads(categories)
        except Exception:
            return []
    if not isinstance(categories, list):
        return []
    out: List[str] = []
    for item in categories:
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, dict) and item.get("code"):
            out.append(str(item["code"]))
    # 去重保序
    seen = set()
    deduped: List[str] = []
    for c in out:
        if c not in seen:
            seen.add(c)
            deduped.append(c)
    return deduped


def evaluate_thresholds(issues: List[Any], thresholds: Dict[str, Any]) -> Tuple[str, List[str]]:
    """根据 issue 列表 + 阈值返回 (severity, reasons)。"""
    high = sum(1 for i in issues if (getattr(i, "risk_level", "") or "").lower() in ("high", "critical"))
    medium = sum(1 for i in issues if (getattr(i, "risk_level", "") or "").lower() == "medium")
    fail_below = int(thresholds.get("score_fail_below") or 70)
    high_limit = int(thresholds.get("high_issue_count") if thresholds.get("high_issue_count") is not None else 1)
    medium_limit = int(thresholds.get("medium_issue_count") or 3)
    reasons: List[str] = []
    if high >= high_limit:
        reasons.append(f"high_issues={high}>={high_limit}")
    if medium >= medium_limit:
        reasons.append(f"medium_issues={medium}>={medium_limit}")
    if reasons:
        return ("high" if high else "medium"), reasons
    return ("pass", [])


# ──────────────────────────────────────────────────────────────────────────
# 单个 tier 执行
# ──────────────────────────────────────────────────────────────────────────

def execute_tier(schedule: InspectionTierSchedule) -> Dict[str, Any]:
    """执行一个 tier，返回执行结果 dict。"""
    log.info(
        "▶ tier=%s name=%s cron=%s starting",
        schedule.tier, schedule.name, schedule.cron_expression,
    )

    # 解析 JSON 字段（ORM 读取后是 dict/list；如果 seed 直接写了 str 也能兼容）
    def _as_dict(v: Any) -> Dict[str, Any]:
        if isinstance(v, dict):
            return v
        if isinstance(v, str):
            try:
                return json.loads(v) if v.strip() else {}
            except Exception:
                return {}
        return {}

    def _as_list(v: Any) -> List[Any]:
        if isinstance(v, list):
            return v
        if isinstance(v, str):
            try:
                return json.loads(v) if v.strip() else []
            except Exception:
                return []
        return []

    thresholds = _as_dict(schedule.thresholds)
    categories = _as_list(schedule.categories)

    servers = resolve_target_servers_all()
    if not servers:
        msg = "no active servers"
        log.warning("  skip: %s", msg)
        return {"status": "skipped", "reason": msg, "server_count": 0}

    cat_codes = build_category_args(categories)
    log.info("  servers=%d categories=%s", len(servers), cat_codes)

    started = _utcnow()
    try:
        # 懒导入避免循环依赖
        from app.services.inspection_center import run_servers_batch_inspection

        timeout_sec = int((schedule.timeout_minutes or 60) * 60)
        batch_result = run_servers_batch_inspection(
            SessionLocal(),
            server_ids=[s.id for s in servers],
            categories=cat_codes,
            trigger_type=schedule.tier.upper(),  # DAILY / WEEKLY / MONTHLY
            created_by=f"tier:{schedule.tier}",
            generate_report=False,                # 报告由后续 reporter 异步生成
            concurrency=int(schedule.concurrency or 3),
            command_timeout_seconds=min(120, max(10, timeout_sec // max(1, len(cat_codes) or 1))),
            run_timeout_seconds=timeout_sec,
            skip_disabled=True,
            all_servers=False,
        )
        # batch_result 是 dict（来自 run_servers_batch_inspection）
        # 它返回一个 "runs" 列表（每个 run 一台 server）+ summary
        runs = batch_result.get("runs") or []
        # 取本批次的 issue 列表（汇总所有 run 的 issue）
        all_issues: List[Any] = []
        for r in runs:
            issues = r.get("issues") or []
            all_issues.extend(issues)
        # 计算 score
        score = int(batch_result.get("score") or batch_result.get("avg_score") or 0)
        if not score:
            high = sum(1 for i in all_issues if (getattr(i, "risk_level", "") or "").lower() in ("high", "critical"))
            medium = sum(1 for i in all_issues if (getattr(i, "risk_level", "") or "").lower() == "medium")
            low = sum(1 for i in all_issues if (getattr(i, "risk_level", "") or "").lower() == "low")
            from app.services.inspection_center import _compute_score
            score = _compute_score(high, medium, low, server_count=len(servers))

        severity, reasons = evaluate_thresholds(all_issues, thresholds)

        primary_run_id = runs[0].get("id") if runs else None

        result = {
            "status": "success" if severity != "high" else "high_alert",
            "tier": schedule.tier,
            "run_id": primary_run_id,
            "run_ids": [r.get("id") for r in runs if r.get("id")],
            "score": score,
            "severity": severity,
            "reasons": reasons,
            "server_count": len(servers),
            "category_count": len(cat_codes),
            "issue_count": len(all_issues),
            "started_at": started.isoformat(),
            "finished_at": _utcnow().isoformat(),
        }
        log.info(
            "  ✓ done severity=%s score=%s issues=%d servers=%d",
            severity, score, len(all_issues), len(servers),
        )
        return result
    except Exception as exc:  # noqa
        log.exception("  ✗ tier failed: %s", exc)
        return {
            "status": "failed",
            "error": str(exc),
            "started_at": started.isoformat(),
            "finished_at": _utcnow().isoformat(),
        }


# ──────────────────────────────────────────────────────────────────────────
# Worker 循环
# ──────────────────────────────────────────────────────────────────────────

def tick_once() -> List[Dict[str, Any]]:
    """扫描所有 enabled schedule，把 due 的执行一次，返回结果列表。"""
    db = SessionLocal()
    results: List[Dict[str, Any]] = []
    try:
        now = _utcnow()
        schedules = db.execute(
            select(InspectionTierSchedule).where(InspectionTierSchedule.enabled == True)  # noqa: E712
        ).scalars().all()
        for sch in schedules:
            # 1) 第一次执行：next_run_at 为空 → 算一次
            if not sch.next_run_at:
                sch.next_run_at = compute_next_run_at(sch.cron_expression, base=now)
                db.commit()
            # 2) 未到期：跳过
            if sch.next_run_at and sch.next_run_at > now:
                continue
            # 3) 需审批且从未跑过：跳过（等 UI/工具审批解锁）
            if sch.require_approval and not sch.last_run_at:
                log.info("  ⏸ %s requires approval before first run, skip", sch.name)
                continue
            # 4) 执行
            result = execute_tier(sch)
            # 5) 更新 last_* 与 next_run_at
            sch.last_run_at = _utcnow()
            sch.last_status = result.get("status")
            sch.last_error = result.get("error")
            sch.last_run_id = result.get("run_id")
            sch.next_run_at = compute_next_run_at(sch.cron_expression, base=sch.last_run_at)
            db.commit()
            results.append(result)
    finally:
        db.close()
    return results


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s | %(message)s",
    )
    p = argparse.ArgumentParser()
    p.add_argument("--loop", action="store_true", help="run as a long-lived worker (poll every 30s)")
    p.add_argument("--once", action="store_true", help="run a single tick and exit")
    p.add_argument("--interval", type=int, default=30, help="poll interval in seconds (default 30)")
    args = p.parse_args()
    if not (args.loop or args.once):
        args.once = True
    if args.once:
        results = tick_once()
        print(json.dumps(results, ensure_ascii=False, indent=2, default=str))
        return 0
    log.info("starting inspection tier dispatcher (loop mode, interval=%ds)", args.interval)
    while True:
        try:
            results = tick_once()
            if results:
                log.info("tick: %d tier(s) executed", len(results))
        except Exception as exc:  # noqa
            log.exception("tick error: %s", exc)
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
