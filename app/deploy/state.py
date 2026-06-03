"""Small deployment state helpers shared by API, worker and tests.

The project deliberately keeps deployment orchestration lightweight for local
and small-team installs.  Centralising status semantics prevents subtle bugs
where a canceled/failed task is accidentally marked successful later in the
same worker loop.
"""
from __future__ import annotations

from typing import Any

PENDING = "pending"
RUNNING = "running"
SUCCESS = "success"
FAILED = "failed"
CANCELED = "canceled"
CANCELLED = "cancelled"  # legacy spelling accepted for old rows/API clients
SKIPPED = "skipped"
ROLLBACKED = "rollbacked"
PARTIAL_FAILED = "partial_failed"

TERMINAL_STATUSES = {SUCCESS, FAILED, CANCELED, CANCELLED, SKIPPED, ROLLBACKED, PARTIAL_FAILED}
ACTIVE_STATUSES = {PENDING, RUNNING}
CANCELED_STATUSES = {CANCELED, CANCELLED}


def normalize_status(status: str | None) -> str:
    value = str(status or "").strip().lower()
    if value == CANCELLED:
        return CANCELED
    return value


def is_terminal_status(status: str | None) -> bool:
    return normalize_status(status) in TERMINAL_STATUSES


def is_active_status(status: str | None) -> bool:
    return normalize_status(status) in ACTIVE_STATUSES


def is_canceled_status(status: str | None) -> bool:
    return normalize_status(status) in CANCELED_STATUSES


def task_cancel_requested(task: Any) -> bool:
    if not task:
        return False
    return bool(getattr(task, "cancel_requested", False) or is_canceled_status(getattr(task, "status", "")))
