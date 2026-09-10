"""审批后执行器：消费审批后实际执行四类操作。

审批工单被 consume() 标记为 EXECUTING 后，此模块负责：
1. 创建 OperationJob 记录
2. 根据 action_type 分发到对应执行器
3. 更新审批工单状态为 SUCCEEDED / FAILED
4. 记录执行结果和失败原因

执行逻辑（发布/回滚/DML/包清理）抽为模块级共享函数 execute_*，供
ApprovalExecutor 方法和 PlanExecutor 步骤处理器共同调用。PlanExecutor
复用同一套业务实现，避免在计划链路内部再次调用旧 prepare_* 审批工具。

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


class FileUploadExecutionError(RuntimeError):
    """Raised when one or more targets in a file upload batch fail."""

    def __init__(self, execution_result: dict[str, Any]):
        self.execution_result = execution_result
        super().__init__(execution_result.get("message") or "File upload batch failed")


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
            approval.execution_result = getattr(e, "execution_result", None) or {
                "error": error_msg,
                "traceback": error_traceback,
            }
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
        elif action_type == "SERVICE_CONTROL":
            return self._execute_service_control(approval, payload)
        elif action_type == "FILE_UPLOAD":
            return self._execute_file_upload(approval, payload)
        elif action_type == "EXEC_REMOTE":
            return self._execute_exec_remote(approval, payload)
        else:
            raise ValueError(f"未知的操作类型: {action_type}")

    # ── 发布执行 ──

    def _execute_release(self, approval: AiActionApproval, payload: dict) -> dict[str, Any]:
        """执行发布操作。

        复用现有 DeploymentRepository 创建部署记录，然后触发部署 worker。
        部署 worker 异步执行实际发布，此处返回部署 ID 供监控。
        """
        return execute_release(
            self.db,
            payload,
            operator=approval.approved_by or "system",
            package_name=approval.package_name or "",
        )

    # ── 回滚执行 ──

    def _execute_rollback(self, approval: AiActionApproval, payload: dict) -> dict[str, Any]:
        """执行回滚操作。

        复用现有部署回滚逻辑：通过 DeploymentRepository 查找原部署记录，
        创建一条新的回滚部署记录（status=pending），由部署 worker 执行。
        """
        return execute_rollback(
            self.db,
            payload,
            operator=approval.approved_by or "system",
        )

    # ── DML 执行 ──

    def _execute_dml(self, approval: AiActionApproval, payload: dict) -> dict[str, Any]:
        """执行 DML 操作。

        复用 DbQueryExportService.execute_sql，它内部包含预检、影响行数
        校验、审计日志记录。
        """
        return execute_dml(
            self.db,
            payload,
            operator=approval.approved_by or "system",
        )

    # ── 包清理执行 ──

    def _execute_package_cleanup(self, approval: AiActionApproval, payload: dict) -> dict[str, Any]:
        """执行包清理操作。

        复用 package_retention.cleanup_packages，按保留策略清理过期包。
        """
        return execute_package_cleanup(
            self.db,
            payload,
            operator=approval.approved_by or "system",
        )

    # ── 服务控制执行 ──

    def _execute_service_control(self, approval: AiActionApproval, payload: dict) -> dict[str, Any]:
        """执行服务控制操作（重启/停止/启动/更新）。

        通过 SSH 连接到目标服务器，使用服务配置中的控制命令执行操作。
        支持 Docker Compose / PM2 / process_keyword 三种服务类型。
        action_parameters 额外支持 env（dict[str,str] 命令前缀）与
        compose_args（list[str] 追加到 up/restart 命令），用于 trace 启动等场景。
        """
        action_parameters = payload.get("action_parameters", {})
        control_action = action_parameters.get("control_action", "restart")
        compose_service = action_parameters.get("compose_service", "")
        env = action_parameters.get("env")
        compose_args = action_parameters.get("compose_args")
        system_name = payload.get("system_name", "")
        service_name = payload.get("service_name", "")
        targets = payload.get("targets", [])

        if not targets:
            raise ValueError("服务控制操作需要指定目标服务器列表")
        if not system_name or not service_name:
            raise ValueError("服务控制操作需要指定 system_name 和 service_name")

        # 构建一个简单的 ctx 对象用于传递上下文
        class _Ctx:
            username = approval.approved_by or "system"
            token_owner = approval.approved_by or "system"

        ctx = _Ctx()

        results = []
        for server_name in targets:
            try:
                result = self._control_single_server(
                    server_name, system_name, service_name, control_action, ctx,
                    compose_service=compose_service,
                    env=env,
                    compose_args=compose_args,
                )
                results.append(result)
            except Exception as e:
                results.append({
                    "server": server_name,
                    "ok": False,
                    "error": str(e),
                })

        success_count = sum(1 for r in results if r.get("ok"))
        fail_count = len(results) - success_count

        return {
            "action": "SERVICE_CONTROL",
            "control_action": control_action,
            "system": system_name,
            "service": service_name,
            "targets": targets,
            "results": results,
            "success_count": success_count,
            "fail_count": fail_count,
            "message": f"服务控制完成: {success_count}/{len(targets)} 成功, {fail_count} 失败",
        }

    # ── Ad-hoc 远程命令执行（EXEC_REMOTE）──

    def _execute_exec_remote(self, approval: AiActionApproval, payload: dict) -> dict[str, Any]:
        """执行审批通过的 ad-hoc 远程命令（逐目标串行，不自动重试）。

        设计：docs/exec-remote-approval-design.md §4.4

        与 SERVICE_CONTROL 的关键差别——命令来自冻结的审批载荷而非服务配置，
        因此在真正 SSH 之前做第 3 层护栏复核：

        1. 重跑 prepare 时的同一套命令护栏（denylist + 白名单模板）；
        2. 比对命令 SHA256，防止审批载荷被篡改（防 TOCTOU）；
        3. 串行执行、不自动重试，部分失败保留逐目标明细；
        4. 输出经 ``mask_sensitive`` 脱敏并截断后才落库/回执。
        """
        import hashlib

        from app.services.sensitive_data import mask_sensitive
        from app.services.exec_command_policy import (
            assert_exec_command,
            ExecCommandRejected,
            policy_from_settings,
        )
        from app.services.tool_policy import get_capability_settings
        from app.services.tool_adapters.server_tools import _connect

        action_parameters = dict(payload.get("action_parameters") or {})
        command = str(action_parameters.get("command") or "")
        expected_sha256 = str(action_parameters.get("command_sha256") or "")
        targets = [str(item or "").strip() for item in (payload.get("targets") or []) if str(item or "").strip()]
        environment = str(payload.get("environment") or "")

        if not targets:
            raise ValueError("命令执行操作需要至少一个目标服务器")
        if not command:
            raise ValueError("命令执行操作缺少冻结的命令文本")
        if not expected_sha256:
            raise ValueError("命令执行操作缺少 command_sha256，拒绝执行（无法校验完整性）")

        actual_sha256 = hashlib.sha256(command.encode("utf-8")).hexdigest()
        if actual_sha256 != expected_sha256:
            raise ValueError(
                "命令完整性校验失败（command_sha256 不匹配），审批载荷可能被篡改，已拒绝执行"
            )

        settings = get_capability_settings(self.db)
        guard = policy_from_settings(settings)
        try:
            assert_exec_command(
                command,
                mode=guard["mode"],
                templates=guard["templates"],
                deny_patterns=guard["deny_patterns"],
                destructive_patterns=guard["destructive_patterns"],
                environment=environment,
                max_length=guard["max_length"],
                allow_multi_line=guard["allow_multi_line"],
                allow_destructive=bool(action_parameters.get("allow_destructive", False)),
                target_count=len(targets),
                max_targets=int(guard["max_targets"]),
            )
        except ExecCommandRejected as exc:
            raise ValueError(f"执行前护栏复核未通过：{exc.reason}") from exc

        max_timeout = int(settings.get("exec_remote_max_timeout_seconds") or 300)
        try:
            timeout = int(action_parameters.get("timeout") or 120)
        except (TypeError, ValueError):
            timeout = 120
        timeout = max(5, min(timeout, max_timeout))

        results = []
        for server_name in targets:
            entry: dict[str, Any] = {"server": server_name, "command": command, "timeout": timeout}
            try:
                ssh, _srv = _connect(server_name)
                try:
                    code, out, err = ssh.exec(command, timeout=timeout)
                finally:
                    ssh.close()
                entry.update({
                    "ok": code == 0,
                    "exit_code": code,
                    "stdout": mask_sensitive(out or "", limit=8192),
                    "stderr": mask_sensitive(err or "", limit=8192),
                })
            except Exception as exc:  # 单机失败不影响其余目标
                entry.update({"ok": False, "error": str(exc)})
            results.append(entry)

        success_count = sum(1 for item in results if item.get("ok"))
        fail_count = len(results) - success_count
        return {
            "action": "EXEC_REMOTE",
            "command": command,
            "command_sha256": actual_sha256,
            "mode": guard["mode"],
            "template_id": action_parameters.get("template_id", ""),
            "system": payload.get("system_name", ""),
            "environment": environment,
            "targets": targets,
            "timeout": timeout,
            "results": results,
            "success_count": success_count,
            "fail_count": fail_count,
            "message": (
                f"远程命令执行完成: {success_count}/{len(targets)} 成功, {fail_count} 失败"
                "（不自动重试：如需重跑请重新提交审批）"
            ),
        }

    def _execute_file_upload(self, approval: AiActionApproval, payload: dict) -> dict[str, Any]:
        """Execute the frozen package upload request with OPS internal permissions."""
        from app.services.tool_adapters import file_transfer_tools

        action_parameters = dict(payload.get("action_parameters") or {})
        targets = [str(item or "").strip() for item in (payload.get("targets") or [])]
        if not targets:
            raise ValueError("文件上传操作需要至少一个目标服务器")
        if not action_parameters.get("remote_path"):
            raise ValueError("文件上传操作需要远端文件路径")

        class _Ctx:
            username = approval.approved_by or "system"
            token_owner = approval.approved_by or "system"

        ctx = _Ctx()
        results = []
        for server_name in targets:
            upload_args = {
                **action_parameters,
                "server": server_name,
                "confirm_text": "CONFIRM ops.upload_file",
            }
            try:
                results.append(file_transfer_tools.upload_file(upload_args, ctx, self.db))
            except Exception as exc:
                results.append({"server": server_name, "ok": False, "error": str(exc)})

        success_count = sum(1 for item in results if item.get("ok"))
        execution_result = {
            "action": "FILE_UPLOAD",
            "system": payload.get("system_name", ""),
            "service": payload.get("service_name", ""),
            "targets": targets,
            "remote_path": action_parameters["remote_path"],
            "package_sha256": action_parameters.get("expected_sha256", ""),
            "package_size_bytes": action_parameters.get("expected_size_bytes"),
            "results": results,
            "success_count": success_count,
            "fail_count": len(results) - success_count,
            "message": f"文件上传完成: {success_count}/{len(targets)} 成功, {len(results) - success_count} 失败",
        }
        if execution_result["fail_count"]:
            raise FileUploadExecutionError(execution_result)
        return execution_result

    def _control_single_server(self, server_name: str, system: str, service: str, action: str, ctx, *, compose_service: str = "", env: dict | None = None, compose_args: list[str] | None = None) -> dict:
        """在单台服务器上执行服务控制。"""
        from app.services.tool_adapters.server_tools import (
            _resolve_service_control_command,
            _check_remote_dir_exists,
            _connect,
            get_service_config,
            run_health_check,
        )

        cfg = get_service_config({"system": system, "service": service}, ctx, self.db)
        if not cfg.get("found"):
            return {"server": server_name, "ok": False, "error": f"服务配置未找到: {system}/{service}"}

        command = _resolve_service_control_command(cfg, action, compose_service=compose_service, env=env, compose_args=compose_args)
        tv = cfg.get("template_variables") or {}
        base_dir = tv.get("compose_dir") or tv.get("deploy_path") or tv.get("service_dir") or ""

        ssh, srv = _connect(server_name)
        try:
            # 执行前健康检查
            pre_health = {}
            try:
                hc = run_health_check({"server": server_name, "system": system, "service": service}, ctx, self.db)
                pre_health = {"healthy": hc.get("healthy"), "exit_code": hc.get("exit_code")}
            except Exception:
                pre_health = {"note": "pre-check skipped"}

            # 部署前路径校验：compose_dir 不存在时跳过该服务器，避免 cd 脏错误混入批次结果
            ok, err_msg = _check_remote_dir_exists(ssh, base_dir)
            if not ok:
                return {"server": server_name, "ok": False, "error": err_msg}

            # 执行控制命令（update 操作需要更长超时，因为要拉取镜像）
            import shlex
            full_cmd = f"cd {shlex.quote(base_dir)} && {command}" if base_dir else command
            timeout = 300 if action == "update" else 120
            code, out, err = ssh.exec(full_cmd, timeout=timeout)

            # 执行后健康检查
            post_health = {}
            if action in ("restart", "start", "update"):
                import time
                time.sleep(5 if action == "update" else 3)
                try:
                    hc = run_health_check({"server": server_name, "system": system, "service": service}, ctx, self.db)
                    post_health = {"healthy": hc.get("healthy"), "exit_code": hc.get("exit_code")}
                except Exception:
                    post_health = {"note": "post-check skipped"}

            return {
                "server": server_name,
                "ok": code == 0,
                "action": action,
                "command": full_cmd,
                "exit_code": code,
                "stdout": out[:500],
                "stderr": err[:500],
                "pre_health": pre_health,
                "post_health": post_health,
            }
        finally:
            ssh.close()


# ── 共享执行函数 ──
#
# 这些函数以 (db, payload, operator, ...) 为签名，供旧 ApprovalExecutor 方法
# 和新的 PlanExecutor 步骤处理器共同调用。PlanExecutor 复用同一套业务实现，
# 避免在计划链路内部再次调用旧 prepare_* 审批工具（防止审批递归）。


def execute_release(db: Session, payload: dict, *, operator: str = "system", package_name: str = "") -> dict[str, Any]:
    """执行发布操作：创建部署记录、入队 DeployTask 并由后台 worker 执行。

    与 execute_deploy_plan 保持一致的入队链路（构建 request → 解析服务器名 →
    派生步骤 → 获取发布锁 → 创建 DeployTask → 触发 worker），避免计划式发布
    只建 deployment 记录却不入队导致任务卡在 pending。
    """
    import uuid
    import json as _json
    from app.db.repository import DeploymentRepository
    from app.db import DeployTaskRepository
    from app.api.deploy._shared import (
        ensure_deploy_worker_running,
        _default_release_steps,
        _db_pipeline_steps,
        _json_safe,
    )
    from app.deploy.locks import acquire_deployment_locks, release_deployment_locks
    from app.deploy.schemas import DeployRequest
    from app.config.servers import resolve_server
    from app.services.package_retention import ensure_releasable_artifact

    # 发版制品格式守卫：文件中心允许任意普通文件入库（如 Matrix 拉取），
    # 但发布必须限定部署包格式；不合规直接拒绝，避免把 .txt/.log 当制品发出去。
    ensure_releasable_artifact(db, package_name)

    system_name = payload.get("system_name", "")
    service_name = payload.get("service_name", "") or ""
    environment = payload.get("environment", "") or ""
    targets = [t for t in (payload.get("targets") or []) if t]
    action_parameters = payload.get("action_parameters", {}) if isinstance(payload.get("action_parameters"), dict) else {}

    # targets 可能是 host/IP，需解析成注册服务器名（部署 worker 仅按 name 匹配）。
    # 解析失败的保留原值，让 worker 暴露明确的“服务器未找到”错误而非静默丢弃。
    resolved_servers: list[str] = []
    for target in targets:
        srv = resolve_server(str(target), db=db)
        resolved_servers.append(str(srv.get("name")) if srv else str(target))

    req = DeployRequest(
        system=system_name,
        service=service_name,
        environment=environment,
        file_name=package_name or "",
        version=package_name or "",
        servers=resolved_servers,
        pipeline_id=action_parameters.get("pipeline_id") or "",
    )
    steps = _db_pipeline_steps(db, req.pipeline_id) or _default_release_steps(req, db)

    repo = DeploymentRepository(db)
    deployment = repo.create(
        system=system_name,
        service=service_name,
        environment=environment,
        strategy="DIRECT",
        servers=",".join(resolved_servers) if resolved_servers else ",".join(targets),
        created_by=operator or "system",
        version=package_name or "",
        status="pending",
    )
    db.commit()

    task_id = uuid.uuid4().hex[:12]
    try:
        keys = acquire_deployment_locks(req, deployment.id, task_id, operator or "system", db)
    except Exception:
        DeploymentRepository(db).update_status(deployment.id, "failed", "Failed to acquire deployment locks")
        raise

    task_payload = {
        "request": _json_safe(req.model_dump()),
        "steps": _json_safe(steps),
        "created_by": operator or "system",
        "reason": action_parameters.get("reason")
        or action_parameters.get("change_reason")
        or payload.get("reason")
        or "",
        "precheck": {},
    }
    try:
        DeployTaskRepository(db).create(
            deployment_id=deployment.id,
            task_id=task_id,
            payload_json=_json.dumps(task_payload, ensure_ascii=False),
            lock_key=",".join(keys),
        )
    except Exception:
        release_deployment_locks(keys, db)
        DeploymentRepository(db).update_status(deployment.id, "failed", "Failed to create deployment task")
        raise
    db.commit()

    # 入队成功后才触发后台 worker
    try:
        ensure_deploy_worker_running()
    except Exception:
        # worker 启动失败不阻塞审批完成，任务已在 DB 中排队
        pass

    return {
        "action": "RELEASE",
        "deployment_id": str(deployment.id),
        "task_id": task_id,
        "system": system_name,
        "service": service_name,
        "environment": environment,
        "servers": resolved_servers,
        "package": package_name,
        "message": "部署记录与发布任务已创建，部署 worker 将异步执行",
    }


def execute_rollback(db: Session, payload: dict, *, operator: str = "system") -> dict[str, Any]:
    """执行回滚操作：校验原部署后入队 rollback DeployTask 并由后台 worker 执行。

    与 executions.py 的规范回滚保持一致——以【原部署】而非新建副本作为任务目标，
    使用单把回滚锁，任务 payload 带 action=rollback 供 durable worker 识别；不再
    只建 deployment 记录却不入队（否则 worker 无任务可拾取、回滚卡死）。
    """
    import uuid
    import json as _json
    from app.db.repository import DeploymentRepository
    from app.db import DeployTaskRepository
    from app.api.deploy._shared import (
        ensure_deploy_worker_running,
        _rollback_plan_for,
        _rollback_precheck_for,
        _merge_release_variables,
    )
    from app.deploy.locks import release_deployment_locks
    from app.deploy.schemas import DeployRequest
    from app.core.deploy_lock import DeployLock

    action_parameters = payload.get("action_parameters", {})
    deployment_id = action_parameters.get("deployment_id", "")
    if not deployment_id:
        raise ValueError("回滚操作需要指定 deployment_id")

    repo = DeploymentRepository(db)
    original = repo.get_by_id(deployment_id) if hasattr(repo, "get_by_id") else None
    if not original:
        from app.db.models import Deployment
        original = db.query(Deployment).filter(Deployment.id == deployment_id).first()
    if not original:
        raise ValueError(f"原部署记录不存在: {deployment_id}")
    if original.status != "success":
        raise ValueError("只有成功状态的发布单可以自动回滚")

    servers_for_check = [x.strip() for x in (original.servers or "").split(",") if x.strip()]
    check_req = DeployRequest(
        system=original.system,
        service=original.service or "",
        environment=original.environment or "",
        version=original.version or "",
        servers=servers_for_check,
        file_name=original.version or "",
        variables={},
    )
    check_variables = _merge_release_variables(check_req, db)
    rollback_plan = _rollback_plan_for(
        original.system, original.service or "", original.environment or "", servers_for_check, check_variables, db
    )
    check = _rollback_precheck_for(original, rollback_plan, db)
    if check.get("blockers"):
        raise ValueError("; ".join(check.get("blockers") or []))

    lock_key = f"{original.system}:{original.service or 'default'}:rollback"
    if not DeployLock.acquire(lock_key):
        raise ValueError("同一服务已有回滚任务正在执行")

    task_id = uuid.uuid4().hex[:12]
    payload_json = _json.dumps({"action": "rollback", "lock_key": lock_key}, ensure_ascii=False)
    try:
        DeployTaskRepository(db).create(
            deployment_id=original.id,
            task_id=task_id,
            payload_json=payload_json,
            lock_key=lock_key,
        )
        db.commit()
    except Exception:
        release_deployment_locks([lock_key], db)
        raise

    # 入队成功后才触发后台 worker
    try:
        ensure_deploy_worker_running()
    except Exception:
        # worker 启动失败不阻塞审批完成，任务已在 DB 中排队
        pass

    return {
        "action": "ROLLBACK",
        "rollback_deployment_id": str(original.id),
        "original_deployment_id": deployment_id,
        "task_id": task_id,
        "system": original.system,
        "service": original.service,
        "environment": original.environment,
        "servers": servers_for_check,
        "message": "回滚任务已入队，部署 worker 将异步执行",
    }


def execute_dml(db: Session, payload: dict, *, operator: str = "system") -> dict[str, Any]:
    """执行 DML 操作，复用 DbQueryExportService.execute_sql（含预检与审计）。"""
    from app.services.db_query_export import DbQueryExportService

    action_parameters = payload.get("action_parameters", {})
    db_connection_id = action_parameters.get("database_connection_id", "")
    sql_text = action_parameters.get("sql_text", "")
    max_affected_rows = action_parameters.get("max_affected_rows", 100)
    database_name = action_parameters.get("database_name", "")

    if not sql_text:
        raise ValueError("DML 操作需要指定 sql_text")

    service = DbQueryExportService(db)
    result = service.execute_sql(
        sql=sql_text,
        operator=operator or "system",
        connection_id=db_connection_id,
        database_name=database_name,
        max_affected_rows=max_affected_rows,
        # 审批已通过，跳过 confirm_text 二次确认
        confirm_text="EXECUTE SQL",
        reason=f"审批执行: {operator or 'system'}",
    )

    return {
        "action": "DML",
        "database_connection_id": db_connection_id,
        "affected_rows": result.get("affected_rows", 0),
        "sql_text": sql_text[:200],
        "result": result,
    }


def execute_package_cleanup(db: Session, payload: dict, *, operator: str = "system") -> dict[str, Any]:
    """执行包清理操作，复用 package_retention.cleanup_packages。"""
    from app.services.package_retention import cleanup_packages

    action_parameters = payload.get("action_parameters", {})
    package_ids = action_parameters.get("package_ids", [])

    # cleanup_packages 按保留策略执行，不传 package_ids（它不接受此参数）
    result = cleanup_packages(
        db,
        policy=None,  # 使用默认保留策略
        dry_run=False,
        actor=operator or "system",
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
