import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    Column, String, Integer, DateTime, Text, Boolean, ForeignKey, JSON,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship
from .base import Base


def _uuid() -> str:
    return uuid.uuid4().hex


_utcnow = lambda: datetime.now(timezone.utc).replace(tzinfo=None)


class User(Base):
    __tablename__ = "users"

    id = Column(String(32), primary_key=True, default=_uuid)
    username = Column(String(64), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(32), default="developer")
    is_admin = Column(Boolean, default=False)
    can_deploy = Column(Boolean, default=True)
    session_version = Column(Integer, default=1, nullable=False)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

class Server(Base):
    __tablename__ = "servers"

    id = Column(String(32), primary_key=True, default=_uuid)
    name = Column(String(64), unique=True, nullable=False, index=True)
    host = Column(String(255), nullable=False)
    port = Column(Integer, default=22)
    user = Column(String(64), default="root")
    key = Column(String(255), default="~/.ssh/id_rsa")
    key_content = Column(Text, nullable=True)
    password = Column(Text, nullable=True)
    jump_host = Column(String(64), nullable=True)
    status = Column(String(24), default="online", index=True)
    # Extra dict (description, tags, group, sftp_allowed_roots, auth_type, enabled,
    # inline_jump_host, etc.). Readers fall back to {} if this column is NULL.
    metadata_json = Column(JSON, nullable=True, default=dict)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

class Environment(Base):
    __tablename__ = "environments"

    id = Column(String(32), primary_key=True, default=_uuid)
    name = Column(String(64), unique=True, nullable=False, index=True)
    variables = Column(JSON, default=dict)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)


class SystemEnvironment(Base):
    __tablename__ = "system_environments"
    __table_args__ = (
        UniqueConstraint("system_name", "name", name="uq_system_environment_name"),
    )

    id = Column(String(32), primary_key=True, default=_uuid)
    system_name = Column(String(64), ForeignKey("systems.name", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(64), nullable=False, index=True)
    display_name = Column(String(128), nullable=True)
    category = Column(String(32), default="custom")
    description = Column(Text, nullable=True)
    base_path = Column(String(255), nullable=True)
    servers = Column(JSON, default=list)
    variables = Column(JSON, default=dict)
    service_overrides = Column(JSON, default=dict)
    group_overrides = Column(JSON, default=dict)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)


class System(Base):
    """System metadata; services and environments use dedicated tables."""
    __tablename__ = "systems"

    id = Column(String(32), primary_key=True, default=_uuid)
    name = Column(String(64), unique=True, nullable=False, index=True)
    display_name = Column(String(128), nullable=True)
    strategy = Column(String(32), default="WORKFLOW")
    base_path = Column(String(255), default="/data/web/app")
    description = Column(Text, nullable=True)
    variables = Column(JSON, default=dict)
    servers = Column(JSON, default=list)
    message_routing = Column(JSON, default=dict)
    environments = Column(JSON, default=dict)  # per-system 环境配置（含 service_overrides/group_overrides）
    services = Column(JSON, default=list)  # 服务列表（legacy 形态，Service 表是独立 SSOT）
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)


class CapabilitySetting(Base):
    __tablename__ = "capability_settings"

    id = Column(String(32), primary_key=True, default="default")
    settings = Column(JSON, default=dict, nullable=False)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)


class RetentionPolicy(Base):
    __tablename__ = "retention_policies"

    policy_type = Column(String(32), primary_key=True)
    settings = Column(JSON, default=dict, nullable=False)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)


class NotificationSetting(Base):
    __tablename__ = "notification_settings"

    id = Column(String(32), primary_key=True, default="default")
    settings = Column(JSON, default=dict, nullable=False)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)


class InspectionProfileSetting(Base):
    __tablename__ = "inspection_profiles"

    name = Column(String(128), primary_key=True)
    settings = Column(JSON, default=dict, nullable=False)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)


class WorkflowTemplateSetting(Base):
    __tablename__ = "workflow_templates"

    name = Column(String(128), primary_key=True)
    template = Column(JSON, default=dict, nullable=False)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)


class DeploymentDefault(Base):
    __tablename__ = "deployment_defaults"

    id = Column(String(32), primary_key=True, default="default")
    settings = Column(JSON, default=dict, nullable=False)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)


class GlobalVariable(Base):
    __tablename__ = "global_variables"

    name = Column(String(128), primary_key=True)
    value = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)


class Service(Base):
    __tablename__ = "services"
    __table_args__ = (
        UniqueConstraint("system_name", "name", name="uq_services_system_name"),
    )

    id = Column(String(32), primary_key=True, default=_uuid)
    name = Column(String(64), nullable=False, index=True)
    display_name = Column(String(128), nullable=True)
    system_name = Column(String(64), nullable=False, index=True)
    repo = Column(String(255), nullable=True)
    build_cmd = Column(String(255), nullable=True)
    start_cmd = Column(String(255), nullable=True)
    template = Column(String(64), nullable=True)
    pipeline_id = Column(String(64), nullable=True)
    template_variables = Column(JSON, default=dict)
    servers = Column(JSON, default=list)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)
class Deployment(Base):
    __tablename__ = "deployments"

    id = Column(String(32), primary_key=True, default=_uuid)
    system = Column(String(64), nullable=False, index=True)
    service = Column(String(64), nullable=True)
    environment = Column(String(64), nullable=True)
    strategy = Column(String(32), default="DIRECT")
    status = Column(String(16), nullable=False, default="running", index=True)
    servers = Column(Text, default="")
    server_group = Column(String(128), nullable=True, index=True)
    version = Column(String(128), nullable=True)
    message = Column(Text, default="")
    started_at = Column(DateTime, default=_utcnow)
    finished_at = Column(DateTime, nullable=True)
    created_by = Column(String(64), nullable=True)


class Pipeline(Base):
    __tablename__ = "pipelines"

    id = Column(String(32), primary_key=True, default=_uuid)
    name = Column(String(128), nullable=False)
    system_name = Column(String(64), nullable=False, index=True)
    description = Column(Text, nullable=True)
    strategy = Column(String(32), default="DIRECT")
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    steps = relationship("PipelineStep", back_populates="pipeline", cascade="all, delete-orphan")
class PipelineStep(Base):
    __tablename__ = "pipeline_steps"

    id = Column(String(32), primary_key=True, default=_uuid)
    pipeline_id = Column(String(32), ForeignKey("pipelines.id"), nullable=False, index=True)
    name = Column(String(128), nullable=False)
    step_type = Column(String(32), default="command")
    config = Column(JSON, default=dict)
    sort_order = Column(Integer, default=0)

    pipeline = relationship("Pipeline", back_populates="steps")


