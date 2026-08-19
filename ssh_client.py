import logging
import os
import re
import shlex
import threading
import time
from typing import Optional, Tuple

import paramiko

logger = logging.getLogger(__name__)

SECRET_PATTERNS = [
    r'password\s*[=:]\s*[^\s&|;]+',
    r'token\s*[=:]\s*[^\s&|;]+',
    r'secret\s*[=:]\s*[^\s&|;]+',
    r'Authorization:\s*Bearer\s+[^\s]+',
    r'-p\s*[^\s]+',
    r'--password\s*[=:]\s*[^\s&|;]+',
]


def mask_secret(text: str) -> str:
    for pattern in SECRET_PATTERNS:
        text = re.sub(pattern, '***', text, flags=re.IGNORECASE)
    return text


def apply_host_key_policy(client: paramiko.SSHClient):
    policy = os.getenv("SSH_HOST_KEY_POLICY", "autoadd")
    if policy == "warning":
        client.set_missing_host_key_policy(paramiko.WarningPolicy())
    elif policy == "reject":
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
    else:
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())


class SSHClient:
    def __init__(self, host: str, port: int = 22, user: str = "root",
                 key: str = None, password: str = None,
                 jumps: list = None, key_content: str = None,
                 connect_timeout: int = 15):
        self.host = host
        self.port = port
        self.user = user
        self.key_path = key
        self.key_content = key_content
        self.password = password
        # 统一只保留 jumps 链，单跳也包装成 list
        self.jump_hosts = jumps or []
        self.connect_timeout = connect_timeout

        self._client: Optional[paramiko.SSHClient] = None
        self._jump_clients: list = []
        self._jump_channels: list = []

    def connect(self, max_retries: int = 3, retry_delay: float = 2.0,
                per_attempt_timeout: Optional[int] = None):
        """
        连接 SSH 服务器，支持重试机制

        Args:
            max_retries: 最大重试次数
            retry_delay: 重试间隔（秒）
            per_attempt_timeout: 每次连接的单次超时秒数（None = 使用 self.connect_timeout）
        """
        if self._client is not None:
            return

        last_exception = None
        # K1 修复：单次超时可在调用点覆盖（用于"添加服务器"探测性场景，
        # 让"网络不可达"快速失败而不卡 45s+）
        original_timeout = self.connect_timeout
        if per_attempt_timeout is not None and per_attempt_timeout > 0:
            self.connect_timeout = min(per_attempt_timeout, self.connect_timeout)
        try:
            for attempt in range(1, max_retries + 1):
                try:
                    self._client = paramiko.SSHClient()
                    apply_host_key_policy(self._client)

                    pkey = self._load_key(self.key_path) if self.key_path else None

                    if self.jump_hosts:
                        self._connect_via_hops(pkey)
                    else:
                        self._connect_direct(pkey)

                    logger.info(f"SSH connected to {self.user}@{self.host}:{self.port}")
                    return

                except (paramiko.SSHException, ConnectionRefusedError, TimeoutError, OSError) as e:
                    last_exception = e
                    if attempt < max_retries:
                        logger.warning(f"SSH connection attempt {attempt}/{max_retries} failed: {e}. Retrying in {retry_delay}s...")
                        time.sleep(retry_delay)
                        if self._client:
                            try:
                                self._client.close()
                            except Exception:
                                logger.warning("Failed to close client during retry", exc_info=True)
                            self._client = None
                    else:
                        logger.error(f"All {max_retries} connection attempts failed")
        finally:
            # K1 修复：恢复原 connect_timeout（避免污染重试过程中的中间状态）
            self.connect_timeout = original_timeout

        raise ConnectionError(
            f"Failed to connect to {self.user}@{self.host}:{self.port} after {max_retries} attempts: {last_exception}"
        )

    @staticmethod
    def _load_key_from_file(key_path: str) -> paramiko.PKey:
        for key_cls in [paramiko.RSAKey, paramiko.Ed25519Key, paramiko.ECDSAKey]:
            try:
                return key_cls.from_private_key_file(key_path)
            except (paramiko.SSHException, ValueError):
                continue
        raise ValueError(f"Cannot load SSH key from {key_path}")

    @staticmethod
    def _load_key_from_content(key_content: str) -> paramiko.PKey:
        from io import StringIO
        for key_cls in [paramiko.RSAKey, paramiko.Ed25519Key, paramiko.ECDSAKey]:
            try:
                return key_cls.from_private_key(StringIO(key_content))
            except (paramiko.SSHException, ValueError):
                continue
        raise ValueError("Cannot load SSH key from content")

    def _load_key(self, key_path: str) -> paramiko.PKey:
        from config_manager import get_key_file_path
        resolved = get_key_file_path(key_path)
        return self._load_key_from_file(resolved)

    def _build_auth_kwargs(self, pkey: paramiko.PKey = None) -> dict:
        """构建目标机的认证参数，优先 key_content > pkey > password。"""
        if self.key_content:
            return {"pkey": self._load_key_from_content(self.key_content)}
        if pkey:
            return {"pkey": pkey}
        if self.password:
            return {"password": self.password}
        raise paramiko.SSHException(
            f"No authentication method available for {self.user}@{self.host}:{self.port}. "
            "Provide password, key file, or key content."
        )

    def _connect_direct(self, pkey: paramiko.PKey = None):
        kwargs = {
            "hostname": self.host,
            "port": self.port,
            "username": self.user,
            "timeout": self.connect_timeout,
            **self._build_auth_kwargs(pkey),
        }
        self._client.connect(**kwargs)


    def _build_hop_auth_kwargs(self, hop: dict) -> dict:
        """构建单个跳板机的认证参数，缺少凭据时直接抛错。"""
        hop_key_content = hop.get("key_content")
        hop_key_path = hop.get("key") or hop.get("key_file")
        hop_password = hop.get("password")

        if hop_key_content:
            return {"pkey": self._load_key_from_content(hop_key_content)}
        if hop_key_path:
            # 跳板机 key_path 也需要经过 get_key_file_path 解析（keys 目录/绝对路径）
            return {"pkey": self._load_key(hop_key_path)}
        if hop_password:
            return {"password": hop_password}

        hop_host = hop.get("host") or hop.get("name")
        hop_port = hop.get("port", 22)
        hop_user = hop.get("user", hop.get("username", "root"))
        raise ConnectionError(
            f"跳板机 {hop_user}@{hop_host}:{hop_port} 缺少认证凭据"
            f"（密码、密钥文件、密钥内容均为空）。"
            f"请在服务器配置的 jump_host 中添加 key 或 password 字段。"
        )

    def _connect_via_hops(self, pkey: paramiko.PKey = None):
        prev_sock = None
        for idx, hop in enumerate(self.jump_hosts):
            hop_host = hop.get("host") or hop.get("name")
            hop_port = hop.get("port", 22)
            hop_user = hop.get("user", hop.get("username", "root"))

            hop_kwargs = {
                "hostname": hop_host,
                "port": hop_port,
                "username": hop_user,
                **self._build_hop_auth_kwargs(hop),
            }
            if prev_sock:
                hop_kwargs["sock"] = prev_sock

            hop_client = paramiko.SSHClient()
            apply_host_key_policy(hop_client)
            hop_client.connect(**hop_kwargs)
            self._jump_clients.append(hop_client)

            if idx < len(self.jump_hosts) - 1:
                next_hop = self.jump_hosts[idx + 1]
                next_host = next_hop.get("host") or next_hop.get("name")
                next_port = next_hop.get("port", 22)
                transport = hop_client.get_transport()
                dest_addr = (next_host, next_port)
                local_addr = ("127.0.0.1", 0)
                channel = transport.open_channel("direct-tcpip", dest_addr, local_addr)
                self._jump_channels.append(channel)
                prev_sock = channel
                logger.info(f"SSH hop {idx + 1}/{len(self.jump_hosts)}: {hop_host} -> {next_host}")
            else:
                transport = hop_client.get_transport()
                dest_addr = (self.host, self.port)
                local_addr = ("127.0.0.1", 0)
                channel = transport.open_channel("direct-tcpip", dest_addr, local_addr)
                self._jump_channels.append(channel)
                prev_sock = channel
                logger.info(f"SSH hop {idx + 1}/{len(self.jump_hosts)}: {hop_host} -> {self.host}")

        kwargs = {
            "hostname": self.host,
            "port": self.port,
            "username": self.user,
            "sock": prev_sock,
            **self._build_auth_kwargs(pkey),
        }
        self._client.connect(**kwargs)
        logger.info(f"SSH connected via {len(self.jump_hosts)} hops to {self.host}")

    def exec(self, command: str, timeout: int = 300, on_output=None) -> Tuple[int, str, str]:
        if self._client is None:
            self.connect()
        start = time.monotonic()

        logger.info(f"Executing on {self.host}: {mask_secret(command)}")
        try:
            stdin, stdout, stderr = self._client.exec_command(command, timeout=timeout)
        except (EOFError, paramiko.SSHException, OSError) as exc:
            logger.warning(
                "SSH transport unusable on %s (%s); reconnecting and retrying once",
                self.host, type(exc).__name__,
            )
            try:
                self.close()
            except Exception:
                logger.debug("close after dead transport failed", exc_info=True)
            self.connect()
            stdin, stdout, stderr = self._client.exec_command(command, timeout=timeout)

        if on_output:
            # 与 else 分支一致：先等命令退出，再用 ChannelFile 读到 EOF。
            # 不要用 channel.recv_ready() 轮询（短命令存在瞬时 False 竞态，会丢输出）。
            try:
                stdout.channel.settimeout(timeout)
                exit_code = stdout.channel.recv_exit_status()
                out_data = stdout.read().decode("utf-8", errors="replace")
                err_data = stderr.read().decode("utf-8", errors="replace")
            except TimeoutError:
                raise TimeoutError(
                    f"exec timed out after {timeout}s: {mask_secret(command)}")
            if on_output is not None:
                for i in range(0, len(out_data), 4096):
                    try:
                        on_output("stdout", out_data[i:i + 4096])
                    except Exception:
                        logger.warning("on_output(stdout) failed in exec_command", exc_info=True)
                for i in range(0, len(err_data), 4096):
                    try:
                        on_output("stderr", err_data[i:i + 4096])
                    except Exception:
                        logger.warning("on_output(stderr) failed in exec_command", exc_info=True)
            out = out_data.strip()
            err = err_data.strip()
        else:
            stdout.channel.settimeout(timeout)
            try:
                exit_code = stdout.channel.recv_exit_status()
                out = stdout.read().decode("utf-8", errors="replace").strip()
                err = stderr.read().decode("utf-8", errors="replace").strip()
            except TimeoutError:
                raise TimeoutError(
                    f"exec timed out after {timeout}s: {mask_secret(command)}")

        if exit_code != 0:
            logger.warning(f"Command failed (exit={exit_code}): {mask_secret(command)}\nstderr: {err}")
        else:
            logger.info(f"Command succeeded: {mask_secret(command)}\nstdout: {out[:500]}")

        return exit_code, out, err

    def upload(self, local_path: str, remote_path: str):
        import posixpath
        if self._client is None:
            self.connect()

        logger.info(f"Uploading {local_path} -> {self.host}:{remote_path}")
        sftp = self._client.open_sftp()
        try:
            remote_dir = posixpath.dirname(remote_path)
            try:
                sftp.stat(remote_dir)
            except FileNotFoundError:
                self.exec(f"mkdir -p {shlex.quote(remote_dir)}")

            sftp.put(local_path, remote_path)
            logger.info(f"Upload complete: {local_path} -> {remote_path}")
        finally:
            sftp.close()

    def download(self, remote_path: str, local_path: str):
        if self._client is None:
            self.connect()

        logger.info(f"Downloading {self.host}:{remote_path} -> {local_path}")
        sftp = self._client.open_sftp()
        try:
            local_dir = os.path.dirname(local_path)
            os.makedirs(local_dir, exist_ok=True)
            sftp.get(remote_path, local_path)
            logger.info(f"Download complete: {remote_path} -> {local_path}")
        finally:
            sftp.close()

    def read_file(self, remote_path: str) -> str:
        exit_code, out, err = self.exec(f"cat {shlex.quote(remote_path)}")
        if exit_code != 0:
            raise IOError(f"Cannot read {remote_path}: {err}")
        return out

    def write_file(self, remote_path: str, content: str):
        import posixpath
        remote_dir = posixpath.dirname(remote_path)
        if self._client is None:
            self.connect()
        sftp = self._client.open_sftp()
        try:
            try:
                sftp.stat(remote_dir)
            except IOError:
                sftp.mkdir(remote_dir)
            with sftp.file(remote_path, "w") as f:
                f.write(content)
        finally:
            sftp.close()

    def close(self):
        if self._client:
            self._client.close()
            self._client = None
        for channel in self._jump_channels:
            try:
                channel.close()
            except Exception:
                logger.warning("Failed to close jump channel", exc_info=True)
        self._jump_channels.clear()
        for client in self._jump_clients:
            try:
                client.close()
            except Exception:
                logger.warning("Failed to close jump client", exc_info=True)
        self._jump_clients.clear()
        self.password = None
        self.key_content = None
        logger.info(f"SSH connection closed: {self.host}")

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False


