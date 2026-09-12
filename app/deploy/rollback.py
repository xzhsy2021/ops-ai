"""Rollback execution helpers for deploy_v2.

This module intentionally contains no FastAPI route code.  It centralizes the
actual rollback runner so browser-triggered rollbacks and Capability Server/MCP
queued rollbacks follow the same execution, lock release, logging and
notification path.
"""
from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional


@dataclass
class RollbackRuntime:
    session_factory: Callable[[], Any]
    task_repo_cls: Callable[[Any], Any]
    deployment_repo_cls: Callable[[Any], Any]
    deploy_request_cls: Callable[..., Any]
    get_server_by_name: Callable[[str], Any]
    connect_ssh: Callable[[Any], Any]
    log_to_db: Callable[[str, str, str, str, str], None]
    merge_release_variables: Callable[[Any, Any], Dict[str, Any]]
    rollback_plan_for: Callable[[str, str, str, List[str], Dict[str, Any], Any], Dict[str, Any]]
    service_topology: Callable[[str, str, str, str, Any], Dict[str, Any]]
    # 可返回 bool 或 awaitable[bool]：async 实现（app/api/deploy/_shared）会带上重试，
    # 同步实现仍被兼容（见 run() 里的 inspect.isawaitable 桥接）。
    run_health_checks: Callable[..., Any]
    send_release_notification: Callable[[str, Any, Optional[Dict[str, Any]], Any], None]
    release_deployment_locks: Callable[[List[str], Any], None]

    async def run(
        self,
        task_id: str,
        deployment_id: str,
        *,
        lock_keys: Optional[List[str]] = None,
        notification_context: Optional[Dict[str, Any]] = None,
        worker_name: str = "rollback-worker",
    ) -> bool:
        """Execute a rollback task and update DB state.

        Returns True when all target servers rolled back successfully.  Errors are
        logged and persisted before being re-raised so the caller/worker can make
        a scheduling decision without losing the audit trail.
        """
        db = self.session_factory()
        lock_keys = [x for x in (lock_keys or []) if x]
        try:
            task_repo = self.task_repo_cls(db)
            deploy_repo = self.deployment_repo_cls(db)
            task_repo.update_status(task_id, "running", worker=worker_name)

            deployment = deploy_repo.get_by_id(deployment_id)
            if not deployment:
                self.log_to_db(task_id, "error", "Deployment not found", "rollback", deployment_id)
                task_repo.update_status(task_id, "failed", result="Deployment not found")
                return False

            servers = [x.strip() for x in (getattr(deployment, "servers", "") or "").split(",") if x.strip()]
            req = self.deploy_request_cls(
                system=getattr(deployment, "system", "") or "",
                service=getattr(deployment, "service", "") or "",
                environment=getattr(deployment, "environment", "") or "",
                version=getattr(deployment, "version", "") or "",
                servers=servers,
                file_name=getattr(deployment, "version", "") or "",
                variables={},
            )
            variables = self.merge_release_variables(req, db)
            plan = self.rollback_plan_for(
                getattr(deployment, "system", "") or "",
                getattr(deployment, "service", "") or "",
                getattr(deployment, "environment", "") or "",
                servers,
                variables,
                db,
            )
            topology = self.service_topology(
                getattr(deployment, "system", "") or "",
                getattr(deployment, "service", "") or "",
                getattr(deployment, "environment", "") or "",
                variables.get("server_group", ""),
                db,
            )
            command = plan.get("command") or ""
            if not plan.get("safe") or not command:
                self.log_to_db(
                    task_id,
                    "error",
                    f"当前发布缺少可自动执行的回滚方案: {plan.get('description')}",
                    "rollback",
                    deployment_id,
                )
                task_repo.update_status(task_id, "failed", result="No safe rollback plan")
                deploy_repo.update_status(deployment_id, "failed", "Rollback plan is not executable")
                return False

            self.log_to_db(task_id, "info", f"回滚方案: {plan.get('mode')} - {plan.get('description')}", "rollback", deployment_id)
            success_all = True
            health_failed_servers: List[str] = []
            health_checked_servers: List[str] = []
            for server_name in servers:
                srv = self.get_server_by_name(server_name)
                if not srv:
                    self.log_to_db(task_id, "error", f"Server not found: {server_name}", f"rollback:{server_name}", deployment_id)
                    success_all = False
                    continue
                ssh = None
                try:
                    loop = asyncio.get_running_loop()
                    ssh = await loop.run_in_executor(None, lambda: self.connect_ssh(srv))
                    if not ssh:
                        self.log_to_db(task_id, "error", f"SSH connect failed: {server_name}", f"rollback:{server_name}", deployment_id)
                        success_all = False
                        continue
                    self.log_to_db(task_id, "info", f"> {command}", f"rollback:{server_name}", deployment_id)
                    exit_code, out, err = await loop.run_in_executor(None, lambda: ssh.exec(command, timeout=600))
                    if out:
                        self.log_to_db(task_id, "info", "\n".join((out or "").splitlines()[-20:]), f"rollback:{server_name}", deployment_id)
                    if exit_code == 0:
                        self.log_to_db(task_id, "info", f"Rollback success on {server_name}", f"rollback:{server_name}", deployment_id)
                        # 第 10 轮：回滚后健康检查结果此前被直接丢弃——命令执行成功但服务
                        # 没起来（或探测未通过）时，回滚仍被记为 success 并发出 rollback.success
                        # 通知；而正向发布的 HealthCheckStep 在健康检查失败时是直接让发布失败。
                        # 现在把探测结果纳入判定：健康检查未通过的服务器会让整次回滚不算成功。
                        health = self.run_health_checks(task_id, deployment_id, server_name, ssh, topology)
                        if inspect.isawaitable(health):
                            health = await health
                        health_checked_servers.append(server_name)
                        if health is False:
                            success_all = False
                            health_failed_servers.append(server_name)
                            self.log_to_db(
                                task_id,
                                "error",
                                f"回滚命令已执行，但健康检查未通过: {server_name}（服务可能仍未恢复）",
                                f"rollback:{server_name}",
                                deployment_id,
                            )
                    else:
                        self.log_to_db(task_id, "error", f"Rollback failed({exit_code}): {err or out}", f"rollback:{server_name}", deployment_id)
                        success_all = False
                except Exception as exc:
                    self.log_to_db(task_id, "error", f"Rollback error on {server_name}: {exc}", f"rollback:{server_name}", deployment_id)
                    success_all = False
                finally:
                    if ssh:
                        try:
                            ssh.close()
                        except Exception:
                            pass

            final_status = "success" if success_all else "failed"
            if success_all:
                result_text = "Rollback completed"
            elif health_failed_servers:
                result_text = "回滚命令已执行，但健康检查未通过：" + ", ".join(health_failed_servers)
            else:
                result_text = "Rollback failed"
            task_repo.update_status(task_id, final_status, result=result_text)
            deploy_repo.update_status(deployment_id, final_status, result_text)
            updated = deploy_repo.get_by_id(deployment_id)
            payload = {
                "task_id": task_id,
                "result": result_text,
                "health_checked_servers": health_checked_servers,
                "health_failed_servers": health_failed_servers,
            }
            payload.update(notification_context or {})
            self.send_release_notification("rollback.success" if success_all else "rollback.failed", updated, payload, db)
            return success_all
        except Exception as exc:
            self.log_to_db(task_id, "error", f"Rollback worker error: {exc}", "rollback", deployment_id)
            try:
                self.task_repo_cls(db).update_status(task_id, "failed", result=str(exc))
                self.deployment_repo_cls(db).update_status(deployment_id, "failed", str(exc))
            except Exception:
                pass
            raise
        finally:
            if lock_keys:
                self.release_deployment_locks(lock_keys, db)
            db.close()
