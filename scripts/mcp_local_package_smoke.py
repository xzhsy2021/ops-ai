#!/usr/bin/env python3
"""Smoke test the stdio MCP local package capability.

This checks the part that can work before a backend is reachable: inspecting a
local package path and dry-running ops_upload_package. Real upload still needs
OPS backend, OPS_TOOL_TOKEN, and package write enabled.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile


def send(proc, obj):
    body = json.dumps(obj).encode("utf-8")
    proc.stdin.write(b"Content-Length: " + str(len(body)).encode("ascii") + b"\r\n\r\n" + body)
    proc.stdin.flush()


def recv(proc):
    line = proc.stdout.readline()
    if not line:
        stderr = proc.stderr.read().decode("utf-8", errors="replace")
        raise RuntimeError("MCP server produced no stdout. stderr=" + stderr)
    if not line.lower().startswith(b"content-length:"):
        raise RuntimeError("Unexpected stdout: " + line.decode("utf-8", errors="replace"))
    length = int(line.split(b":", 1)[1].strip())
    proc.stdout.readline()
    return json.loads(proc.stdout.read(length).decode("utf-8"))


def main() -> int:
    with tempfile.NamedTemporaryFile(prefix="ops-mcp-smoke-", suffix=".tar.gz", delete=False) as tmp:
        tmp.write(b"ops local package smoke\n")
        local_path = tmp.name
    env = os.environ.copy()
    env.setdefault("OPS_BASE_URL", "http://127.0.0.1:9")
    env.setdefault("OPS_TOOL_TOKEN", "")
    env.setdefault("OPS_MCP_HTTP_TIMEOUT", "1")
    proc = subprocess.Popen(
        [sys.executable, "-m", "app.mcp.server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=os.getcwd(),
        env=env,
    )
    try:
        send(proc, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}})
        print(json.dumps(recv(proc), ensure_ascii=False, indent=2))
        send(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "ops_inspect_local_package", "arguments": {"local_path": local_path}}})
        print(json.dumps(recv(proc), ensure_ascii=False, indent=2))
        send(proc, {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "ops_upload_package", "arguments": {"local_path": local_path, "dry_run": True}}})
        print(json.dumps(recv(proc), ensure_ascii=False, indent=2))
        send(proc, {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "ops_prepare_release_from_local_package", "arguments": {"local_path": local_path, "system": "demo", "service": "api", "environment": "dev", "dry_run": True}}})
        print(json.dumps(recv(proc), ensure_ascii=False, indent=2))
        return 0
    finally:
        try:
            os.remove(local_path)
        except OSError:
            pass
        proc.kill()
        try:
            proc.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            proc.terminate()
            proc.communicate(timeout=2)


if __name__ == "__main__":
    raise SystemExit(main())