def _has_auth(config: dict) -> bool:
    """检查 SSH 配置是否包含任一认证凭据。"""
    return bool(
        config.get("key") or config.get("key_file")
        or config.get("key_content") or config.get("password")
    )


def _server_to_jump_config(srv: dict) -> dict:
    """将 servers 表中的记录转换为跳板机可用配置，字段统一为 user。"""
    result = {
        "name": srv.get("name"),
        "host": srv.get("host"),
        "port": srv.get("port", 22),
        "user": srv.get("user") or srv.get("username", "root"),
    }
    for k in ("key", "key_file", "password", "key_content"):
        if srv.get(k):
            result[k] = srv[k]
    return result


def create_ssh_client(server_config: dict, max_retries: int = 3,
                      retry_delay: float = 2.0,
                      per_attempt_timeout: Optional[int] = None) -> SSHClient:
    """从连接池获取或创建 SSH 客户端

    K1 修复：支持短超时参数（用于"添加服务器"探测性场景，避免网络不可达时卡 45s+）。
    生产环境默认 15s × 3 次 = 45s+；探测场景可传 (max_retries=1, per_attempt_timeout=5) 让
    失败在 5s 内返回。
    """
    pool = SSHConnectionPool.get_instance()
    return pool.get(server_config, max_retries=max_retries,
                    retry_delay=retry_delay,
                    per_attempt_timeout=per_attempt_timeout)


