import asyncio
import logging
from typing import List, Dict, Any, Callable
from .step import StepContext
from .factory import StepFactory

logger = logging.getLogger(__name__)


class PipelineEngine:
    def __init__(self, log_callback: Callable = None, step_callback: Callable = None, cancel_checker: Callable = None):
        self.log_callback = log_callback
        self.step_callback = step_callback
        self.cancel_checker = cancel_checker

    async def run(
        self,
        task_id: str,
        deployment_id: str,
        steps: List[Dict[str, Any]],
        ctx_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        ctx = StepContext(
            task_id=task_id,
            deployment_id=deployment_id,
            system=ctx_data.get("system", ""),
            service=ctx_data.get("service", ""),
            environment=ctx_data.get("environment", ""),
            server=ctx_data.get("server", {}),
            variables=ctx_data.get("variables", {}),
            version=ctx_data.get("version", ""),
            deploy_path=ctx_data.get("deploy_path", "/data/web/app"),
            ssh_client=ctx_data.get("ssh_client"),
            log_callback=self.log_callback,
        )

        if self.log_callback:
            self.log_callback(task_id, "info", "Pipeline 开始执行", "pipeline")

        for i, step_cfg in enumerate(steps):
            step_type = step_cfg.get("type", "command")
            step_name = step_cfg.get("name", f"step_{i}")
            config = step_cfg.get("config", {})

            if self.cancel_checker and self.cancel_checker():
                if self.log_callback:
                    self.log_callback(task_id, "warning", f"CANCELED before {step_name}", step_name)
                return {"success": False, "canceled": True, "failed_step": step_name, "error": "Deployment canceled"}

            if self.log_callback:
                self.log_callback(task_id, "info", f"START {step_name} ({step_type})", step_name)
            step_task_id = None
            if self.step_callback:
                try:
                    try:
                        step_task_id = self.step_callback("start", step_name, step_type, "", None, config)
                    except TypeError:
                        step_task_id = self.step_callback("start", step_name, step_type, "")
                except Exception:
                    logger.exception("step_callback start failed")

            try:
                step_impl = StepFactory.get(step_type)
                await step_impl.run(ctx, config)

                if self.log_callback:
                    self.log_callback(task_id, "info", f"SUCCESS {step_name}", step_name)
                if self.step_callback:
                    try:
                        self.step_callback("success", step_name, step_type, "", step_task_id)
                    except Exception:
                        logger.exception("step_callback success failed")

            except Exception as e:
                logger.exception(f"Step {step_name} failed")
                if self.log_callback:
                    self.log_callback(task_id, "error", f"FAIL {step_name}: {e}", step_name)
                if self.step_callback:
                    try:
                        self.step_callback("failed", step_name, step_type, str(e), step_task_id)
                    except Exception:
                        logger.exception("step_callback failed failed")

                await self._rollback(ctx)
                return {"success": False, "failed_step": step_name, "error": str(e)}

        if self.log_callback:
            self.log_callback(task_id, "info", "Pipeline 执行完成", "pipeline")

        return {"success": True}

    async def _rollback(self, ctx: StepContext):
        ctx.log("warning", "开始回滚...", "rollback")
        try:
            rollback_step = StepFactory.get("rollback")
            await rollback_step.run(ctx, {})
        except Exception as e:
            ctx.log("error", f"回滚失败: {e}", "rollback")
