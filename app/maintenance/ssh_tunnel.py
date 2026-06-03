"""Small SSH local port forwarder for database access through a bastion host."""
import os
import select
import socket
import socketserver
import threading
from contextlib import AbstractContextManager
from typing import Optional
from io import StringIO

import paramiko


class _ForwardHandler(socketserver.BaseRequestHandler):
    def handle(self):
        tunnel = self.server.tunnel  # type: ignore[attr-defined]
        chan = tunnel.ssh_transport.open_channel(
            "direct-tcpip",
            (tunnel.remote_host, tunnel.remote_port),
            self.request.getpeername(),
        )
        if chan is None:
            return
        try:
            while True:
                r, _, _ = select.select([self.request, chan], [], [], 1)
                if self.request in r:
                    data = self.request.recv(65535)
                    if len(data) == 0:
                        break
                    chan.sendall(data)
                if chan in r:
                    data = chan.recv(65535)
                    if len(data) == 0:
                        break
                    self.request.sendall(data)
        finally:
            chan.close()
            self.request.close()


class _ForwardServer(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


class SSHTunnel(AbstractContextManager):
    def __init__(
        self,
        ssh_host: str,
        ssh_port: int,
        ssh_username: str,
        remote_host: str,
        remote_port: int,
        ssh_password: Optional[str] = None,
        ssh_key_path: Optional[str] = None,
        ssh_key_passphrase: Optional[str] = None,
        ssh_key_content: Optional[str] = None,
        target_ssh_host: Optional[str] = None,
        target_ssh_port: int = 22,
        target_ssh_username: Optional[str] = None,
        target_ssh_password: Optional[str] = None,
        target_ssh_key_path: Optional[str] = None,
        target_ssh_key_passphrase: Optional[str] = None,
        target_ssh_key_content: Optional[str] = None,
    ):
        self.ssh_host = ssh_host
        self.ssh_port = int(ssh_port or 22)
        self.ssh_username = ssh_username
        self.ssh_password = ssh_password or None
        self.ssh_key_path = os.path.expanduser(ssh_key_path) if ssh_key_path else None
        self.ssh_key_passphrase = ssh_key_passphrase or None
        self.ssh_key_content = ssh_key_content or None
        self.target_ssh_host = target_ssh_host or None
        self.target_ssh_port = int(target_ssh_port or 22)
        self.target_ssh_username = target_ssh_username or None
        self.target_ssh_password = target_ssh_password or None
        self.target_ssh_key_path = os.path.expanduser(target_ssh_key_path) if target_ssh_key_path else None
        self.target_ssh_key_passphrase = target_ssh_key_passphrase or None
        self.target_ssh_key_content = target_ssh_key_content or None
        self.remote_host = remote_host
        self.remote_port = int(remote_port)
        self.local_host = "127.0.0.1"
        self.local_port: Optional[int] = None
        self.client: Optional[paramiko.SSHClient] = None
        self.target_client: Optional[paramiko.SSHClient] = None
        self.target_sock = None
        self.server: Optional[_ForwardServer] = None
        self.thread: Optional[threading.Thread] = None


    def _resolve_key_path(self, key_path: str) -> str:
        try:
            from config_manager import get_key_file_path
            return get_key_file_path(key_path)
        except Exception:
            return os.path.expanduser(key_path)

    def _load_key_from_content(self, key_content: str) -> paramiko.PKey:
        for key_cls in [paramiko.RSAKey, paramiko.Ed25519Key, paramiko.ECDSAKey]:
            try:
                return key_cls.from_private_key(StringIO(key_content))
            except (paramiko.SSHException, ValueError):
                continue
        raise ValueError("Cannot load SSH key from content")

    @property
    def ssh_transport(self):
        active_client = self.target_client or self.client
        if not active_client:
            raise RuntimeError("SSH tunnel is not connected")
        transport = active_client.get_transport()
        if not transport or not transport.is_active():
            raise RuntimeError("SSH transport is not active")
        return transport

    def _connect_kwargs(
        self,
        host: str,
        port: int,
        username: str,
        password: Optional[str],
        key_path: Optional[str],
        key_content: Optional[str],
        key_passphrase: Optional[str],
        sock=None,
    ):
        kwargs = {
            "hostname": host,
            "port": int(port or 22),
            "username": username,
            "timeout": 15,
            "banner_timeout": 15,
            "auth_timeout": 15,
        }
        if sock is not None:
            kwargs["sock"] = sock
        if key_content:
            kwargs["pkey"] = self._load_key_from_content(key_content)
        elif key_path:
            kwargs["key_filename"] = self._resolve_key_path(key_path)
            if key_passphrase:
                kwargs["passphrase"] = key_passphrase
        if password:
            kwargs["password"] = password
        return kwargs

    def __enter__(self):
        self.client = paramiko.SSHClient()
        self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        connect_kwargs = self._connect_kwargs(
            self.ssh_host, self.ssh_port, self.ssh_username,
            self.ssh_password, self.ssh_key_path, self.ssh_key_content, self.ssh_key_passphrase,
        )
        self.client.connect(**connect_kwargs)

        if self.target_ssh_host and self.target_ssh_username:
            bastion_transport = self.client.get_transport()
            if not bastion_transport or not bastion_transport.is_active():
                raise RuntimeError("Bastion SSH transport is not active")
            self.target_sock = bastion_transport.open_channel(
                "direct-tcpip",
                (self.target_ssh_host, self.target_ssh_port),
                ("127.0.0.1", 0),
            )
            self.target_client = paramiko.SSHClient()
            self.target_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            target_kwargs = self._connect_kwargs(
                self.target_ssh_host, self.target_ssh_port, self.target_ssh_username,
                self.target_ssh_password, self.target_ssh_key_path, self.target_ssh_key_content,
                self.target_ssh_key_passphrase, sock=self.target_sock,
            )
            self.target_client.connect(**target_kwargs)

        self.server = _ForwardServer((self.local_host, 0), _ForwardHandler)
        self.server.tunnel = self  # type: ignore[attr-defined]
        self.local_port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, name="db-ssh-tunnel", daemon=True)
        self.thread.start()
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        if self.target_client:
            self.target_client.close()
        if self.target_sock:
            try:
                self.target_sock.close()
            except Exception:
                pass
        if self.client:
            self.client.close()
        return False
