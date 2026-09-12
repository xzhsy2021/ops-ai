"""密钥强度策略回归测试（2026-09-12 复盘修复）。

背景缺陷：运行实例 `.env` 的 `SESSION_SECRET` / `OPS_SECRET_KEY` 与仓库 `.env.example`
逐字节相同（公开可预测），而 `diagnostics` / `system_health` / `recommendations` /
`preflight` / `doctor` / `check_env.*` 全都只按"是否配置 + 长度"判断，一律显示正常：
- 占位 `SESSION_SECRET` ⇒ 任何人可离线伪造 admin 会话令牌；
- 占位 `OPS_SECRET_KEY` ⇒ 拿到库即可解密服务器凭据。

这些测试锁定修复后的行为，并防止示例文件里再次出现"可直接使用的密钥"。
"""
from __future__ import annotations

import secrets
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.secrets_policy import (
    STATUS_INSECURE,
    STATUS_MISSING,
    STATUS_OK,
    STATUS_WEAK,
    classify_secret,
    enforce_secret_strength,
    secret_key_report,
)

ROOT = Path(__file__).resolve().parents[1]

# 历史 .env 与 .env.example 中真实出现过的占位值
HISTORICAL_SESSION_PLACEHOLDER = "change-me-to-a-long-random-session-secret"
HISTORICAL_OPS_PLACEHOLDER = "change-me-to-a-long-random-local-secret-at-least-32-chars"


def test_placeholder_and_weak_values_are_rejected():
    for bad in (
        HISTORICAL_SESSION_PLACEHOLDER,
        HISTORICAL_OPS_PLACEHOLDER,
        "please-change-this-to-a-long-random-value",
        "changeme",
        "change-me",
        "secret",
        "dev-fallback-key-do-not-use-in-production",
        "replace-me-with-a-random-value",
    ):
        info = classify_secret(bad)
        assert info["status"] == STATUS_INSECURE, f"{bad!r} 应判为 insecure，实际 {info}"

    # 长度不足 / 字符多样性不足 ⇒ weak（而非 ok）
    assert classify_secret("unit-test-secret")["status"] == STATUS_WEAK
    assert classify_secret("a" * 40)["status"] == STATUS_WEAK
    assert classify_secret("")["status"] == STATUS_MISSING
    assert classify_secret(None)["status"] == STATUS_MISSING

    # 真正的随机值才是 ok
    for _ in range(5):
        assert classify_secret(secrets.token_urlsafe(48))["status"] == STATUS_OK


def test_example_env_files_never_contain_a_usable_secret():
    """根因回归：示例文件里的任何密钥值都不得被判为合格（防止再次被逐字节复制）。"""
    checked = 0
    for name in (".env.example", ".env.local.example", ".env.docker.example", ".env.windows.example"):
        source = (ROOT / name).read_text(encoding="utf-8")
        for line in source.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, _, raw = stripped.partition("=")
            if key.strip() not in {"SESSION_SECRET", "OPS_SECRET_KEY", "APPROVAL_SIGNING_KEY"}:
                continue
            value = raw.strip().strip('"').strip("'")
            checked += 1
            assert classify_secret(value)["status"] != STATUS_OK, f"{name} 的 {key.strip()} 必须是空值或占位值"
    assert checked >= 9, "示例文件应覆盖 SESSION_SECRET / OPS_SECRET_KEY / APPROVAL_SIGNING_KEY"


def test_production_fails_closed_for_placeholder_secret():
    with pytest.raises(RuntimeError) as excinfo:
        enforce_secret_strength("SESSION_SECRET", HISTORICAL_SESSION_PLACEHOLDER, production=True)
    assert "SESSION_SECRET" in str(excinfo.value)
    assert "token_urlsafe" in str(excinfo.value)

    # 非生产：只记录告警，不抛异常（本地开发仍可启动）
    info = enforce_secret_strength("SESSION_SECRET", HISTORICAL_SESSION_PLACEHOLDER, production=False)
    assert info["status"] == STATUS_INSECURE


