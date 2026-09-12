from __future__ import annotations
from app.services.tool_registry import registry
from app.core.secret_store import encrypt_secret, decrypt_secret

# 与 ops.get_connection / ops.list_connections 保持一致的掩码字面量。
_MASK = "***"

@registry.register(
    name="ops.list_ssh_keys",
    description="列出已注册的 SSH 密钥。Use when user asks about available SSH keys. 中文: 查看SSH密钥列表/密钥列表.",
    scopes=["ops:read"],
    risk="low",
    category="ssh_key_read",
    write=False,
    ai_callable=True,
    ai_auto_callable=True,
    data_sensitivity="internal",
    input_schema={
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "description": "最大返回数量", "default": 100},
        },
    },
)
def list_ssh_keys_tool(args, ctx, db):
    from app.db.models import SshKey
    limit = min(int(args.get("limit") or 100), 500)
    rows = db.query(SshKey).order_by(SshKey.name).limit(limit).all()
    return {"items": [{"name": r.name, "description": r.description, "created_at": str(r.created_at) if hasattr(r, 'created_at') else ""} for r in rows], "total": len(rows)}


@registry.register(
    name="ops.get_ssh_key",
    description="获取 SSH 密钥详情（仅元数据与指纹，不回传私钥内容）。中文: 查看SSH密钥详情/密钥信息.",
    scopes=["ops:read"],
    risk="low",
    category="ssh_key_read",
    write=False,
    ai_callable=True,
    ai_auto_callable=False,
    data_sensitivity="sensitive",
    output_masking=True,
    input_schema={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "密钥名称"},
        },
        "required": ["name"],
    },
)
def get_ssh_key_tool(args, ctx, db):
    """返回密钥元数据；私钥内容一律不回传。

    历史缺陷（2026-09-12 复盘第 4 轮修复）：这里曾直接返回 ``decrypt_secret(...)``
    的明文私钥，而本工具只需 ``ops:read`` 作用域、``risk=low`` 且同时出现在
    daily_ops/ai_full 两个 AI 档位中。任何持有基础只读作用域的 AI 客户端都能取走
    全量 SSH 私钥，从而绕开部署/执行通道的全部审批设计；MCP 描述还写成
    "no private key content"，与实际返回相反，调用方无从察觉。
    现在只回传存在性 + 不可逆指纹（用于辨识"部署用的是哪把钥匙"）。
    """
    from app.db.models import SshKey
    from app.core.secrets_policy import secret_fingerprint

    name = args.get("name", "")
    row = db.query(SshKey).filter(SshKey.name == name).first()
    if not row:
        return {"found": False, "name": name}
    has_private_key = bool(row.private_key_encrypted)
    fingerprint = ""
    if has_private_key:
        try:
            # 仅在进程内计算指纹，明文不进入返回值/日志。
            fingerprint = secret_fingerprint(decrypt_secret(row.private_key_encrypted) or "")
        except Exception:
            fingerprint = ""
    return {
        "found": True,
        "name": row.name,
        "private_key": _MASK if has_private_key else "",
        "has_private_key": has_private_key,
        "private_key_fingerprint": fingerprint,
        "has_passphrase": bool(row.passphrase_encrypted),
        "description": row.description,
        "created_at": str(row.created_at) if hasattr(row, 'created_at') else "",
        "notice": "私钥内容不回传；服务器登录请走 OPS 的部署/执行通道。",
    }


