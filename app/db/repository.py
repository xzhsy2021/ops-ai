import json
import logging
from copy import copy
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Dict, Any
from app.deploy.state import TERMINAL_STATUSES, normalize_status
from sqlalchemy.orm import Session
from .models import (
    User, Server, Service, Environment, System, SystemEnvironment,
    CapabilitySetting, RetentionPolicy, NotificationSetting,
    InspectionProfileSetting, WorkflowTemplateSetting,
    DeploymentDefault, GlobalVariable,
    Deployment, Pipeline, PipelineStep,
    DeployTask, DeployLog, ServerGroup,
    DeploymentRecord, AuditRecord,
    DeploymentServerTask, DeploymentStepTask, DeploymentPackageDistribution,
    DeploymentLockRecord, NotificationEvent,
    JumpHost, SshKey,
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
               jump_host: str = None, status: str = "online") -> Server:
        server = Server(
            name=name, host=host, port=port, user=user,
            key=key, key_content=key_content, password=password,
            jump_host=jump_host, status=status or "online"
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
        for field in ("name", "host", "port", "user", "key", "key_content", "password", "jump_host", "status"):
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


class SystemRepository:
    """Phase 3e SSOT: 系统配置 CRUD — systems 表。"""
    def __init__(self, db: Session):
        self.db = db

    def get_by_id(self, system_id: str) -> Optional[System]:
        return self.db.query(System).filter(System.id == system_id).first()

    def get_by_name(self, name: str) -> Optional[System]:
        return self.db.query(System).filter(System.name == name).first()

    def list_all(self) -> List[System]:
        return self.db.query(System).order_by(System.name).all()

    def create(self, name: str, display_name: str = None, strategy: str = "WORKFLOW",
               base_path: str = "/data/web/app", description: str = None,
               variables: dict = None, servers: list = None,
               environments: dict = None, services: list = None,
               message_routing: dict = None) -> System:
        system = System(
            name=name,
            display_name=display_name or name,
            strategy=strategy,
            base_path=base_path,
            description=description,
            variables=variables or {},
            servers=servers or [],
            environments=environments or {},
            services=services or [],
            message_routing=message_routing or {},
        )
        self.db.add(system)
        self.db.commit()
        self.db.refresh(system)
        return system

    def update(self, system: System) -> System:
        """Commit changes to an attached System object.

        NOTE: The caller must pass an attached object (e.g. returned by
        get_by_name/get_by_id). Passing a detached object will silently
        no-op — the commit succeeds but writes nothing to the database.
        """
        self.db.commit()
        self.db.refresh(system)
        return system

    def delete(self, system_id: str) -> bool:
        system = self.get_by_id(system_id)
        if system:
            self.db.delete(system)
            self.db.commit()
            return True
        return False

    def delete_by_name(self, name: str) -> bool:
        system = self.get_by_name(name)
        if system:
            self.db.delete(system)
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
        row = query.first()
        if row:
            return row
        # 模糊匹配：精确匹配失败后，尝试 endswith 和 display_name
        target = name.strip().lower()
        if not target:
            return None
        candidates = self.db.query(Service)
        if system_name:
            candidates = candidates.filter(Service.system_name == system_name)
        for svc in candidates.all():
            svc_name = (svc.name or "").strip().lower()
            svc_display = (svc.display_name or "").strip().lower()
            if svc_name == target or svc_display == target:
                return svc
            if svc_name.endswith(f"-{target}") or target.endswith(f"-{svc_name}"):
                return svc
        return None

    def list_all(self) -> List[Service]:
        return self.db.query(Service).all()

    def list_by_system(self, system_name: str) -> List[Service]:
        return self.db.query(Service).filter(Service.system_name == system_name).all()

    def create(self, name: str, system_name: str, display_name: str = None,
               repo: str = None, build_cmd: str = None, start_cmd: str = None,
               template: str = None, pipeline_id: str = None,
               template_variables: dict = None, servers: list = None) -> Service:
        service = Service(
            name=name,
            system_name=system_name,
            display_name=display_name,
            repo=repo,
            build_cmd=build_cmd,
            start_cmd=start_cmd,
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


class SystemEnvironmentRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_by_id(self, environment_id: str) -> Optional[SystemEnvironment]:
        return self.db.query(SystemEnvironment).filter(SystemEnvironment.id == environment_id).first()

    def get_by_name(self, system_name: str, name: str) -> Optional[SystemEnvironment]:
        return self.db.query(SystemEnvironment).filter(
            SystemEnvironment.system_name == system_name,
            SystemEnvironment.name == name,
        ).first()

    def list_all(self) -> List[SystemEnvironment]:
        return self.db.query(SystemEnvironment).order_by(
            SystemEnvironment.system_name,
            SystemEnvironment.name,
        ).all()

    def list_by_system(self, system_name: str) -> List[SystemEnvironment]:
        return self.db.query(SystemEnvironment).filter(
            SystemEnvironment.system_name == system_name,
        ).order_by(SystemEnvironment.name).all()

    def create(
        self,
        *,
        system_name: str,
        name: str,
        display_name: str = None,
        category: str = "custom",
        description: str = None,
        base_path: str = None,
        servers: list = None,
        variables: dict = None,
        service_overrides: dict = None,
        group_overrides: dict = None,
    ) -> SystemEnvironment:
        row = SystemEnvironment(
            system_name=system_name,
            name=name,
            display_name=display_name or name,
            category=category or "custom",
            description=description,
            base_path=base_path,
            servers=servers or [],
            variables=variables or {},
            service_overrides=service_overrides or {},
            group_overrides=group_overrides or {},
        )
        self.db.add(row)
        self.db.flush()
        return row

    def upsert(
        self,
        *,
        system_name: str,
        name: str,
        display_name: str = None,
        category: str = "custom",
        description: str = None,
        base_path: str = None,
        servers: list = None,
        variables: dict = None,
        service_overrides: dict = None,
        group_overrides: dict = None,
    ) -> SystemEnvironment:
        row = self.get_by_name(system_name, name)
        if row is None:
            return self.create(
                system_name=system_name,
                name=name,
                display_name=display_name,
                category=category,
                description=description,
                base_path=base_path,
                servers=servers,
                variables=variables,
                service_overrides=service_overrides,
                group_overrides=group_overrides,
            )
        row.display_name = display_name or name
        row.category = category or "custom"
        row.description = description
        row.base_path = base_path
        row.servers = servers or []
        row.variables = variables or {}
        row.service_overrides = service_overrides or {}
        row.group_overrides = group_overrides or {}
        self.db.flush()
        return row

    def delete(self, environment_id: str) -> bool:
        row = self.get_by_id(environment_id)
        if row is None:
            return False
        self.db.delete(row)
        self.db.flush()
        return True


class _SingletonSettingsRepository:
    model = None

    def __init__(self, db: Session):
        self.db = db

    def get(self) -> Optional[Dict[str, Any]]:
        row = self.db.query(self.model).filter(self.model.id == "default").first()
        return dict(row.settings or {}) if row else None

    def set(self, settings: Dict[str, Any]) -> Dict[str, Any]:
        row = self.db.query(self.model).filter(self.model.id == "default").first()
        payload = dict(settings or {})
        if row is None:
            self.db.add(self.model(id="default", settings=payload))
        else:
            row.settings = payload
        self.db.flush()
        return payload


class CapabilitySettingsRepository(_SingletonSettingsRepository):
    model = CapabilitySetting


class NotificationSettingsRepository(_SingletonSettingsRepository):
    model = NotificationSetting


class DeploymentDefaultsRepository(_SingletonSettingsRepository):
    model = DeploymentDefault


class RetentionPolicyRepository:
    def __init__(self, db: Session):
        self.db = db

    def get(self, policy_type: str) -> Optional[Dict[str, Any]]:
        row = self.db.query(RetentionPolicy).filter(RetentionPolicy.policy_type == policy_type).first()
        return dict(row.settings or {}) if row else None

    def set(self, policy_type: str, settings: Dict[str, Any]) -> Dict[str, Any]:
        row = self.db.query(RetentionPolicy).filter(RetentionPolicy.policy_type == policy_type).first()
        payload = dict(settings or {})
        if row is None:
            self.db.add(RetentionPolicy(policy_type=policy_type, settings=payload))
        else:
            row.settings = payload
        self.db.flush()
        return payload


class _NamedSettingsRepository:
    model = None
    payload_field = "settings"

    def __init__(self, db: Session):
        self.db = db

    def get(self, name: str) -> Optional[Dict[str, Any]]:
        row = self.db.query(self.model).filter(self.model.name == name).first()
        if row is None:
            return None
        return dict(getattr(row, self.payload_field) or {})

    def get_all(self) -> Dict[str, Dict[str, Any]]:
        rows = self.db.query(self.model).order_by(self.model.name).all()
        return {row.name: dict(getattr(row, self.payload_field) or {}) for row in rows}

    def set(self, name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        row = self.db.query(self.model).filter(self.model.name == name).first()
        value = dict(payload or {})
        if row is None:
            self.db.add(self.model(name=name, **{self.payload_field: value}))
        else:
            setattr(row, self.payload_field, value)
        self.db.flush()
        return value

    def delete(self, name: str) -> bool:
        row = self.db.query(self.model).filter(self.model.name == name).first()
        if row is None:
            return False
        self.db.delete(row)
        self.db.flush()
        return True


class InspectionProfileRepository(_NamedSettingsRepository):
    model = InspectionProfileSetting


class WorkflowTemplateRepository(_NamedSettingsRepository):
    model = WorkflowTemplateSetting
    payload_field = "template"


class GlobalVariableRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_all(self) -> Dict[str, Any]:
        return {
            row.name: row.value
            for row in self.db.query(GlobalVariable).order_by(GlobalVariable.name).all()
        }

    def set(self, name: str, value: Any) -> Any:
        row = self.db.query(GlobalVariable).filter(GlobalVariable.name == name).first()
        if row is None:
            self.db.add(GlobalVariable(name=name, value=value))
        else:
            row.value = value
        self.db.flush()
        return value

    def replace_all(self, values: Dict[str, Any]) -> Dict[str, Any]:
        wanted = dict(values or {})
        existing = {row.name: row for row in self.db.query(GlobalVariable).all()}
        for name, row in existing.items():
            if name not in wanted:
                self.db.delete(row)
        for name, value in wanted.items():
            self.set(name, value)
        self.db.flush()
        return wanted


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


def _encrypt_jump_host_fields(jh: JumpHost) -> JumpHost:
    """Encrypt password / key_content on the row in place (Phase 3.g)."""
    from app.core.secret_store import encrypt_secret
    if getattr(jh, "password", None):
        jh.password = encrypt_secret(jh.password)
    if getattr(jh, "key_content", None):
        jh.key_content = encrypt_secret(jh.key_content)
    return jh


def _decrypt_jump_host_copy(jh: Optional[JumpHost]) -> Optional[JumpHost]:
    """Return a detached copy with secrets decrypted in-memory."""
    if jh is None:
        return None
    from app.core.secret_store import decrypt_secret
    detached = copy(jh)
    if getattr(detached, "password", None):
        detached.password = decrypt_secret(detached.password)
    if getattr(detached, "key_content", None):
        detached.key_content = decrypt_secret(detached.key_content)
    return detached


class JumpHostRepository:
    """Phase 3.g SSOT CRUD for the `jump_hosts` table.

    Mirrors ServerRepository's secret handling: writes encrypt password /
    key_content; reads return a detached copy with secrets decrypted so SSH
    clients can consume ORM attributes directly.
    """
    def __init__(self, db: Session):
        self.db = db

    def get_by_id(self, jh_id: str) -> Optional[JumpHost]:
        return _decrypt_jump_host_copy(self.db.query(JumpHost).filter(JumpHost.id == jh_id).first())

    def get_by_name(self, name: str) -> Optional[JumpHost]:
        return _decrypt_jump_host_copy(self.db.query(JumpHost).filter(JumpHost.name == name).first())

    def list_all(self) -> List[JumpHost]:
        return [_decrypt_jump_host_copy(jh) for jh in self.db.query(JumpHost).order_by(JumpHost.name).all()]

    def list_names(self) -> List[str]:
        return [r[0] for r in self.db.query(JumpHost.name).order_by(JumpHost.name).all()]

    def create(self, name: str, host: str, port: int = 22, user: str = "root",
               key: str = "~/.ssh/id_rsa", key_content: str = None, password: str = None,
               status: str = "online", description: str = None, tags: list = None) -> JumpHost:
        jh = JumpHost(
            name=name, host=host, port=port, user=user,
            key=key, key_content=key_content, password=password,
            status=status or "online", description=description, tags=tags or [],
        )
        _encrypt_jump_host_fields(jh)
        self.db.add(jh)
        self.db.commit()
        self.db.refresh(jh)
        return _decrypt_jump_host_copy(jh)

    def update(self, jh_id: str, **fields) -> Optional[JumpHost]:
        persisted = self.db.query(JumpHost).filter(JumpHost.id == jh_id).first()
        if not persisted:
            return None
        for key, value in fields.items():
            if hasattr(persisted, key) and value is not None:
                setattr(persisted, key, value)
        _encrypt_jump_host_fields(persisted)
        self.db.commit()
        self.db.refresh(persisted)
        return _decrypt_jump_host_copy(persisted)

    def delete(self, jh_id: str) -> bool:
        jh = self.db.query(JumpHost).filter(JumpHost.id == jh_id).first()
        if jh:
            self.db.delete(jh)
            self.db.commit()
            return True
        return False

    def count_referencing_servers(self, name: str) -> int:
        """Number of Server rows that reference this jump host by name.

        Used by the API to refuse deletion of a still-referenced bastion.
        """
        return self.db.query(Server).filter(Server.jump_host == name).count()


class SshKeyRepository:
    """SSH 私钥 SSOT CRUD — ssh_keys 表（加密存储）。

    私钥材料加密后存入 private_key_encrypted 字段；
    passphrase 同理加密后存入 passphrase_encrypted。
    读路径返回解密后的明文供 SSH 客户端使用。
    """
    def __init__(self, db: Session):
        self.db = db

    def get_by_name(self, name: str) -> Optional[SshKey]:
        return self.db.query(SshKey).filter(SshKey.name == name).first()

    def list_all(self, limit: int = 100) -> List[SshKey]:
        return self.db.query(SshKey).order_by(SshKey.name).limit(limit).all()

    def create(self, name: str, private_key: str, passphrase: str = None, description: str = None) -> SshKey:
        from app.core.secret_store import encrypt_secret
        key = SshKey(
            name=name,
            private_key_encrypted=encrypt_secret(private_key),
            passphrase_encrypted=encrypt_secret(passphrase) if passphrase else None,
            description=description,
        )
        self.db.add(key)
        self.db.commit()
        self.db.refresh(key)
        return key

    def update(self, name: str, private_key: str = None, passphrase: str = None, description: str = None,
               new_name: str = None) -> Optional[SshKey]:
        from app.core.secret_store import encrypt_secret
        row = self.get_by_name(name)
        if not row:
            return None
        if private_key is not None:
            row.private_key_encrypted = encrypt_secret(private_key)
        if passphrase is not None:
            row.passphrase_encrypted = encrypt_secret(passphrase) if passphrase else None
        if description is not None:
            row.description = description
        if new_name and new_name != name:
            row.name = new_name
        self.db.commit()
        self.db.refresh(row)
        return row

    def delete(self, name: str) -> bool:
        row = self.get_by_name(name)
        if row:
            self.db.delete(row)
            self.db.commit()
            return True
        return False

    def get_decrypted(self, name: str) -> Optional[Dict[str, Any]]:
        """返回解密后的私钥和 passphrase dict。"""
        from app.core.secret_store import decrypt_secret
        row = self.get_by_name(name)
        if not row:
            return None
        return {
            "name": row.name,
            "private_key": decrypt_secret(row.private_key_encrypted) if row.private_key_encrypted else "",
            "passphrase": decrypt_secret(row.passphrase_encrypted) if row.passphrase_encrypted else None,
            "description": row.description,
        }


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
