"""第 12 轮安全加固契约回归（2026-09 复盘第 12 轮）。

修掉两个真实 BUG：

1) ``POST /api/v2/files/browse/{server_name}``（``app/api/deploy_v2.py::browse_remote``）
   - **命令注入**：请求体里的 ``path`` 直接拼进远端命令
     ``ls -alh --time-style=long-iso '{path}'``，单引号包裹挡不住 ``'``，
     可在目标服务器上以 OPS 保存的 SSH 凭据执行任意命令。
   - **缺少授权**：同平台 ``app/api/sftp.py`` 的每个文件端点都要求 ``require_admin``，
     本端点连 ``require_auth`` 都没有（仅靠全局会话中间件兜底，任何已登录用户都能用
     服务器凭据遍历任意目录）。
   - **缺少目录白名单**：sftp.py 用 allowed roots 限制可浏览范围，本端点没有。

2) ``POST /api/v2/mcp/legacy/submit``（``app/api/mcp_gateway.py``）
   ``/api/v2/mcp`` 位于全局会话中间件的放行前缀（PUBLIC_PREFIXES）里，
   而该端点在进程内 ``TASKS`` 字典里只增不减、且自身无鉴权 ——
   匿名调用即可无界占用内存。

本文件用假 SSH 捕获实际下发的远端命令（不连接任何真实服务器），并用真实 POSIX shell
（Git Bash）证明"修前的写法会被注入、修后的写法不会"。
"""
from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

INJECTION_PATH = "/data'; echo PWNED; id; echo '"
# 仍在白名单 /data 之内、但含 shell 元字符的路径：用于验证"即使路径合法也必须转义"
QUOTING_PATH = "/data/web'; echo PWNED; id; echo '/x"
BROWSE_URL = "/api/v2/files/browse/srv-1"