def test_secret_key_report_lists_only_configured_problems(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", HISTORICAL_SESSION_PLACEHOLDER)
    monkeypatch.setenv("OPS_SECRET_KEY", secrets.token_urlsafe(48))
    monkeypatch.setenv("APPROVAL_SIGNING_KEY", secrets.token_urlsafe(48))
    report = secret_key_report()
    assert report["ok"] is False
    assert report["insecure"] == ["SESSION_SECRET"]
    assert report["keys"]["OPS_SECRET_KEY"]["status"] == STATUS_OK

    monkeypatch.setenv("SESSION_SECRET", secrets.token_urlsafe(48))
    assert secret_key_report()["ok"] is True


def test_diagnostics_reports_error_for_placeholder_secrets(monkeypatch):
    from app.services.diagnostics import _secret_key_check

    monkeypatch.setenv("SESSION_SECRET", HISTORICAL_SESSION_PLACEHOLDER)
    monkeypatch.setenv("OPS_SECRET_KEY", HISTORICAL_OPS_PLACEHOLDER)
    result = _secret_key_check()
    assert result["status"] == "error"
    assert "SESSION_SECRET" in result["message"] or "密钥不安全" in result["message"]
    assert result["keys"]["SESSION_SECRET"]["status"] == "insecure"
    assert result["keys"]["OPS_SECRET_KEY"]["documented_example"] is False  # 值已不在示例文件中


def test_diagnostics_ok_for_strong_secrets(monkeypatch):
    from app.services.diagnostics import _secret_key_check

    monkeypatch.setenv("SESSION_SECRET", secrets.token_urlsafe(48))
    monkeypatch.setenv("OPS_SECRET_KEY", secrets.token_urlsafe(48))
    assert _secret_key_check()["status"] == "ok"


def test_system_health_marks_insecure_secret_key_as_error(monkeypatch):
    from app.db.base import SessionLocal
    from app.services.system_health import build_system_health

    monkeypatch.setenv("SESSION_SECRET", HISTORICAL_SESSION_PLACEHOLDER)
    monkeypatch.setenv("OPS_SECRET_KEY", HISTORICAL_OPS_PLACEHOLDER)
    db = SessionLocal()
    try:
        health = build_system_health(db)
    finally:
        db.close()
    secret_check = health["checks"]["secret_key"]
    assert secret_check["status"] == "error"
    assert "轮换" in secret_check["message"] or "不安全" in secret_check["message"]
    assert health["status"] == "unhealthy"


def test_recommendations_include_rotation_actions_for_insecure_secret():
    from app.services.recommendations import build_recommendations

    items = build_recommendations(
        {"checks": {"secret_key": {"status": "error", "message": "密钥不安全（占位值）"}}}
    )
    secret_items = [item for item in items if item["key"] == "secret_key"]
    assert len(secret_items) == 1
    assert secret_items[0]["severity"] == "high"
    assert any("rotate_secrets.py" in action for action in secret_items[0]["actions"])


def test_preflight_secret_strength_check_detects_placeholder():
    from scripts.preflight_start_check import _secret_strength_check

    # 生产环境：fail-closed（error）
    prod_env = {
        "ENV": "production",
        "SESSION_SECRET": HISTORICAL_SESSION_PLACEHOLDER,
        "OPS_SECRET_KEY": HISTORICAL_OPS_PLACEHOLDER,
    }
    check = _secret_strength_check(prod_env)
    assert check.status == "error"
    assert check.details["keys"]["SESSION_SECRET"]["status"] == "insecure"

    # 非生产：只告警，避免历史占位密钥把服务卡在无法启动的状态
    dev_env = {
        "ENV": "local",
        "SESSION_SECRET": HISTORICAL_SESSION_PLACEHOLDER,
        "OPS_SECRET_KEY": HISTORICAL_OPS_PLACEHOLDER,
    }
    dev_check = _secret_strength_check(dev_env)
    assert dev_check.status == "warn"
    assert "不安全" in dev_check.message

    ok_env = {
        "ENV": "production",
        "SESSION_SECRET": secrets.token_urlsafe(48),
        "OPS_SECRET_KEY": secrets.token_urlsafe(48),
    }
    assert _secret_strength_check(ok_env).status == "ok"


def test_preflight_approval_key_check_unchanged_behaviour():
    from scripts.preflight_start_check import _approval_signing_key_check

    assert _approval_signing_key_check({}).status == "error"
    assert _approval_signing_key_check({"APPROVAL_SIGNING_KEY": "change-me"}).status == "error"
    assert _approval_signing_key_check({"APPROVAL_SIGNING_KEY": "x" * 40}).status == "error"  # 字符多样性不足
    assert _approval_signing_key_check({"APPROVAL_SIGNING_KEY": secrets.token_urlsafe(48)}).status == "ok"
    # 兼容旧别名
    assert _approval_signing_key_check({"QCLAW_APPROVAL_SIGNING_KEY": secrets.token_urlsafe(48)}).status == "ok"


# ────────────────────────── 轮换脚本 ──────────────────────────


def _make_db(tmp_path: Path, old_key: str, values, different_key: str | None = None) -> Path:
    """构造一个带 `_encrypted` 列的临时库，用指定密钥写入密文。"""
    from app.core.secret_store import _normalize_fernet_key
    from cryptography.fernet import Fernet

    db_path = tmp_path / "ops.db"
    connection = sqlite3.connect(str(db_path))
    connection.execute("CREATE TABLE servers (id INTEGER PRIMARY KEY, password TEXT, key_content TEXT)")
    connection.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, password_hash TEXT)")
    connection.execute("INSERT INTO users (password_hash) VALUES ('salt$hash')")
    fernet = Fernet(_normalize_fernet_key(old_key))
    other = Fernet(_normalize_fernet_key(different_key)) if different_key else None
    for index, value in enumerate(values, start=1):
        token = "fernet:" + fernet.encrypt(value.encode()).decode()
        if different_key and index == len(values):
            token = "fernet:" + other.encrypt(value.encode()).decode()
        connection.execute(
            "INSERT INTO servers (id, password, key_content) VALUES (?, ?, '')", (index, token)
        )
    connection.commit()
    connection.close()
    return db_path


