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
                 jump: dict = None, jumps: list = None, key_content: str = None,
                 connect_timeout: int = 15):
        self.host = host
        self.port = port
        self.user = user
        self.key_path = key
        self.key_content = key_content
        self.password = password
        self.jump_config = jump
        self.jump_hosts = jumps or ([jump] if jump else [])
        self.connect_timeout = connect_timeout

        self._client: Optional[paramiko.SSHClient] = None
        self._jump_clients: list = []
        self._jump_channels: list = []

    def connect(self, max_retries: int = 3, retry_delay: float = 2.0):
        """
        连接 SSH 服务器，支持重试机制
        
        Args:
            max_retries: 最大重试次数
            retry_delay: 重试间隔（秒）
        """
        if self._client is not None:
            return

        last_exception = None
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

        raise ConnectionError(
            f"Failed to connect to {self.user}@{self.host}:{self.port} after {max_retries} attempts: {last_exception}"
        )

    def _load_key(self, key_path: str) -> paramiko.PKey:
        from config_manager import get_key_file_path
        resolved = get_key_file_path(key_path)
        return self._load_key_from_file(resolved)
    
    def _load_key_from_file(self, key_path: str) -> paramiko.PKey:
        for key_cls in [paramiko.RSAKey, paramiko.Ed25519Key, paramiko.ECDSAKey]:
            try:
                return key_cls.from_private_key_file(key_path)
            except (paramiko.SSHException, ValueError):
                continue
        raise ValueError(f"Cannot load SSH key from {key_path}")
    
    def _load_key_from_content(self, key_content: str) -> paramiko.PKey:
        from io import StringIO
        for key_cls in [paramiko.RSAKey, paramiko.Ed25519Key, paramiko.ECDSAKey]:
            try:
                return key_cls.from_private_key(StringIO(key_content))
            except (paramiko.SSHException, ValueError):
                continue
        raise ValueError("Cannot load SSH key from content")

    def _connect_direct(self, pkey: paramiko.PKey = None):
        kwargs = {
            "hostname": self.host,
            "port": self.port,
            "username": self.user,
            "timeout": self.connect_timeout,
        }
        key_content = getattr(self, 'key_content', None)
        if key_content:
            kwargs["pkey"] = self._load_key_from_content(key_content)
        elif pkey:
            kwargs["pkey"] = pkey
        elif self.password:
            kwargs["password"] = self.password
        else:
            raise paramiko.SSHException(
                f"No authentication method available for {self.user}@{self.host}:{self.port}. "
                "Provide password, key file, or key content."
            )
        self._client.connect(**kwargs)

    def _connect_via_hops(self, pkey: paramiko.PKey = None):
        prev_sock = None
        for idx, hop in enumerate(self.jump_hosts):
            hop_host = hop.get("host") or hop.get("name")
            hop_port = hop.get("port", 22)
            hop_user = hop.get("user", hop.get("username", "root"))
            hop_key_path = hop.get("key") or hop.get("key_file")
            hop_key_content = hop.get("key_content")
            hop_password = hop.get("password")

            hop_pkey = None
            if hop_key_content:
                hop_pkey = self._load_key_from_content(hop_key_content)
            elif hop_key_path:
                hop_pkey = self._load_key(hop_key_path)

            if not hop_pkey and not hop_password:
                raise ConnectionError(
                    f"跳板机 {hop_user}@{hop_host}:{hop_port} 缺少认证凭据"
                    f"（密码、密钥文件、密钥内容均为空）。"
                    f"请在服务器配置的 jump_host 中添加 key 或 password 字段。"
                )

            hop_client = paramiko.SSHClient()
            apply_host_key_policy(hop_client)
            hop_kwargs = {
                "hostname": hop_host,
                "port": hop_port,
                "username": hop_user,
            }
            if hop_pkey:
                hop_kwargs["pkey"] = hop_pkey
            elif hop_password:
                hop_kwargs["password"] = hop_password
            if prev_sock:
                hop_kwargs["sock"] = prev_sock

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
        }
        if pkey:
            kwargs["pkey"] = pkey
        elif self.key_content:
            kwargs["pkey"] = self._load_key_from_content(self.key_content)
        elif self.password:
            kwargs["password"] = self.password
        else:
            raise ConnectionError(
                f"目标服务器 {self.user}@{self.host}:{self.port} 缺少认证凭据"
                f"（密码、密钥文件、密钥内容均为空）。"
                f"请编辑服务器配置添加认证信息。"
            )
        self._client.connect(**kwargs)
        logger.info(f"SSH connected via {len(self.jump_hosts)} hops to {self.host}")

    def _connect_via_jump(self, pkey: paramiko.PKey = None):
        jump_host = self.jump_config.get("host") or self.jump_config.get("name")
        jump_port = self.jump_config.get("port", 22)
        jump_user = self.jump_config.get("user", "root")
        jump_key_path = self.jump_config.get("key")
        jump_key_content = self.jump_config.get("key_content")

        jump_pkey = None
        if jump_key_content:
            jump_pkey = self._load_key_from_content(jump_key_content)
        elif jump_key_path:
            jump_pkey = self._load_key(jump_key_path)

        jump_password = self.jump_config.get("password")
        if not jump_pkey and not jump_password:
            raise ConnectionError(
                f"跳板机 {jump_user}@{jump_host}:{jump_port} 缺少认证凭据"
                f"（密码、密钥文件、密钥内容均为空）。"
                f"请在服务器配置的 jump_host 中添加 key 或 password 字段。"
            )

        self._jump_client = paramiko.SSHClient()
        apply_host_key_policy(self._jump_client)
        jump_kwargs = {
            "hostname": jump_host,
            "port": jump_port,
            "username": jump_user,
        }
        if jump_pkey:
            jump_kwargs["pkey"] = jump_pkey
        elif jump_password:
            jump_kwargs["password"] = jump_password
        self._jump_client.connect(**jump_kwargs)

        transport = self._jump_client.get_transport()
        dest_addr = (self.host, self.port)
        local_addr = ("127.0.0.1", 0)
        self._jump_channel = transport.open_channel("direct-tcpip", dest_addr, local_addr)

        kwargs = {
            "hostname": self.host,
            "port": self.port,
            "username": self.user,
            "sock": self._jump_channel,
        }
        if pkey:
            kwargs["pkey"] = pkey
        elif self.key_content:
            kwargs["pkey"] = self._load_key_from_content(self.key_content)
        elif self.password:
            kwargs["password"] = self.password
        else:
            raise ConnectionError(
                f"目标服务器 {self.user}@{self.host}:{self.port} 缺少认证凭据"
                f"（密码、密钥文件、密钥内容均为空）。"
                f"请编辑服务器配置添加认证信息。"
            )
        self._client.connect(**kwargs)

        self._jump_transport = transport
        logger.info(f"SSH connected via jump {jump_host} -> {self.host}")

    def exec(self, command: str, timeout: int = 300, on_output=None) -> Tuple[int, str, str]:
        if self._client is None:
            self.connect()

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
            channel = stdout.channel
            channel.settimeout(0.5)
            out_lines = []
            err_lines = []
            while True:
                if channel.recv_ready():
                    data = channel.recv(4096).decode("utf-8", errors="replace")
                    out_lines.append(data)
                    try:
                        on_output("stdout", data)
                    except Exception:
                        logger.warning("on_output(stdout) failed in exec_command", exc_info=True)
                if channel.recv_stderr_ready():
                    data = channel.recv_stderr(4096).decode("utf-8", errors="replace")
                    err_lines.append(data)
                    try:
                        on_output("stderr", data)
                    except Exception:
                        logger.warning("on_output(stderr) failed in exec_command", exc_info=True)
                if channel.exit_status_ready():
                    while channel.recv_ready():
                        data = channel.recv(4096).decode("utf-8", errors="replace")
                        out_lines.append(data)
                        try:
                            on_output("stdout", data)
                        except Exception:
                            logger.warning("on_output(stdout) failed in final read", exc_info=True)
                    while channel.recv_stderr_ready():
                        data = channel.recv_stderr(4096).decode("utf-8", errors="replace")
                        err_lines.append(data)
                        try:
                            on_output("stderr", data)
                        except Exception:
                            logger.warning("on_output(stderr) failed in final read", exc_info=True)
                    break
                try:
                    channel.send_ignore()
                except Exception:
                    break

            exit_code = channel.recv_exit_status()
            out = "".join(out_lines).strip()
            err = "".join(err_lines).strip()
        else:
            exit_code = stdout.channel.recv_exit_status()
            out = stdout.read().decode("utf-8", errors="replace").strip()
            err = stderr.read().decode("utf-8", errors="replace").strip()

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


