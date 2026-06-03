import json
import logging
from copy import copy
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Dict, Any
from app.deploy.state import TERMINAL_STATUSES, normalize_status
from sqlalchemy.orm import Session
from .models import (
    User, Server, Service, Environment,
    Deployment, Pipeline, PipelineStep,
    DeployTask, DeployLog, ServerGroup,
    ConfigKV, DeploymentRecord, AuditRecord,
    DeploymentServerTask, DeploymentStepTask, DeploymentPackageDistribution,
    DeploymentLockRecord, NotificationEvent,
)

logger = logging.getLogger(__name__)


def _encrypt_server_fields(server: Server) -> Server:
    from app.core.secret_store import encrypt_secret
    if getattr(server, "password", None):
        server.password = encrypt_secret(server.password)
    if getattr(server, "key_content", None):
        server.key_content = encrypt_secret(server.key_content)
    return server


def _decrypt_server_copy(server: Optional[Server]) -> Optional[Server]:
    # ServerRepository is currently lightly used, but returning decrypted fields
    # keeps behavior compatible with callers that instantiate SSH clients from
    # ORM objects.  Values are decrypted in-memory only.
    if server is None:
        return None
    from app.core.secret_store import decrypt_secret
    detached = copy(server)
    if getattr(detached, "password", None):
        detached.password = decrypt_secret(detached.password)
    if getattr(detached, "key_content", None):
        detached.key_content = decrypt_secret(detached.key_content)
    return detached



class UserRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_by_id(self, user_id: str) -> Optional[User]:
        return self.db.query(User).filter(User.id == user_id).first()

    def get_by_username(self, username: str) -> Optional[User]:
        return self.db.query(User).filter(User.username == username).first()

    def list_all(self) -> List[User]:
        return self.db.query(User).all()

    def create(self, username: str, password_hash: str, role: str = "developer",
               is_admin: bool = False, can_deploy: bool = True, session_version: int = 1) -> User:
        user = User(
            username=username,
            password_hash=password_hash,
            role=role,
            is_admin=is_admin,
            can_deploy=can_deploy,
            session_version=session_version,
        )
        self.db.add(user)
        self.db.commit()
        self.db.refresh(user)
        return user

    def update(self, user: User) -> User:
        self.db.commit()
        self.db.refresh(user)
        return user

    def delete(self, user_id: str) -> bool:
        user = self.get_by_id(user_id)
        if user:
            self.db.delete(user)
            self.db.commit()
            return True
        return False


class ServerRepository:
    def __init__(self, db: Session):
        self.db = db

    def _get_server_row_by_id(self, server_id: str) -> Optional[Server]:
        return self.db.query(Server).filter(Server.id == server_id).first()

    def _get_server_row_by_name(self, name: str) -> Optional[Server]:
        return self.db.query(Server).filter(Server.name == name).first()

    def get_by_id(self, server_id: str) -> Optional[Server]:
        return _decrypt_server_copy(self._get_server_row_by_id(server_id))

    def get_by_name(self, name: str) -> Optional[Server]:
        return _decrypt_server_copy(self._get_server_row_by_name(name))

    def list_all(self) -> List[Server]:
        return [_decrypt_server_copy(s) for s in self.db.query(Server).all()]

    def create(self, name: str, host: str, port: int = 22, user: str = "root",
               key: str = "~/.ssh/id_rsa", key_content: str = None, password: str = None,
               jump_host: str = None) -> Server:
        server = Server(
            name=name, host=host, port=port, user=user,
            key=key, key_content=key_content, password=password,
            jump_host=jump_host
        )
        _encrypt_server_fields(server)
        self.db.add(server)
        self.db.commit()
        self.db.refresh(server)
        return server

    def update(self, server: Server) -> Server:
        persisted = self._get_server_row_by_id(server.id)
        if not persisted:
            raise ValueError(f"Server not found: {server.id}")
        for field in ("name", "host", "port", "user", "key", "key_content", "password", "jump_host"):
            if hasattr(server, field):
                setattr(persisted, field, getattr(server, field))
        _encrypt_server_fields(persisted)
        self.db.commit()
        self.db.refresh(persisted)
        return _decrypt_server_copy(persisted)

    def delete(self, server_id: str) -> bool:
        server = self._get_server_row_by_id(server_id)
        if server:
            self.db.delete(server)
            self.db.commit()
            return True
        return False


class ServiceRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_by_id(self, service_id: str) -> Optional[Service]:
        return self.db.query(Service).filter(Service.id == service_id).first()

    def get_by_name(self, name: str, system_name: str = None) -> Optional[Service]:
        query = self.db.query(Service).filter(Service.name == name)
        if system_name:
            query = query.filter(Service.system_name == system_name)
        return query.first()

    def list_all(self) -> List[Service]:
        return self.db.query(Service).all()

    def list_by_system(self, system_name: str) -> List[Service]:
        return self.db.query(Service).filter(Service.system_name == system_name).all()

    def create(self, name: str, system_name: str, display_name: str = None,
               template: str = None, pipeline_id: str = None, template_variables: dict = None, servers: list = None) -> Service:
        service = Service(
            name=name,
            system_name=system_name,
            display_name=display_name,
            template=template,
            pipeline_id=pipeline_id,
            template_variables=template_variables or {},
            servers=servers or []
        )
        self.db.add(service)
        self.db.commit()
        self.db.refresh(service)
        return service

    def update(self, service: Service) -> Service:
        self.db.commit()
        self.db.refresh(service)
        return service

    def delete(self, service_id: str) -> bool:
        service = self.get_by_id(service_id)
        if service:
            self.db.delete(service)
            self.db.commit()
            return True
        return False


class EnvironmentRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_by_id(self, env_id: str) -> Optional[Environment]:
        return self.db.query(Environment).filter(Environment.id == env_id).first()

    def get_by_name(self, name: str) -> Optional[Environment]:
        return self.db.query(Environment).filter(Environment.name == name).first()

    def list_all(self) -> List[Environment]:
        return self.db.query(Environment).all()

    def create(self, name: str, variables: dict = None) -> Environment:
        env = Environment(name=name, variables=variables or {})
        self.db.add(env)
        self.db.commit()
        self.db.refresh(env)
        return env

    def update(self, env: Environment) -> Environment:
        self.db.commit()
        self.db.refresh(env)
        return env

    def delete(self, env_id: str) -> bool:
        env = self.get_by_id(env_id)
        if env:
            self.db.delete(env)
            self.db.commit()
            return True
        return False


class DeploymentRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_by_id(self, deployment_id: str) -> Optional[Deployment]:
        return self.db.query(Deployment).filter(Deployment.id == deployment_id).first()

    def list_all(self, limit: int = 100) -> List[Deployment]:
        return self.db.query(Deployment).order_by(Deployment.started_at.desc()).limit(limit).all()

    def list_by_system(self, system: str, limit: int = 100) -> List[Deployment]:
        return self.db.query(Deployment).filter(Deployment.system == system)\
            .order_by(Deployment.started_at.desc()).limit(limit).all()

    def create(self, system: str, service: str = None, environment: str = None,
               strategy: str = "DIRECT", servers: str = "", created_by: str = None,
               version: str = None, server_group: str = None, status: str = "running") -> Deployment:
        deployment = Deployment(
            system=system,
            service=service,
            environment=environment,
            strategy=strategy,
            servers=servers,
            server_group=server_group,
            created_by=created_by,
            version=version,
            status=status
        )
        self.db.add(deployment)
        self.db.commit()
        self.db.refresh(deployment)
        return deployment

    def update_status(self, deployment_id: str, status: str, message: str = None) -> bool:
        deployment = self.get_by_id(deployment_id)
        if deployment:
            status = normalize_status(status)
            deployment.status = status
            if message:
                deployment.message = message
            if status in TERMINAL_STATUSES:
                deployment.finished_at = datetime.now(timezone.utc).replace(tzinfo=None)
            self.db.commit()
            return True
        return False


class PipelineRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_by_id(self, pipeline_id: str) -> Optional[Pipeline]:
        return self.db.query(Pipeline).filter(Pipeline.id == pipeline_id).first()

    def get_by_name(self, name: str, system_name: str = None) -> Optional[Pipeline]:
        query = self.db.query(Pipeline).filter(Pipeline.name == name)
        if system_name:
            query = query.filter(Pipeline.system_name == system_name)
        return query.first()

    def list_all(self) -> List[Pipeline]:
        return self.db.query(Pipeline).all()

    def list_by_system(self, system_name: str) -> List[Pipeline]:
        return self.db.query(Pipeline).filter(Pipeline.system_name == system_name).all()

    def create(self, name: str, system_name: str, description: str = None, strategy: str = "DIRECT") -> Pipeline:
        pipeline = Pipeline(
            name=name,
            system_name=system_name,
            description=description,
            strategy=strategy
        )
        self.db.add(pipeline)
        self.db.commit()
        self.db.refresh(pipeline)
        return pipeline

    def update(self, pipeline: Pipeline) -> Pipeline:
        self.db.commit()
        self.db.refresh(pipeline)
        return pipeline

    def delete(self, pipeline_id: str) -> bool:
        pipeline = self.get_by_id(pipeline_id)
        if pipeline:
            self.db.delete(pipeline)
            self.db.commit()
            return True
        return False


class PipelineStepRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_by_id(self, step_id: str) -> Optional[PipelineStep]:
        return self.db.query(PipelineStep).filter(PipelineStep.id == step_id).first()

    def list_by_pipeline(self, pipeline_id: str) -> List[PipelineStep]:
        return self.db.query(PipelineStep)\
            .filter(PipelineStep.pipeline_id == pipeline_id)\
            .order_by(PipelineStep.sort_order).all()

    def create(self, pipeline_id: str, name: str, step_type: str = "command",
               config: dict = None, sort_order: int = 0) -> PipelineStep:
        step = PipelineStep(
            pipeline_id=pipeline_id,
            name=name,
            step_type=step_type,
            config=config or {},
            sort_order=sort_order
        )
        self.db.add(step)
        self.db.commit()
        self.db.refresh(step)
        return step

    def update(self, step: PipelineStep) -> PipelineStep:
        self.db.commit()
        self.db.refresh(step)
        return step

    def delete(self, step_id: str) -> bool:
        step = self.get_by_id(step_id)
        if step:
            self.db.delete(step)
            self.db.commit()
            return True
        return False

    def reorder(self, pipeline_id: str, step_ids: List[str]) -> bool:
        steps = self.list_by_pipeline(pipeline_id)
        step_map = {s.id: s for s in steps}
        for i, step_id in enumerate(step_ids):
            if step_id in step_map:
                step_map[step_id].sort_order = i
        self.db.commit()
        return True


class DeployTaskRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_by_id(self, task_id: str) -> Optional[DeployTask]:
        return self.db.query(DeployTask).filter(DeployTask.id == task_id).first()

    def list_by_deployment(self, deployment_id: str) -> List[DeployTask]:
        return self.db.query(DeployTask)\
            .filter(DeployTask.deployment_id == deployment_id)\
            .order_by(DeployTask.created_at).all()

    def create(self, deployment_id: str = None, task_id: str = None, payload_json: str = None, lock_key: str = None) -> DeployTask:
        task = DeployTask(id=task_id, deployment_id=deployment_id, status="pending", payload_json=payload_json, lock_key=lock_key)
        self.db.add(task)
        self.db.commit()
        self.db.refresh(task)
        return task

    def update_status(self, task_id: str, status: str, worker: str = None, result: str = None) -> bool:
        task = self.get_by_id(task_id)
        if task:
            status = normalize_status(status)
            task.status = status
            if worker:
                task.worker = worker
            if result is not None:
                task.result = result
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            if status == "running" and not task.started_at:
                task.started_at = now
            if status in TERMINAL_STATUSES:
                task.finished_at = now
            self.db.commit()
            return True
        return False

    def request_cancel(self, task_id: str) -> bool:
        task = self.get_by_id(task_id)
        if not task:
            return False
        task.cancel_requested = True
        self.db.commit()
        return True

    def next_pending(self) -> Optional[DeployTask]:
        task = self.db.query(DeployTask).filter(DeployTask.status == "pending").order_by(DeployTask.created_at).first()
        if task:
            task.status = "running"
            task.started_at = datetime.now(timezone.utc).replace(tzinfo=None)
            task.worker = "deploy-worker"
            self.db.commit()
            self.db.refresh(task)
        return task


class DeployLogRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_by_id(self, log_id: str) -> Optional[DeployLog]:
        return self.db.query(DeployLog).filter(DeployLog.id == log_id).first()

    def list_by_task(self, task_id: str, limit: int = 1000) -> List[DeployLog]:
        # Return the latest N lines in chronological order to keep long-running
        # local browsers from downloading unbounded deployment logs.
        rows = self.db.query(DeployLog)\
            .filter(DeployLog.task_id == task_id)\
            .order_by(DeployLog.created_at.desc(), DeployLog.id.desc()).limit(limit).all()
        return list(reversed(rows))

    def list_by_deployment(self, deployment_id: str, limit: int = 1000) -> List[DeployLog]:
        rows = self.db.query(DeployLog)\
            .filter(DeployLog.deployment_id == deployment_id)\
            .order_by(DeployLog.created_at.desc(), DeployLog.id.desc()).limit(limit).all()
        return list(reversed(rows))

    def list_by_deployment_cursor(self, deployment_id: str, *, cursor: str = None, limit: int = 500) -> tuple[List[DeployLog], Optional[str]]:
        q = self.db.query(DeployLog)\
            .filter(DeployLog.deployment_id == deployment_id)
        if cursor:
            cursor_row = self.db.query(DeployLog).filter(DeployLog.id == cursor).first()
            if cursor_row:
                q = q.filter(
                    (DeployLog.created_at < cursor_row.created_at) |
                    ((DeployLog.created_at == cursor_row.created_at) & (DeployLog.id < cursor_row.id))
                )
        rows = q.order_by(DeployLog.created_at.desc(), DeployLog.id.desc()).limit(limit + 1).all()
        has_more = len(rows) > limit
        if has_more:
            rows = rows[:limit]
        next_cursor = rows[-1].id if has_more and rows else None
        return list(reversed(rows)), next_cursor

    def count_by_deployment(self, deployment_id: str) -> int:
        return self.db.query(DeployLog).filter(DeployLog.deployment_id == deployment_id).count()

    def max_created_at_for_deployment(self, deployment_id: str):
        row = self.db.query(DeployLog.created_at).filter(DeployLog.deployment_id == deployment_id).order_by(DeployLog.created_at.desc()).first()
        return row[0] if row else None

    def list_by_task_cursor(self, task_id: str, *, cursor: str = None, limit: int = 500) -> tuple[List[DeployLog], Optional[str]]:
        q = self.db.query(DeployLog)\
            .filter(DeployLog.task_id == task_id)
        if cursor:
            cursor_row = self.db.query(DeployLog).filter(DeployLog.id == cursor).first()
            if cursor_row:
                q = q.filter(
                    (DeployLog.created_at < cursor_row.created_at) |
                    ((DeployLog.created_at == cursor_row.created_at) & (DeployLog.id < cursor_row.id))
                )
        rows = q.order_by(DeployLog.created_at.desc(), DeployLog.id.desc()).limit(limit + 1).all()
        has_more = len(rows) > limit
        if has_more:
            rows = rows[:limit]
        next_cursor = rows[-1].id if has_more and rows else None
        return list(reversed(rows)), next_cursor

    def count_by_task(self, task_id: str) -> int:
        return self.db.query(DeployLog).filter(DeployLog.task_id == task_id).count()

    def max_created_at_for_task(self, task_id: str):
        row = self.db.query(DeployLog.created_at).filter(DeployLog.task_id == task_id).order_by(DeployLog.created_at.desc()).first()
        return row[0] if row else None

    def create(self, message: str, level: str = "info", step_name: str = None,
               task_id: str = None, deployment_id: str = None, *,
               commit: bool = True, refresh: bool = True) -> DeployLog:
        log = DeployLog(
            message=message,
            level=level,
            step_name=step_name,
            task_id=task_id,
            deployment_id=deployment_id
        )
        self.db.add(log)
        if commit:
            self.db.commit()
            if refresh:
                self.db.refresh(log)
        return log

    def create_many(self, rows: List[Dict[str, Any]]) -> int:
        logs = []
        for row in rows:
            message = row.get("message")
            if message is None:
                continue
            logs.append(DeployLog(
                message=str(message),
                level=str(row.get("level") or "info"),
                step_name=row.get("step_name") or None,
                task_id=row.get("task_id") or None,
                deployment_id=row.get("deployment_id") or None,
            ))
        if not logs:
            return 0
        self.db.add_all(logs)
        self.db.commit()
        return len(logs)

    def clear_old_logs(self, days: int = 30) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        result = self.db.query(DeployLog).filter(DeployLog.created_at < cutoff).delete()
        self.db.commit()
        return result


class ServerGroupRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_by_id(self, group_id: str) -> Optional[ServerGroup]:
        return self.db.query(ServerGroup).filter(ServerGroup.id == group_id).first()

    def get_by_name(self, name: str) -> Optional[ServerGroup]:
        return self.db.query(ServerGroup).filter(ServerGroup.name == name).first()

    def list_all(self) -> List[ServerGroup]:
        return self.db.query(ServerGroup).order_by(ServerGroup.name).all()

    def create(self, name: str, display_name: str = None, description: str = None,
               server_names: list = None, tags: list = None) -> ServerGroup:
        group = ServerGroup(
            name=name,
            display_name=display_name or name,
            description=description,
            server_names=server_names or [],
            tags=tags or [],
        )
        self.db.add(group)
        self.db.commit()
        self.db.refresh(group)
        return group

    def update(self, group: ServerGroup) -> ServerGroup:
        group.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        self.db.commit()
        self.db.refresh(group)
        return group

    def delete(self, group_id: str) -> bool:
        group = self.get_by_id(group_id)
        if group:
            self.db.delete(group)
            self.db.commit()
            return True
        return False


class ConfigRepository:
    def __init__(self, session: Session):
        self.session = session

    def get_all(self) -> Dict[str, Any]:
        rows = self.session.query(ConfigKV).all()
        result = {}
        for row in rows:
            try:
                result[row.key] = json.loads(row.value)
            except json.JSONDecodeError:
                result[row.key] = row.value
        return result

    def get(self, key: str) -> Any:
        row = self.session.query(ConfigKV).filter(ConfigKV.key == key).first()
        if not row:
            return None
        try:
            return json.loads(row.value)
        except json.JSONDecodeError:
            return row.value

    def set(self, key: str, value: Any):
        existing = self.session.query(ConfigKV).filter_by(key=key).first()
        json_value = json.dumps(value, ensure_ascii=False)
        if existing:
            existing.value = json_value
        else:
            self.session.add(ConfigKV(key=key, value=json_value))
        self.session.flush()

    def save_all(self, config: Dict[str, Any]):
        for key, value in config.items():
            self.set(key, value)
        self.session.commit()

    def save_deploy_log(self, log_dict: Dict[str, Any]) -> bool:
        try:
            existing = self.session.query(DeploymentRecord).filter_by(
                id=log_dict.get("id", "")
            ).first()
            if existing:
                for k, v in log_dict.items():
                    if hasattr(existing, k):
                        if k == "steps" and not isinstance(v, str):
                            v = json.dumps(v, ensure_ascii=False)
                        setattr(existing, k, v)
            else:
                record = DeploymentRecord(**{
                    k: log_dict.get(k) for k in [
                        "id", "system", "server", "strategy", "status",
                        "steps", "output", "message", "started_at", "finished_at",
                    ]
                })
                record.steps = json.dumps(log_dict.get("steps", []), ensure_ascii=False)
                self.session.add(record)
            self.session.commit()
            return True
        except Exception:
            self.session.rollback()
            return False

    def load_deploy_logs(self, limit: int = 1000) -> List[Dict[str, Any]]:
        rows = self.session.query(DeploymentRecord).order_by(
            DeploymentRecord.started_at.desc()
        ).limit(limit).all()
        result = []
        for row in rows:
            d = {
                "id": row.id, "system": row.system, "server": row.server,
                "strategy": row.strategy, "status": row.status,
                "output": row.output, "message": row.message,
                "started_at": row.started_at, "finished_at": row.finished_at,
            }
            try:
                d["steps"] = json.loads(row.steps)
            except (json.JSONDecodeError, TypeError):
                d["steps"] = []
            result.append(d)
        return result

    def save_audit_log(self, action: str, target_type: str = None,
                       target_name: str = None, details: str = None) -> bool:
        try:
            record = AuditRecord(
                action=action,
                target_type=target_type,
                target_name=target_name,
                details=details,
                created_at=datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
            )
            self.session.add(record)
            self.session.commit()
            return True
        except Exception:
            self.session.rollback()
            return False

    def load_audit_logs(self, limit: int = 1000) -> List[Dict[str, Any]]:
        rows = self.session.query(AuditRecord).order_by(
            AuditRecord.created_at.desc()
        ).limit(limit).all()
        return [
            {
                "id": row.id, "action": row.action,
                "target_type": row.target_type, "target_name": row.target_name,
                "details": row.details, "created_at": row.created_at,
            }
            for row in rows
        ]


