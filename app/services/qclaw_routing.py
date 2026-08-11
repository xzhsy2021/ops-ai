"""确定性消息路由和签名票据签发，用于 QClaw 渠道集成。

QClaw 从消息渠道读取消息后，调用此模块将消息确定性路由到
对应的 OPS 系统/服务，并签发一个 15 分钟有效的路由票据。
"""
import base64
import hashlib
import hmac
import json
import secrets
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

from app.core.config import QCLAW_APPROVAL_SIGNING_KEY, QCLAW_APPROVAL_TTL_SECONDS
from app.services.message_context import MessageContext, normalize_identity, normalize_message_context


class RoutingOutcome(str, Enum):
    """路由解析结果"""

    RESOLVED = "RESOLVED"
    UNMATCHED = "UNMATCHED"
    AMBIGUOUS = "AMBIGUOUS"


@dataclass(frozen=True)
class RoutingDecision:
    """路由决策结果"""

    outcome: RoutingOutcome
    system_name: str | None = None
    service_name: str | None = None
    matched_by: str | None = None
    routing_config_revision: str | None = None
    candidates: tuple[str, ...] = ()
    approvers: tuple[dict[str, str], ...] = ()


@dataclass(frozen=True)
class RoutingTicket:
    """签名路由票据"""

    ticket: str
    digest: str
    expires_at: datetime


# ──────────────────────────────────────────────────────────────
# 规范化
# ──────────────────────────────────────────────────────────────

def normalize_text(text: str) -> str:
    """Unicode NFKC 规范化，小写拉丁字母，折叠空白，保留 CJK。"""
    if not text:
        return ""
    # NFKC 规范化（全角→半角等）
    text = unicodedata.normalize("NFKC", text)
    # 小写（只影响拉丁字母，CJK 不受影响）
    text = text.lower()
    # 折叠连续空白为单个空格
    text = " ".join(text.split())
    return text.strip()


def _extract_routing(system_or_service: dict) -> dict:
    """从系统或服务配置中提取 message_routing。"""
    routing = system_or_service.get("message_routing")
    if isinstance(routing, dict):
        return routing
    # 服务级路由在 template_variables.message_routing 下
    tv = system_or_service.get("template_variables", {})
    if isinstance(tv, dict):
        routing = tv.get("message_routing")
        if isinstance(routing, dict):
            return routing
    return {}


def normalize_routing_approvers(value: Any) -> list[dict[str, str]]:
    """Normalize structured approvers and legacy Matrix sender IDs."""
    if value is None:
        raise ValueError("approvers must be a list")
    if not isinstance(value, list):
        raise ValueError("approvers must be a list")
    result: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for index, item in enumerate(value):
        try:
            identity = normalize_identity(item)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"approvers[{index}]: {exc}") from exc
        key = (
            identity["channel"],
            identity["channel_account_id"],
            identity["sender_id"],
        )
        if key not in seen:
            seen.add(key)
            result.append(identity)
    return result