class DeployTask(Base):
    __tablename__ = "deploy_tasks"

    id = Column(String(32), primary_key=True, default=_uuid)
    deployment_id = Column(String(32), ForeignKey("deployments.id"), nullable=True, index=True)
    status = Column(String(16), default="pending", index=True)
    worker = Column(String(64), nullable=True)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    result = Column(Text, nullable=True)
    payload_json = Column(Text, nullable=True)
    lock_key = Column(String(512), nullable=True)
    cancel_requested = Column(Boolean, default=False)
    created_at = Column(DateTime, default=_utcnow)
class DeployLog(Base):
    __tablename__ = "deploy_logs"

    id = Column(String(32), primary_key=True, default=_uuid)
    task_id = Column(String(32), ForeignKey("deploy_tasks.id"), nullable=True, index=True)
    deployment_id = Column(String(32), ForeignKey("deployments.id"), nullable=True, index=True)
    step_name = Column(String(128), nullable=True)
    level = Column(String(16), default="info")
    message = Column(Text, nullable=False)
    created_at = Column(DateTime, default=_utcnow)


class ServerGroup(Base):
    """服务器组 - 发版目标集合"""
    __tablename__ = "server_groups"

    id = Column(String(32), primary_key=True, default=_uuid)
    name = Column(String(128), unique=True, nullable=False, index=True)
    display_name = Column(String(256), nullable=True)
    description = Column(Text, nullable=True)
    server_names = Column(JSON, default=list)
    tags = Column(JSON, default=list)
    # Extra dict (legacy `server` (single fallback), `variables`, etc.).
    # Phase 3b SSOT migration: legacy save_group writes go through here; v2 API
    # creates groups with all data in direct columns.
    metadata_json = Column(JSON, nullable=True, default=dict)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)


class JumpHost(Base):
    """跳板机 (bastion) inventory.

    The `servers.jump_host` column holds the JumpHost name;
    full connection metadata (host/port/user/key/key_content/password) lives
    here and is resolved on demand by get_jump_host_by_name().

    The same SSH target may exist in BOTH the `servers` and `jump_hosts`
    tables — e.g. the `tiaobanji` server is both an SSH target itself and a
    bastion that other servers route through. Keeping them in separate
    tables avoids forcing the same row to satisfy two slightly different
    shapes (server has group/description/tags/...; jump host is connection-
    only).
    """
    __tablename__ = "jump_hosts"

    id = Column(String(32), primary_key=True, default=_uuid)
    name = Column(String(64), unique=True, nullable=False, index=True)
    host = Column(String(255), nullable=False)
    port = Column(Integer, default=22)
    user = Column(String(64), default="root")
    key = Column(String(255), default="~/.ssh/id_rsa")
    key_content = Column(Text, nullable=True)  # encrypted at rest
    password = Column(Text, nullable=True)     # encrypted at rest
    status = Column(String(24), default="online", index=True)
    description = Column(Text, nullable=True)
    tags = Column(JSON, default=list)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)


class CommandExecutionLog(Base):
    """命令执行历史记录"""
    __tablename__ = "command_execution_logs"

    id = Column(String(32), primary_key=True, default=_uuid)
    server_name = Column(String(128), nullable=False, index=True)
    username = Column(String(64), nullable=False, index=True)
    command = Column(Text, nullable=False)
    exit_code = Column(Integer, nullable=True)
    stdout_preview = Column(Text, nullable=True)
    stderr_preview = Column(Text, nullable=True)
    duration_ms = Column(Integer, nullable=True)
    risk_level = Column(String(16), default="safe")
    hop_context = Column(JSON, nullable=True)
    auth_mode = Column(String(32), nullable=True)
    is_terminal = Column(Boolean, default=False)
    session_id = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=_utcnow, index=True)


class DmlExecutionLog(Base):
    """受控 DML 执行历史。用于数据库工作台、审计和 MCP 两阶段调用复盘。"""
    __tablename__ = "dml_execution_logs"

    id = Column(String(32), primary_key=True, default=_uuid)
    preview_id = Column(String(64), nullable=True, index=True)
    source = Column(String(32), default="local_ops_db", index=True)
    connection_id = Column(String(32), nullable=True, index=True)
    connection_name = Column(String(128), nullable=True)
    database_name = Column(String(128), nullable=True)
    environment = Column(String(64), nullable=True, index=True)
    statement_type = Column(String(16), nullable=False, index=True)
    table_name = Column(String(128), nullable=True, index=True)
    history_sql = Column(Text, nullable=False)
    where_summary = Column(Text, nullable=True)
    changed_columns = Column(JSON, default=list)
    before_sample_json = Column(JSON, default=list)
    estimated_affected_rows = Column(Integer, nullable=True)
    affected_rows = Column(Integer, nullable=True)
    max_affected_rows = Column(Integer, nullable=True)
    risk_level = Column(String(24), default="high", index=True)
    status = Column(String(24), default="success", index=True)
    reason = Column(Text, nullable=True)
    operator = Column(String(128), nullable=True, index=True)
    duration_ms = Column(Integer, nullable=True)
    audit_id = Column(String(64), nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=_utcnow, index=True)




class SchemaMigration(Base):
    """数据库结构迁移版本记录。"""
    __tablename__ = "schema_migrations"

    version = Column(String(64), primary_key=True)
    name = Column(String(255), nullable=False)
    applied_at = Column(DateTime, default=_utcnow, nullable=False)
    checksum = Column(String(128), nullable=True)


class DeploymentServerTask(Base):
    """发布任务的服务器维度执行状态。"""
    __tablename__ = "deployment_server_tasks"

    id = Column(String(32), primary_key=True, default=_uuid)
    deployment_id = Column(String(32), ForeignKey("deployments.id"), nullable=False, index=True)
    task_id = Column(String(32), ForeignKey("deploy_tasks.id"), nullable=True, index=True)
    server_name = Column(String(128), nullable=False, index=True)
    status = Column(String(24), default="pending", index=True)
    current_step = Column(String(128), nullable=True)
    message = Column(Text, nullable=True)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    duration_ms = Column(Integer, default=0)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)


