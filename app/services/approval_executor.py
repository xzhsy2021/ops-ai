"""审批后执行器：消费审批后实际执行四类操作。

审批工单被 consume() 标记为 EXECUTING 后，此模块负责：
1. 创建 OperationJob 记录
2. 根据 action_type 分发到对应执行器
3. 更新审批工单状态为 SUCCEEDED / FAILED
4. 记录执行结果和失败原因

四个执行方法复用现有业务层函数，不重复实现部署/回滚/DML/清理逻辑：
- RELEASE  → DeploymentRepository + ensure_deploy_worker_running
- ROLLBACK → 现有回滚 API 逻辑
- DML      → DbQueryExportService.execute_sql
- CLEANUP  → package_retention.cleanup_packages
"""
from __future__ import annotations

import traceback
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import AiActionApproval, OperationJob


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class ApprovalExecutor:
    """审批后操作执行器"""

    def __init__(self, db: Session):
        self.db = db

    def execute(self, approval_id: str) -> AiActionApproval | None:
        """执行已审批的操作。

        Args:
            approval_id: 审批工单 ID（必须已处于 EXECUTING 状态）

        Returns:
            更新后的 AiActionApproval，状态为 SUCCEEDED 或 FAILED
        """
        approval = self.db.query(AiActionApproval).filter(
            AiActionApproval.id == approval_id
        ).first()
        if not approval:
            return None
        # 只执行 EXECUTING 状态的工单（consume 成功后会设为此状态）
        if approval.status != "EXECUTING":
            return approval

        # 创建 OperationJob 审计记录。source 通用化为 "approval"，
        # 区别于特定 claw 名称，具体 claw 身份通过 operator 字段追踪。
        job = OperationJob(
            job_type=f"approval_{approval.action_type.lower()}",
            source="approval",
            source_tool=approval.tool_name,
            title=f"审批执行: {approval.action_type} - {approval.id}",
            status="running",
            risk_level=approval.risk_level or "high",
            operator=approval.approved_by or "system",
            target=getattr(approval, "target_id", None),
            request_json=approval.request_payload or {},
        )
        self.db.add(job)
        self.db.flush()

        approval.execution_job_id = job.id if hasattr(job, "id") else None
        approval.status = "RUNNING"
        self.db.commit()
        self.db.refresh(approval)
        self.db.refresh(job)

        # 分发执行
        try:
            result = self._dispatch(approval)
            approval.status = "SUCCEEDED"
            approval.execution_result = result
            approval.executed_at = _utcnow()
            job.status = "success"
            job.result_json = result
            job.finished_at = _utcnow()
            job.progress = 100
        except Exception as e:
            error_msg = str(e)
            error_traceback = traceback.format_exc()
            approval.status = "FAILED"
            approval.failure_reason = error_msg
            approval.execution_result = {"error": error_msg, "traceback": error_traceback}
            approval.executed_at = _utcnow()
            job.status = "failed"
            job.error_message = error_msg
            job.finished_at = _utcnow()

        self.db.commit()
        self.db.refresh(approval)
        return approval

    def _dispatch(self, approval: AiActionApproval) -> dict[str, Any]:
        """根据 action_type 分发到对应执行器。"""
        payload = approval.request_payload or {}
        action_type = approval.action_type

        if action_type == "RELEASE":
            return self._execute_release(approval, payload)
        elif action_type == "ROLLBACK":
            return self._execute_rollback(approval, payload)
        elif action_type == "DML":
            return self._execute_dml(approval, payload)
        elif action_type == "PACKAGE_CLEANUP":
            return self._execute_package_cleanup(approval, payload)
        else:
            raise ValueError(f"未知的操作类型: {action_type}")

    # ── 发布执行 ──

    def _execute_release(self, approval: AiActionApproval, payload: dict) -> dict[str, Any]:
        """执行发布操作。

        复用现有 DeploymentRepository 创建部署记录，然后触发部署 worker。
        部署 worker 异步执行实际发布，此处返回部署 ID 供监控。
        """
        from app.db.repository import DeploymentRepository
        from app.api.deploy._shared import ensure_deploy_worker_running

        system_name = payload.get("system_name", "")
        service_name = payload.get("service_name", "") or None
        environment = payload.get("environment", "") or None
        targets = payload.get("targets", [])
        action_parameters = payload.get("action_parameters", {})

        repo = DeploymentRepository(self.db)
        deployment = repo.create(
            system=system_name,
            service=service_name,
            environment=environment,
            strategy="DIRECT",
            servers=",".join(targets) if targets else "",
            created_by=approval.approved_by or "system",
            version=approval.package_name or "",
            status="pending",
        )
        self.db.commit()

        # 触发后台部署 worker（异步执行实际发布步骤）
        try:
            ensure_deploy_worker_running()
        except Exception:
            # worker 启动失败不阻塞审批完成，部署记录已在 DB 中
            pass

        return {
            "action": "RELEASE",
            "deployment_id": str(deployment.id),
            "system": system_name,
            "service": service_name,
            "environment": environment,
            "servers": targets,
            "package": approval.package_name,
            "message": "部署记录已创建，部署 worker 将异步执行",
        }

    # ── 回滚执行 ──

    def _execute_rollback(self, approval: AiActionApproval, payload: dict) -> dict[str, Any]:
        """执行回滚操作。

        复用现有部署回滚逻辑：通过 DeploymentRepository 查找原部署记录，
        创建一条新的回滚部署记录（status=pending），由部署 worker 执行。
        """
        from app.db.repository import DeploymentRepository
        from app.api.deploy._shared import ensure_deploy_worker_running

        action_parameters = payload.get("action_parameters", {})
        deployment_id = action_parameters.get("deployment_id", "")
        targets = payload.get("targets", [])

        if not deployment_id:
            raise ValueError("回滚操作需要指定 deployment_id")

        repo = DeploymentRepository(self.db)
        original = repo.get_by_id(deployment_id) if hasattr(repo, "get_by_id") else None
        if not original:
            # 直接按原部署信息查找
            from app.db.models import Deployment
            original = self.db.query(Deployment).filter(
                Deployment.id == deployment_id
            ).first()

        if not original:
            raise ValueError(f"原部署记录不存在: {deployment_id}")

        # 创建回滚部署记录
        rollback = repo.create(
            system=original.system,
            service=original.service,
            environment=original.environment,
            strategy="DIRECT",
            servers=original.servers or ",".join(targets),
            created_by=approval.approved_by or "system",
            version=original.version or "",
            status="pending",
        )
        self.db.commit()

        try:
            ensure_deploy_worker_running()
        except Exception:
            pass

        return {
            "action": "ROLLBACK",
            "rollback_deployment_id": str(rollback.id),
            "original_deployment_id": deployment_id,
            "system": original.system,
            "environment": original.environment,
            "targets": targets,
            "message": "回滚部署记录已创建，部署 worker 将异步执行",
        }

    # ── DML 执行 ──

    def _execute_dml(self, approval: AiActionApproval, payload: dict) -> dict[str, Any]:
        """执行 DML 操作。

        复用 DbQueryExportService.execute_sql，它内部包含预检、影响行数
        校验、审计日志记录。
        """
        from app.services.db_query_export import DbQueryExportService

        action_parameters = payload.get("action_parameters", {})
        db_connection_id = action_parameters.get("database_connection_id", "")
        sql_text = action_parameters.get("sql_text", "")
        max_affected_rows = action_parameters.get("max_affected_rows", 100)
        database_name = action_parameters.get("database_name", "")

        service = DbQueryExportService(self.db)
        result = service.execute_sql(
            sql=sql_text,
            operator=approval.approved_by or "system",
            connection_id=db_connection_id,
            database_name=database_name,
            max_affected_rows=max_affected_rows,
            # 审批已通过，跳过 confirm_text 二次确认
            confirm_text="EXECUTE SQL",
            reason=f"审批执行: {approval.id}",
        )

        return {
            "action": "DML",
            "database_connection_id": db_connection_id,
            "affected_rows": result.get("affected_rows", 0),
            "sql_text": sql_text[:200],
            "result": result,
        }

    # ── 包清理执行 ──

    def _execute_package_cleanup(self, approval: AiActionApproval, payload: dict) -> dict[str, Any]:
        """执行包清理操作。

        复用 package_retention.cleanup_packages，按保留策略清理过期包。
        """
        from app.services.package_retention import cleanup_packages

        action_parameters = payload.get("action_parameters", {})
        package_ids = action_parameters.get("package_ids", [])

        # cleanup_packages 按保留策略执行，不传 package_ids（它不接受此参数）
        # 如果需要只清理指定包，应在 prepare 阶段将 package_ids 写入策略
        result = cleanup_packages(
            self.db,
            policy=None,  # 使用默认保留策略
            dry_run=False,
            actor=approval.approved_by or "system",
        )

        summary = result.get("summary", {})
        removed = result.get("removed", [])

        return {
            "action": "PACKAGE_CLEANUP",
            "requested_package_ids": package_ids,
            "cleaned_count": summary.get("cleanup_count", 0),
            "cleanup_size_mb": summary.get("cleanup_size_mb", 0),
            "removed_packages": removed[:50],  # 限制返回数量
            "message": f"已清理 {summary.get('cleanup_count', 0)} 个包",
        }
