"""链路回放历史数据清理（dry-run 审查 + --apply 执行）。

背景（2026-09-04 诊断）：审计已 SSOT 化——AuditRecord 单表（audit_logs 原生表
已被 migration 081_002 DROP）。历史数据问题：
1. ToolPlan 4 条（2026-07-21/22）——旧"AI 计划"实验系统遗留，死数据；
   其 ToolCallLog.related_plan_id 引用造成链路回放混入幽灵节点
2. AuditRecord 4940 条中大量早期低风险行（inspection.issue.delete 刷量）
   ——按 14 天 + 高风险全留策略清理

保留不动（审计链核心）：
- ToolCallLog（回放主数据源）  - OperationJob（任务链）
- ExecutionPlan + steps（发布链）  - Deployment（旧发布记录，本就 0 行）
"""
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\code\ops-ai")

import os

def _env(n):
    for line in open(r"D:\code\ops-ai\.env", encoding="utf-8"):
        line = line.strip()
        if line.startswith(n + "="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""

os.environ.setdefault("APPROVAL_SIGNING_KEY", _env("APPROVAL_SIGNING_KEY"))

APPLY = "--apply" in sys.argv

from datetime import datetime, timedelta
from app.db.base import SessionLocal
from app.db.models import ToolPlan, ToolPlanEvent, ToolCallLog, AuditRecord, OperationJob, ExecutionPlan
from app.services.release_retention import _is_high_risk_action

db = SessionLocal()

print(f"模式：{'APPLY（实际删除）' if APPLY else 'DRY-RUN（只审不删）'}")
print()

# ── 1. ToolPlan 死数据（2026-07-23 之前的旧实验计划系统）──
cutoff = datetime(2026, 7, 23)
dead_plans = db.query(ToolPlan).filter(ToolPlan.created_at < cutoff).all()
print(f"[1] ToolPlan 早于 {cutoff.date()}：{len(dead_plans)} 条（旧实验系统遗留）")
for p in dead_plans:
    print(f"      {p.id[:8]}  {p.plan_type:20s} {p.status:10s} {p.created_at}")
if APPLY and dead_plans:
    ids = [p.id for p in dead_plans]
    n_ev = db.query(ToolPlanEvent).filter(ToolPlanEvent.plan_id.in_(ids)).delete(synchronize_session=False)
    db.query(ToolCallLog).filter(ToolCallLog.related_plan_id.in_(ids)).update(
        {ToolCallLog.related_plan_id: None}, synchronize_session=False)
    db.query(ToolPlan).filter(ToolPlan.id.in_(ids)).delete(synchronize_session=False)
    print(f"      -> 已删 {len(ids)} 计划 + {n_ev} 事件（引用置空，调用日志保留）")
print()

# ── 2. AuditRecord 低风险早期行（14 天 + 高风险全留）──
keep_after = datetime.utcnow() - timedelta(days=14)
old_rows = db.query(AuditRecord).filter(AuditRecord.created_at < keep_after).all()
keep_high = [r for r in old_rows if _is_high_risk_action(r.action)]
drop_rows = [r for r in old_rows if not _is_high_risk_action(r.action)]
total = db.query(AuditRecord).count()
print(f"[2] AuditRecord：总 {total}  早于14天 {len(old_rows)}（高风险保留 {len(keep_high)}）")
print(f"    清理候选 {len(drop_rows)} 条")
from collections import Counter
top = Counter(r.action for r in drop_rows).most_common(6)
for a, n in top:
    print(f"      {n:5d}  {a}")
if APPLY and drop_rows:
    db.query(AuditRecord).filter(AuditRecord.id.in_([r.id for r in drop_rows])).delete(synchronize_session=False)
    print(f"    -> 已删 {len(drop_rows)} 行")
print()

# ── 3. 不动清单 ──
print("[3] 保留不动（审计链核心）：")
print(f"    ToolCallLog    {db.query(ToolCallLog).count():5d} 行 —— 回放主数据源")
print(f"    OperationJob   {db.query(OperationJob).count():5d} 行 —— 任务审计链")
print(f"    ExecutionPlan  {db.query(ExecutionPlan).count():5d} 行 —— 发布审计链")

db.commit() if APPLY else db.rollback()
db.close()
print()
print("完成" if APPLY else "DRY-RUN 完成——确认后加 --apply 执行")