class DeploymentStepTask(Base):
    """发布任务的步骤维度执行状态。"""
    __tablename__ = "deployment_step_tasks"

    id = Column(String(32), primary_key=True, default=_uuid)
    deployment_id = Column(String(32), ForeignKey("deployments.id"), nullable=False, index=True)
    task_id = Column(String(32), ForeignKey("deploy_tasks.id"), nullable=True, index=True)
    server_task_id = Column(String(32), ForeignKey("deployment_server_tasks.id"), nullable=True, index=True)
    server_name = Column(String(128), nullable=True, index=True)
    step_name = Column(String(128), nullable=False)
    step_type = Column(String(64), nullable=True)
    sort_order = Column(Integer, default=0)
    status = Column(String(24), default="pending", index=True)
    message = Column(Text, nullable=True)
    captured_config = Column(Text, nullable=True)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    duration_ms = Column(Integer, default=0)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)


class DeploymentPackageDistribution(Base):
    """发布包分发到远程服务器的校验记录。"""
    __tablename__ = "deployment_package_distributions"

    id = Column(String(32), primary_key=True, default=_uuid)
    deployment_id = Column(String(32), ForeignKey("deployments.id"), nullable=False, index=True)
    task_id = Column(String(32), ForeignKey("deploy_tasks.id"), nullable=True, index=True)
    server_name = Column(String(128), nullable=False, index=True)
    package_name = Column(String(255), nullable=False)
    local_path = Column(String(1024), nullable=True)
    remote_path = Column(String(1024), nullable=True)
    local_sha256 = Column(String(128), nullable=True)
    remote_sha256 = Column(String(128), nullable=True)
    size_bytes = Column(Integer, nullable=True)
    status = Column(String(24), default="pending", index=True)
    reused = Column(Boolean, default=False)
    message = Column(Text, nullable=True)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    duration_ms = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)


class DeploymentLockRecord(Base):
    """持久化发布锁，用于进程重启后的排查和强制释放。"""
    __tablename__ = "deployment_lock_records"

    lock_key = Column(String(255), primary_key=True)
    deployment_id = Column(String(32), nullable=True, index=True)
    task_id = Column(String(32), nullable=True, index=True)
    owner = Column(String(64), nullable=True)
    status = Column(String(24), default="locked", index=True)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)
    expires_at = Column(DateTime, nullable=True)


class NotificationEvent(Base):
    """通知发送事件。"""
    __tablename__ = "notification_events"

    id = Column(String(32), primary_key=True, default=_uuid)
    event_type = Column(String(64), nullable=False, index=True)
    target = Column(String(255), nullable=True)
    status = Column(String(24), default="pending", index=True)
    message = Column(Text, nullable=True)
    payload = Column(JSON, default=dict)
    created_at = Column(DateTime, default=_utcnow)




class ReportArtifact(Base):
    """Generated report artifact managed by Report Center."""
    __tablename__ = "report_artifacts"

    id = Column(String(32), primary_key=True, default=_uuid)
    report_type = Column(String(64), nullable=False, index=True)
    title = Column(String(255), nullable=False)
    target_type = Column(String(64), nullable=True, index=True)
    target_id = Column(String(255), nullable=True, index=True)
    status = Column(String(24), default="ready", index=True)
    format = Column(String(16), default="json", index=True)
    file_path = Column(String(1024), nullable=True)
    size_bytes = Column(Integer, default=0)
    sha256 = Column(String(128), nullable=True)
    summary = Column(Text, nullable=True)
    metadata_json = Column(JSON, default=dict)
    created_by = Column(String(128), nullable=True, index=True)
    created_at = Column(DateTime, default=_utcnow, index=True)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)


class ToolToken(Base):
    """Capability Server / MCP-like tool token. Token secret is stored as SHA256 only."""
    __tablename__ = "tool_tokens"

    id = Column(String(32), primary_key=True, default=_uuid)
    name = Column(String(128), nullable=False)
    token_hash = Column(String(128), unique=True, nullable=False, index=True)
    token_prefix = Column(String(32), nullable=True)
    owner = Column(String(128), nullable=False, index=True)
    description = Column(Text, nullable=True)
    scopes = Column(JSON, default=list)
    allow_write = Column(Boolean, default=False)
    allow_prod = Column(Boolean, default=False)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)
    expires_at = Column(DateTime, nullable=True)
    last_used_at = Column(DateTime, nullable=True)
    revoked_at = Column(DateTime, nullable=True)
    channel_bindings = Column(JSON, default=list)
    approver_identities = Column(JSON, default=list)
    # qclaw Element room binding: if non-empty, this token is only allowed to
    # call qclaw routing/approval tools from the listed Matrix room IDs.
    # Empty list (= []) means no room restriction; default is empty for
    # backward compatibility (existing tokens keep working unchanged).
    bound_room_ids = Column(JSON, default=list)
    # qclaw Element approver whitelist: if non-empty, only these Matrix user
    # IDs may consume approval short codes created through this token. The
    # whitelist is stored on the token (not the system config) so binding
    # approvers is a per-credential, admin-managed decision. Empty list
    # (= []) means "no token-level restriction"; approval still requires the
    # system/service-level approvers (message_routing.approvers) when
    # configured, and falls back to any room member when neither is set
    # (backward compatible with pre-binding tokens).
    approver_matrix_ids = Column(JSON, default=list)


class ToolCallLog(Base):
    """Auditable record of each external capability/tool invocation."""
    __tablename__ = "tool_call_logs"

    id = Column(String(32), primary_key=True, default=_uuid)
    tool_name = Column(String(128), nullable=False, index=True)
    client_name = Column(String(128), nullable=True)
    token_id = Column(String(32), nullable=True, index=True)
    token_owner = Column(String(128), nullable=True, index=True)
    username = Column(String(128), nullable=True, index=True)
    input_args = Column(Text, nullable=True)
    normalized_args = Column(Text, nullable=True)
    result_preview = Column(Text, nullable=True)
    status = Column(String(24), default="success", index=True)
    risk_level = Column(String(24), default="low", index=True)
    policy_result = Column(Text, nullable=True)
    blocked_reason = Column(Text, nullable=True)
    related_plan_id = Column(String(32), nullable=True, index=True)
    related_deployment_id = Column(String(32), nullable=True, index=True)
    related_job_id = Column(String(32), nullable=True, index=True)
    ip_address = Column(String(64), nullable=True)
    user_agent = Column(String(255), nullable=True)
    duration_ms = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=_utcnow, index=True)


