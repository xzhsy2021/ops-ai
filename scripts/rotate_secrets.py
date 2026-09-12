#!/usr/bin/env python3
"""OPS 密钥体检与轮换工具（SESSION_SECRET / OPS_SECRET_KEY）。

## 为什么需要它

运行实例 `.env` 中的 `SESSION_SECRET` 与 `OPS_SECRET_KEY` 曾与仓库 `.env.example`
逐字节相同 —— 即"公开可预测"：

- `SESSION_SECRET` 泄漏 ⇒ 可离线伪造任意用户（含 admin）会话令牌，直接接管平台；
- `OPS_SECRET_KEY` 泄漏 ⇒ 拿到数据库即可解密服务器 SSH 凭据、数据库连接等。

轮换 `OPS_SECRET_KEY` 不能只改 `.env`：库内已加密的值必须用旧密钥解密、再用新密钥
重加密，否则凭据全部失效。本脚本负责这条链路。

## 用法（默认 dry-run，加 --apply 才写入）

    python scripts/rotate_secrets.py --check
    python scripts/rotate_secrets.py --rotate-session-secret --apply
    python scripts/rotate_secrets.py --rotate-ops-key --apply
    python scripts/rotate_secrets.py --encrypt-plaintext --apply

`--rotate-ops-key` 的顺序：旧密钥解密校验 → 备份数据库 → 单事务重加密 → 复读校验 →
写回 `.env`（含 `.env` 备份）。
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import os
import secrets
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.secrets_policy import (  # noqa: E402
    classify_secret,
    rotate_hint,
    secret_key_report,
)

ENV_PATH = ROOT / ".env"
FERNET_PREFIX = "fernet:"
ENCRYPTED_SUFFIX = "_encrypted"
# 明文存储的凭据列（历史遗留）：与 app/db/models.py 中的字段一致
PLAIN_SECRET_COLUMNS = {"password", "key_content"}
TRACKED_COLUMNS = ("SESSION_SECRET", "OPS_SECRET_KEY")


def log(message: str = "") -> None:
    print(message, flush=True)


# ────────────────────────────── .env 读写 ──────────────────────────────


def parse_env_file(path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, raw = stripped.partition("=")
        values[key.strip()] = raw.strip().strip('"').strip("'")
    return values


def load_env_into_process(path: Path = ENV_PATH) -> Dict[str, str]:
    """把 .env 的值注入 os.environ（不覆盖已存在的进程变量，与启动脚本一致）。"""
    values = parse_env_file(path)
    for key, value in values.items():
        os.environ.setdefault(key, value)
    return values


def write_env_value(path: Path, key: str, value: str) -> Optional[Path]:
    """替换（或追加）`.env` 中的某个键，返回备份文件路径。"""
    if not path.exists():
        raise SystemExit(f"未找到 {path}；请先创建 .env 再轮换密钥。")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = path.with_name(f"{path.name}.bak-{stamp}")
    backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")

    lines = path.read_text(encoding="utf-8").splitlines()
    replaced = False
    for index, line in enumerate(lines):
        if line.strip().startswith(f"{key}=") or line.strip() == f"{key}=":
            lines[index] = f"{key}={value}"
            replaced = True
            break
    if not replaced:
        lines.append(f"{key}={value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return backup


# ────────────────────────────── 数据库访问 ──────────────────────────────


def database_path() -> Path:
    """与 app.core.config.get_database_path 保持一致（此处不引入 app.db 依赖）。"""
    explicit = os.getenv("OPS_DB_PATH")
    if explicit:
        return Path(explicit).resolve()
    app_data = os.getenv("APP_DATA_DIR") or str(ROOT / "data")
    return (Path(app_data) / "ops.db").resolve()


def normalize_fernet_key(raw_key: str) -> bytes:
    """与 app.core.secret_store._normalize_fernet_key 完全一致的派生方式。"""
    from cryptography.fernet import Fernet

    key_bytes = raw_key.encode("utf-8")
    try:
        Fernet(key_bytes)
        return key_bytes
    except Exception:
        return base64.urlsafe_b64encode(hashlib.sha256(key_bytes).digest())


def fernet_for(raw_key: str):
    from cryptography.fernet import Fernet

    return Fernet(normalize_fernet_key(raw_key))


def connect(db_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(str(db_path))
    connection.row_factory = sqlite3.Row
    return connection


def secret_columns(connection: sqlite3.Connection) -> List[Tuple[str, str]]:
    """返回 (表名, 列名) 形式的敏感列清单。"""
    found: List[Tuple[str, str]] = []
    tables = [
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
    ]
    for table in tables:
        try:
            columns = [row[1] for row in connection.execute(f"PRAGMA table_info({table})").fetchall()]
        except sqlite3.Error:
            continue
        for column in columns:
            if column.endswith(ENCRYPTED_SUFFIX) or column in PLAIN_SECRET_COLUMNS:
                found.append((table, column))
    return found


def collect_secret_rows(connection: sqlite3.Connection) -> Dict[Tuple[str, str], List[Tuple[int, str]]]:
    rows: Dict[Tuple[str, str], List[Tuple[int, str]]] = {}
    for table, column in secret_columns(connection):
        try:
            result = connection.execute(
                f"SELECT rowid, {column} FROM {table} "
                f"WHERE {column} IS NOT NULL AND {column} != ''"
            ).fetchall()
        except sqlite3.Error:
            continue
        if result:
            rows[(table, column)] = [(int(r[0]), str(r[1])) for r in result]
    return rows


def summarise_rows(rows: Dict[Tuple[str, str], List[Tuple[int, str]]]) -> Dict[str, int]:
    encrypted = plain = 0
    for values in rows.values():
        for _, value in values:
            if value.startswith(FERNET_PREFIX):
                encrypted += 1
            else:
                plain += 1
    return {"columns": len(rows), "encrypted": encrypted, "plaintext": plain}


def backup_database(db_path: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = db_path.with_name(f"{db_path.name}.bak-{stamp}")
    source = sqlite3.connect(str(db_path))
    target = sqlite3.connect(str(backup))
    try:
        source.backup(target)  # 一致性快照（含 WAL 内容）
    finally:
        target.close()
        source.close()
    return backup


# ────────────────────────────── 子命令 ──────────────────────────────


def cmd_check(args) -> int:
    values = load_env_into_process()
    report = secret_key_report()
    log("═" * 66)
    log("OPS 密钥体检")
    log(f"  .env: {ENV_PATH}（{'存在' if ENV_PATH.exists() else '缺失'}）  ENV={values.get('ENV', os.getenv('ENV', '未设置'))}")
    log("─" * 66)
    problems = False
    for name in TRACKED_COLUMNS:
        info = report["keys"][name]
        flag = "OK  " if info["status"] == "ok" else "问题"
        if info["status"] != "ok":
            problems = True
        log(f"  [{flag}] {name:16} 状态={info['status']:9} 长度={info['length']:3} "
            f"指纹={info['fingerprint'] or '-':12} {info['reason']}")
    approval = report["keys"]["APPROVAL_SIGNING_KEY"]
    log(f"  [{'OK  ' if approval['status'] == 'ok' else '问题'}] APPROVAL_SIGNING_KEY "
        f"状态={approval['status']:9} {approval['reason']}")

    db_path = database_path()
    log("─" * 66)
    log(f"数据库: {db_path}（{'存在' if db_path.exists() else '不存在'}）")
    if db_path.exists():
        connection = connect(db_path)
        try:
            rows = collect_secret_rows(connection)
            stats = summarise_rows(rows)
            log(f"  库内敏感列: {stats['columns']} 个有数据；已加密 {stats['encrypted']} 条，"
                f"未加密(明文) {stats['plaintext']} 条")
            for (table, column), values_ in rows.items():
                enc = sum(1 for _, v in values_ if v.startswith(FERNET_PREFIX))
                log(f"    {table}.{column}: 共 {len(values_)} 条（加密 {enc} / 明文 {len(values_) - enc}）")
            if stats["plaintext"]:
                log("  ⚠ 存在明文凭据：建议执行 python scripts/rotate_secrets.py --encrypt-plaintext --apply")
        finally:
            connection.close()

    log("═" * 66)
    if problems:
        log("结论：存在不安全密钥，建议立即轮换：")
        log("  1) python scripts/rotate_secrets.py --rotate-session-secret --apply")
        log("  2) python scripts/rotate_secrets.py --rotate-ops-key --apply")
        log("  3) 重启 OPS 服务，然后用 --check 复核")
        return 2
    log("结论：密钥强度合格。")
    return 0


def cmd_rotate_session_secret(args) -> int:
    load_env_into_process()
    current = os.getenv("SESSION_SECRET") or ""
    info = classify_secret(current, name="SESSION_SECRET")
    log(f"当前 SESSION_SECRET: 状态={info['status']}（{info['reason']}）")
    new_secret = secrets.token_urlsafe(48)
    if not args.apply:
        log("[dry-run] 将生成新的 SESSION_SECRET 并写入 .env（所有登录会话需重新登录）。")
        log(f"[dry-run] 新值长度={len(new_secret)}，指纹={classify_secret(new_secret)['fingerprint']}")
        log("确认无误后加 --apply 执行。")
        return 0
    backup = write_env_value(ENV_PATH, "SESSION_SECRET", new_secret)
    log(f"已写入新的 SESSION_SECRET（长度 {len(new_secret)}）。")
    log(f".env 备份: {backup}")
    log("注意：所有既有登录会话立即失效；重启 OPS 服务后生效。")
    return 0


def cmd_rotate_ops_key(args) -> int:
    load_env_into_process()
    db_path = database_path()
    old_key = os.getenv("OPS_SECRET_KEY") or ""
    old_info = classify_secret(old_key, name="OPS_SECRET_KEY")
    log(f"当前 OPS_SECRET_KEY: 状态={old_info['status']}（{old_info['reason']}，指纹={old_info['fingerprint'] or '-'}）")
    if not db_path.exists():
        raise SystemExit(f"未找到数据库 {db_path}")

    if not old_key:
        # 没有旧密钥：只需为新密钥加密现有明文值
        log("未配置旧 OPS_SECRET_KEY：将直接用新密钥加密库内明文凭据。")
        rows = {}
    else:
        connection = connect(db_path)
        try:
            rows = collect_secret_rows(connection)
        finally:
            connection.close()
        stats = summarise_rows(rows)
        log(f"库内敏感列: {stats['columns']} 个有数据（加密 {stats['encrypted']} / 明文 {stats['plaintext']}）")

    # 1) 旧密钥解密校验（有任何失败立即中止，绝不半途改库）
    old_fernet = fernet_for(old_key) if old_key else None
    decrypted: Dict[Tuple[str, str], List[Tuple[int, str]]] = {}
    failures: List[str] = []
    for (table, column), values in rows.items():
        items: List[Tuple[int, str]] = []
        for rowid, value in values:
            if value.startswith(FERNET_PREFIX):
                if old_fernet is None:
                    failures.append(f"{table}.{column} rowid={rowid}（缺少旧密钥，无法解密）")
                    continue
                try:
                    plain = old_fernet.decrypt(value[len(FERNET_PREFIX):].encode("utf-8")).decode("utf-8")
                except Exception as exc:  # noqa: BLE001 - 需要汇总所有失败行
                    failures.append(f"{table}.{column} rowid={rowid}（{type(exc).__name__}）")
                    continue
                items.append((rowid, plain))
            else:
                items.append((rowid, value))
        if items:
            decrypted[(table, column)] = items

    if failures:
        log("✗ 旧密钥无法解密以下数据，已中止（未做任何修改）：")
        for item in failures[:20]:
            log(f"    - {item}")
        log("  请确认 .env 中的 OPS_SECRET_KEY 与写入这些数据时一致。")
        return 2

    # 2) 生成新密钥并在内存中重加密 + 自校验
    new_key = secrets.token_urlsafe(48)
    new_fernet = fernet_for(new_key)
    originals = {(t, c): {rowid: value for rowid, value in items} for (t, c), items in rows.items()}
    updates: List[Tuple[str, str, int, str]] = []
    plaintext_touched = 0
    for (table, column), items in decrypted.items():
        for rowid, original in items:
            token = FERNET_PREFIX + new_fernet.encrypt(original.encode("utf-8")).decode("utf-8")
            assert new_fernet.decrypt(token[len(FERNET_PREFIX):].encode("utf-8")).decode("utf-8") == original
            updates.append((table, column, rowid, token))
            if not originals.get((table, column), {}).get(rowid, "").startswith(FERNET_PREFIX):
                plaintext_touched += 1

    log(f"将重加密 {len(updates)} 条（其中原本是明文的 {plaintext_touched} 条）；新密钥指纹={classify_secret(new_key)['fingerprint']}")
    if not args.apply:
        log("[dry-run] 未做任何修改。加 --apply 执行（会先备份数据库与 .env）。")
        return 0

    # 3) 备份 → 单事务写入 → 复读校验
    db_backup = backup_database(db_path)
    log(f"数据库已备份: {db_backup}")
    connection = connect(db_path)
    try:
        with connection:  # 单事务：失败自动回滚
            for table, column, rowid, token in updates:
                connection.execute(
                    f"UPDATE {table} SET {column} = ? WHERE rowid = ?", (token, rowid)
                )
        verify_failures: List[str] = []
        for table, column, rowid, _ in updates:
            stored = connection.execute(
                f"SELECT {column} FROM {table} WHERE rowid = ?", (rowid,)
            ).fetchone()
            value = stored[0] if stored else None
            try:
                new_fernet.decrypt(str(value)[len(FERNET_PREFIX):].encode("utf-8"))
            except Exception as exc:  # noqa: BLE001
                verify_failures.append(f"{table}.{column} rowid={rowid}（{type(exc).__name__}）")
    finally:
        connection.close()
    if verify_failures:
        log("✗ 写入后校验失败，请用备份回滚：")
        log(f"    {db_backup}")
        for item in verify_failures[:10]:
            log(f"    - {item}")
        return 2

    env_backup = write_env_value(ENV_PATH, "OPS_SECRET_KEY", new_key)
    log(f"已写入新的 OPS_SECRET_KEY（长度 {len(new_key)}）。")
    log(f".env 备份: {env_backup}")
    log("注意：必须重启 OPS 服务，且不要再使用旧密钥（旧密钥已无法解密库内数据）。")
    return 0


def cmd_encrypt_plaintext(args) -> int:
    load_env_into_process()
    db_path = database_path()
    key = os.getenv("OPS_SECRET_KEY") or ""
    if not key:
        raise SystemExit("未配置 OPS_SECRET_KEY；请先执行 --rotate-session-secret / --rotate-ops-key。")
    fernet = fernet_for(key)

    reader = connect(db_path)
    try:
        rows = collect_secret_rows(reader)
    finally:
        reader.close()

    targets: List[Tuple[str, str, int, str]] = []
    for (table, column), values in rows.items():
        for rowid, value in values:
            if not value.startswith(FERNET_PREFIX):
                token = FERNET_PREFIX + fernet.encrypt(value.encode("utf-8")).decode("utf-8")
                targets.append((table, column, rowid, token))
    if not targets:
        log("未发现明文凭据，无需处理。")
        return 0
    log(f"发现 {len(targets)} 条明文凭据，将使用当前 OPS_SECRET_KEY 加密。")
    if not args.apply:
        log("[dry-run] 未做任何修改。加 --apply 执行。")
        return 0

    backup = backup_database(db_path)
    log(f"数据库已备份: {backup}")
    writer = connect(db_path)
    try:
        with writer:
            for table, column, rowid, token in targets:
                writer.execute(f"UPDATE {table} SET {column} = ? WHERE rowid = ?", (token, rowid))
    finally:
        writer.close()

    verifier = connect(db_path)
    try:
        bad = 0
        for table, column, rowid, _ in targets:
            value = verifier.execute(f"SELECT {column} FROM {table} WHERE rowid = ?", (rowid,)).fetchone()[0]
            try:
                fernet.decrypt(str(value)[len(FERNET_PREFIX):].encode("utf-8"))
            except Exception:  # noqa: BLE001 - 逐条统计失败数
                bad += 1
    finally:
        verifier.close()
    if bad:
        log(f"✗ {bad} 条校验失败，请用备份回滚：{backup}")
        return 2
    log("校验通过：明文凭据已加密。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="OPS 密钥体检与轮换")
    parser.add_argument("--check", action="store_true", help="体检密钥强度与库内密文（只读）")
    parser.add_argument("--rotate-session-secret", action="store_true", help="轮换 SESSION_SECRET（会话签名）")
    parser.add_argument("--rotate-ops-key", action="store_true", help="轮换 OPS_SECRET_KEY 并重加密库内凭据")
    parser.add_argument("--encrypt-plaintext", action="store_true", help="用当前密钥加密库内明文凭据")
    parser.add_argument("--apply", action="store_true", help="真正写入（默认 dry-run）")
    args = parser.parse_args()

    actions = [args.check, args.rotate_session_secret, args.rotate_ops_key, args.encrypt_plaintext]
    if sum(1 for a in actions if a) != 1:
        parser.print_help()
        log(f"\n请且仅选择一个动作。生成随机密钥命令：{rotate_hint()}")
        return 2
    if args.check:
        return cmd_check(args)
    if args.rotate_session_secret:
        return cmd_rotate_session_secret(args)
    if args.rotate_ops_key:
        return cmd_rotate_ops_key(args)
    return cmd_encrypt_plaintext(args)


if __name__ == "__main__":
    raise SystemExit(main())