def normalize_message_routing_config(value: Any) -> dict[str, Any]:
    """Normalize one persisted message-routing object without adding defaults."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("message_routing must be an object")
    normalized = dict(value)
    if "approvers" in normalized:
        normalized["approvers"] = normalize_routing_approvers(normalized["approvers"])
    return normalized


def _approver_sort_key(identity: dict[str, str]) -> tuple[str, str, str]:
    return (
        identity["channel"],
        identity["channel_account_id"],
        identity["sender_id"],
    )


def normalize_routing_config(systems: list[dict]) -> str:
    """规范化路由配置 JSON，用于计算 config revision。"""
    normalized = []
    for sys_cfg in systems:
        routing = _extract_routing(sys_cfg)
        if not routing.get("enabled", False):
            continue
        entry = {
            "name": sys_cfg.get("name", ""),
            "aliases": sorted(routing.get("aliases", [])),
            "keywords": sorted(routing.get("keywords", [])),
            "priority": routing.get("priority", 0),
            "approvers": sorted(
                normalize_routing_approvers(routing.get("approvers", [])),
                key=_approver_sort_key,
            ),
        }
        # 服务级路由
        for svc in sys_cfg.get("services", []):
            svc_routing = _extract_routing(svc)
            if svc_routing.get("enabled", False):
                entry.setdefault("services", []).append({
                    "name": svc.get("name", ""),
                    "aliases": sorted(svc_routing.get("aliases", [])),
                    "keywords": sorted(svc_routing.get("keywords", [])),
                    "priority": svc_routing.get("priority", 0),
                    "approvers": sorted(
                        normalize_routing_approvers(svc_routing.get("approvers", [])),
                        key=_approver_sort_key,
                    ),
                })
        normalized.append(entry)
    canonical = json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return canonical


def compute_routing_revision(systems: list[dict]) -> str:
    """计算路由配置 revision 的 SHA-256 摘要。"""
    return hashlib.sha256(normalize_routing_config(systems).encode("utf-8")).hexdigest()


# ──────────────────────────────────────────────────────────────
# 路由解析
# ──────────────────────────────────────────────────────────────

def _extract_approvers(
    routing: dict,
    svc_routing: dict | None = None,
) -> tuple[dict[str, str], ...]:
    """从路由配置提取授权审批人。

    优先使用服务级 approvers（如果非空），否则回退到系统级 approvers。
    配置在边界统一规范化；无效身份会抛错并由调用方 fail closed。
    """
    if not isinstance(routing, dict):
        raise ValueError("system message_routing must be an object")
    system_approvers = normalize_routing_approvers(routing.get("approvers", []))
    if svc_routing is not None:
        if not isinstance(svc_routing, dict):
            raise ValueError("service message_routing must be an object")
        service_approvers = normalize_routing_approvers(
            svc_routing.get("approvers", [])
        )
        if service_approvers:
            return tuple(service_approvers)
    return tuple(system_approvers)


def resolve_message_target(message_text: str, systems: list[dict]) -> RoutingDecision:
    """将消息文本确定性路由到对应的系统/服务。

    解析顺序：
    1. 精确匹配系统规范名
    2. 精确匹配系统别名
    3. 唯一服务名或服务别名，返回其父系统
    4. 唯一最高优先级关键词匹配
    5. 否则 UNMATCHED 或 AMBIGUOUS
    """
    revision = compute_routing_revision(systems)
    normalized_msg = normalize_text(message_text)

    if not normalized_msg:
        return RoutingDecision(
            outcome=RoutingOutcome.UNMATCHED,
            routing_config_revision=revision,
        )

    # 收集启用了路由的系统
    active_systems = []
    for sys_cfg in systems:
        routing = _extract_routing(sys_cfg)
        if routing.get("enabled", False):
            active_systems.append((sys_cfg, routing))

    # 1. 精确匹配系统规范名
    for sys_cfg, routing in active_systems:
        sys_name = sys_cfg.get("name", "")
        if sys_name and normalize_text(sys_name) == normalized_msg:
            return RoutingDecision(
                outcome=RoutingOutcome.RESOLVED,
                system_name=sys_name,
                matched_by="system_name",
                routing_config_revision=revision,
                approvers=_extract_approvers(routing),
            )

    # 2. 精确匹配系统别名
    for sys_cfg, routing in active_systems:
        for alias in routing.get("aliases", []):
            if alias and normalize_text(alias) == normalized_msg:
                return RoutingDecision(
                    outcome=RoutingOutcome.RESOLVED,
                    system_name=sys_cfg.get("name", ""),
                    matched_by="system_alias",
                    routing_config_revision=revision,
                    approvers=_extract_approvers(routing),
                )

    # 3. 唯一服务名或服务别名，返回其父系统
    service_matches = []  # (system_name, service_name, matched_by, sys_routing, svc_routing)
    for sys_cfg, routing in active_systems:
        for svc in sys_cfg.get("services", []):
            svc_routing = _extract_routing(svc)
            svc_name = svc.get("name", "")
            # 服务规范名
            if svc_name and normalize_text(svc_name) == normalized_msg:
                service_matches.append((sys_cfg.get("name", ""), svc_name, "service_name", routing, svc_routing))
                continue
            # 服务别名
            for alias in svc_routing.get("aliases", []):
                if alias and normalize_text(alias) == normalized_msg:
                    service_matches.append((sys_cfg.get("name", ""), svc_name, "service_alias", routing, svc_routing))
                    break

    if len(service_matches) == 1:
        sys_name, svc_name, matched_by, sys_routing, svc_routing = service_matches[0]
        return RoutingDecision(
            outcome=RoutingOutcome.RESOLVED,
            system_name=sys_name,
            service_name=svc_name,
            matched_by=matched_by,
            routing_config_revision=revision,
            approvers=_extract_approvers(sys_routing, svc_routing),
        )
    if len(service_matches) > 1:
        candidates = tuple(f"{s}/{sv}" for s, sv, *_ in service_matches)
        return RoutingDecision(
            outcome=RoutingOutcome.AMBIGUOUS,
            candidates=candidates,
            routing_config_revision=revision,
        )

    # 4. 唯一最高优先级关键词匹配
    keyword_matches = []  # (system_name, service_name, priority, matched_by, sys_routing, svc_routing)
    for sys_cfg, routing in active_systems:
        priority = routing.get("priority", 0)
        for keyword in routing.get("keywords", []):
            if keyword and normalize_text(keyword) in normalized_msg:
                keyword_matches.append((sys_cfg.get("name", ""), None, priority, "system_keyword", routing, None))
                break
        # 服务级关键词
        for svc in sys_cfg.get("services", []):
            svc_routing = _extract_routing(svc)
            svc_priority = svc_routing.get("priority", 0)
            for keyword in svc_routing.get("keywords", []):
                if keyword and normalize_text(keyword) in normalized_msg:
                    keyword_matches.append((sys_cfg.get("name", ""), svc.get("name", ""), svc_priority, "service_keyword", routing, svc_routing))
                    break

    if not keyword_matches:
        return RoutingDecision(
            outcome=RoutingOutcome.UNMATCHED,
            routing_config_revision=revision,
        )

    # 找最高优先级
    max_priority = max(m[2] for m in keyword_matches)
    top_matches = [m for m in keyword_matches if m[2] == max_priority]

    if len(top_matches) == 1:
        sys_name, svc_name, _, matched_by, sys_routing, svc_routing = top_matches[0]
        return RoutingDecision(
            outcome=RoutingOutcome.RESOLVED,
            system_name=sys_name,
            service_name=svc_name,
            matched_by=matched_by,
            routing_config_revision=revision,
            approvers=_extract_approvers(sys_routing, svc_routing),
        )

    # 同优先级多匹配 → AMBIGUOUS
    candidates = tuple(f"{s}/{sv}" if sv else s for s, sv, *_ in top_matches)
    return RoutingDecision(
        outcome=RoutingOutcome.AMBIGUOUS,
        candidates=candidates,
        routing_config_revision=revision,
    )


# ──────────────────────────────────────────────────────────────
# 签名票据
# ──────────────────────────────────────────────────────────────

def _get_signing_key() -> str:
    """获取签名密钥，为空时生成临时密钥（测试场景）。"""
    key = QCLAW_APPROVAL_SIGNING_KEY
    if not key:
        # 临时密钥：进程内一致即可
        key = "dev-fallback-key-do-not-use-in-production"
    return key


def issue_ticket(
    message_context: MessageContext | dict[str, Any],
    system_name: str,
    service_name: str | None,
    routing_config_revision: str,
) -> RoutingTicket:
    """签发 HMAC-SHA256 签名的路由票据，15 分钟有效。"""
    context = normalize_message_context(message_context)
    issued_at = datetime.now(timezone.utc)
    expires_at = issued_at + timedelta(seconds=QCLAW_APPROVAL_TTL_SECONDS)
    nonce = secrets.token_hex(16)

    payload = {
        "message_context": context.to_dict(),
        "system_name": system_name,
        "service_name": service_name,
        "routing_config_revision": routing_config_revision,
        "issued_at": issued_at.isoformat(),
        "expires_at": expires_at.isoformat(),
        "nonce": nonce,
    }

    payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    payload_b64 = base64.urlsafe_b64encode(payload_json.encode("utf-8")).decode("ascii")

    key = _get_signing_key().encode("utf-8")
    sig = hmac.new(key, payload_b64.encode("ascii"), hashlib.sha256).hexdigest()

    ticket = f"{payload_b64}.{sig}"
    digest = hashlib.sha256(ticket.encode("ascii")).hexdigest()

    return RoutingTicket(ticket=ticket, digest=digest, expires_at=expires_at)


def verify_ticket(
    ticket_str: str,
    expected_message_context: MessageContext | dict[str, Any],
    expected_system_name: str,
    expected_service_name: str | None,
    expected_revision: str,
) -> bool:
    """验证路由票据：签名、过期时间、所有绑定字段。"""
    try:
        context = normalize_message_context(expected_message_context)
    except (TypeError, ValueError):
        return False
    if not ticket_str or "." not in ticket_str:
        return False

    parts = ticket_str.rsplit(".", 1)
    if len(parts) != 2:
        return False
    payload_b64, sig = parts

    # 验证签名
    key = _get_signing_key().encode("utf-8")
    expected_sig = hmac.new(key, payload_b64.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected_sig):
        return False

    # 解码 payload
    try:
        payload_json = base64.urlsafe_b64decode(payload_b64.encode("ascii")).decode("utf-8")
        payload = json.loads(payload_json)
    except Exception:
        return False

    # 验证过期
    expires_str = payload.get("expires_at", "")
    try:
        expires_at = datetime.fromisoformat(expires_str)
    except Exception:
        return False
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) > expires_at:
        return False

    # 验证绑定字段
    checks = [
        ("message_context", payload.get("message_context") == context.to_dict()),
        ("system_name", payload.get("system_name") == expected_system_name),
        ("service_name", payload.get("service_name") == expected_service_name),
        ("revision", payload.get("routing_config_revision") == expected_revision),
    ]
    for name, ok in checks:
        if not ok:
            return False

    return True
