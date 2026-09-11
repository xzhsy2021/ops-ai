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
from typing import Any, Mapping

from app.core.config import QCLAW_APPROVAL_SIGNING_KEY, QCLAW_APPROVAL_TTL_SECONDS
from app.services.message_context import (
    MessageContext,
    normalize_conversation_binding,
    normalize_identity,
)


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
    # 消息里被命中的服务名（可能多个）。批量发版场景下调用方据此逐个建 step。
    matched_services: tuple[str, ...] = ()


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
    """从系统配置中提取 message_routing（仅系统级，服务级已收敛）。"""
    routing = system_or_service.get("message_routing")
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
        # Matrix 房间 ID 以 '!' 开头，不是用户身份；防止将房间误配为审批人。
        if identity["channel"] == "matrix" and identity["sender_id"].startswith("!"):
            raise ValueError(
                f"approvers[{index}]: matrix sender_id must be a user (@...), "
                f"got room ID {identity['sender_id']!r}; rooms must go in message_routing.rooms"
            )
        key = (
            identity["channel"],
            identity["channel_account_id"],
            identity["sender_id"],
        )
        if key not in seen:
            seen.add(key)
            result.append(identity)
    return result


def normalize_routing_rooms(value: Any) -> list[dict[str, str]]:
    """Normalize structured conversation bindings for message_routing.rooms."""
    if value is None:
        raise ValueError("rooms must be a list")
    if not isinstance(value, list):
        raise ValueError("rooms must be a list")
    result: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for index, item in enumerate(value):
        try:
            binding = normalize_conversation_binding(item)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"rooms[{index}]: {exc}") from exc
        key = (
            binding["channel"],
            binding["channel_account_id"],
            binding["conversation_id"],
        )
        if key not in seen:
            seen.add(key)
            result.append(binding)
    return result


def normalize_message_routing_config(value: Any) -> dict[str, Any]:
    """Validate and normalize one complete persisted message-routing object."""
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ValueError("message_routing must be an object")

    allowed_keys = {"enabled", "aliases", "keywords", "priority", "approvers", "rooms"}
    unsupported = sorted(set(value) - allowed_keys)
    if unsupported:
        raise ValueError(f"unsupported message_routing keys: {', '.join(unsupported)}")

    normalized: dict[str, Any] = {}
    if "enabled" in value:
        enabled = value["enabled"]
        if type(enabled) is not bool:
            raise ValueError("enabled must be a boolean")
        normalized["enabled"] = enabled

    def normalize_string_list(field: str) -> list[str]:
        items = value[field]
        if not isinstance(items, list):
            raise ValueError(f"{field} must be a list")
        result: list[str] = []
        seen: set[str] = set()
        for index, item in enumerate(items):
            if not isinstance(item, str) or not item.strip():
                raise ValueError(f"{field}[{index}] must be a nonblank string")
            normalized_item = item.strip()
            if normalized_item not in seen:
                seen.add(normalized_item)
                result.append(normalized_item)
        return result

    for field in ("aliases", "keywords"):
        if field in value:
            normalized[field] = normalize_string_list(field)

    if "priority" in value:
        priority = value["priority"]
        if type(priority) is not int:
            raise ValueError("priority must be an integer")
        if not 0 <= priority <= 1000:
            raise ValueError("priority must be between 0 and 1000")
        normalized["priority"] = priority

    if "approvers" in value:
        normalized["approvers"] = normalize_routing_approvers(value["approvers"])

    if "rooms" in value:
        normalized["rooms"] = normalize_routing_rooms(value["rooms"])
    return normalized


def _approver_sort_key(identity: dict[str, str]) -> tuple[str, str, str]:
    return (
        identity["channel"],
        identity["channel_account_id"],
        identity["sender_id"],
    )


def _room_sort_key(binding: dict[str, str]) -> tuple[str, str, str]:
    return (
        binding["channel"],
        binding["channel_account_id"],
        binding["conversation_id"],
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
            "rooms": sorted(
                normalize_routing_rooms(routing.get("rooms", [])),
                key=_room_sort_key,
            ),
        }
        normalized.append(entry)
    normalized.sort(key=lambda item: item["name"])
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
) -> tuple[dict[str, str], ...]:
    """从系统级路由配置提取授权审批人。

    服务级已收敛到系统级，只从系统级 approvers 解析。
    配置在边界统一规范化；无效身份会抛错并由调用方 fail closed。
    """
    if not isinstance(routing, dict):
        raise ValueError("system message_routing must be an object")
    return tuple(normalize_routing_approvers(routing.get("approvers", [])))


