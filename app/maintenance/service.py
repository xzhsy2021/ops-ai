import json
import logging
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.core.secret_store import decrypt_secret, encrypt_secret
from app.db.models import CleanupJob, CleanupJobBatch, CleanupJobEvent, DatabaseConnection
from app.maintenance.executor import MAX_BATCH_SIZE, TARGET_DB_WRITE_NOTICE, MySQLExecutor, PostgreSQLExecutor, calculate_risk_level

logger = logging.getLogger(__name__)

_utcnow = lambda: datetime.now(timezone.utc).replace(tzinfo=None)

VALID_STATUSES = {
    "draft", "dry_running", "ready", "pending_approval", "approved", "running",
    "paused", "completed", "failed", "cancelled",
}

STATUS_TRANSITIONS = {
    "draft": {"dry_running", "ready", "cancelled"},
    "dry_running": {"ready", "draft", "failed"},
    "ready": {"pending_approval", "approved", "cancelled"},
    "pending_approval": {"approved", "draft", "cancelled"},
    "approved": {"running", "cancelled"},
    "running": {"paused", "completed", "failed", "cancelled"},
    "paused": {"running", "cancelled"},
    "completed": set(),
    "failed": set(),
    "cancelled": set(),
}


def _job_id() -> str:
    now = datetime.now()
    return f"cleanup_{now.strftime('%Y%m%d')}_{uuid.uuid4().hex[:8]}"


def _is_within_execution_window(job: CleanupJob) -> bool:
    if not job.execution_window_start or not job.execution_window_end:
        return True
    current_time = datetime.now().strftime("%H:%M")
    if job.execution_window_start <= job.execution_window_end:
        return job.execution_window_start <= current_time < job.execution_window_end
    return current_time >= job.execution_window_start or current_time < job.execution_window_end


def _cutoff_days_from_now(cutoff_time: str) -> int:
    try:
        cutoff_dt = datetime.fromisoformat(cutoff_time.replace("Z", "+00:00").replace(" ", "T"))
        now = datetime.now(cutoff_dt.tzinfo) if cutoff_dt.tzinfo else datetime.now()
        return max(0, (now - cutoff_dt).days)
    except Exception:
        logger.warning("Unable to parse cutoff_time for risk calculation: %s", cutoff_time)
        return 0



def _cleanup_recommendations(job: CleanupJob, dry_run: Dict[str, Any], risk_level: str) -> List[str]:
    recommendations: List[str] = []
    matched = int(dry_run.get("matched_rows") or 0)
    batch_size = int(job.batch_size or 0)
    max_rows = int(job.max_delete_rows or 0) if job.max_delete_rows is not None else None
    if matched == 0:
        recommendations.append("当前条件没有匹配数据，可以取消或调整截止时间")
    if not dry_run.get("has_index"):
        recommendations.append(f"日期列 {job.date_column} 未检测到索引，执行前建议补充索引或缩小评估窗口")
    if max_rows is None:
        recommendations.append("当前为受控执行模式：执行前必须完成 Dry Run、复核和一键风险确认；max_delete_rows 将作为执行保护阈值")
    elif matched > max_rows:
        recommendations.append(f"匹配行数 {matched} 超过参考阈值 {max_rows}，建议拆分处理窗口并交由 DBA 流程处理")
    if batch_size > 50000:
        recommendations.append("批次较大，建议先用较小 batch_size 试跑，避免锁表或主从延迟")
    if risk_level == "high":
        recommendations.append("高风险评估建议交由 DBA 流程复核，并确认已有最近备份")
    return recommendations


def _enrich_dry_run_result(job: CleanupJob, result: Dict[str, Any], risk_level: str) -> Dict[str, Any]:
    matched = int(result.get("matched_rows") or 0)
    batch_size = max(1, int(job.batch_size or 1))
    max_rows = int(job.max_delete_rows or 0) if job.max_delete_rows is not None else None
    protected_rows = min(matched, max_rows) if max_rows is not None else matched
    estimated_batches_to_limit = (protected_rows + batch_size - 1) // batch_size if protected_rows > 0 else 0
    enriched = dict(result)
    enriched.update({
        "risk_level": risk_level,
        "protected_delete_rows": protected_rows,
        "execution_mode": "controlled_cleanup",
        "write_enabled": True,
        "write_notice": TARGET_DB_WRITE_NOTICE,
        "max_delete_rows": max_rows,
        "will_stop_at_max_delete_rows": bool(max_rows is not None and matched > max_rows),
        "estimated_batches_to_limit": estimated_batches_to_limit,
        "effective_first_batch_size": min(batch_size, protected_rows) if protected_rows > 0 else 0,
        "execution_window": {
            "start": job.execution_window_start,
            "end": job.execution_window_end,
            "currently_allowed": _is_within_execution_window(job),
        },
        "recommendations": _cleanup_recommendations(job, result, risk_level),
    })
    return enriched