class OperationJob(Base):
    """Unified background job used by MCP/tool execution and future OPS tasks."""
    __tablename__ = "operation_jobs"

    id = Column(String(32), primary_key=True, default=_uuid)
    job_type = Column(String(64), default="mcp_tool", index=True)
    source = Column(String(64), default="tool", index=True)
    source_tool = Column(String(128), nullable=True, index=True)
    title = Column(String(255), nullable=False)
    status = Column(String(32), default="queued", index=True)
    progress = Column(Integer, default=0)
    risk_level = Column(String(24), default="low", index=True)
    operator = Column(String(128), nullable=True, index=True)
    target = Column(String(255), nullable=True)
    request_json = Column(JSON, default=dict)
    result_json = Column(JSON, default=dict)
    error_message = Column(Text, nullable=True)
    audit_id = Column(String(32), nullable=True, index=True)
    worker_id = Column(String(128), nullable=True)
    created_at = Column(DateTime, default=_utcnow, index=True)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)


class ToolPlan(Base):
    """Operation plan generated by external tools. Execution must reference plan_id."""
    __tablename__ = "tool_plans"

    id = Column(String(32), primary_key=True, default=_uuid)
    plan_type = Column(String(64), default="generic", index=True)
    status = Column(String(32), default="draft", index=True)
    created_by = Column(String(128), nullable=True, index=True)
    source_tool = Column(String(128), nullable=True)
    system = Column(String(64), nullable=True, index=True)
    service = Column(String(128), nullable=True, index=True)
    environment = Column(String(64), nullable=True, index=True)
    servers = Column(JSON, default=list)
    package_name = Column(String(255), nullable=True)
    pipeline_id = Column(String(32), nullable=True)
    payload = Column(JSON, default=dict)
    confirmation = Column(JSON, default=dict)
    precheck = Column(JSON, default=dict)
    diff = Column(JSON, default=dict)
    risk_level = Column(String(24), default="low", index=True)
    confirm_text = Column(String(255), nullable=True)
    confirmed_by = Column(String(128), nullable=True)
    confirmed_at = Column(DateTime, nullable=True)
    executed_at = Column(DateTime, nullable=True)
    related_deployment_id = Column(String(32), nullable=True, index=True)
    created_at = Column(DateTime, default=_utcnow, index=True)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)


class ToolPlanEvent(Base):
    """Timeline events for ToolPlan."""
    __tablename__ = "tool_plan_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    plan_id = Column(String(32), ForeignKey("tool_plans.id"), nullable=False, index=True)
    event_type = Column(String(64), nullable=False, index=True)
    actor = Column(String(128), nullable=True)
    message = Column(Text, nullable=True)
    payload = Column(JSON, default=dict)
    created_at = Column(DateTime, default=_utcnow, index=True)





class AiActionApproval(Base):
    """Human approval request for high-risk AI/MCP suggested actions.

    Extended for qclaw Element approval flow: supports immutable action
    manifests, one-time approval codes, routing tickets, package bindings,
    and execution lifecycle tracking.
    """
    __tablename__ = "ai_action_approvals"

    id = Column(String(32), primary_key=True, default=_uuid)
    action_type = Column(String(64), nullable=False, index=True)
    tool_name = Column(String(128), nullable=False, index=True)
    target_type = Column(String(64), nullable=True, index=True)
    target_id = Column(String(255), nullable=True, index=True)
    request_payload = Column(JSON, default=dict)
    risk_level = Column(String(32), nullable=True, index=True)
    ai_reason = Column(Text, nullable=True)
    status = Column(String(32), default="PENDING_APPROVAL", index=True)
    requested_by = Column(String(128), nullable=True, index=True)
    approved_by = Column(String(128), nullable=True, index=True)
    created_at = Column(DateTime, default=_utcnow, index=True)
    approved_at = Column(DateTime, nullable=True)
    executed_at = Column(DateTime, nullable=True)

    # ── qclaw Element approval extension fields ──
    action_digest = Column(String(64), nullable=True, index=True)
    approval_code_hash = Column(String(128), nullable=True)
    room_id = Column(String(255), nullable=True)
    request_event_id = Column(String(255), nullable=True)
    approval_event_id = Column(String(255), nullable=True)
    content_sha256 = Column(String(64), nullable=True)
    routing_ticket_digest = Column(String(64), nullable=True)
    routing_config_revision = Column(String(64), nullable=True)
    expires_at = Column(DateTime, nullable=True, index=True)
    consumed_at = Column(DateTime, nullable=True)
    rejected_by = Column(String(255), nullable=True)
    rejected_at = Column(DateTime, nullable=True)
    package_name = Column(String(255), nullable=True)
    package_sha256 = Column(String(64), nullable=True)
    package_size_bytes = Column(Integer, nullable=True)
    execution_job_id = Column(Integer, nullable=True, index=True)
    execution_result = Column(JSON, nullable=True)
    failure_reason = Column(Text, nullable=True)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)


