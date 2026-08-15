"""One-time data migration (Phase 2): remove token-level room/approver bindings.

房间/审批人绑定已统一到系统级 message_routing（rooms/approvers）。本脚本清空
存量 tool_tokens 上的 channel_bindings / approver_identities / bound_room_ids /
approver_matrix_ids，使 token 不再携带任何绑定数据。

Idempotent：重复执行无副作用；仅统计并清除非空绑定。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.base import SessionLocal
from app.db.models import ToolToken

BINDING_COLUMNS = (
    "channel_bindings",
    "approver_identities",
    "bound_room_ids",
    "approver_matrix_ids",
)


def _is_empty(value) -> bool:
    if not value:
        return True
    if isinstance(value, list):
        return len(value) == 0
    return False


def main() -> None:
    db = SessionLocal()
    try:
        total_cleared = 0
        affected_tokens = 0
        for row in db.query(ToolToken).all():
            changed = False
            for column in BINDING_COLUMNS:
                value = getattr(row, column, None)
                if not _is_empty(value):
                    print(
                        f"[migrate] {row.name} (id={row.id}).{column}: "
                        f"{json.dumps(value, ensure_ascii=False)} -> []"
                    )
                    setattr(row, column, [])
                    changed = True
            if changed:
                db.add(row)
                affected_tokens += 1
                total_cleared += 1
        db.commit()
        print(
            f"[migrate] done: cleared bindings on {affected_tokens} token(s) "
            f"(columns touched: {total_cleared})"
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()