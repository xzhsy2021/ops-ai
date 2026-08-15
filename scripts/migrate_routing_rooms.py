"""One-time data migration: move Matrix room IDs out of message_routing.approvers
into the new message_routing.rooms field.

Idempotent: only touches systems whose approvers contain matrix sender_id starting
with '!'. Existing rooms are preserved/merged.
"""
from __future__ import annotations

import json

from app.db.base import SessionLocal
from app.db.models import System
from app.services.message_context import normalize_conversation_binding, normalize_identity


def _split_rooms_from_approvers(routing: dict) -> tuple[list, list, bool]:
    approvers = routing.get("approvers") or []
    existing_rooms = routing.get("rooms") or []
    keep_approvers = []
    moved_rooms = []
    changed = False
    for item in approvers:
        try:
            identity = normalize_identity(item)
        except Exception:
            keep_approvers.append(item)
            continue
        if identity["channel"] == "matrix" and identity["sender_id"].startswith("!"):
            moved_rooms.append(
                {
                    "channel": "matrix",
                    "channel_account_id": identity["channel_account_id"],
                    "conversation_id": identity["sender_id"],
                }
            )
            changed = True
        else:
            keep_approvers.append(item)
    if changed:
        merged_rooms = list(existing_rooms)
        seen = {
            (b["channel"], b["channel_account_id"], b["conversation_id"])
            for b in merged_rooms
        }
        for room in moved_rooms:
            key = (room["channel"], room["channel_account_id"], room["conversation_id"])
            if key not in seen:
                seen.add(key)
                merged_rooms.append(room)
        return keep_approvers, merged_rooms, True
    return approvers, existing_rooms, False


def main() -> None:
    db = SessionLocal()
    try:
        total_changed = 0
        for sys_row in db.query(System).all():
            routing = sys_row.message_routing
            if not isinstance(routing, dict):
                continue
            new_approvers, new_rooms, changed = _split_rooms_from_approvers(routing)
            if not changed:
                continue
            routing["approvers"] = new_approvers
            if new_rooms:
                routing["rooms"] = new_rooms
            # 赋一个全新的顶层 dict，确保 SQLAlchemy JSON 列检测到变更并持久化
            sys_row.message_routing = dict(routing)
            db.add(sys_row)
            total_changed += 1
            print(f"[migrate] {sys_row.name}: approvers={json.dumps(new_approvers, ensure_ascii=False)} rooms={json.dumps(new_rooms, ensure_ascii=False)}")
        db.commit()
        print(f"[migrate] done: {total_changed} system(s) updated")
    finally:
        db.close()


if __name__ == "__main__":
    main()