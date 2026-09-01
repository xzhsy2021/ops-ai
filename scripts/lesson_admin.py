"""教训库管理：确认 pending 教训 / 置 superseded / 列表查看。

用法（venv python，PYTHONPATH=D:\\code\\ops-ai）：
  python scripts/lesson_admin.py list [--status pending|active|superseded]
  python scripts/lesson_admin.py activate L009
  python scripts/lesson_admin.py supersede L003 --note "2026-09-xx OPS 已修复（commit xxx）"
  python scripts/lesson_admin.py add --pattern "..." --guidance "..." [--severity warning]

对应 docs/agent-integration-abstraction-layer.md Phase 2：OPS 侧修复落地时
用 supersede 把相关教训置失效（附修复说明），从根上消除旧结论误导。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:  # Windows GBK 控制台兜底
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from app.db.base import SessionLocal  # noqa: E402
from app.db.models import AgentLesson  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Agent 教训库管理")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="列出教训")
    p_list.add_argument("--status", default="", help="过滤状态：pending/active/superseded")

    p_act = sub.add_parser("activate", help="确认 pending 教训为 active")
    p_act.add_argument("lesson_id")

    p_sup = sub.add_parser("supersede", help="置教训为 superseded（失效）")
    p_sup.add_argument("lesson_id")
    p_sup.add_argument("--note", required=True, help="失效说明（修复 commit/原因）")

    p_add = sub.add_parser("add", help="人工录入教训（直接 active）")
    p_add.add_argument("--pattern", required=True)
    p_add.add_argument("--guidance", required=True)
    p_add.add_argument("--severity", default="warning", choices=["info", "warning"])

    args = parser.parse_args()
    db = SessionLocal()
    try:
        if args.cmd == "list":
            query = db.query(AgentLesson)
            if args.status:
                query = query.filter(AgentLesson.status == args.status)
            rows = query.order_by(AgentLesson.created_at).all()
            if not rows:
                print("（库中无 AgentLesson 记录；内置静态教训见 agent_context.LESSONS）")
                return 0
            for row in rows:
                flag = {"pending": "◻", "active": "✓", "superseded": "✗"}.get(row.status, "?")
                print(f"{flag} {row.id} [{row.status}/{row.severity}] {row.pattern[:50]}")
                print(f"    -> {row.guidance[:80]}")
                if row.superseded_note:
                    print(f"    失效: {row.superseded_note[:80]}")
            return 0

        if args.cmd == "activate":
            row = db.get(AgentLesson, args.lesson_id)
            if not row:
                print(f"未找到 {args.lesson_id}")
                return 1
            row.status = "active"
            db.commit()
            print(f"{args.lesson_id} -> active（所有 agent 的 pack 将携带此教训）")
            return 0

        if args.cmd == "supersede":
            row = db.get(AgentLesson, args.lesson_id)
            if not row:
                print(f"未找到 {args.lesson_id}")
                return 1
            row.status = "superseded"
            row.superseded_note = args.note
            db.commit()
            print(f"{args.lesson_id} -> superseded：{args.note[:80]}")
            return 0

        if args.cmd == "add":
            import uuid

            row = AgentLesson(
                id=f"M{uuid.uuid4().hex[:6]}",
                pattern=args.pattern,
                guidance=args.guidance,
                severity=args.severity,
                status="active",
                origin="manual",
            )
            db.add(row)
            db.commit()
            print(f"已录入 {row.id}（active/manual）")
            return 0
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