def _server_to_jump_config(srv: dict) -> dict:
    result = {
        "name": srv.get("name"),
        "host": srv.get("host"),
        "port": srv.get("port", 22),
        "username": srv.get("user") or srv.get("username", "root"),
    }
    for k in ("key", "key_file", "password", "key_content"):
        if srv.get(k):
            result[k] = srv[k]
    return result


def create_ssh_client(server_config: dict) -> SSHClient:
    """从连接池获取或创建 SSH 客户端"""
    pool = SSHConnectionPool.get_instance()
    return pool.get(server_config)


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
        jump_chain = "|".join(
            f"{j.get('user') or j.get('username', 'root')}@{j.get('host', '')}:{j.get('port', 22)}"
            for j in jumps if isinstance(j, dict)
        ) if jumps else "direct"
        return f"{user}@{host}:{port}:{jump_chain}"

    def get(self, server_config: dict) -> SSHClient:
        pool_key = self._make_key(server_config)
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
        host = server_config.get("host")
        port = server_config.get("port", 22)
        user = server_config.get("user") or server_config.get("username", "root")
        ssh_key_path = server_config.get("key") or server_config.get("key_file")
        password = server_config.get("password")
        key_content = server_config.get("key_content")
        jump = server_config.get("jump") or server_config.get("jump_host")
        logger.debug(f"SSHConnectionPool.get() config: host={host}, port={port}, user={user}, "
                     f"has_key={bool(ssh_key_path)}, has_password={bool(password)}, "
                     f"has_key_content={bool(key_content)}, jump={jump}, "
                     f"jump_hosts={server_config.get('jump_hosts')}")
        jump_hosts = server_config.get("jump_hosts")

        if isinstance(jump, str):
            from config_manager import load_config_cached
            cfg = load_config_cached()
            for jh in cfg.get("jump_hosts", []):
                if jh.get("name") == jump:
                    jump = jh
                    break
            else:
                for srv in cfg.get("servers", []):
                    if srv.get("name") == jump:
                        jump = _server_to_jump_config(srv)
                        break
        elif isinstance(jump, dict):
            from config_manager import load_config_cached
            cfg = load_config_cached()
            has_auth = jump.get("key") or jump.get("key_file") or jump.get("password") or jump.get("key_content")
            if not has_auth:
                jump_name = jump.get("name")
                jump_host = jump.get("host")
                jump_port = jump.get("port", 22)
                matched = None
                if jump_name:
                    for jh in cfg.get("jump_hosts", []):
                        if jh.get("name") == jump_name:
                            matched = jh
                            break
                    if not matched:
                        for srv in cfg.get("servers", []):
                            if srv.get("name") == jump_name:
                                matched = _server_to_jump_config(srv)
                                break
                if not matched and jump_host:
                    for jh in cfg.get("jump_hosts", []):
                        if jh.get("host") == jump_host and jh.get("port", 22) == jump_port:
                            matched = jh
                            break
                    if not matched:
                        for srv in cfg.get("servers", []):
                            if srv.get("host") == jump_host and srv.get("port", 22) == jump_port:
                                matched = _server_to_jump_config(srv)
                                break
                if matched:
                    merged = dict(jump)
                    for k in ("key", "key_file", "password", "key_content"):
                        if not merged.get(k) and matched.get(k):
                            merged[k] = matched[k]
                    jump = merged

        if isinstance(jump_hosts, list) and jump_hosts:
            jumps = jump_hosts
        elif jump and isinstance(jump, dict):
            jumps = [jump]
        else:
            jumps = None

        if not any([ssh_key_path, password, key_content]):
            raise ConnectionError(
                f"无法连接到 {host}:{port}：缺少认证凭据（密码、密钥文件、密钥内容均为空）。"
                f"请编辑服务器配置添加认证信息后重试。"
            )

        client = SSHClient(host, port, user, key=ssh_key_path, password=password,
                          jumps=jumps, key_content=key_content)
        _connect_started = time.time()
        client.connect()
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
