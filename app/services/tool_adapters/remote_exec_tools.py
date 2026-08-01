"""远程命令执行 & 文件读写工具。

提供三个核心能力，填补 AI Agent 无法直接操作远程服务器的缺口：
- ops.exec_remote: 在远程服务器上执行任意 shell 命令
- ops.file_read:   读取远程文件内容
- ops.file_write:  写入远程文件内容（自动备份）
"""
from __future__ import annotations

import base64
import shlex
import time
from fastapi import HTTPException

from app.services.tool_registry import registry


def _resolve_server_or_404(server_key: str):
    from config_manager import resolve_server
    if not server_key:
        raise HTTPException(status_code=404, detail="Server not found: <empty>")
    srv = resolve_server(server_key)
    if not srv:
        raise HTTPException(
            status_code=404,
            detail=f"Server not found: {server_key}. Pass a server name, host, full UUID, or short UUID prefix.",
        )
    return srv


def _connect(server_key: str):
    srv = _resolve_server_or_404(server_key)
    from ssh_client import create_ssh_client
    ssh = create_ssh_client(srv)
    ssh.connect()
    return ssh, srv


# ──────────────────────────────────────────────────────────────
# ops.exec_remote — 远程命令执行
# ──────────────────────────────────────────────────────────────

@registry.register(
    name="ops.exec_remote",
    title="远程执行命令",
    description=(
        "在指定服务器上执行任意 shell 命令，通过 OPS 的 SSH 基础设施（含跳板机）连接。"
        "适用于：docker 操作、文件查找、进程管理、配置检查等 ad-hoc 运维操作。"
        "高危操作，需要人工审批。"
        "中文：执行远程命令/远程执行/运行命令/跑脚本/远程shell。"
    ),
    scopes=["ops:read", "server:read"],
    risk="high",
    category="server_write",
    write=True,
    requires_confirmation=True,
    requires_human_approval=True,
    data_sensitivity="sensitive",
    keywords=[
        "远程执行", "执行命令", "shell", "远程命令", "remote exec",
        "docker", "ps", "grep", "find", "cat", "ls", "tail",
        "执行脚本", "运行命令", "跑命令",
    ],
    input_schema={
        "type": "object",
        "properties": {
            "server": {
                "type": "string",
                "description": "服务器名称、host、UUID 或短 UUID 前缀",
            },
            "command": {
                "type": "string",
                "description": "要执行的 shell 命令",
            },
            "timeout": {
                "type": "integer",
                "description": "命令超时秒数，默认 60，最大 300",
                "default": 60,
            },
            "confirm_text": {
                "type": "string",
                "description": "确认短语: CONFIRM ops.exec_remote",
            },
        },
        "required": ["server", "command", "confirm_text"],
        "additionalProperties": False,
    },
)
def exec_remote(args, ctx, db):
    server = args.get("server", "")
    command = args.get("command", "")
    timeout = min(int(args.get("timeout", 60)), 300)

    ssh, srv = _connect(server)
    try:
        code, out, err = ssh.exec(command, timeout=timeout)
        return {
            "ok": code == 0,
            "server": server,
            "command": command,
            "exit_code": code,
            "stdout": out,
            "stderr": err,
        }
    finally:
        ssh.close()


# ──────────────────────────────────────────────────────────────
# ops.file_read — 远程文件读取（只读，无需确认短语）
# ──────────────────────────────────────────────────────────────

@registry.register(
    name="ops.file_read",
    title="读取远程文件",
    description=(
        "读取远程服务器上的文件内容。"
        "用于查看配置文件、日志文件、脚本等。"
        "只读操作，无需确认短语。"
        "中文：读文件/查看配置/远程文件/读取远程文件/cat文件。"
    ),
    scopes=["ops:read", "server:read"],
    risk="low",
    category="server_read",
    data_sensitivity="sensitive",
    keywords=["读文件", "查看配置", "cat", "远程文件", "read file", "查看文件"],
    input_schema={
        "type": "object",
        "properties": {
            "server": {
                "type": "string",
                "description": "服务器名称、host、UUID 或短 UUID 前缀",
            },
            "path": {
                "type": "string",
                "description": "远程文件的绝对路径",
            },
            "max_lines": {
                "type": "integer",
                "description": "最大返回行数，默认 500",
                "default": 500,
            },
        },
        "required": ["server", "path"],
        "additionalProperties": False,
    },
)
def file_read(args, ctx, db):
    server = args.get("server", "")
    path = args.get("path", "")
    max_lines = min(int(args.get("max_lines", 500)), 2000)

    ssh, srv = _connect(server)
    try:
        # 先检查文件是否存在
        code, _, _ = ssh.exec(f"test -f {shlex.quote(path)}", timeout=10)
        if code != 0:
            return {"ok": False, "server": server, "path": path, "error": "文件不存在"}

        # 读取文件内容
        cmd = f"head -n {max_lines} {shlex.quote(path)}"
        code, out, err = ssh.exec(cmd, timeout=30)
        line_count = len(out.split("\n")) if out else 0
        truncated = line_count >= max_lines

        return {
            "ok": True,
            "server": server,
            "path": path,
            "exit_code": code,
            "content": out,
            "stderr": err,
            "line_count": line_count,
            "truncated": truncated,
        }
    finally:
        ssh.close()