class DeploymentRuntimeRepository:
    """Repository for deployment server tasks, step tasks, distributions, locks and notifications."""

    def __init__(self, db: Session):
        self.db = db

    def _commit(self):
        try:
            self.db.commit()
        except Exception:
            logger.error("Commit failed", exc_info=True)
            self.db.rollback()
            raise

    def create_server_task(self, deployment_id: str, task_id: str, server_name: str, status: str = "pending") -> DeploymentServerTask:
        item = DeploymentServerTask(deployment_id=deployment_id, task_id=task_id, server_name=server_name, status=status)
        self.db.add(item)
        self._commit()
        self.db.refresh(item)
        return item

    def update_server_task(self, item_id: str, status: str, message: str = None) -> bool:
        item = self.db.query(DeploymentServerTask).filter(DeploymentServerTask.id == item_id).first()
        if not item:
            return False
        status = normalize_status(status)
        item.status = status
        if message is not None:
            item.message = message
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        if status == "running" and not item.started_at:
            item.started_at = now
        if status in TERMINAL_STATUSES:
            item.finished_at = now
        self._commit()
        return True

    def create_step_task(self, deployment_id: str, task_id: str, step_name: str, step_type: str = "", server_task_id: str = None, server_name: str = None, status: str = "pending", captured_config: str = None) -> DeploymentStepTask:
        item = DeploymentStepTask(
            deployment_id=deployment_id,
            task_id=task_id,
            server_task_id=server_task_id,
            server_name=server_name,
            step_name=step_name,
            step_type=step_type,
            status=status,
            captured_config=captured_config,
        )
        self.db.add(item)
        self._commit()
        self.db.refresh(item)
        return item

    def update_step_task(self, item_id: str, status: str, message: str = None) -> bool:
        item = self.db.query(DeploymentStepTask).filter(DeploymentStepTask.id == item_id).first()
        if not item:
            return False
        status = normalize_status(status)
        item.status = status
        if message is not None:
            item.message = message
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        if status == "running" and not item.started_at:
            item.started_at = now
        if status in TERMINAL_STATUSES:
            item.finished_at = now
        self._commit()
        return True

    def create_distribution(self, deployment_id: str, task_id: str, server_name: str, package_name: str, local_path: str = "", remote_path: str = "", local_sha256: str = "", size_bytes: int = None) -> DeploymentPackageDistribution:
        item = DeploymentPackageDistribution(
            deployment_id=deployment_id,
            task_id=task_id,
            server_name=server_name,
            package_name=package_name,
            local_path=local_path,
            remote_path=remote_path,
            local_sha256=local_sha256,
            size_bytes=size_bytes,
            status="pending",
        )
        self.db.add(item)
        self._commit()
        self.db.refresh(item)
        return item

    def update_distribution(self, item_id: str, **updates) -> bool:
        item = self.db.query(DeploymentPackageDistribution).filter(DeploymentPackageDistribution.id == item_id).first()
        if not item:
            return False
        status = updates.get("status")
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        for key, value in updates.items():
            if hasattr(item, key):
                setattr(item, key, value)
        if status == "running" and not item.started_at:
            item.started_at = now
        if status in TERMINAL_STATUSES:
            item.finished_at = now
        item.updated_at = now
        self._commit()
        return True

    def list_for_deployment(self, deployment_id: str) -> Dict[str, Any]:
        server_tasks = self.db.query(DeploymentServerTask).filter(DeploymentServerTask.deployment_id == deployment_id).order_by(DeploymentServerTask.created_at).all()
        step_tasks = self.db.query(DeploymentStepTask).filter(DeploymentStepTask.deployment_id == deployment_id).order_by(DeploymentStepTask.created_at).all()
        distributions = self.db.query(DeploymentPackageDistribution).filter(DeploymentPackageDistribution.deployment_id == deployment_id).order_by(DeploymentPackageDistribution.created_at).all()
        return {"server_tasks": server_tasks, "step_tasks": step_tasks, "distributions": distributions}