def _parse_dry_run_json(value: Optional[str]) -> Dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _cleanup_confirm_text(job: CleanupJob) -> str:
    return f"ASSESS {job.table_name} BEFORE {job.cutoff_time}"


def recover_interrupted_jobs(db: Session) -> int:
    jobs = db.query(CleanupJob).filter(CleanupJob.status == "running").all()
    count = 0
    for job in jobs:
        job.status = "paused"
        job.last_error = "Application restarted while job was running; auto-paused for safety"
        job.updated_at = _utcnow()
        db.add(CleanupJobEvent(
            job_id=job.id,
            event_type="cleanup_job_auto_paused",
            operator="system",
            message="Auto-paused because application restarted",
            details=None,
            created_at=_utcnow(),
        ))
        count += 1
    if count:
        db.commit()
    return count


class CleanupService:
    def __init__(self, db: Session):
        self.db = db

    def _add_event(self, job_id: str, event_type: str, operator: str = None, message: str = None, details: str = None):
        self.db.add(CleanupJobEvent(
            job_id=job_id,
            event_type=event_type,
            operator=operator,
            message=message,
            details=details,
            created_at=_utcnow(),
        ))

    def _transition(self, job: CleanupJob, new_status: str, operator: str = None):
        if new_status not in VALID_STATUSES:
            raise ValueError(f"Invalid status: {new_status}")
        allowed = STATUS_TRANSITIONS.get(job.status, set())
        if new_status not in allowed:
            raise ValueError(f"Cannot transition from {job.status} to {new_status}")
        job.status = new_status
        job.updated_at = _utcnow()

    def create_job(self, data: Dict[str, Any], created_by: str) -> CleanupJob:
        batch_size = int(data.get("batch_size", MAX_BATCH_SIZE))
        if batch_size <= 0:
            raise ValueError("batch_size must be greater than 0")
        if batch_size > MAX_BATCH_SIZE:
            raise ValueError(f"batch_size cannot exceed {MAX_BATCH_SIZE}")

        connection_name = data.get("connection_name")
        if not connection_name and data.get("connection_id"):
            conn = self.get_connection(data["connection_id"])
            if not conn:
                raise ValueError(f"Connection {data['connection_id']} not found")
            connection_name = conn.name
            data.setdefault("database_name", conn.database_name)

        if not connection_name:
            raise ValueError("connection_name or connection_id is required")

        job = CleanupJob(
            id=_job_id(),
            name=data["name"],
            environment=data["environment"],
            connection_id=data.get("connection_id"),
            connection_name=connection_name,
            database_name=data["database_name"],
            table_name=data["table_name"],
            date_column=data["date_column"],
            cutoff_time=data["cutoff_time"],
            batch_size=batch_size,
            batch_interval_seconds=int(data.get("batch_interval_seconds", 3)),
            max_delete_rows=data.get("max_delete_rows"),
            approval_required=data.get("approval_required", True),
            execution_window_start=data.get("execution_window_start"),
            execution_window_end=data.get("execution_window_end"),
            created_by=created_by,
            status="draft",
        )
        self.db.add(job)
        self._add_event(job.id, "cleanup_job_created", created_by, f"Job created: {job.name}")
        self.db.commit()
        self.db.refresh(job)
        return job

    def get_job(self, job_id: str) -> Optional[CleanupJob]:
        return self.db.query(CleanupJob).filter(CleanupJob.id == job_id).first()

    def list_jobs(self, status: str = None, limit: int = 50) -> List[CleanupJob]:
        q = self.db.query(CleanupJob).order_by(CleanupJob.created_at.desc())
        if status:
            q = q.filter(CleanupJob.status == status)
        return q.limit(limit).all()

    def _get_executor(self, job: CleanupJob) -> MySQLExecutor:
        conn = None
        if job.connection_id:
            conn = self.db.query(DatabaseConnection).filter(DatabaseConnection.id == job.connection_id).first()
        if not conn:
            conn = self.db.query(DatabaseConnection).filter(DatabaseConnection.name == job.connection_name).first()
        if not conn:
            raise ValueError(f"Connection '{job.connection_name}' not found")
        return MySQLExecutor(
            host=conn.host,
            port=conn.port,
            username=conn.username,
            password=decrypt_secret(conn.password_encrypted),
            database=job.database_name,
            tunnel_config=self._build_tunnel_config(conn),
        )

    def dry_run(self, job_id: str, operator: str = None) -> Dict[str, Any]:
        job = self.get_job(job_id)
        if not job:
            raise ValueError(f"Job {job_id} not found")
        if job.status not in ("draft", "ready"):
            raise ValueError(f"Job cannot run dry-run in {job.status} state")
        if job.status == "draft":
            self._transition(job, "dry_running", operator)
        else:
            job.status = "dry_running"
            job.updated_at = _utcnow()
        self._add_event(job_id, "cleanup_job_dry_run_started", operator)
        self.db.commit()

        executor = None
        try:
            executor = self._get_executor(job)
            result = executor.dry_run(
                table_name=job.table_name,
                date_column=job.date_column,
                cutoff_time=job.cutoff_time,
                batch_size=job.batch_size,
            )
            risk_level = calculate_risk_level(
                environment=job.environment,
                matched_rows=result["matched_rows"],
                batch_size=job.batch_size,
                has_index=result["has_index"],
                table_name=job.table_name,
                max_delete_rows=job.max_delete_rows,
                cutoff_days_from_now=_cutoff_days_from_now(job.cutoff_time),
            )
            result = _enrich_dry_run_result(job, result, risk_level)
            job.matched_rows = result["matched_rows"]
            job.estimated_batches = result["estimated_batches"]
            job.risk_level = risk_level
            job.dry_run_result = json.dumps(result, ensure_ascii=False, default=str)
            job.status = "ready"
            job.updated_at = _utcnow()
            self._add_event(job_id, "cleanup_job_dry_run_completed", operator,
                            f"matched_rows={result['matched_rows']}, risk={risk_level}")
            self.db.commit()
            return result
        except Exception as e:
            job.status = "draft"
            job.last_error = str(e)
            job.updated_at = _utcnow()
            self._add_event(job_id, "cleanup_job_dry_run_failed", operator, str(e))
            self.db.commit()
            raise
        finally:
            if executor:
                executor.close()

    def submit_for_approval(self, job_id: str, operator: str = None):
        job = self.get_job(job_id)
        if not job:
            raise ValueError(f"Job {job_id} not found")
        if job.status != "ready":
            raise ValueError(f"Job must pass dry-run and be ready before submission, current: {job.status}")
        self._transition(job, "pending_approval", operator)
        self._add_event(job_id, "cleanup_job_submitted", operator, "Submitted for approval")
        self.db.commit()

    def approve(self, job_id: str, approver: str):
        job = self.get_job(job_id)
        if not job:
            raise ValueError(f"Job {job_id} not found")
        if job.status not in ("pending_approval", "ready"):
            raise ValueError(f"Job must be pending_approval or ready before approval, current: {job.status}")
        job.status = "approved"
        job.approved_by = approver
        job.approved_at = _utcnow()
        job.updated_at = _utcnow()
        self._add_event(job_id, "cleanup_job_approved", approver, f"Approved by {approver}")
        self.db.commit()

    def reject(self, job_id: str, operator: str):
        job = self.get_job(job_id)
        if not job:
            raise ValueError(f"Job {job_id} not found")
        if job.status != "pending_approval":
            raise ValueError(f"Job must be pending_approval before reject, current: {job.status}")
        job.status = "draft"
        job.updated_at = _utcnow()
        self._add_event(job_id, "cleanup_job_rejected", operator, f"Rejected by {operator}")
        self.db.commit()

    def _validate_startable(self, job: CleanupJob):
        if not job.dry_run_result:
            raise ValueError("Job must pass dry-run before start")
        if job.matched_rows is None:
            raise ValueError("matched_rows is missing; please run dry-run first")
        if job.status != "approved":
            raise ValueError(f"Job must be approved before start, current: {job.status}")
        if job.approval_required and not job.approved_by:
            raise ValueError("Job requires approval before start")
        if job.risk_level == "high" and not job.approved_by:
            raise ValueError("High-risk job requires approval before start")
        if job.risk_level == "high" and job.max_delete_rows is None:
            raise ValueError("High-risk job requires max_delete_rows")


    def build_start_plan(self, job_id: str) -> Dict[str, Any]:
        """Build a final confirmation plan before a destructive cleanup run."""
        job = self.get_job(job_id)
        if not job:
            raise ValueError(f"Job {job_id} not found")
        blockers: List[str] = []
        warnings: List[str] = []
        recommendations: List[str] = []
        try:
            self._validate_startable(job)
        except ValueError as exc:
            blockers.append(str(exc))
        within_window = _is_within_execution_window(job)
        if not within_window:
            window = f"{job.execution_window_start}-{job.execution_window_end}"
            blockers.append(f"Current time is outside execution window {window}")
        dry_run = _parse_dry_run_json(job.dry_run_result)
        matched_rows = int(job.matched_rows or dry_run.get("matched_rows") or 0)
        deleted_rows = int(job.deleted_rows or 0)
        max_rows = int(job.max_delete_rows or 0) if job.max_delete_rows is not None else None
        remaining_cap = None if max_rows is None else max(max_rows - deleted_rows, 0)
        protected_rows = min(matched_rows, max_rows) if max_rows is not None else matched_rows
        if matched_rows == 0:
            warnings.append("Dry Run matched_rows=0，当前条件没有匹配数据")
        if max_rows is None:
            warnings.append("当前为受控执行模式，max_delete_rows 将作为执行保护阈值")
        elif matched_rows > max_rows:
            warnings.append(f"匹配行数 {matched_rows} 超过参考阈值 {max_rows}，建议拆分处理窗口并走 DBA 变更流程")
        if job.risk_level == "high":
            warnings.append("高风险评估：请确认已有近期备份，并交由 DBA 流程处理实际变更")
        if not dry_run.get("has_index", True):
            warnings.append(f"日期列 {job.date_column} 未检测到索引，可能造成慢查询或锁等待")
        recommendations.extend(dry_run.get("recommendations") or [])
        confirm_text = _cleanup_confirm_text(job)
        return {
            "ready": len(blockers) == 0,
            "blockers": blockers,
            "warnings": warnings,
            "recommendations": recommendations,
            "confirm_text": confirm_text,
            "requires_exact_confirm": False,
            "execution_mode": "controlled_cleanup",
            "write_enabled": True,
            "write_notice": TARGET_DB_WRITE_NOTICE,
            "job": {
                "id": job.id,
                "name": job.name,
                "environment": job.environment,
                "connection_name": job.connection_name,
                "database_name": job.database_name,
                "table_name": job.table_name,
                "date_column": job.date_column,
                "cutoff_time": job.cutoff_time,
                "status": job.status,
                "risk_level": job.risk_level,
                "approved_by": job.approved_by,
                "approved_at": job.approved_at.isoformat() if job.approved_at else None,
            },
            "execution_window": {
                "start": job.execution_window_start,
                "end": job.execution_window_end,
                "currently_allowed": within_window,
            },
            "dry_run": dry_run,
            "summary": {
                "matched_rows": matched_rows,
                "deleted_rows": deleted_rows,
                "protected_delete_rows": protected_rows,
                "execution_mode": "controlled_cleanup",
                "write_enabled": True,
                "write_notice": TARGET_DB_WRITE_NOTICE,
                "max_delete_rows": max_rows,
                "remaining_delete_cap": remaining_cap,
                "batch_size": job.batch_size,
                "batch_interval_seconds": job.batch_interval_seconds,
                "estimated_batches_to_limit": dry_run.get("estimated_batches_to_limit") or job.estimated_batches,
                "will_stop_at_max_delete_rows": bool(max_rows is not None and matched_rows > max_rows),
            },
            "generated_sql": dry_run.get("generated_sql"),
        }

    def start(self, job_id: str, confirm_text: str, operator: str):
        job = self.get_job(job_id)
        if not job:
            raise ValueError(f"Job {job_id} not found")
        self._validate_startable(job)
        if not _is_within_execution_window(job):
            window = f"{job.execution_window_start}-{job.execution_window_end}"
            raise ValueError(f"Current time is outside execution window {window}")
        expected = _cleanup_confirm_text(job)
        if confirm_text != expected:
            raise ValueError(f"Confirmation text mismatch. Expected: {expected}")
        self._transition(job, "running", operator)
        job.started_at = _utcnow()
        self._add_event(job_id, "cleanup_job_started", operator, "Execution started")
        self.db.commit()
        self._run_in_background(job_id, operator)

    def _run_in_background(self, job_id: str, operator: str):
        def _worker():
            from app.db.base import SessionLocal
            db = SessionLocal()
            try:
                CleanupService(db)._execute_loop(job_id, operator)
            except Exception as e:
                logger.exception("Cleanup job %s failed: %s", job_id, e)
            finally:
                db.close()
        threading.Thread(target=_worker, daemon=True).start()

    def _execute_loop(self, job_id: str, operator: str):
        job = self.get_job(job_id)
        if not job:
            return
        executor = self._get_executor(job)
        existing_max = self.db.query(CleanupJobBatch.batch_no).filter(CleanupJobBatch.job_id == job_id).order_by(CleanupJobBatch.batch_no.desc()).first()
        batch_no = int(existing_max[0]) if existing_max and existing_max[0] else 0
        try:
            while True:
                job = self.get_job(job_id)
                if not job or job.status in ("paused", "cancelled") or job.status != "running":
                    return
                if not _is_within_execution_window(job):
                    job.status = "paused"
                    job.updated_at = _utcnow()
                    self._add_event(job_id, "cleanup_job_paused", operator, "Auto-paused: outside execution window")
                    self.db.commit()
                    return

                max_delete_rows = job.max_delete_rows
                if max_delete_rows is not None:
                    remaining_cap = int(max_delete_rows) - int(job.deleted_rows or 0)
                    if remaining_cap <= 0:
                        job.status = "paused"
                        job.updated_at = _utcnow()
                        self._add_event(job_id, "cleanup_job_paused", operator,
                                        f"Paused before next batch: max_delete_rows={job.max_delete_rows} reached")
                        self.db.commit()
                        return
                    effective_batch_size = min(int(job.batch_size), remaining_cap)
                else:
                    effective_batch_size = int(job.batch_size)

                batch_no += 1
                started_at = _utcnow()
                try:
                    affected, sql = executor.delete_batch(job.table_name, job.date_column, job.cutoff_time, effective_batch_size)
                    finished_at = _utcnow()
                    duration_ms = int((finished_at - started_at).total_seconds() * 1000)
                    remaining_rows = max((job.matched_rows or 0) - ((job.deleted_rows or 0) + affected), 0)
                    self.db.add(CleanupJobBatch(
                        job_id=job_id,
                        batch_no=batch_no,
                        affected_rows=affected,
                        duration_ms=duration_ms,
                        remaining_rows=remaining_rows,
                        sql_text=sql,
                        status="success",
                        started_at=started_at,
                        finished_at=finished_at,
                    ))
                    job.deleted_rows = (job.deleted_rows or 0) + affected
                    job.updated_at = _utcnow()
                    self._add_event(job_id, "cleanup_job_batch_completed", operator,
                                    f"Batch {batch_no}: affected_rows={affected}, batch_size={effective_batch_size}, duration={duration_ms}ms")
                    self.db.commit()

                    if affected == 0:
                        job.status = "completed"
                        job.finished_at = _utcnow()
                        job.updated_at = _utcnow()
                        self._add_event(job_id, "cleanup_job_completed", operator,
                                        f"Job completed, deleted_rows={job.deleted_rows}")
                        self.db.commit()
                        return

                    if job.max_delete_rows and job.deleted_rows >= job.max_delete_rows:
                        job.status = "paused"
                        job.updated_at = _utcnow()
                        self._add_event(job_id, "cleanup_job_paused", operator,
                                        f"Paused: max_delete_rows={job.max_delete_rows} reached")
                        self.db.commit()
                        return

                    time.sleep(max(0, job.batch_interval_seconds or 0))
                except Exception as e:
                    finished_at = _utcnow()
                    self.db.add(CleanupJobBatch(
                        job_id=job_id,
                        batch_no=batch_no,
                        affected_rows=0,
                        duration_ms=int((finished_at - started_at).total_seconds() * 1000),
                        status="failed",
                        error_message=str(e),
                        started_at=started_at,
                        finished_at=finished_at,
                    ))
                    job.status = "failed"
                    job.last_error = str(e)
                    job.finished_at = _utcnow()
                    job.updated_at = _utcnow()
                    self._add_event(job_id, "cleanup_job_failed", operator, str(e))
                    self.db.commit()
                    raise
        finally:
            executor.close()

    def pause(self, job_id: str, operator: str = None):
        job = self.get_job(job_id)
        if not job:
            raise ValueError(f"Job {job_id} not found")
        self._transition(job, "paused", operator)
        self._add_event(job_id, "cleanup_job_paused", operator, "Paused by operator")
        self.db.commit()

    def resume(self, job_id: str, operator: str = None):
        job = self.get_job(job_id)
        if not job:
            raise ValueError(f"Job {job_id} not found")
        if not job.dry_run_result:
            raise ValueError("Job must pass dry-run before resume")
        if job.approval_required and not job.approved_by:
            raise ValueError("Job requires approval before resume")
        self._transition(job, "running", operator)
        self._add_event(job_id, "cleanup_job_resumed", operator, "Resumed by operator")
        self.db.commit()
        self._run_in_background(job_id, operator)

    def cancel(self, job_id: str, operator: str = None):
        job = self.get_job(job_id)
        if not job:
            raise ValueError(f"Job {job_id} not found")
        if job.status not in ("draft", "ready", "pending_approval", "approved", "paused", "running"):
            raise ValueError(f"Cannot cancel job in {job.status} state")
        job.status = "cancelled"
        job.finished_at = _utcnow()
        job.updated_at = _utcnow()
        self._add_event(job_id, "cleanup_job_cancelled", operator, "Cancelled by operator")
        self.db.commit()

    def get_batches(self, job_id: str) -> List[CleanupJobBatch]:
        return self.db.query(CleanupJobBatch).filter(CleanupJobBatch.job_id == job_id).order_by(CleanupJobBatch.batch_no).all()

    def get_events(self, job_id: str) -> List[CleanupJobEvent]:
        return self.db.query(CleanupJobEvent).filter(CleanupJobEvent.job_id == job_id).order_by(CleanupJobEvent.created_at).all()

    def create_connection(self, data: Dict[str, Any], created_by: str) -> DatabaseConnection:
        conn = DatabaseConnection(
            id=uuid.uuid4().hex[:32],
            name=data["name"],
            environment=data["environment"],
            db_type=data.get("db_type", "mysql"),
            host=data["host"],
            port=data.get("port", 3306),
            username=data["username"],
            password_encrypted=encrypt_secret(data.get("password") or data.get("password_encrypted")),
            database_name=data.get("database_name"),
            description=data.get("description"),
            use_ssh_tunnel=bool(data.get("use_ssh_tunnel")),
            ssh_mode=data.get("ssh_mode") or "manual",
            ssh_server_id=data.get("ssh_server_id"),
            ssh_server_name=data.get("ssh_server_name"),
            ssh_host=data.get("ssh_host"),
            ssh_port=int(data.get("ssh_port") or 22),
            ssh_username=data.get("ssh_username"),
            ssh_password_encrypted=encrypt_secret(data.get("ssh_password")) if data.get("ssh_password") else None,
            ssh_key_path=data.get("ssh_key_path"),
            ssh_key_passphrase_encrypted=encrypt_secret(data.get("ssh_key_passphrase")) if data.get("ssh_key_passphrase") else None,
            ssh_key_content_encrypted=encrypt_secret(data.get("ssh_key_content")) if data.get("ssh_key_content") else None,
            ssh_remote_bind_host=data.get("ssh_remote_bind_host"),
            ssh_target_server_name=data.get("ssh_target_server_name"),
            ssh_target_host=data.get("ssh_target_host"),
            ssh_target_port=int(data.get("ssh_target_port") or 22),
            ssh_target_username=data.get("ssh_target_username"),
            ssh_target_password_encrypted=encrypt_secret(data.get("ssh_target_password")) if data.get("ssh_target_password") else None,
            ssh_target_key_path=data.get("ssh_target_key_path"),
            ssh_target_key_passphrase_encrypted=encrypt_secret(data.get("ssh_target_key_passphrase")) if data.get("ssh_target_key_passphrase") else None,
            allow_dml=bool(data.get("allow_dml", False)),
            allowed_dml_types=data.get("allowed_dml_types") or [],
            allowed_tables=data.get("allowed_tables") or [],
            blocked_tables=data.get("blocked_tables") or [],
            max_affected_rows_default=int(data.get("max_affected_rows_default") or 100),
            require_dml_reason=bool(data.get("require_dml_reason", True)),
            created_by=created_by,
        )
        self.db.add(conn)
        self.db.commit()
        self.db.refresh(conn)
        return conn

    def list_connections(self, environment: str = None) -> List[DatabaseConnection]:
        q = self.db.query(DatabaseConnection).order_by(DatabaseConnection.created_at.desc())
        if environment:
            q = q.filter(DatabaseConnection.environment == environment)
        return q.all()

    def get_connection(self, connection_id: str) -> Optional[DatabaseConnection]:
        return self.db.query(DatabaseConnection).filter(DatabaseConnection.id == connection_id).first()

    def update_connection(self, connection_id: str, data: Dict[str, Any]) -> DatabaseConnection:
        conn = self.get_connection(connection_id)
        if not conn:
            raise ValueError("Connection not found")
        plain_fields = [
            "name", "environment", "db_type", "host", "port", "username", "database_name",
            "description", "use_ssh_tunnel", "ssh_mode", "ssh_server_id", "ssh_server_name",
            "ssh_host", "ssh_port", "ssh_username",
            "ssh_key_path", "ssh_remote_bind_host",
            "ssh_target_server_name", "ssh_target_host", "ssh_target_port", "ssh_target_username", "ssh_target_key_path",
            "allow_dml", "allowed_dml_types", "allowed_tables", "blocked_tables",
            "max_affected_rows_default", "require_dml_reason",
        ]
        for field in plain_fields:
            if field in data:
                setattr(conn, field, data.get(field))
        if "password" in data and data.get("password"):
            conn.password_encrypted = encrypt_secret(data.get("password"))
        if "ssh_password" in data:
            conn.ssh_password_encrypted = encrypt_secret(data.get("ssh_password")) if data.get("ssh_password") else None
        if "ssh_key_passphrase" in data:
            conn.ssh_key_passphrase_encrypted = encrypt_secret(data.get("ssh_key_passphrase")) if data.get("ssh_key_passphrase") else None
        if "ssh_target_password" in data:
            conn.ssh_target_password_encrypted = encrypt_secret(data.get("ssh_target_password")) if data.get("ssh_target_password") else None
        if "ssh_target_key_passphrase" in data:
            conn.ssh_target_key_passphrase_encrypted = encrypt_secret(data.get("ssh_target_key_passphrase")) if data.get("ssh_target_key_passphrase") else None
        if "ssh_key_content" in data:
            if data.get("ssh_key_content"):
                conn.ssh_key_content_encrypted = encrypt_secret(data.get("ssh_key_content"))
            else:
                conn.ssh_key_content_encrypted = None
        self.db.commit()
        self.db.refresh(conn)
        return conn

    def delete_connection(self, connection_id: str) -> bool:
        conn = self.get_connection(connection_id)
        if not conn:
            return False
        self.db.query(CleanupJob).filter(CleanupJob.connection_id == connection_id).update(
            {CleanupJob.connection_id: None}, synchronize_session="fetch"
        )
        self.db.delete(conn)
        self.db.commit()
        return True

    def set_ssh_key_content(self, connection_id: str, key_content: str) -> DatabaseConnection:
        conn = self.get_connection(connection_id)
        if not conn:
            raise ValueError("Connection not found")
        conn.ssh_key_content_encrypted = encrypt_secret(key_content)
        self.db.commit()
        self.db.refresh(conn)
        return conn

    def remove_ssh_key_content(self, connection_id: str) -> DatabaseConnection:
        conn = self.get_connection(connection_id)
        if not conn:
            raise ValueError("Connection not found")
        conn.ssh_key_content_encrypted = None
        self.db.commit()
        self.db.refresh(conn)
        return conn

    def _load_inventory_server(self, server_name: Optional[str], role: str) -> Optional[Dict[str, Any]]:
        if not server_name:
            return None
        try:
            from app.maintenance.server_assets import get_server_asset
            selected_server = get_server_asset(server_name, self.db)
        except Exception:
            selected_server = None
        if not selected_server:
            raise ValueError(f"{role} server '{server_name}' not found in server inventory")
        return selected_server

    def _server_auth(self, selected_server: Optional[Dict[str, Any]], key_path_override: Optional[str]) -> Dict[str, Any]:
        server = selected_server or {}
        return {
            "password": server.get("password"),
            "key_path": key_path_override or server.get("key") or server.get("key_file"),
            "key_content": server.get("key_content") if not key_path_override else None,
        }

    def _build_tunnel_config(self, conn: DatabaseConnection) -> Optional[Dict[str, Any]]:
        if not getattr(conn, "use_ssh_tunnel", False):
            return None

        ssh_mode = getattr(conn, "ssh_mode", None) or "manual"
        bastion_name = getattr(conn, "ssh_server_name", None)
        bastion_server = None
        bastion_auth = {"password": None, "key_path": None, "key_content": None}

        if ssh_mode == "server" and bastion_name:
            bastion_server = self._load_inventory_server(bastion_name, "SSH bastion")
            bastion_auth = self._server_auth(bastion_server, conn.ssh_key_path)

        ssh_host = conn.ssh_host or (bastion_server or {}).get("host")
        ssh_port = conn.ssh_port or (bastion_server or {}).get("port") or 22
        ssh_username = conn.ssh_username or (bastion_server or {}).get("user") or (bastion_server or {}).get("username")
        ssh_password = decrypt_secret(conn.ssh_password_encrypted) if conn.ssh_password_encrypted else bastion_auth.get("password")
        ssh_key_path = bastion_auth.get("key_path") or conn.ssh_key_path
        ssh_key_content = bastion_auth.get("key_content")
        if conn.ssh_key_content_encrypted:
            ssh_key_content = decrypt_secret(conn.ssh_key_content_encrypted)

        if not ssh_host or not ssh_username:
            raise ValueError("SSH tunnel is enabled but bastion ssh_host or ssh_username is missing")

        target_name = getattr(conn, "ssh_target_server_name", None)
        target_server = self._load_inventory_server(target_name, "SSH target") if target_name else None
        target_key_override = getattr(conn, "ssh_target_key_path", None)
        target_auth = self._server_auth(target_server, target_key_override)
        target_host = getattr(conn, "ssh_target_host", None) or (target_server or {}).get("host")
        target_port = getattr(conn, "ssh_target_port", None) or (target_server or {}).get("port") or 22
        target_username = getattr(conn, "ssh_target_username", None) or (target_server or {}).get("user") or (target_server or {}).get("username")
        target_password_encrypted = getattr(conn, "ssh_target_password_encrypted", None)
        target_password = decrypt_secret(target_password_encrypted) if target_password_encrypted else target_auth.get("password")

        if (target_name or target_host or target_username) and (not target_host or not target_username):
            raise ValueError("Two-hop SSH is enabled but target host or username is missing")

        return {
            "ssh_server_name": bastion_name,
            "ssh_host": ssh_host,
            "ssh_port": ssh_port,
            "ssh_username": ssh_username,
            "ssh_password": ssh_password,
            "ssh_key_path": ssh_key_path,
            "ssh_key_content": ssh_key_content,
            "ssh_key_passphrase": decrypt_secret(conn.ssh_key_passphrase_encrypted) if conn.ssh_key_passphrase_encrypted else None,
            "target_ssh_server_name": target_name,
            "target_ssh_host": target_host,
            "target_ssh_port": target_port,
            "target_ssh_username": target_username,
            "target_ssh_password": target_password,
            "target_ssh_key_path": target_auth.get("key_path"),
            "target_ssh_key_content": target_auth.get("key_content"),
            "target_ssh_key_passphrase": decrypt_secret(conn.ssh_target_key_passphrase_encrypted) if getattr(conn, "ssh_target_key_passphrase_encrypted", None) else None,
            # Database address is resolved from the final execution host. In two-hop mode this is the target host view.
            "remote_bind_host": conn.ssh_remote_bind_host or conn.host,
        }

    def _connection_executor(self, conn: DatabaseConnection, database_name: str):
        executor_cls = PostgreSQLExecutor if getattr(conn, "db_type", "mysql") == "postgresql" else MySQLExecutor
        return executor_cls(
            host=conn.host,
            port=conn.port,
            username=conn.username,
            password=decrypt_secret(conn.password_encrypted),
            database=database_name or conn.database_name,
            tunnel_config=self._build_tunnel_config(conn),
        )

    def get_tables(self, connection_id: str, database_name: str) -> List[str]:
        conn = self.get_connection(connection_id)
        if not conn:
            raise ValueError(f"Connection {connection_id} not found")
        executor = self._connection_executor(conn, database_name)
        try:
            return executor.list_tables(database_name)
        finally:
            executor.close()

    def get_columns(self, connection_id: str, database_name: str, table_name: str) -> List[Dict[str, Any]]:
        conn = self.get_connection(connection_id)
        if not conn:
            raise ValueError(f"Connection {connection_id} not found")
        executor = self._connection_executor(conn, database_name)
        try:
            return executor.list_columns(table_name)
        finally:
            executor.close()