def _prepare_rotation_env(monkeypatch, tmp_path: Path, old_key: str):
    from scripts import rotate_secrets

    env_file = tmp_path / ".env"
    env_file.write_text(f"ENV=local\nSESSION_SECRET=x\nOPS_SECRET_KEY={old_key}\n", encoding="utf-8")
    monkeypatch.setattr(rotate_secrets, "ENV_PATH", env_file)
    monkeypatch.setenv("OPS_SECRET_KEY", old_key)
    return rotate_secrets, env_file


def test_rotate_ops_key_reencrypts_and_updates_env(monkeypatch, tmp_path):
    old_key = secrets.token_urlsafe(48)
    db_path = _make_db(tmp_path, old_key, ["p@ss-1", "p@ss-2"])
    rotate_secrets, env_file = _prepare_rotation_env(monkeypatch, tmp_path, old_key)
    monkeypatch.setenv("OPS_DB_PATH", str(db_path))

    assert rotate_secrets.cmd_rotate_ops_key(SimpleNamespace(apply=True)) == 0

    new_key = None
    for line in env_file.read_text(encoding="utf-8").splitlines():
        if line.startswith("OPS_SECRET_KEY="):
            new_key = line.split("=", 1)[1]
    assert new_key and new_key != old_key
    assert classify_secret(new_key)["status"] == STATUS_OK

    from app.core.secret_store import _normalize_fernet_key
    from cryptography.fernet import Fernet

    fernet = Fernet(_normalize_fernet_key(new_key))
    connection = sqlite3.connect(str(db_path))
    rows = connection.execute("SELECT password FROM servers ORDER BY id").fetchall()
    connection.close()
    assert [fernet.decrypt(row[0][len("fernet:"):].encode()).decode() for row in rows] == ["p@ss-1", "p@ss-2"]

    backups = list(tmp_path.glob("ops.db.bak-*"))
    env_backups = list(tmp_path.glob(".env.bak-*"))
    assert backups and env_backups, "必须同时备份数据库与 .env"