# ──────────────────────────────────────────────────────────────
# ops.file_write — 远程文件写入（高危，需审批）
# ──────────────────────────────────────────────────────────────

@registry.register(
    name="ops.file_write",
    title="写入远程文件",
    description=(
        "向远程服务器写入文件内容。写入前自动备份原文件（如存在）。"
        "用于修改配置文件、部署脚本等。"
        "高危操作，需要人工审批。"
        "中文：写文件/修改配置/写入远程文件/编辑远程文件/更新配置。"
    ),
    scopes=["ops:read", "server:read"],
    risk="high",
    category="server_write",
    write=True,
    requires_confirmation=True,
    requires_human_approval=True,
    data_sensitivity="sensitive",
    keywords=[
        "写文件", "修改配置", "编辑文件", "write file", "edit file",
        "更新配置", "修改远程文件", "编辑远程配置",
    ],
    input_schema={
        "type": "object",
        "properties": {
            "server": {
                "type": "string",
                "description": "服务器名称、host、UUID 或短 UUID 前缀",
            },
            "path": {
                "type": "string",
                "description": "远程文件的绝对路径",
            },
            "content": {
                "type": "string",
                "description": "要写入的文件内容",
            },
            "backup": {
                "type": "boolean",
                "description": "是否在写入前备份原文件，默认 true",
                "default": True,
            },
            "confirm_text": {
                "type": "string",
                "description": "确认短语: CONFIRM ops.file_write",
            },
        },
        "required": ["server", "path", "content", "confirm_text"],
        "additionalProperties": False,
    },
)
def file_write(args, ctx, db):
    server = args.get("server", "")
    path = args.get("path", "")
    content = args.get("content", "")
    backup = args.get("backup", True)

    ssh, srv = _connect(server)
    try:
        # 备份原文件
        backup_path = ""
        if backup:
            code, _, _ = ssh.exec(f"test -f {shlex.quote(path)}", timeout=10)
            if code == 0:
                ts = int(time.time())
                backup_path = f"{path}.bak.{ts}"
                code2, out2, err2 = ssh.exec(f"cp {shlex.quote(path)} {shlex.quote(backup_path)}", timeout=15)
                if code2 != 0:
                    return {"ok": False, "server": server, "path": path, "error": f"备份失败: {err2}"}

        # 确保父目录存在
        parent = "/".join(path.rstrip("/").split("/")[:-1]) or "/"
        ssh.exec(f"mkdir -p {shlex.quote(parent)}", timeout=10)

        # 通过 base64 写入避免转义问题
        content_b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")
        write_cmd = f"echo {shlex.quote(content_b64)} | base64 -d > {shlex.quote(path)}"
        code, out, err = ssh.exec(write_cmd, timeout=30)

        if code != 0:
            return {"ok": False, "server": server, "path": path, "exit_code": code, "stderr": err, "error": "写入失败"}

        # 验证写入
        code2, verify_out, _ = ssh.exec(f"wc -c < {shlex.quote(path)}", timeout=10)
        try:
            bytes_written = int(verify_out.strip())
        except ValueError:
            bytes_written = 0

        return {
            "ok": True,
            "server": server,
            "path": path,
            "backup_path": backup_path or None,
            "bytes_written": bytes_written,
            "message": f"写入成功 ({bytes_written} bytes)" + (f"，备份: {backup_path}" if backup_path else ""),
        }
    finally:
        ssh.close()