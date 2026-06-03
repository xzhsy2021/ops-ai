from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from typing import Dict, List, Tuple

logger = logging.getLogger(__name__)

MAX_SUBSCRIBERS_PER_DEPLOYMENT = 16
SUBSCRIBER_TTL_SECONDS = 30 * 60

_subscribers: Dict[str, List[Tuple[asyncio.Queue, float]]] = {}
_lock = threading.Lock()


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _is_expired(created_at: float) -> bool:
    return (time.monotonic() - created_at) > SUBSCRIBER_TTL_SECONDS


def publish(deployment_id: str, event: str, data: dict) -> None:
    payload = json.dumps(data, ensure_ascii=False, default=str)
    with _lock:
        entries = list(_subscribers.get(deployment_id, []))
    dead: List[Tuple[asyncio.Queue, float]] = []
    for q, created_at in entries:
        if _is_expired(created_at):
            dead.append((q, created_at))
            continue
        try:
            q.put_nowait((event, payload))
        except asyncio.QueueFull:
            dead.append((q, created_at))
    if dead:
        with _lock:
            subs = _subscribers.get(deployment_id, [])
            for entry in dead:
                if entry in subs:
                    subs.remove(entry)
            if not subs:
                _subscribers.pop(deployment_id, None)


def publish_log(deployment_id: str, task_id: str, level: str, message: str,
                step_name: str = None) -> None:
    publish(deployment_id, "log", {
        "task_id": task_id,
        "level": level,
        "message": message,
        "step_name": step_name,
        "created_at": _now_iso(),
    })


def publish_status(deployment_id: str, task_id: str, status: str) -> None:
    publish(deployment_id, "status", {
        "task_id": task_id,
        "status": status,
        "created_at": _now_iso(),
    })


def publish_done(deployment_id: str, status: str) -> None:
    publish(deployment_id, "done", {
        "deployment_id": deployment_id,
        "final_status": status,
        "created_at": _now_iso(),
    })


class SubscriberLimitExceeded(Exception):
    pass


async def subscribe(deployment_id: str) -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=1000)
    created_at = time.monotonic()
    with _lock:
        subs = _subscribers.setdefault(deployment_id, [])
        subs = [(q2, ca) for q2, ca in subs if not _is_expired(ca)]
        if len(subs) >= MAX_SUBSCRIBERS_PER_DEPLOYMENT:
            raise SubscriberLimitExceeded(
                f"deployment {deployment_id} has {len(subs)} subscribers (max {MAX_SUBSCRIBERS_PER_DEPLOYMENT})"
            )
        subs.append((q, created_at))
        _subscribers[deployment_id] = subs
    return q


def unsubscribe(deployment_id: str, q: asyncio.Queue) -> None:
    with _lock:
        subs = _subscribers.get(deployment_id)
        if subs:
            subs[:] = [(q2, ca) for q2, ca in subs if q2 is not q]
            if not subs:
                _subscribers.pop(deployment_id, None)


def cleanup_expired_subscribers() -> int:
    removed = 0
    with _lock:
        to_remove_keys: List[str] = []
        for dep_id, subs in _subscribers.items():
            before = len(subs)
            subs[:] = [(q, ca) for q, ca in subs if not _is_expired(ca)]
            removed += before - len(subs)
            if not subs:
                to_remove_keys.append(dep_id)
        for k in to_remove_keys:
            _subscribers.pop(k, None)
    if removed:
        logger.info("Cleaned up %d expired SSE subscribers", removed)
    return removed


def shutdown_all_subscribers() -> None:
    with _lock:
        for dep_id, subs in _subscribers.items():
            for q, _ in subs:
                try:
                    q.put_nowait(("done", json.dumps({"deployment_id": dep_id, "final_status": "shutdown", "created_at": _now_iso()})))
                except asyncio.QueueFull:
                    pass
        _subscribers.clear()


async def sse_generator(deployment_id: str):
    try:
        q = await subscribe(deployment_id)
    except SubscriberLimitExceeded:
        yield f"event: error\ndata: {{\"error\": \"503 - subscriber limit exceeded\"}}\n\n"
        return
    try:
        yield f": heartbeat\n\n"
        while True:
            try:
                event, data = await asyncio.wait_for(q.get(), timeout=15.0)
                yield f"event: {event}\ndata: {data}\n\n"
            except asyncio.TimeoutError:
                yield f": heartbeat {int(time.time())}\n\n"
    except asyncio.CancelledError:
        pass
    finally:
        unsubscribe(deployment_id, q)
