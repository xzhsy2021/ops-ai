"""密钥强度策略：统一判定 SESSION_SECRET / OPS_SECRET_KEY / APPROVAL_SIGNING_KEY。

## 背景（2026-09-12 复盘发现的真实缺陷）

运行实例 `.env` 中的 `SESSION_SECRET` 与 `OPS_SECRET_KEY` 与仓库内 `.env.example`
**逐字节相同**，即"公开可预测"的密钥：

- `SESSION_SECRET` 用于签发会话令牌（`app/core/auth_v2.py`）。泄漏 ⇒ 任何人可离线
  伪造任意用户（含 admin）的会话令牌，直接接管平台（含生产发布与终端执行）。
- `OPS_SECRET_KEY` 用于加密库内凭据（服务器 SSH 密码/私钥、数据库连接、跳板机）。
  泄漏 ⇒ 拿到数据库即可解密全部被管服务器凭据。

而当时的可观测性完全看不出来：`diagnostics._secret_key_check` 与
`system_health` 只判断"是否配置 + 长度 ≥ 24"，对占位值一律返回 `ok`
（"OPS_SECRET_KEY 已配置"/"密钥已配置"），`recommendations` 只在 `warn` 时才提示，
生产模式的唯一硬校验是 `SESSION_SECRET` **缺失**才报错。

本模块把四种状态统一起来，供会话签名、凭据加密、诊断/健康检查/建议、启动自检复用：

- `missing`  ：未配置
- `insecure` ：常见占位值，或与仓库示例文件中的值完全相同（公开可预测）
- `weak`     ：长度不足或字符多样性不足
- `ok`       ：强度合格
"""
from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional

logger = logging.getLogger(__name__)

STATUS_MISSING = "missing"
STATUS_INSECURE = "insecure"
STATUS_WEAK = "weak"
STATUS_OK = "ok"

MIN_SECRET_LENGTH = 32
MIN_DISTINCT_CHARS = 8

# 被跟踪的密钥名（APPROVAL_SIGNING_KEY 允许 QCLAW_ 前缀别名）
TRACKED_SECRETS = ("SESSION_SECRET", "OPS_SECRET_KEY", "APPROVAL_SIGNING_KEY")
APPROVAL_KEY_ALIASES = ("APPROVAL_SIGNING_KEY", "QCLAW_APPROVAL_SIGNING_KEY")

# 与 app/services/qclaw_routing.py::_get_signing_key、scripts/preflight_start_check.py 的
# 既有黑名单保持一致，避免同一项目里出现两套判定标准。
INSECURE_EXACT_VALUES = {
    "changeme",
    "change-me",
    "change_me",
    "default",
    "password",
    "secret",
    "test",
    "dev",
    "dev-fallback-key-do-not-use-in-production",
}

# 子串命中即判定为占位/示例值：历史 .env 与 .env.example 使用 "change-me-to-a-…" 这类值，
# 仅靠"精确等于 blacklist"会漏判（这正是缺陷根源）。
INSECURE_MARKERS = (
    "change-me",
    "change_me",
    "changeme",
    "please-change",
    "please-generate",
    "replace-me",
    "replace_me",
    "your-secret",
    "your_secret",
    "yoursecret",
    "your-key",
    "your_key",
    "placeholder",
    "do-not-use-in-production",
    "dev-fallback",
    "<set",
    "<your",
    "todo:",
)

# 仓库内随源码提交的示例 env 文件：其中出现的任何值都属于"公开可预测"
EXAMPLE_ENV_FILES = (
    ".env.example",
    ".env.local.example",
    ".env.docker.example",
    ".env.windows.example",
)

_EXAMPLE_ENV_CACHE: Optional[Dict[str, str]] = None


def is_production(environ: Optional[Mapping[str, str]] = None) -> bool:
    """与 app/core/secret_store.py 原判定保持一致（ENV 优先，其次 APP_ENV）。"""
    env: Mapping[str, str] = os.environ if environ is None else environ
    raw = env.get("ENV")
    if raw is None:
        raw = env.get("APP_ENV", "development")
    return str(raw).strip().lower() in {"prod", "production"}


def rotate_hint() -> str:
    """生成合格密钥的命令（可复制执行）。"""
    return 'python -c "import secrets; print(secrets.token_urlsafe(48))"'


