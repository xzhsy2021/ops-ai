"""Small async worker lifecycle helper for deploy_v2.

The polling loop still lives near the deployment orchestration code, but task
lifecycle state is kept here so route files do not own mutable asyncio state.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional


@dataclass
class AsyncWorkerHandle:
    name: str
    task: Optional[asyncio.Task] = None
    started_at: Optional[datetime] = None

    def ensure_running(self, coro_factory: Callable[[], Awaitable[None]]) -> bool:
        """Ensure a background coroutine is scheduled on the current loop.

        Returns True when a new task is created, False when an existing task is
        still alive or no running event loop exists.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return False
        if self.task is None or self.task.done():
            self.started_at = datetime.now(timezone.utc).replace(tzinfo=None)
            self.task = loop.create_task(coro_factory())
            return True
        return False

    def status(self) -> dict:
        return {
            "name": self.name,
            "running": bool(self.task and not self.task.done()),
            "started_at": self.started_at.isoformat() if self.started_at else None,
        }