class ExecutionPlan(Base):
    """消息级执行计划：一条 Element/qclaw 消息对应一次人工审批。

    与 AiActionApproval（单动作审批）不同，ExecutionPlan 冻结完整计划 manifest，
    授权人批准一次后，计划内声明的步骤按顺序自动执行，不再逐步骤审批。

    设计要点：
    - plan_digest 是不可变 plan manifest 的 SHA-256，用于幂等去重和审批后完整性校验。
    - 短码只在 prepare 时返回一次明文，数据库只存加盐哈希。
    - consume() 使用条件更新保证原子性，多个并发调用只有一个成功。
    """
    __tablename__ = "execution_plans"

    id = Column(String(32), primary_key=True, default=_uuid)
    status = Column(String(32), default="PENDING_APPROVAL", index=True)
    plan_digest = Column(String(64), nullable=False, index=True)
    risk_level = Column(String(32), default="high", index=True)

    # ── 消息与路由绑定 ──
    room_id = Column(String(255), nullable=False, index=True)
    request_event_id = Column(String(255), nullable=False, index=True)
    content_sha256 = Column(String(64), nullable=True)
    system_name = Column(String(64), nullable=True, index=True)
    service_name = Column(String(128), nullable=True)
    environment = Column(String(64), nullable=True)
    targets = Column(JSON, default=list)
    routing_ticket_digest = Column(String(64), nullable=True)
    routing_config_revision = Column(String(64), nullable=True)

    # ── 发布包绑定 ──
    package_name = Column(String(255), nullable=True)
    package_sha256 = Column(String(64), nullable=True)
    package_size_bytes = Column(Integer, nullable=True)

    # ── 计划内容 ──
    manifest = Column(JSON, nullable=False, default=dict)
    policy = Column(JSON, nullable=False, default=dict)
    ai_reason = Column(Text, nullable=True)
    authorized_matrix_users = Column(JSON, default=list)

    # ── 审批生命周期 ──
    approval_code_hash = Column(String(128), nullable=True)
    requested_by = Column(String(128), nullable=True)
    approved_by = Column(String(255), nullable=True)
    approval_event_id = Column(String(255), nullable=True)
    expires_at = Column(DateTime, nullable=True, index=True)
    consumed_at = Column(DateTime, nullable=True)
    rejected_by = Column(String(255), nullable=True)
    rejected_at = Column(DateTime, nullable=True)
    approved_at = Column(DateTime, nullable=True)

    # ── 执行生命周期 ──
    execution_job_id = Column(String(32), nullable=True, index=True)
    execution_result = Column(JSON, nullable=True)
    failure_reason = Column(Text, nullable=True)

    created_at = Column(DateTime, default=_utcnow, index=True)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    steps = relationship(
        "ExecutionPlanStep",
        back_populates="plan",
        cascade="all, delete-orphan",
        order_by="ExecutionPlanStep.step_order",
        lazy="selectin",
    )


class ExecutionPlanStep(Base):
    """执行计划内的有序步骤。审批后按顺序执行，不再触发审批。"""
    __tablename__ = "execution_plan_steps"

    id = Column(String(32), primary_key=True, default=_uuid)
    plan_id = Column(String(32), ForeignKey("execution_plans.id"), nullable=False, index=True)
    step_key = Column(String(128), nullable=False)
    step_order = Column(Integer, nullable=False, default=0)
    action_type = Column(String(64), nullable=False, index=True)
    parameters = Column(JSON, nullable=False, default=dict)
    dependencies = Column(JSON, nullable=False, default=list)
    status = Column(String(24), default="PENDING", index=True)
    attempt_count = Column(Integer, nullable=False, default=0)
    result = Column(JSON, nullable=True)
    error_message = Column(Text, nullable=True)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_utcnow, index=True)

    plan = relationship("ExecutionPlan", back_populates="steps")


class DeployPackage(Base):
    """Local deploy package metadata managed by File Center."""
    __tablename__ = "deploy_packages"

    id = Column(String(32), primary_key=True, default=_uuid)
    package_name = Column(String(255), unique=True, nullable=False, index=True)
    file_path = Column(String(1024), nullable=True)
    size_bytes = Column(Integer, default=0)
    sha256 = Column(String(128), nullable=True, index=True)
    system = Column(String(64), nullable=True, index=True)
    service = Column(String(128), nullable=True, index=True)
    service_hint = Column(String(128), nullable=True, index=True)
    version_hint = Column(String(128), nullable=True, index=True)
    uploaded_by = Column(String(128), nullable=True)
    uploaded_at = Column(DateTime, default=_utcnow, index=True)
    last_used_at = Column(DateTime, nullable=True, index=True)
    used_count = Column(Integer, default=0)
    protected = Column(Boolean, default=False, index=True)
    deleted = Column(Boolean, default=False, index=True)
    deleted_at = Column(DateTime, nullable=True)
    delete_reason = Column(Text, nullable=True)


class DeployPackageRef(Base):
    """Reference between a package and a release/rollback/retry/distribution."""
    __tablename__ = "deploy_package_refs"

    id = Column(String(32), primary_key=True, default=_uuid)
    package_id = Column(String(32), ForeignKey("deploy_packages.id"), nullable=True, index=True)
    package_name = Column(String(255), nullable=False, index=True)
    deployment_id = Column(String(32), nullable=True, index=True)
    system = Column(String(64), nullable=True, index=True)
    service = Column(String(128), nullable=True, index=True)
    environment = Column(String(64), nullable=True, index=True)
    server_name = Column(String(128), nullable=True, index=True)
    usage_type = Column(String(32), default="deploy", index=True)
    created_at = Column(DateTime, default=_utcnow, index=True)

class SshKey(Base):
    __tablename__ = "ssh_keys"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), unique=True, nullable=False)
    private_key_encrypted = Column(Text, nullable=False)
    passphrase_encrypted = Column(Text, nullable=True)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)


class DeploymentRecord(Base):
    __tablename__ = "deployment_records"
    id = Column(String, primary_key=True)
    system = Column(String, nullable=False, index=True)
    server = Column(String, nullable=False)
    strategy = Column(String, default="DIRECT")
    status = Column(String, default="running", index=True)
    steps = Column(Text, default="[]")
    output = Column(Text, default="")
    message = Column(Text, default="")
    started_at = Column(String, index=True)
    finished_at = Column(String)


class AuditRecord(Base):
    __tablename__ = "audit_records"
    id = Column(Integer, primary_key=True, autoincrement=True)
    action = Column(String, nullable=False)
    target_type = Column(String)
    target_name = Column(String)
    details = Column(Text)
    created_at = Column(String, nullable=False, index=True)


