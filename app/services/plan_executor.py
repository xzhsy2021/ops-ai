"""Sequential execution of approved execution plans.

审批通过后，PlanExecutor 按冻结的步骤清单顺序执行步骤。每个步骤通过
类型注册表（STEP_HANDLERS）分发到对应业务执行器。所有 handler 接收
(plan, step, db) 三元组，返回可序列化结果。

设计要点：
- 步骤状态独立记录：PENDING -> RUNNING -> SUCCEEDED / FAILED / SKIPPED。
- 依赖前置步骤失败时，依赖步骤标记为 SKIPPED。
- continue_on_error=False 时，首个失败即停止，计划 FAILED。
- 已 SUCCEEDED 的步骤在恢复执行时不会重复执行。
- 计划处于 RUNNING 且存在 RUNNING 步骤（崩溃残留）时，无法证明安全恢复，
  标记计划 FAILED 并要求创建新计划，而不是猜测。
- handler 复用现有业务层实现，不调用旧的 prepare_* 审批工具（避免审批递归）。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.db.models import ExecutionPlan, ExecutionPlanStep, OperationJob


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# 步骤类型注册表：action_type -> handler(plan, step, db) -> dict
StepHandler = Callable[[ExecutionPlan, ExecutionPlanStep, Session], dict[str, Any]]


def _service_control_handler(plan: ExecutionPlan, step: ExecutionPlanStep, db: Session) -> dict[str, Any]:
    """服务控制（重启/停止/启动/更新）。复用 approval_executor 的 SSH 执行逻辑。"""
    from app.services.approval_executor import ApprovalExecutor

    params = step.parameters or {}
    control_action = params.get("control_action", "restart")
    system_name = params.get("system_name") or plan.system_name
    service_name = params.get("service_name") or plan.service_name
    targets = params.get("targets") or plan.targets or []
    compose_service = params.get("compose_service", "")

    if not targets:
        raise ValueError("服务控制步骤需要指定目标服务器列表")
    if not system_name or not service_name:
        raise ValueError("服务控制步骤需要指定 system_name 和 service_name")

    executor = ApprovalExecutor(db)
    results = []
    for server_name in targets:
        try:
            result = executor._control_single_server(
                server_name, system_name, service_name, control_action,
                _approver_ctx(plan), compose_service=compose_service,
            )
            results.append(result)
        except Exception as e:
            results.append({"server": server_name, "ok": False, "error": str(e)})

    success_count = sum(1 for r in results if r.get("ok"))
    return {
        "action": "SERVICE_CONTROL",
        "control_action": control_action,
        "system": system_name,
        "service": service_name,
        "targets": targets,
        "results": results,
        "success_count": success_count,
        "fail_count": len(results) - success_count,
    }


def _approver_ctx(plan: ExecutionPlan):
    """构建一个最小 ctx 对象，用于复用服务器工具层。"""
    class _Ctx:
        username = plan.approved_by or "system"
        token_owner = plan.approved_by or "system"
    return _Ctx()


def _health_check_handler(plan: ExecutionPlan, step: ExecutionPlanStep, db: Session) -> dict[str, Any]:
    """健康检查步骤。复用 run_health_check。"""
    from app.services.tool_adapters.server_tools import run_health_check

    params = step.parameters or {}
    system_name = params.get("system_name") or plan.system_name
    service_name = params.get("service_name") or plan.service_name
    targets = params.get("targets") or plan.targets or []
    ctx = _approver_ctx(plan)

    results = []
    for server_name in targets:
        try:
            hc = run_health_check(
                {"server": server_name, "system": system_name, "service": service_name},
                ctx, db,
            )
            results.append({
                "server": server_name,
                "ok": bool(hc.get("healthy")),
                "exit_code": hc.get("exit_code"),
            })
        except Exception as e:
            results.append({"server": server_name, "ok": False, "error": str(e)})

    success_count = sum(1 for r in results if r.get("ok"))
    if success_count != len(results):
        raise RuntimeError(f"健康检查未全部通过: {success_count}/{len(results)} 通过")
    return {"action": "HEALTH_CHECK", "results": results, "success_count": success_count}


def _release_handler(plan: ExecutionPlan, step: ExecutionPlanStep, db: Session) -> dict[str, Any]:
    """发布步骤。复用共享执行函数 execute_release，不触发旧审批工具。"""
    from app.services.approval_executor import execute_release

    params = step.parameters or {}
    payload = {
        "system_name": params.get("system_name") or plan.system_name,
        "service_name": params.get("service_name") or plan.service_name,
        "environment": params.get("environment") or plan.environment,
        "targets": params.get("targets") or plan.targets or [],
        "action_parameters": params.get("action_parameters", {}),
    }
    package_name = params.get("package_name") or plan.package_name or ""
    return execute_release(db, payload, operator=plan.approved_by or "system", package_name=package_name)


def _rollback_handler(plan: ExecutionPlan, step: ExecutionPlanStep, db: Session) -> dict[str, Any]:
    """回滚步骤。复用共享执行函数 execute_rollback。"""
    from app.services.approval_executor import execute_rollback

    params = step.parameters or {}
    payload = {
        "targets": params.get("targets") or plan.targets or [],
        "action_parameters": params.get("action_parameters", {}),
    }
    return execute_rollback(db, payload, operator=plan.approved_by or "system")


def _dml_handler(plan: ExecutionPlan, step: ExecutionPlanStep, db: Session) -> dict[str, Any]:
    """DML 步骤。复用共享执行函数 execute_dml。"""
    from app.services.approval_executor import execute_dml

    params = step.parameters or {}
    payload = {
        "targets": params.get("targets") or plan.targets or [],
        "action_parameters": params.get("action_parameters", {}),
    }
    return execute_dml(db, payload, operator=plan.approved_by or "system")


def _package_cleanup_handler(plan: ExecutionPlan, step: ExecutionPlanStep, db: Session) -> dict[str, Any]:
    """包清理步骤。复用共享执行函数 execute_package_cleanup。"""
    from app.services.approval_executor import execute_package_cleanup

    params = step.parameters or {}
    payload = {
        "action_parameters": params.get("action_parameters", {}),
    }
    return execute_package_cleanup(db, payload, operator=plan.approved_by or "system")


STEP_HANDLERS: dict[str, StepHandler] = {
    "SERVICE_CONTROL": _service_control_handler,
    "HEALTH_CHECK": _health_check_handler,
    "RELEASE": _release_handler,
    "ROLLBACK": _rollback_handler,
    "DML": _dml_handler,
    "PACKAGE_CLEANUP": _package_cleanup_handler,
}


class PlanExecutor:
    """审批后执行器：按顺序执行执行计划的全部步骤。"""

    def __init__(self, db: Session, handlers: dict[str, StepHandler] | None = None):
        self.db = db
        self._handlers = dict(STEP_HANDLERS if handlers is None else handlers)

    def execute(self, plan_id: str) -> ExecutionPlan | None:
        """执行已审批的计划。返回更新后的 ExecutionPlan。

        - PENDING_APPROVAL / REJECTED / EXPIRED：直接返回，不执行。
        - SUCCEEDED / FAILED / PARTIAL_FAILED：直接返回（终态）。
        - APPROVED：置 RUNNING 并顺序执行步骤。
        - RUNNING：恢复执行；若存在 RUNNING 残留步骤则标记 FAILED。
        """
        plan = self.db.query(ExecutionPlan).filter(
            ExecutionPlan.id == plan_id
        ).first()
        if not plan:
            return None
        if plan.status == "PENDING_APPROVAL":
            return plan
        if plan.status in ("SUCCEEDED", "FAILED", "PARTIAL_FAILED", "REJECTED", "EXPIRED"):
            return plan

        if plan.status == "RUNNING":
            running_steps = [s for s in plan.steps if s.status == "RUNNING"]
            if running_steps:
                plan.status = "FAILED"
                plan.failure_reason = (
                    f"计划处于 RUNNING 且步骤 {running_steps[0].step_key} 未完成，"
                    "无法安全恢复，请创建新计划重新审批"
                )
                self.db.commit()
                self.db.refresh(plan)
                return plan
            # 无残留 RUNNING 步骤 → 继续从 PENDING 步骤恢复

        if plan.status == "APPROVED":
            plan.status = "RUNNING"
        plan.started_running_at = None  # 无此字段，忽略

        # 创建计划级 OperationJob 审计记录
        job = OperationJob(
            job_type="execution_plan",
            source="execution_plan",
            source_tool="ops.approval.execute_plan",
            title=f"执行计划: {plan.id}",
            status="running",
            risk_level=plan.risk_level or "high",
            operator=plan.approved_by or "system",
            target=plan.system_name or None,
            request_json=plan.manifest or {},
        )
        self.db.add(job)
        self.db.flush()
        plan.execution_job_id = job.id
        self.db.commit()
        self.db.refresh(plan)

        continue_on_error = bool((plan.policy or {}).get("continue_on_error", False))
        by_key = {s.step_key: s for s in plan.steps}

        try:
            self._run_steps(plan, by_key, continue_on_error)
            final_status = self._final_status(plan)
            plan.status = final_status
            job.status = "success" if final_status == "SUCCEEDED" else "failed"
            job.result_json = {
                "plan_status": final_status,
                "steps": [
                    {"step_key": s.step_key, "status": s.status}
                    for s in plan.steps
                ],
            }
            job.finished_at = _utcnow()
            if final_status != "SUCCEEDED":
                failed = next((s for s in plan.steps if s.status == "FAILED"), None)
                if failed:
                    plan.failure_reason = failed.error_message
                    job.error_message = failed.error_message
        except Exception as e:
            plan.status = "FAILED"
            plan.failure_reason = str(e)
            job.status = "failed"
            job.error_message = str(e)
            job.finished_at = _utcnow()

        self.db.commit()
        self.db.refresh(plan)
        return plan

    def _run_steps(
        self,
        plan: ExecutionPlan,
        by_key: dict[str, ExecutionPlanStep],
        continue_on_error: bool,
    ) -> None:
        """按声明顺序执行 PENDING 步骤。"""
        for step in plan.steps:
            if step.status in ("SUCCEEDED", "SKIPPED"):
                continue
            if step.status == "PENDING":
                pass  # 正常执行
            else:
                # 其他状态（如 FAILED）在恢复场景不应重复执行
                continue

            # 检查依赖
            dep_blocked = False
            for dep in step.dependencies or []:
                dep_step = by_key.get(dep)
                if dep_step is None:
                    step.status = "FAILED"
                    step.error_message = f"依赖步骤不存在: {dep}"
                    step.finished_at = _utcnow()
                    dep_blocked = True
                    break
                if dep_step.status in ("FAILED", "SKIPPED"):
                    dep_blocked = True
                    break
                if dep_step.status != "SUCCEEDED":
                    dep_blocked = True
                    break
            if dep_blocked:
                step.status = "SKIPPED"
                step.finished_at = _utcnow()
                continue

            # 执行步骤
            step.status = "RUNNING"
            step.attempt_count = (step.attempt_count or 0) + 1
            step.started_at = _utcnow()
            self.db.commit()
            self.db.refresh(step)

            try:
                handler = self._handlers.get(step.action_type)
                if handler is None:
                    raise ValueError(f"未知步骤类型: {step.action_type}")
                result = handler(plan, step, self.db)
                step.result = result
                step.status = "SUCCEEDED"
                step.error_message = None
                step.finished_at = _utcnow()
            except Exception as e:
                step.status = "FAILED"
                step.error_message = str(e)
                step.finished_at = _utcnow()
                self.db.commit()
                if not continue_on_error:
                    return  # 停止执行后续步骤

            self.db.commit()
            self.db.refresh(step)

    def _final_status(self, plan: ExecutionPlan) -> str:
        """根据步骤状态计算计划终态。"""
        statuses = [s.status for s in plan.steps]
        if all(s == "SUCCEEDED" for s in statuses):
            return "SUCCEEDED"
        if any(s == "FAILED" for s in statuses):
            # 有失败步骤：若还有独立步骤成功过，则 PARTIAL_FAILED，否则 FAILED
            succeeded = any(s == "SUCCEEDED" for s in statuses)
            return "PARTIAL_FAILED" if succeeded else "FAILED"
        if any(s == "SKIPPED" for s in statuses):
            return "FAILED" if not any(s == "SUCCEEDED" for s in statuses) else "PARTIAL_FAILED"
        # 无失败/跳过，但非全部成功（理论上不该发生）
        return "FAILED"