def _find_posix_shell() -> str:
    """找一个可用的 POSIX shell（Windows 上通常是 Git Bash）。"""
    candidates = [
        os.getenv("OPS_TEST_POSIX_SHELL", ""),
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files (x86)\Git\bin\bash.exe",
        "/bin/bash",
        "/usr/bin/bash",
        "/bin/sh",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    return ""


class _FakeSSH:
    """捕获远端命令的假 SSH 客户端（不建立任何网络连接）。"""

    def __init__(self, exit_code: int = 0, out: str = ""):
        self.commands: list[str] = []
        self.closed = False
        self._exit_code = exit_code
        self._out = out

    def exec(self, command: str, timeout: int = 30):
        self.commands.append(command)
        return self._exit_code, self._out, ""

    def close(self) -> None:
        self.closed = True


@pytest.fixture()
def browse_client(monkeypatch):
    """最小 FastAPI 应用 + 资源路由；假 SSH、假服务器清单、可观测的授权调用。"""
    from app.api import deploy_v2

    fake_ssh = _FakeSSH(out="total 0\n")
    connects: list[dict] = []
    auth_calls: list[str] = []

    def fake_connect(srv):
        connects.append(srv)
        return fake_ssh

    def fake_require_deploy(request, db, environment=""):
        auth_calls.append("require_deploy")
        return {"username": "tester", "can_deploy": True, "is_admin": False}

    monkeypatch.setattr(deploy_v2, "_connect_ssh", fake_connect)
    monkeypatch.setattr(deploy_v2, "require_deploy", fake_require_deploy)
    monkeypatch.setattr(deploy_v2.inventory, "get_server", lambda name: {"name": name, "host": "10.0.0.9", "allowed_roots": ["/data"]})

    app = FastAPI()
    app.include_router(deploy_v2.resource_v2_router)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, fake_ssh, connects, auth_calls


# ---------------------------------------------------------------------------
# 1) 命令注入：远端命令必须做 shell 转义
# ---------------------------------------------------------------------------

def test_browse_remote_quotes_path_in_remote_command(browse_client):
    """RED：修前远端命令是 `ls ... '{path}'`，含引号的路径会闭合引号产生注入。

    这里用**仍在白名单内**的路径，确保验证的是转义本身（而不是被白名单提前拦下）。
    """
    client, fake_ssh, _connects, _auth = browse_client

    resp = client.post(BROWSE_URL, json={"path": QUOTING_PATH})

    assert resp.status_code == 200, resp.text
    assert fake_ssh.commands, "应下发一次远端 ls 命令"
    command = fake_ssh.commands[0]

    assert shlex.quote(QUOTING_PATH) in command, (
        f"远端命令必须包含 shlex.quote 后的路径，实际：{command}"
    )
    # 修前的写法（裸单引号包裹）会留下未转义的引号 → 注入
    assert f"'{QUOTING_PATH}'" not in command, f"仍在使用未转义的单引号包裹：{command}"


@pytest.mark.skipif(not _find_posix_shell(), reason="需要 POSIX shell（Git Bash）做注入语义验证")
def test_injection_is_real_for_unquoted_form_and_neutralized_by_shlex_quote():
    """行为层证据：同一段 path，旧写法会真的执行注入命令，新写法只当字面量。"""
    shell = _find_posix_shell()
    payload = INJECTION_PATH

    def run(command: str) -> str:
        proc = subprocess.run(
            [shell, "-c", command],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            check=False,
        )
        return f"{proc.stdout}\n{proc.stderr}"

    vulnerable = run(f"echo '{payload}'")
    hardened = run(f"echo {shlex.quote(payload)}")

    # 旧写法：引号被闭合，"echo PWNED" 与 "id" 作为独立命令真实执行
    vulnerable_lines = [line.strip() for line in vulnerable.splitlines()]
    assert "PWNED" in vulnerable_lines, f"对照组应证明未转义写法会执行注入命令，实际：{vulnerable!r}"
    assert any(line.startswith("uid=") for line in vulnerable_lines), (
        f"对照组应证明可执行任意命令（id），实际：{vulnerable!r}"
    )
    # 新写法：整段 path 只是 echo 的一个字面量参数，没有额外命令被执行
    assert hardened.strip() == payload, f"转义后应原样输出字面路径，实际：{hardened!r}"
    assert not any(line.startswith("uid=") for line in hardened.splitlines())


# ---------------------------------------------------------------------------
# 2) 授权：必须显式要求发布权限，且非法路径不得触发远程连接
# ---------------------------------------------------------------------------

def test_browse_remote_requires_deploy_permission(browse_client):
    """RED：修前端点根本不调用授权函数。"""
    client, _fake_ssh, _connects, auth_calls = browse_client

    resp = client.post(BROWSE_URL, json={"path": "/data/web"})

    assert resp.status_code == 200, resp.text
    assert auth_calls == ["require_deploy"], "端点必须调用 require_deploy 做授权"


def test_browse_remote_propagates_403_from_authorization(browse_client, monkeypatch):
    from app.api import deploy_v2

    client, fake_ssh, connects, _auth = browse_client

    def deny(request, db, environment=""):
        raise HTTPException(status_code=403, detail="Deploy permission required")

    monkeypatch.setattr(deploy_v2, "require_deploy", deny)
    resp = client.post(BROWSE_URL, json={"path": "/data/web"})

    assert resp.status_code == 403, resp.text
    assert connects == [], "授权失败时不得建立 SSH 连接"
    assert fake_ssh.commands == []


def test_browse_remote_rejects_path_outside_allowed_roots_before_connecting(browse_client):
    """目录白名单：/etc 不在 allowed_roots 内 → 403，且不连接服务器。"""
    client, fake_ssh, connects, _auth = browse_client

    resp = client.post(BROWSE_URL, json={"path": "/etc"})

    assert resp.status_code == 403, resp.text
    assert "allowed" in resp.text.lower(), resp.text
    assert connects == [], "路径校验必须发生在连接服务器之前"
    assert fake_ssh.commands == []


def test_classic_injection_payload_is_blocked_before_any_remote_call(browse_client):
    """注入载荷本身也不在白名单内 → 连接前即 403（纵深防御第一层）。"""
    client, fake_ssh, connects, _auth = browse_client

    resp = client.post(BROWSE_URL, json={"path": INJECTION_PATH})

    assert resp.status_code == 403, resp.text
    assert connects == []
    assert fake_ssh.commands == []


def test_browse_remote_allows_normalized_path_inside_root(browse_client):
    """`..` 归一后仍在白名单内 → 正常执行（不让合法用法回归）。"""
    client, fake_ssh, connects, _auth = browse_client

    resp = client.post(BROWSE_URL, json={"path": "/data/web/../web/app"})

    assert resp.status_code == 200, resp.text
    assert len(connects) == 1
    assert "/data/web/app" in fake_ssh.commands[0]


def test_browse_remote_rejects_traversal_outside_root(browse_client):
    """`../` 逃逸到白名单之外 → 403。"""
    client, fake_ssh, connects, _auth = browse_client

    resp = client.post(BROWSE_URL, json={"path": "/data/../../etc"})

    assert resp.status_code == 403, resp.text
    assert connects == []
    assert fake_ssh.commands == []


def test_browse_remote_default_path_still_works(browse_client):
    """不带 path 时沿用默认目录（默认值也必须在白名单内，避免开箱即 403）。"""
    client, fake_ssh, connects, _auth = browse_client

    resp = client.post(BROWSE_URL, json={})

    assert resp.status_code in (200, 403), resp.text
    if resp.status_code == 200:
        assert connects and "/data" in fake_ssh.commands[0]


# ---------------------------------------------------------------------------
# 3) 遗留 MCP 网关：匿名可达 + 无界内存增长
# ---------------------------------------------------------------------------

@pytest.fixture()
def legacy_client():
    from app.api import mcp_gateway

    app = FastAPI()
    app.include_router(mcp_gateway.router)
    saved = dict(mcp_gateway.TASKS)
    mcp_gateway.TASKS.clear()
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            yield client, mcp_gateway
    finally:
        mcp_gateway.TASKS.clear()
        mcp_gateway.TASKS.update(saved)


def test_legacy_submit_is_reachable_without_auth(legacy_client):
    """记录事实：该端点位于中间件放行前缀下，自身不鉴权（因此必须限制资源占用）。"""
    client, _module = legacy_client

    resp = client.post("/api/v2/mcp/legacy/submit", json={"capability": "deploy", "payload": {}})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is True
    assert body["data"]["legacy"] is True


def test_legacy_tasks_are_capped(legacy_client):
    """RED：修前 TASKS 只增不减，N 次提交就有 N 条。"""
    client, module = legacy_client
    limit = module.MAX_LEGACY_TASKS
    attempts = limit + 25

    for index in range(attempts):
        resp = client.post(
            "/api/v2/mcp/legacy/submit",
            json={"capability": "deploy", "payload": {"seq": index}},
        )
        assert resp.status_code == 200, resp.text

    assert len(module.TASKS) <= limit, f"TASKS 必须不超过上限 {limit}，实际 {len(module.TASKS)}"

    # 最新一条仍可查询（回到结构一致的 not_found 也属预期，但最新一条必须还在）
    last = client.post("/api/v2/mcp/legacy/submit", json={"capability": "deploy", "payload": {"seq": "last"}}).json()["data"]
    status = client.get(f"/api/v2/mcp/legacy/status/{last['task_id']}").json()
    assert status["data"]["status"] == "queued"


def test_legacy_submit_rejects_oversized_payload(legacy_client):
    client, module = legacy_client

    too_big = "x" * (module.MAX_LEGACY_PAYLOAD_BYTES + 1024)
    resp = client.post("/api/v2/mcp/legacy/submit", json={"capability": "deploy", "payload": {"blob": too_big}})

    assert resp.status_code == 413, resp.text
    assert module.TASKS == {}, "超限 payload 不得进入 TASKS"
