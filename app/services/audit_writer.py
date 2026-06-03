from __future__ import annotations

import logging
import queue
import threading
import uuid
from typing import Any, Dict, Callable

logger = logging.getLogger(__name__)

_audit_queue: queue.Queue = queue.Queue(maxsize=2048)
_writer_thread: threading.Thread | None = None
_shutdown_event = threading.Event()


def _audit_writer_loop() -> None:
    while True:
        try:
            fn = _audit_queue.get(timeout=1.0)
        except queue.Empty:
            if _shutdown_event.is_set() and _audit_queue.empty():
                return
            continue
        try:
            fn()
        except Exception:
            logger.exception("Audit writer task failed")
        finally:
            _audit_queue.task_done()


def start_audit_writer() -> None:
    global _writer_thread
    if _writer_thread is not None and _writer_thread.is_alive():
        return
    _shutdown_event.clear()
    _writer_thread = threading.Thread(target=_audit_writer_loop, daemon=True, name="audit-writer")
    _writer_thread.start()
    logger.info("Audit writer thread started")


def shutdown_audit_writer(timeout: float = 5.0) -> None:
    _shutdown_event.set()
    if _writer_thread is not None and _writer_thread.is_alive():
        try:
            _audit_queue.join()
        except Exception:
            pass
        _writer_thread.join(timeout=timeout)
    remaining = _audit_queue.qsize()
    if remaining > 0:
        logger.warning("Audit writer shutdown with %d items remaining, draining synchronously", remaining)
        while not _audit_queue.empty():
            try:
                fn = _audit_queue.get_nowait()
                fn()
            except Exception:
                pass
    logger.info("Audit writer shutdown complete, queue size: %d", _audit_queue.qsize())


def submit_audit_write(fn: Callable[[], None]) -> None:
    try:
        _audit_queue.put_nowait(fn)
    except queue.Full:
        logger.warning("Audit queue full (size=%d), falling back to sync write", _audit_queue.qsize())
        try:
            fn()
        except Exception:
            pass


def record_tool_call_async(
    tool_name: str,
    ctx: Any,
    input_args: Dict[str, Any],
    normalized_args: Dict[str, Any] | None = None,
    result: Any = None,
    status: str = "success",
    risk_level: str = "low",
    policy_result: Dict[str, Any] | None = None,
    blocked_reason: str = "",
    related_plan_id: str = "",
    related_deployment_id: str = "",
    related_job_id: str = "",
    duration_ms: int | None = None,
    audit_id: str | None = None,
) -> str:
    resolved_audit_id = audit_id or uuid.uuid4().hex

    def _write() -> None:
        from app.db import SessionLocal
        from app.services.tool_audit import record_tool_call
        db = SessionLocal()
        try:
            record_tool_call(
                db,
                audit_id=resolved_audit_id,
                tool_name=tool_name,
                ctx=ctx,
                input_args=input_args,
                normalized_args=normalized_args,
                result=result,
                status=status,
                risk_level=risk_level,
                policy_result=policy_result,
                blocked_reason=blocked_reason,
                related_plan_id=related_plan_id,
                related_deployment_id=related_deployment_id,
                related_job_id=related_job_id,
                duration_ms=duration_ms,
            )
        except Exception:
            logger.exception("Async tool call audit write failed")
        finally:
            db.close()

    submit_audit_write(_write)
    return resolved_audit_id


def record_plan_event_async(
    plan_id: str,
    event_type: str,
    actor: str = "",
    message: str = "",
    payload: Any = None,
) -> str:
    event_id = uuid.uuid4().hex

    def _write() -> None:
        from app.db import SessionLocal
        from app.db.models import ToolPlanEvent
        db = SessionLocal()
        try:
            item = ToolPlanEvent(
                id=event_id,
                plan_id=plan_id,
                event_type=event_type,
                actor=actor,
                message=message,
                payload=payload or {},
            )
            db.add(item)
            db.commit()
        except Exception:
            logger.exception("Async tool plan event write failed")
        finally:
            db.close()

    submit_audit_write(_write)
    return event_id