class DatabaseConnection(Base):
    __tablename__ = "database_connections"

    id = Column(String(32), primary_key=True, default=_uuid)
    name = Column(String(64), unique=True, nullable=False, index=True)
    environment = Column(String(64), nullable=False)
    db_type = Column(String(32), default="mysql")
    host = Column(String(255), nullable=False)
    port = Column(Integer, default=3306)
    username = Column(String(64), nullable=False)
    password_encrypted = Column(Text, nullable=True)
    database_name = Column(String(128), nullable=True)
    description = Column(Text, nullable=True)
    use_ssh_tunnel = Column(Boolean, default=False)
    ssh_mode = Column(String(16), default="manual")
    ssh_server_id = Column(String(32), nullable=True)
    ssh_server_name = Column(String(64), nullable=True)
    ssh_host = Column(String(255), nullable=True)
    ssh_port = Column(Integer, default=22)
    ssh_username = Column(String(64), nullable=True)
    ssh_password_encrypted = Column(Text, nullable=True)
    ssh_key_path = Column(String(255), nullable=True)
    ssh_key_passphrase_encrypted = Column(Text, nullable=True)
    ssh_key_content_encrypted = Column(Text, nullable=True)
    ssh_remote_bind_host = Column(String(255), nullable=True)
    # Optional second hop: local -> bastion -> target host, then target host reaches the DB address.
    ssh_target_server_name = Column(String(128), nullable=True)
    ssh_target_host = Column(String(255), nullable=True)
    ssh_target_port = Column(Integer, default=22)
    ssh_target_username = Column(String(64), nullable=True)
    ssh_target_password_encrypted = Column(Text, nullable=True)
    ssh_target_key_path = Column(String(255), nullable=True)
    ssh_target_key_passphrase_encrypted = Column(Text, nullable=True)
    allow_dml = Column(Boolean, default=False)
    allowed_dml_types = Column(JSON, default=list)
    allowed_tables = Column(JSON, default=list)
    blocked_tables = Column(JSON, default=list)
    max_affected_rows_default = Column(Integer, default=100)
    require_dml_reason = Column(Boolean, default=True)
    created_by = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)


class CleanupJob(Base):
    __tablename__ = "cleanup_jobs"

    id = Column(String(32), primary_key=True, default=_uuid)
    name = Column(String(256), nullable=False)
    environment = Column(String(64), nullable=False)
    connection_id = Column(String(32), ForeignKey("database_connections.id", ondelete="SET NULL"), nullable=True)
    connection_name = Column(String(64), nullable=False)
    database_name = Column(String(128), nullable=False)
    table_name = Column(String(128), nullable=False)
    date_column = Column(String(128), nullable=False)
    cutoff_time = Column(String(64), nullable=False)
    batch_size = Column(Integer, nullable=False, default=100000)
    batch_interval_seconds = Column(Integer, nullable=False, default=3)
    max_delete_rows = Column(Integer, nullable=True)
    matched_rows = Column(Integer, default=0)
    deleted_rows = Column(Integer, default=0)
    estimated_batches = Column(Integer, default=0)
    status = Column(String(32), nullable=False, default="draft", index=True)
    risk_level = Column(String(16), nullable=True)
    dry_run_result = Column(Text, nullable=True)
    last_error = Column(Text, nullable=True)
    approval_required = Column(Boolean, nullable=False, default=True)
    approved_by = Column(String(64), nullable=True)
    approved_at = Column(DateTime, nullable=True)
    execution_window_start = Column(String(8), nullable=True)
    execution_window_end = Column(String(8), nullable=True)
    created_by = Column(String(64), nullable=False)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)


class CleanupJobBatch(Base):
    __tablename__ = "cleanup_job_batches"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(String(32), ForeignKey("cleanup_jobs.id"), nullable=False, index=True)
    batch_no = Column(Integer, nullable=False)
    affected_rows = Column(Integer, nullable=False, default=0)
    duration_ms = Column(Integer, nullable=True)
    remaining_rows = Column(Integer, nullable=True)
    sql_text = Column(Text, nullable=True)
    status = Column(String(32), nullable=False)
    error_message = Column(Text, nullable=True)
    started_at = Column(DateTime, nullable=False)
    finished_at = Column(DateTime, nullable=True)


class CleanupJobEvent(Base):
    __tablename__ = "cleanup_job_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(String(32), ForeignKey("cleanup_jobs.id"), nullable=False, index=True)
    event_type = Column(String(64), nullable=False)
    operator = Column(String(64), nullable=True)
    message = Column(Text, nullable=True)
    details = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=_utcnow)


class SqlQueryHistory(Base):
    __tablename__ = "sql_query_history"

    id = Column(String(32), primary_key=True, default=_uuid)
    connection_id = Column(String(32), ForeignKey("database_connections.id"), nullable=True, index=True)
    connection_name = Column(String(64), nullable=True)
    database_name = Column(String(128), nullable=True)
    sql_text = Column(Text, nullable=False)
    normalized_sql = Column(Text, nullable=True)
    query_type = Column(String(32), default="select")
    status = Column(String(32), default="success", index=True)
    row_count = Column(Integer, default=0)
    duration_ms = Column(Integer, nullable=True)
    error_message = Column(Text, nullable=True)
    executed_by = Column(String(64), nullable=True, index=True)
    created_at = Column(DateTime, default=_utcnow, index=True)


class SavedSql(Base):
    __tablename__ = "saved_sql"

    id = Column(String(32), primary_key=True, default=_uuid)
    name = Column(String(128), nullable=False, index=True)
    category = Column(String(64), default="default", index=True)
    description = Column(Text, nullable=True)
    connection_id = Column(String(32), ForeignKey("database_connections.id"), nullable=True, index=True)
    database_name = Column(String(128), nullable=True)
    sql_text = Column(Text, nullable=False)
    created_by = Column(String(64), nullable=True, index=True)
    updated_by = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=_utcnow, index=True)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)




class ProjectServerRelation(Base):
    """项目与服务器部署关系，供项目巡检和综合巡检定位执行环境。"""
    __tablename__ = "project_server_relations"

    id = Column(String(32), primary_key=True, default=_uuid)
    project_id = Column(String(255), nullable=False, index=True)
    server_id = Column(String(255), nullable=False, index=True)
    deploy_role = Column(String(64), nullable=True, index=True)
    deploy_path = Column(String(1024), nullable=True)
    config_path = Column(String(1024), nullable=True)
    log_path = Column(String(1024), nullable=True)
    backup_path = Column(String(1024), nullable=True)
    runtime_user = Column(String(128), nullable=True)
    main_port = Column(String(32), nullable=True)
    active = Column(Boolean, default=True, index=True)
    created_at = Column(DateTime, default=_utcnow, index=True)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)


