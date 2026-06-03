import logging
from typing import Dict, Any, Optional, Tuple
from starlette.concurrency import run_in_threadpool

logger = logging.getLogger(__name__)


class AsyncSSHService:
    """异步包装的 SSH 服务，避免同步 paramiko 阻塞 FastAPI 事件循环"""

    async def exec_command(
        self, server_config: dict, command: str, timeout: int = 300
    ) -> Tuple[int, str, str]:
        return await run_in_threadpool(
            self._sync_exec, server_config, command, timeout
        )

    def _sync_exec(
        self, server_config: dict, command: str, timeout: int
    ) -> Tuple[int, str, str]:
        from ssh_client import create_ssh_client
        client = create_ssh_client(server_config)
        try:
            return client.exec(command, timeout=timeout)
        finally:
            try:
                client.close()
            except Exception:
                pass

    async def upload_file(
        self, server_config: dict, local_path: str, remote_path: str
    ):
        return await run_in_threadpool(
            self._sync_upload, server_config, local_path, remote_path
        )

    def _sync_upload(
        self, server_config: dict, local_path: str, remote_path: str
    ):
        from ssh_client import create_ssh_client
        client = create_ssh_client(server_config)
        try:
            client.upload(local_path, remote_path)
        finally:
            try:
                client.close()
            except Exception:
                pass

    async def test_connection(
        self, server_config: dict
    ) -> Tuple[bool, str, dict]:
        return await run_in_threadpool(
            self._sync_test, server_config
        )

    def _sync_test(self, server_config: dict) -> Tuple[bool, str, dict]:
        from config_manager import test_ssh_connection
        return test_ssh_connection(server_config)

    async def read_file(
        self, server_config: dict, remote_path: str
    ) -> str:
        return await run_in_threadpool(
            self._sync_read_file, server_config, remote_path
        )

    def _sync_read_file(self, server_config: dict, remote_path: str) -> str:
        from ssh_client import create_ssh_client
        client = create_ssh_client(server_config)
        try:
            return client.read_file(remote_path)
        finally:
            try:
                client.close()
            except Exception:
                pass

    async def write_file(
        self, server_config: dict, remote_path: str, content: str
    ):
        return await run_in_threadpool(
            self._sync_write_file, server_config, remote_path, content
        )

    def _sync_write_file(
        self, server_config: dict, remote_path: str, content: str
    ):
        from ssh_client import create_ssh_client
        client = create_ssh_client(server_config)
        try:
            client.write_file(remote_path, content)
        finally:
            try:
                client.close()
            except Exception:
                pass

    async def download_file(
        self, server_config: dict, remote_path: str, local_path: str
    ):
        return await run_in_threadpool(
            self._sync_download, server_config, remote_path, local_path
        )

    def _sync_download(
        self, server_config: dict, remote_path: str, local_path: str
    ):
        from ssh_client import create_ssh_client
        client = create_ssh_client(server_config)
        try:
            client.download(remote_path, local_path)
        finally:
            try:
                client.close()
            except Exception:
                pass


async_ssh_service = AsyncSSHService()
