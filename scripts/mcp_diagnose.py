from __future__ import annotations
import json
import os
import subprocess
import sys


def send(proc, obj):
    body = json.dumps(obj).encode('utf-8')
    proc.stdin.write(b'Content-Length: ' + str(len(body)).encode('ascii') + b'\r\n\r\n' + body)
    proc.stdin.flush()


def recv(proc):
    line = proc.stdout.readline()
    if not line:
        stderr = proc.stderr.read().decode('utf-8', errors='replace')
        raise RuntimeError('MCP server produced no stdout. stderr=' + stderr)
    if not line.lower().startswith(b'content-length:'):
        raise RuntimeError('Unexpected stdout from MCP server: ' + line.decode('utf-8', errors='replace'))
    length = int(line.split(b':', 1)[1].strip())
    proc.stdout.readline()
    return json.loads(proc.stdout.read(length).decode('utf-8'))


def main():
    print('[INFO] Python:', sys.executable)
    print('[INFO] OPS_BASE_URL:', os.getenv('OPS_BASE_URL', 'http://127.0.0.1:8000'))
    print('[INFO] OPS_TOOL_TOKEN present:', bool(os.getenv('OPS_TOOL_TOKEN')))
    proc = subprocess.Popen(
        [sys.executable, '-m', 'app.mcp.server'],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=os.getcwd(),
        env=os.environ.copy(),
    )
    try:
        send(proc, {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {'protocolVersion': '2025-06-18'}})
        print('[MCP initialize]')
        print(json.dumps(recv(proc), ensure_ascii=False, indent=2))
        send(proc, {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list', 'params': {}})
        print('[MCP tools/list]')
        print(json.dumps(recv(proc), ensure_ascii=False, indent=2)[:6000])
    finally:
        proc.kill()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.terminate()
            proc.wait(timeout=2)


if __name__ == '__main__':
    main()