class InspectionTask(Base):
    """巡检任务配置，支持手动、每日、每周、每月和后续定时任务。"""
    __tablename__ = "inspection_tasks"

    id = Column(String(32), primary_key=True, default=_uuid)
    name = Column(String(255), nullable=False)
    scope_type = Column(String(24), nullable=False, index=True)
    server_id = Column(String(255), nullable=True, index=True)
    project_id = Column(String(255), nullable=True, index=True)
    frequency = Column(String(24), default="MANUAL", index=True)
    cron_expression = Column(String(128), nullable=True)
    rule_group_id = Column(String(64), nullable=True)
    enabled = Column(Boolean, default=True, index=True)
    created_by = Column(String(128), nullable=True, index=True)
    created_at = Column(DateTime, default=_utcnow, index=True)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)


class InspectionRun(Base):
    """服务器/项目巡检执行记录。"""
    __tablename__ = "inspection_runs"

    id = Column(String(32), primary_key=True, default=_uuid)
    task_id = Column(String(32), nullable=True, index=True)
    # scope_kind is a legacy-compatible column used by earlier inspection builds.
    # Keep it populated together with scope_type so existing SQLite databases with
    # NOT NULL inspection_runs.scope_kind continue to accept inserts.
    scope_kind = Column(String(24), nullable=False, default="SERVER", index=True)
    scope_type = Column(String(24), nullable=False, index=True)  # SERVER / PROJECT / PROJECT_COMBINED
    server_id = Column(String(255), nullable=True, index=True)
    project_id = Column(String(255), nullable=True, index=True)
    agent_id = Column(String(64), nullable=True, index=True)
    trigger_type = Column(String(24), default="MANUAL", index=True)
    status = Column(String(24), default="PENDING", index=True)
    score = Column(Integer, default=100)
    high_count = Column(Integer, default=0)
    medium_count = Column(Integer, default=0)
    low_count = Column(Integer, default=0)
    normal_count = Column(Integer, default=0)
    summary = Column(Text, nullable=True)
    categories = Column(JSON, default=list)
    metadata_json = Column(JSON, default=dict)
    item_config_snapshot = Column(JSON, default=dict)  # 巡检项配置快照
    report_id = Column(String(32), nullable=True, index=True)
    created_by = Column(String(128), nullable=True, index=True)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    duration_ms = Column(Integer, default=0)
    created_at = Column(DateTime, default=_utcnow, index=True)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)


class InspectionEvidence(Base):
    """巡检证据快照。敏感信息应在入库前完成脱敏。"""
    __tablename__ = "inspection_evidence"

    id = Column(String(32), primary_key=True, default=_uuid)
    run_id = Column(String(32), ForeignKey("inspection_runs.id"), nullable=False, index=True)
    source_type = Column(String(32), default="COMMAND", index=True)
    source_path = Column(String(1024), nullable=True)
    command = Column(Text, nullable=True)
    content_snapshot = Column(Text, nullable=True)
    content_hash = Column(String(128), nullable=True)
    masked = Column(Boolean, default=True)
    created_at = Column(DateTime, default=_utcnow, index=True)


class InspectionItemResult(Base):
    """巡检项执行结果。"""
    __tablename__ = "inspection_item_results"

    id = Column(String(32), primary_key=True, default=_uuid)
    run_id = Column(String(32), ForeignKey("inspection_runs.id"), nullable=False, index=True)
    scope_type = Column(String(24), nullable=False, index=True)
    server_id = Column(String(255), nullable=True, index=True)
    project_id = Column(String(255), nullable=True, index=True)
    category = Column(String(64), nullable=False, index=True)
    item_code = Column(String(128), nullable=False, index=True)
    item_name = Column(String(255), nullable=False)
    status = Column(String(24), default="PASS", index=True)  # PASS / WARNING / RISK / ERROR / SKIPPED
    risk_level = Column(String(24), default="NONE", index=True)  # HIGH / MEDIUM / LOW / NONE
    message = Column(Text, nullable=True)
    suggestion = Column(Text, nullable=True)
    evidence_id = Column(String(32), nullable=True, index=True)
    raw_output = Column(Text, nullable=True)
    parsed_facts = Column(JSON, nullable=True, default=dict)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_utcnow, index=True)


class InspectionIssue(Base):
    """巡检风险问题闭环。"""
    __tablename__ = "inspection_issues"

    id = Column(String(32), primary_key=True, default=_uuid)
    run_id = Column(String(32), ForeignKey("inspection_runs.id"), nullable=False, index=True)
    item_result_id = Column(String(32), ForeignKey("inspection_item_results.id"), nullable=True, index=True)
    scope_type = Column(String(24), nullable=False, index=True)
    server_id = Column(String(255), nullable=True, index=True)
    project_id = Column(String(255), nullable=True, index=True)
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    risk_level = Column(String(24), default="LOW", index=True)
    status = Column(String(24), default="OPEN", index=True)  # OPEN / PROCESSING / FIXED / VERIFIED / IGNORED
    owner_id = Column(String(128), nullable=True, index=True)
    deadline_at = Column(DateTime, nullable=True)
    fixed_at = Column(DateTime, nullable=True)
    verified_at = Column(DateTime, nullable=True)
    suggestion = Column(Text, nullable=True)
    evidence_id = Column(String(32), nullable=True, index=True)
    created_at = Column(DateTime, default=_utcnow, index=True)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)


class InspectionRule(Base):
    """巡检规则配置。当前版本以内置规则为主，表结构预留可视化规则管理。"""
    __tablename__ = "inspection_rules"

    id = Column(String(32), primary_key=True, default=_uuid)
    rule_code = Column(String(128), nullable=False, unique=True, index=True)
    rule_name = Column(String(255), nullable=False)
    category = Column(String(64), nullable=False, index=True)
    scope_type = Column(String(24), nullable=False, index=True)
    risk_level = Column(String(24), default="LOW", index=True)
    enabled = Column(Boolean, default=True, index=True)
    deleted = Column(Boolean, default=False, index=True)
    config_json = Column(JSON, default=dict)
    rule_content = Column(Text, nullable=True)
    description = Column(Text, nullable=True)
    suggestion = Column(Text, nullable=True)
    version = Column(String(64), default="inspection.v1")
    created_at = Column(DateTime, default=_utcnow, index=True)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)