def _extract_rooms(routing: dict) -> tuple[dict[str, str], ...]:
    """从系统级路由配置提取授权房间（会话）绑定。

    服务级不单独配置房间，继承系统级 rooms。配置在边界统一规范化。
    """
    if not isinstance(routing, dict):
        raise ValueError("system message_routing must be an object")
    return tuple(normalize_routing_rooms(routing.get("rooms", [])))


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
    active_systems.sort(key=lambda item: str(item[0].get("name", "")))

    # 1. 精确匹配系统规范名
    system_name_matches = [
        (sys_cfg.get("name", ""), routing)
        for sys_cfg, routing in active_systems
        if sys_cfg.get("name", "")
        and normalize_text(sys_cfg.get("name", "")) == normalized_msg
    ]
    if len(system_name_matches) == 1:
        sys_name, routing = system_name_matches[0]
        return RoutingDecision(
            outcome=RoutingOutcome.RESOLVED,
            system_name=sys_name,
            matched_by="system_name",
            routing_config_revision=revision,
            approvers=_extract_approvers(routing),
        )
    if len(system_name_matches) > 1:
        return RoutingDecision(
            outcome=RoutingOutcome.AMBIGUOUS,
            candidates=tuple(sorted(item[0] for item in system_name_matches)),
            routing_config_revision=revision,
        )

    # 2. 精确匹配系统别名
    system_alias_matches = []
    for sys_cfg, routing in active_systems:
        if any(
            alias and normalize_text(alias) == normalized_msg
            for alias in routing.get("aliases", [])
        ):
            system_alias_matches.append((sys_cfg.get("name", ""), routing))
    if len(system_alias_matches) == 1:
        sys_name, routing = system_alias_matches[0]
        return RoutingDecision(
            outcome=RoutingOutcome.RESOLVED,
            system_name=sys_name,
            matched_by="system_alias",
            routing_config_revision=revision,
            approvers=_extract_approvers(routing),
        )
    if len(system_alias_matches) > 1:
        return RoutingDecision(
            outcome=RoutingOutcome.AMBIGUOUS,
            candidates=tuple(sorted(item[0] for item in system_alias_matches)),
            routing_config_revision=revision,
        )

    # 3. 唯一服务名，返回其父系统
    service_matches = []  # (system_name, service_name, matched_by, sys_routing)
    for sys_cfg, routing in active_systems:
        for svc in sys_cfg.get("services", []):
            svc_name = svc.get("name", "")
            # 服务规范名
            if svc_name and normalize_text(svc_name) == normalized_msg:
                service_matches.append((sys_cfg.get("name", ""), svc_name, "service_name", routing))
                continue

    service_matches.sort(key=lambda item: (item[0], item[1], item[2]))
    if len(service_matches) == 1:
        sys_name, svc_name, matched_by, sys_routing = service_matches[0]
        return RoutingDecision(
            outcome=RoutingOutcome.RESOLVED,
            system_name=sys_name,
            service_name=svc_name,
            matched_by=matched_by,
            routing_config_revision=revision,
            approvers=_extract_approvers(sys_routing),
            matched_services=(svc_name,),
        )
    if len(service_matches) > 1:
        candidates = tuple(sorted(f"{s}/{sv}" for s, sv, *_ in service_matches))
        return RoutingDecision(
            outcome=RoutingOutcome.AMBIGUOUS,
            candidates=candidates,
            routing_config_revision=revision,
        )

    # 4. 唯一最高优先级关键词匹配
    keyword_matches = []  # (system_name, service_name, priority, matched_by, sys_routing)
    for sys_cfg, routing in active_systems:
        priority = routing.get("priority", 0)
        # 系统级关键词：取命中的最长关键词（更具体，避免 'trader' 抢在
        # 'crypto-trader-web' 之前命中）。
        #
        # 服务绑定规则（2026-09-11 修正）：只有当本消息**恰好命中一个**服务规范名时
        # 才签发服务级票据。批量发版场景（如「拉取 system, transaction, strategy 最新
        # 镜像」）会同时命中多个服务名，旧规则按「最长关键词」钉住其中一个（transaction），
        # 导致调用方一旦按自己真正想操作的服务传 service_name 就被判为「绑定到另一个
        # 目标」而 403。命中多个时改为签发系统级票据，并在 matched_services 里回传
        # 识别到的服务清单，由调用方按服务逐个建 step。
        matched_keywords = [
            keyword
            for keyword in routing.get("keywords", [])
            if keyword and normalize_text(keyword) in normalized_msg
        ]
        matched_kw = ""
        for keyword in matched_keywords:
            if len(keyword) > len(matched_kw):
                matched_kw = keyword
        if matched_kw:
            hit_norms = {normalize_text(keyword) for keyword in matched_keywords}
            hit_services = sorted(
                {
                    svc.get("name", "")
                    for svc in sys_cfg.get("services", [])
                    if svc.get("name") and normalize_text(svc.get("name", "")) in hit_norms
                }
            )
            if len(hit_services) >= 2:
                keyword_matches.append(
                    (sys_cfg.get("name", ""), None, priority,
                     "system_keyword_multi_service", routing, tuple(hit_services))
                )
            elif len(hit_services) == 1:
                keyword_matches.append(
                    (sys_cfg.get("name", ""), hit_services[0], priority,
                     "system_keyword_service", routing, tuple(hit_services))
                )
            else:
                keyword_matches.append(
                    (sys_cfg.get("name", ""), None, priority,
                     "system_keyword", routing, ())
                )

    if not keyword_matches:
        return RoutingDecision(
            outcome=RoutingOutcome.UNMATCHED,
            routing_config_revision=revision,
        )

    # 找最高优先级
    max_priority = max(m[2] for m in keyword_matches)
    top_matches = sorted(
        (m for m in keyword_matches if m[2] == max_priority),
        key=lambda item: (item[0], item[1] or "", item[3]),
    )

    if len(top_matches) == 1:
        sys_name, svc_name, _, matched_by, sys_routing, matched_services = top_matches[0]
        return RoutingDecision(
            outcome=RoutingOutcome.RESOLVED,
            system_name=sys_name,
            service_name=svc_name,
            matched_by=matched_by,
            routing_config_revision=revision,
            approvers=_extract_approvers(sys_routing),
            matched_services=matched_services,
        )

    # 同优先级多匹配 → AMBIGUOUS
    candidates = tuple(sorted(f"{s}/{sv}" if sv else s for s, sv, *_ in top_matches))
    return RoutingDecision(
        outcome=RoutingOutcome.AMBIGUOUS,
        candidates=candidates,
        routing_config_revision=revision,
    )


