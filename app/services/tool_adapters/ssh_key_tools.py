from __future__ import annotations
from app.services.tool_registry import registry
from app.core.secret_store import encrypt_secret, decrypt_secret

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
    description="获取 SSH 密钥详情（含解密后的私钥内容）。中文: 查看SSH密钥详情/密钥信息.",
    scopes=["ops:read"],
    risk="low",
    category="ssh_key_read",
    write=False,
    ai_callable=True,
    ai_auto_callable=False,
    data_sensitivity="sensitive",
    input_schema={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "密钥名称"},
        },
        "required": ["name"],
    },
)
def get_ssh_key_tool(args, ctx, db):
    from app.db.models import SshKey
    name = args.get("name", "")
    row = db.query(SshKey).filter(SshKey.name == name).first()
    if not row:
        return {"found": False, "name": name}
    return {
        "found": True,
        "name": row.name,
        "private_key": decrypt_secret(row.private_key_encrypted),
        "has_passphrase": bool(row.passphrase_encrypted),
        "description": row.description,
        "created_at": str(row.created_at) if hasattr(row, 'created_at') else "",
    }