class InspectionItemConfig(Base):
    """巡检项目配置 - 可选/可编辑/可调整的巡检项。"""
    __tablename__ = "inspection_item_configs"

    id = Column(String(32), primary_key=True, default=_uuid)
    item_code = Column(String(128), nullable=False, unique=True, index=True)
    item_name = Column(String(255), nullable=False)
    category = Column(String(64), nullable=False, index=True)
    scope_type = Column(String(24), nullable=False, index=True)  # SERVER / PROJECT
    description = Column(Text, nullable=True)
    enabled = Column(Boolean, default=True, index=True)
    sort_order = Column(Integer, default=0)
    config_json = Column(JSON, default=dict)
    is_builtin = Column(Boolean, default=False, index=True)
    created_at = Column(DateTime, default=_utcnow, index=True)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)


class InspectionItemRule(Base):
    """巡检项目与规则的多对多关联表。"""
    __tablename__ = "inspection_item_rules"

    id = Column(String(32), primary_key=True, default=_uuid)
    item_config_id = Column(String(32), ForeignKey("inspection_item_configs.id"), nullable=False, index=True)
    rule_code = Column(String(128), nullable=False, index=True)
    enabled = Column(Boolean, default=True)
    sort_order = Column(Integer, default=0)
    config_override = Column(JSON, default=dict)
    created_at = Column(DateTime, default=_utcnow, index=True)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)


class InspectionBaseline(Base):
    """项目文件/配置/白名单等巡检基线。"""
    __tablename__ = "inspection_baselines"

    id = Column(String(32), primary_key=True, default=_uuid)
    scope_type = Column(String(24), nullable=False, index=True)
    server_id = Column(String(255), nullable=True, index=True)
    project_id = Column(String(255), nullable=True, index=True)
    baseline_type = Column(String(64), nullable=False, index=True)
    version = Column(String(64), default="v1", index=True)
    content_json = Column(JSON, default=dict)
    content_hash = Column(String(128), nullable=True)
    active = Column(Boolean, default=True, index=True)
    created_by = Column(String(128), nullable=True, index=True)
    created_at = Column(DateTime, default=_utcnow, index=True)


# ──────────────────────────────────────────────────────────────────
# 日/周/月三级巡检配置（DB 单源；不再使用 yaml）
# ──────────────────────────────────────────────────────────────────

class InspectionTierSchedule(Base):
    """三级巡检调度主表（DAILY / WEEKLY / MONTHLY）。

    设计要点：
    - 目标统一为"全量服务器"，不在此处做 server/group 过滤；
      真正区分三级的是 `categories` 字段（巡检项组合）。
    - `target_filter` 字段保留以兼容旧数据；dispatcher 忽略。
    - `require_approval=True` 的 tier（默认 MONTHLY）首跑前需手动审批解锁。
    """
    __tablename__ = "inspection_tier_schedules"

    id = Column(String(32), primary_key=True, default=_uuid)
    name = Column(String(128), unique=True, nullable=False, index=True)
    tier = Column(String(16), nullable=False, index=True)  # DAILY / WEEKLY / MONTHLY
    cron_expression = Column(String(64), nullable=False)
    timezone = Column(String(64), default="Asia/Shanghai")
    enabled = Column(Boolean, default=True, index=True)
    timeout_minutes = Column(Integer, default=60)
    concurrency = Column(Integer, default=3)
    target_filter = Column(JSON, default=dict)        # 兼容字段，dispatcher 忽略
    categories = Column(JSON, default=list)           # 该级要跑的巡检项 code
    thresholds = Column(JSON, default=dict)           # {score_fail_below, high_issue_count, ...}
    notification = Column(JSON, default=dict)         # {on_success, on_failure, channels, recipients, ...}
    retention = Column(JSON, default=dict)            # {runs_keep_days, reports_keep_days, ...}
    report = Column(JSON, default=dict)               # {format, include_passed_items, ...}
    require_approval = Column(Boolean, default=False, index=True)
    next_run_at = Column(DateTime, nullable=True, index=True)
    last_run_at = Column(DateTime, nullable=True, index=True)
    last_run_id = Column(String(32), nullable=True, index=True)
    last_status = Column(String(24), nullable=True)
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=_utcnow, index=True)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)


class InspectionNotificationRoute(Base):
    """巡检结果通知路由：severity → channels/recipients/SLA。

    - severity: high / medium / low / pass
    - tier: 可选，用于覆盖默认（如 MONTHLY 全部走主管通道）
    """
    __tablename__ = "inspection_notification_routes"

    id = Column(String(32), primary_key=True, default=_uuid)
    severity = Column(String(16), nullable=False, index=True)
    tier = Column(String(16), nullable=True, index=True)
    channels = Column(JSON, default=list)             # ["feishu", "sms", "email", "phone", "jira", "audit-system"]
    recipients = Column(JSON, default=list)           # ["oncall@ops.local", "feishu:#oncall", ...]
    sla_minutes = Column(Integer, nullable=True)
    enabled = Column(Boolean, default=True, index=True)
    created_at = Column(DateTime, default=_utcnow, index=True)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)


class InspectionCascadePolicy(Base):
    """跨级联策略：高危/分数骤降 → 自动触发升级巡检或告警。

    - trigger_type: "high_count" / "score_drop" / "manual"
    - trigger_config: 触发条件（窗口、阈值）
    - action: "trigger_monthly" / "trigger_weekly" / "alert_director" / "open_jira"
    - action_config: 动作参数
    """
    __tablename__ = "inspection_cascade_policies"

    id = Column(String(32), primary_key=True, default=_uuid)
    name = Column(String(128), unique=True, nullable=False, index=True)
    trigger_type = Column(String(32), nullable=False, index=True)
    trigger_config = Column(JSON, default=dict)
    action = Column(String(64), nullable=False)
    action_config = Column(JSON, default=dict)
    enabled = Column(Boolean, default=True, index=True)
    last_triggered_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_utcnow, index=True)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)