def test_rotate_ops_key_dry_run_changes_nothing(monkeypatch, tmp_path):
    old_key = secrets.token_urlsafe(48)
    db_path = _make_db(tmp_path, old_key, ["p@ss-1"])
    rotate_secrets, env_file = _prepare_rotation_env(monkeypatch, tmp_path, old_key)
    monkeypatch.setenv("OPS_DB_PATH", str(db_path))
    before = db_path.read_bytes()

    assert rotate_secrets.cmd_rotate_ops_key(SimpleNamespace(apply=False)) == 0
    assert db_path.read_bytes() == before
    assert f"OPS_SECRET_KEY={old_key}" in env_file.read_text(encoding="utf-8")
    assert not list(tmp_path.glob("ops.db.bak-*"))


def test_rotate_ops_key_aborts_when_old_key_cannot_decrypt(monkeypatch, tmp_path):
    old_key = secrets.token_urlsafe(48)
    db_path = _make_db(tmp_path, old_key, ["p@ss-1", "p@ss-2"], different_key=secrets.token_urlsafe(48))
    rotate_secrets, env_file = _prepare_rotation_env(monkeypatch, tmp_path, old_key)
    monkeypatch.setenv("OPS_DB_PATH", str(db_path))
    before = db_path.read_bytes()

    assert rotate_secrets.cmd_rotate_ops_key(SimpleNamespace(apply=True)) == 2
    assert db_path.read_bytes() == before, "解密失败时必须中止且不改库"
    assert not list(tmp_path.glob("ops.db.bak-*"))


def test_rotate_session_secret_writes_new_value_and_backup(monkeypatch, tmp_path):
    rotate_secrets, env_file = _prepare_rotation_env(monkeypatch, tmp_path, secrets.token_urlsafe(48))
    env_file.write_text(
        f"ENV=local\nSESSION_SECRET={HISTORICAL_SESSION_PLACEHOLDER}\nOPS_SECRET_KEY=x\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("SESSION_SECRET", HISTORICAL_SESSION_PLACEHOLDER)

    assert rotate_secrets.cmd_rotate_session_secret(SimpleNamespace(apply=False)) == 0
    assert HISTORICAL_SESSION_PLACEHOLDER in env_file.read_text(encoding="utf-8")

    assert rotate_secrets.cmd_rotate_session_secret(SimpleNamespace(apply=True)) == 0
    content = env_file.read_text(encoding="utf-8")
    value = [line for line in content.splitlines() if line.startswith("SESSION_SECRET=")][0].split("=", 1)[1]
    assert value != HISTORICAL_SESSION_PLACEHOLDER
    assert classify_secret(value)["status"] == STATUS_OK
    assert list(tmp_path.glob(".env.bak-*"))


def test_encrypt_plaintext_encrypts_legacy_values(monkeypatch, tmp_path):
    key = secrets.token_urlsafe(48)
    db_path = tmp_path / "ops.db"
    connection = sqlite3.connect(str(db_path))
    connection.execute("CREATE TABLE servers (id INTEGER PRIMARY KEY, password TEXT, key_content TEXT)")
    connection.execute("INSERT INTO servers (id, password, key_content) VALUES (1, 'plain-secret', '')")
    connection.commit()
    connection.close()

    rotate_secrets, _ = _prepare_rotation_env(monkeypatch, tmp_path, key)
    monkeypatch.setenv("OPS_DB_PATH", str(db_path))

    assert rotate_secrets.cmd_encrypt_plaintext(SimpleNamespace(apply=True)) == 0
    connection = sqlite3.connect(str(db_path))
    stored = connection.execute("SELECT password FROM servers WHERE id = 1").fetchone()[0]
    connection.close()
    assert stored.startswith("fernet:")

    from app.core.secret_store import _normalize_fernet_key
    from cryptography.fernet import Fernet

    fernet = Fernet(_normalize_fernet_key(key))
    assert fernet.decrypt(stored[len("fernet:"):].encode()).decode() == "plain-secret"