class SSHConnectionPool:
    _instance = None
    _lock = threading.Lock()

    def __init__(self, max_idle_time=300, health_check_interval=60):
        self._pool = {}
        self._pool_lock = threading.Lock()
        self._max_idle_time = max_idle_time
        self._health_check_interval = health_check_interval
        self._last_access = {}

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def _make_key(self, server_config: dict) -> str:
        user = server_config.get("user") or server_config.get("username", "root")
        host = server_config.get("host")
        port = server_config.get("port", 22)
        jumps = server_config.get("jump_hosts") or (
            [server_config.get("jump") or server_config.get("jump_host")]
            if (server_config.get("jump") or server_config.get("jump_host")) else []
        )
        # 只有 dict 才算"已解析的跳板机"；字符串说明解析失败/未解析，应按直连算
        resolved_jumps = [j for j in jumps if isinstance(j, dict)]
        if not resolved_jumps:
            return f"{user}@{host}:{port}:direct"
        jump_chain = "|".join(
            f"{j.get('user') or j.get('username', 'root')}@{j.get('host', '')}:{j.get('port', 22)}"
            for j in resolved_jumps
        )
        return f"{user}@{host}:{port}:{jump_chain}"

    def _resolve_jump_config(self, server_config: dict) -> dict:
        """复制 server_config 并将 jump_host（字符串）解析为 dict，
        让后续的 pool_key 计算和连接创建都使用已解析后的凭据信息。
        Phase 3.g SSOT: 跳板机和服务器均从 DB 表查询，不再读 config_kv legacy 桶。
        """
        resolved = dict(server_config)
        jump = resolved.get("jump") or resolved.get("jump_host")
        if not isinstance(jump, (str, dict)):
            return resolved

        if isinstance(jump, str):
            # 字符串 jump_host：先查 jump_hosts DB 表，再查 servers DB 表
            try:
                from app.config.servers import get_jump_host_by_name, get_server_as_jump_by_name
                db_jh = get_jump_host_by_name(jump)
                if db_jh and _has_auth(db_jh):
                    resolved["jump"] = db_jh
                    resolved["jump_host"] = db_jh
                elif db_jh:
                    # 仅当 DB 条目带凭据时才采纳，否则回退到老行为（直连）
                    logger.warning(
                        f"jump_host '{jump}' 在 DB 中存在但缺少认证凭据，"
                        f"将按直连处理以兼容老配置"
                    )
                else:
                    # jump_hosts 表查不到，尝试在 servers 表中找同名 server 作为跳板
                    srv_jump = get_server_as_jump_by_name(jump)
                    if srv_jump and _has_auth(srv_jump):
                        resolved["jump"] = srv_jump
                        resolved["jump_host"] = srv_jump
            except Exception as e:
                logger.warning(f"Failed to resolve jump_host '{jump}' from DB: {e}")
        else:  # isinstance(jump, dict)
            if not _has_auth(jump):
                jump_name = jump.get("name")
                jump_host = jump.get("host")
                jump_port = jump.get("port", 22)
                matched = None
                try:
                    from app.config.servers import (
                        get_jump_host_by_name,
                        get_jump_host_by_host,
                        get_server_as_jump_by_name,
                        get_server_as_jump_by_host,
                    )
                    if jump_name:
                        matched = get_jump_host_by_name(jump_name) or get_server_as_jump_by_name(jump_name)
                    if not matched and jump_host:
                        matched = get_jump_host_by_host(jump_host, jump_port) or get_server_as_jump_by_host(jump_host, jump_port)
                except Exception as e:
                    logger.warning(f"Failed to resolve jump_host dict from DB: {e}")
                if matched:
                    merged = dict(jump)
                    for k in ("key", "key_file", "password", "key_content"):
                        if not merged.get(k) and matched.get(k):
                            merged[k] = matched[k]
                    resolved["jump"] = merged
                    resolved["jump_host"] = merged
        return resolved

    def get(self, server_config: dict, max_retries: int = 3,
            retry_delay: float = 2.0,
            per_attempt_timeout: Optional[int] = None) -> SSHClient:
        # 先解析 jump_host（字符串 → dict），让池键包含真实跳板机身份
        resolved_config = self._resolve_jump_config(server_config)
        pool_key = self._make_key(resolved_config)
        with self._pool_lock:
            if pool_key in self._pool:
                client = self._pool[pool_key]
                if client._client is not None:
                    try:
                        transport = client._client.get_transport()
                        if transport and transport.is_active():
                            self._last_access[pool_key] = time.time()
                            logger.info("ssh pool HIT key=%s", pool_key)
                            return client
                    except Exception:
                        logger.warning("Failed to check transport for pooled client", exc_info=True)
                del self._pool[pool_key]
                self._last_access.pop(pool_key, None)
                try:
                    client.close()
                except Exception:
                    logger.warning("Failed to close stale pooled client", exc_info=True)

        # 直接创建 SSHClient，避免与 create_ssh_client 形成循环递归
        # 使用已解析过 jump 的 resolved_config，确保凭据 / 跳板机链一致
        host = resolved_config.get("host")
        port = resolved_config.get("port", 22)
        user = resolved_config.get("user") or resolved_config.get("username", "root")
        ssh_key_path = resolved_config.get("key") or resolved_config.get("key_file")
        password = resolved_config.get("password")
        key_content = resolved_config.get("key_content")
        logger.debug(f"SSHConnectionPool.get() config: host={host}, port={port}, user={user}, "
                     f"has_key={bool(ssh_key_path)}, has_password={bool(password)}, "
                     f"has_key_content={bool(key_content)}, "
                     f"jump={resolved_config.get('jump') or resolved_config.get('jump_host')}, "
                     f"jump_hosts={resolved_config.get('jump_hosts')}")
        jump_hosts = resolved_config.get("jump_hosts")
        jump = resolved_config.get("jump") or resolved_config.get("jump_host")

        if isinstance(jump_hosts, list) and jump_hosts:
            jumps = jump_hosts
        elif jump and isinstance(jump, dict):
            jumps = [jump]
        else:
            jumps = None

        if not _has_auth(resolved_config):
            raise ConnectionError(
                f"无法连接到 {host}:{port}：缺少认证凭据（密码、密钥文件、密钥内容均为空）。"
                f"请编辑服务器配置添加认证信息后重试。"
            )

        client = SSHClient(host, port, user, key=ssh_key_path, password=password,
                          jumps=jumps, key_content=key_content)
        _connect_started = time.time()
        client.connect(
            max_retries=max_retries,
            retry_delay=retry_delay,
            per_attempt_timeout=per_attempt_timeout,
        )
        logger.info("ssh pool MISS key=%s connect_ms=%d", pool_key, int((time.time() - _connect_started) * 1000))
        with self._pool_lock:
            self._pool[pool_key] = client
            self._last_access[pool_key] = time.time()
        return client

    def release(self, server_config: dict):
        pass

    def cleanup(self):
        now = time.time()
        to_remove = []
        with self._pool_lock:
            for key, last_time in self._last_access.items():
                if now - last_time > self._max_idle_time:
                    to_remove.append(key)
            for key in to_remove:
                client = self._pool.pop(key, None)
                self._last_access.pop(key, None)
                if client:
                    try:
                        client.close()
                    except Exception:
                        logger.warning("Failed to close idle pooled client", exc_info=True)
        if to_remove:
            logger.info(f"SSH pool cleaned up {len(to_remove)} idle connections")

    def stats(self):
        now = time.time()
        with self._pool_lock:
            return {
                "active_connections": len(self._pool),
                "max_idle_time_seconds": self._max_idle_time,
                "health_check_interval_seconds": self._health_check_interval,
                "connections": [
                    {
                        "key": key,
                        "idle_seconds": round(now - self._last_access.get(key, now), 1),
                    }
                    for key in sorted(self._pool.keys())[:20]
                ],
            }

    def close_all(self):
        with self._pool_lock:
            for key, client in self._pool.items():
                try:
                    client.close()
                except Exception:
                    logger.warning("Failed to close client during pool close_all", exc_info=True)
            self._pool.clear()
            self._last_access.clear()