def secret_fingerprint(value: str) -> str:
    """用于日志/报告的安全指纹（不泄漏密钥本身）。"""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def example_env_values(files: Iterable[str] = EXAMPLE_ENV_FILES) -> Dict[str, str]:
    """返回示例 env 文件中出现过的 ``文件名:KEY`` -> 值（非空值）。"""
    global _EXAMPLE_ENV_CACHE
    if _EXAMPLE_ENV_CACHE is None:
        values: Dict[str, str] = {}
        root = repo_root()
        for name in files:
            try:
                content = (root / name).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for line in content.splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, raw = line.partition("=")
                value = raw.strip().strip('"').strip("'")
                if value:
                    values[f"{name}:{key.strip()}"] = value
        _EXAMPLE_ENV_CACHE = values
    return _EXAMPLE_ENV_CACHE


def is_documented_example_value(value: Optional[str]) -> bool:
    """该值是否与仓库示例文件中的某个值完全相同（= 已随源码公开）。"""
    text = (value or "").strip()
    if not text:
        return False
    return text in set(example_env_values().values())


def classify_secret(value: Optional[str], *, name: str = "") -> Dict[str, Any]:
    """判定单个密钥的强度，返回带原因的结构（不包含密钥明文）。"""
    text = (value or "").strip()
    info: Dict[str, Any] = {
        "name": name,
        "length": len(text),
        "distinct_chars": len(set(text)),
        "fingerprint": secret_fingerprint(text) if text else "",
        "documented_example": False,
    }
    if not text:
        return {**info, "status": STATUS_MISSING, "reason": "未配置"}

    documented = is_documented_example_value(text)
    info["documented_example"] = documented
    lower = text.lower()

    if documented:
        return {
            **info,
            "status": STATUS_INSECURE,
            "reason": "与仓库示例文件（.env*.example）中的值逐字节相同，属于公开可预测的密钥，必须更换",
        }
    if lower in INSECURE_EXACT_VALUES or any(marker in lower for marker in INSECURE_MARKERS):
        return {**info, "status": STATUS_INSECURE, "reason": "是常见占位/示例值，必须更换"}
    if len(text) < MIN_SECRET_LENGTH:
        return {**info, "status": STATUS_WEAK, "reason": f"长度 {len(text)} 少于 {MIN_SECRET_LENGTH} 字符"}
    if len(set(text)) < MIN_DISTINCT_CHARS:
        return {**info, "status": STATUS_WEAK, "reason": f"字符多样性不足（仅 {len(set(text))} 种不同字符）"}
    return {**info, "status": STATUS_OK, "reason": "强度合格"}


def is_secret_ok(value: Optional[str]) -> bool:
    return classify_secret(value)["status"] == STATUS_OK


def resolve_secret(name: str, environ: Optional[Dict[str, str]] = None) -> str:
    """读取密钥值；APPROVAL_SIGNING_KEY 兼容 QCLAW_ 前缀别名。"""
    env = os.environ if environ is None else environ
    if name == "APPROVAL_SIGNING_KEY":
        for alias in APPROVAL_KEY_ALIASES:
            value = str(env.get(alias) or "").strip()
            if value:
                return value
        return ""
    return str(env.get(name) or "").strip()


def enforce_secret_strength(
    name: str,
    value: Optional[str],
    *,
    production: Optional[bool] = None,
    environ: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """生产环境对不合格密钥 fail-closed（拒绝启动），非生产环境记录安全告警。"""
    info = classify_secret(value, name=name)
    if info["status"] == STATUS_OK:
        return info
    message = f"{name} 配置不安全（{info['reason']}）。更换命令：{rotate_hint()}"
    if production is None:
        production = is_production()
    if production:
        raise RuntimeError("拒绝启动：" + message)
    logger.error("安全告警：%s", message)
    return info


def secret_key_report(environ: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """汇总所有被跟踪密钥的状态，供诊断/健康检查/建议使用。"""
    env = dict(os.environ) if environ is None else environ
    keys = {name: classify_secret(resolve_secret(name, env), name=name) for name in TRACKED_SECRETS}
    insecure = [n for n, i in keys.items() if i["status"] == STATUS_INSECURE]
    weak = [n for n, i in keys.items() if i["status"] == STATUS_WEAK]
    missing = [n for n, i in keys.items() if i["status"] == STATUS_MISSING]
    return {
        "production": is_production(),
        "keys": keys,
        "insecure": insecure,
        "weak": weak,
        "missing": missing,
        "ok": not insecure and not weak,
    }


def insecure_secret_names(environ: Optional[Dict[str, str]] = None) -> list:
    report = secret_key_report(environ)
    return list(report["insecure"]) + list(report["weak"])
