import os
import asyncio
from typing import Dict, Any, Optional
from dataclasses import dataclass, field


@dataclass
class StepContext:
    task_id: str
    deployment_id: str
    system: str
    service: str = ""
    environment: str = ""
    server: Dict[str, Any] = field(default_factory=dict)
    variables: Dict[str, Any] = field(default_factory=dict)
    version: str = ""
    deploy_path: str = "/data/web/app"
    release_path: str = ""
    current_link: str = ""
    ssh_client: Any = None
    log_callback: Any = None
    rollback_stack: list = field(default_factory=list)

    def log(self, level: str, message: str, step_name: str = ""):
        if self.log_callback:
            self.log_callback(self.task_id, level, message, step_name)

    def add_rollback(self, action: str, data: dict):
        self.rollback_stack.append({"action": action, "data": data})

    async def ssh_exec(self, cmd: str, timeout: int = 120):
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, lambda: self.ssh_client.exec(cmd, timeout=timeout)
        )

    async def ssh_upload(self, local_path: str, remote_path: str):
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, lambda: self.ssh_client.upload(local_path, remote_path)
        )


class Step:
    step_type: str = ""

    async def run(self, ctx: StepContext, config: Dict[str, Any]):
        raise NotImplementedError
