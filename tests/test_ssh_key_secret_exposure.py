"""SSH 私钥不得经工具层外泄（2026-09-12 复盘第 4 轮）。

缺陷：``ops.get_ssh_key`` 直接返回 ``decrypt_secret(row.private_key_encrypted)``
--------------------------------------------------------------------------------
- 作用域只要 ``ops:read``，``risk="low"``、``write=False``，无确认/审批闸门；
- 该工具同时位于 ``DAILY_OPS_TOOL_NAMES`` 与默认 ``ai_full`` 档位，AI 客户端可见可调；
- MCP 描述却写成 "Get SSH key metadata (no private key content)"，与实现相反；
- 全仓 ``decrypt_secret`` 调用点中只有这一处把明文交回给工具调用方
  （其余在 repository/maintenance/sql_query 内部用于真正的 SSH/DB 连接）。

影响：任何持基础只读作用域的 AI 客户端可一次性取走全部已注册 SSH 私钥，
绕开部署/执行通道的全部人工审批设计，并把私钥写入模型上下文。

本文件锁定：
1. 运行期输出扫描——密钥明文标记不得出现在任何工具返回值里；
2. 静态守卫——工具适配器不得在 ``return`` 中直接解密密钥；
3. 描述一致性——MCP 面向 AI 的描述必须与"不回传私钥"的实现一致。
"""
from __future__ import annotations

import ast
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

_MARKER = "SUPER-SECRET-PRIVATE-KEY-MARKER-4f3a"
_REPO_ROOT = Path(__file__).resolve().parents[1]
_ADAPTER_DIR = _REPO_ROOT / "app" / "services" / "tool_adapters"


@pytest.fixture()
def ssh_env(monkeypatch, tmp_path):
    """临时数据库 + 一把含明文标记的加密私钥。"""
    from app.core.secret_store import encrypt_secret
    from app.db.base import Base
    from app.db.models import SshKey

    engine = create_engine(f"sqlite:///{tmp_path / 'ssh.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autocommit=False, autoflush=False)()
    session.add(
        SshKey(
            name="probe-key",
            private_key_encrypted=encrypt_secret(f"-----BEGIN OPENSSH PRIVATE KEY-----\n{_MARKER}\n-----END OPENSSH PRIVATE KEY-----"),
            passphrase_encrypted=encrypt_secret("probe-passphrase"),
            description="round4 probe",
        )
    )
    session.commit()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _ctx():
    return SimpleNamespace(username="probe", token_owner="", is_admin=True, allow_write=False)


def test_get_ssh_key_never_returns_private_key_content(ssh_env):
    """核心回归：返回体里不得出现私钥明文（也不得出现口令明文）。"""
    from app.services.tool_adapters import ssh_key_tools

    result = ssh_key_tools.get_ssh_key_tool({"name": "probe-key"}, _ctx(), ssh_env)

    assert result["found"] is True
    payload = json.dumps(result, ensure_ascii=False, default=str)
    assert _MARKER not in payload, "私钥明文不得出现在工具返回值中"
    assert "probe-passphrase" not in payload, "口令明文不得出现在工具返回值中"
    assert "BEGIN OPENSSH PRIVATE KEY" not in payload

    assert result["private_key"] == "***"
    assert result["has_private_key"] is True
    assert result["has_passphrase"] is True


def test_get_ssh_key_exposes_stable_fingerprint_for_identification(ssh_env):
    """仍要能用不可逆指纹辨识密钥（修复不能把可用信息一起砍掉）。"""
    from app.core.secrets_policy import secret_fingerprint
    from app.core.secret_store import decrypt_secret
    from app.db.models import SshKey
    from app.services.tool_adapters import ssh_key_tools

    result = ssh_key_tools.get_ssh_key_tool({"name": "probe-key"}, _ctx(), ssh_env)

    row = ssh_env.query(SshKey).filter(SshKey.name == "probe-key").first()
    expected = secret_fingerprint(decrypt_secret(row.private_key_encrypted))
    assert result["private_key_fingerprint"] == expected
    assert len(result["private_key_fingerprint"]) == 12


def test_get_ssh_key_missing_key_is_still_found_false(ssh_env):
    from app.services.tool_adapters import ssh_key_tools

    assert ssh_key_tools.get_ssh_key_tool({"name": "nope"}, _ctx(), ssh_env) == {"found": False, "name": "nope"}


def test_list_ssh_keys_returns_metadata_only(ssh_env):
    """列表接口同样不得带出密钥材料。"""
    from app.services.tool_adapters import ssh_key_tools

    result = ssh_key_tools.list_ssh_keys_tool({}, _ctx(), ssh_env)

    payload = json.dumps(result, ensure_ascii=False, default=str)
    assert _MARKER not in payload
    assert result["items"][0]["name"] == "probe-key"
    assert "private_key" not in result["items"][0]


def test_no_tool_adapter_returns_decrypted_secret():
    """静态守卫：工具适配器不得在 return 语句里直接解密密钥。

    内部真正需要明文的地方在 app/services 与 app/maintenance（SSH/DB 连接），
    不属于面向前端/AI 的工具输出层。此守卫防止同类缺陷再次被写回来。
    """
    offenders: list[str] = []
    for path in sorted(_ADAPTER_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Return) or node.value is None:
                continue
            for inner in ast.walk(node.value):
                if (
                    isinstance(inner, ast.Call)
                    and isinstance(inner.func, ast.Name)
                    and inner.func.id == "decrypt_secret"
                ):
                    offenders.append(f"{path.name}:{inner.lineno}")
    assert offenders == [], f"工具适配器在 return 中解密密钥: {offenders}"


def test_mcp_description_does_not_advertise_private_key_content():
    """描述一致性：面向 AI 的 MCP 描述必须与实现一致（不含私钥内容）。"""
    from app.services.mcp_capability_service import MCP_TOOL_DESCRIPTION_OVERRIDES
    from app.services.tool_registry import ensure_builtin_registered, registry

    ensure_builtin_registered()
    tool = registry.get("ops.get_ssh_key")
    assert tool is not None

    for text in (tool.description, MCP_TOOL_DESCRIPTION_OVERRIDES.get("ops.get_ssh_key", "")):
        lowered = text.lower()
        assert "no private key content" in lowered or "不回传私钥" in text, text
        assert "含解密后的私钥内容" not in text