# ──────────────────────────────────────────────────────────────
# 签名票据
# ──────────────────────────────────────────────────────────────

def _get_signing_key() -> str:
    """Return a configured high-entropy signing key or fail closed."""
    key = str(QCLAW_APPROVAL_SIGNING_KEY or "").strip()
    insecure_values = {
        "changeme",
        "change-me",
        "default",
        "dev-fallback-key-do-not-use-in-production",
        "password",
        "secret",
        "test",
    }
    if len(key) < 32 or key.lower() in insecure_values or len(set(key)) < 8:
        raise RuntimeError(
            "APPROVAL_SIGNING_KEY must be configured with at least 32 non-trivial characters"
        )
    return key


def _strict_message_context(
    value: MessageContext | Mapping[str, Any],
) -> MessageContext:
    if isinstance(value, MessageContext):
        return value
    if isinstance(value, Mapping):
        return MessageContext.from_dict(value)
    raise ValueError("message_context must be a MessageContext or generic object")


def issue_ticket(
    message_context: MessageContext | Mapping[str, Any],
    system_name: str,
    service_name: str | None,
    routing_config_revision: str,
) -> RoutingTicket:
    """签发 HMAC-SHA256 签名的路由票据，15 分钟有效。"""
    context = _strict_message_context(message_context)
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


def _decode_ticket_payload(ticket_str: str) -> dict | None:
    """验签并解码路由票据 payload；签名无效/格式非法返回 None。

    供校验方在未显式传 service_name 时读取票据自身绑定的目标服务名。
    """
    if not ticket_str or "." not in ticket_str:
        return None
    parts = ticket_str.rsplit(".", 1)
    if len(parts) != 2:
        return None
    payload_b64, sig = parts
    try:
        key = _get_signing_key().encode("utf-8")
        expected_sig = hmac.new(key, payload_b64.encode("ascii"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected_sig):
            return None
        payload_json = base64.urlsafe_b64decode(payload_b64.encode("ascii")).decode("utf-8")
        payload = json.loads(payload_json)
        return payload if isinstance(payload, dict) else None
    except Exception:
        return None


def verify_ticket(
    ticket_str: str,
    expected_message_context: MessageContext | Mapping[str, Any],
    expected_system_name: str,
    expected_service_name: str | None,
    expected_revision: str | None = None,
) -> bool:
    """验证路由票据：签名、过期时间、绑定字段。

    expected_revision 为 None 时跳过对 payload.routing_config_revision 的比对。
    票据校验通过消息内容、目标系统/服务与签名保证有效性；revision 是全局路由配置
    快照，任何系统的路由字段变更都会使其变化，硬比对会让在途票据在配置变动时集体
    失效。prepare 端已用「当前配置」重新校验房间作用域与审批人，因此跳过 revision
    比对不削弱授权边界，反而让授权遵循最新配置。
    """
    try:
        context = _strict_message_context(expected_message_context)
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
    if not isinstance(payload, dict):
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

    # 验证绑定字段（message/system/service 始终硬校验）
    checks = [
        ("message_context", payload.get("message_context") == context.to_dict()),
        ("system_name", payload.get("system_name") == expected_system_name),
        ("service_name", payload.get("service_name") == expected_service_name),
    ]
    # revision 为全局配置快照：仅当调用方显式要求时才比对（严格模式），默认跳过，
    # 避免路由配置改动导致在途票据整体失效。
    if expected_revision is not None:
        checks.append(
            ("revision", payload.get("routing_config_revision") == expected_revision)
        )
    for name, ok in checks:
        if not ok:
            return False

    return True
