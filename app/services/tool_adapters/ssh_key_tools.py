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


@registry.register(
    name="ops.create_ssh_key",
    description="注册 SSH 密钥。High risk; requires human approval. 中文: 创建SSH密钥/新增密钥.",
    scopes=["server:write"],
    risk="high",
    category="ssh_key_write",
    write=True,
    requires_confirmation=True,
    requires_human_approval=True,
    ai_callable=True,
    ai_auto_callable=False,
    data_sensitivity="sensitive",
    input_schema={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "密钥名称（唯一标识）"},
            "private_key": {"type": "string", "description": "私钥内容（PEM格式）"},
            "passphrase": {"type": "string", "description": "私钥口令（可选）"},
            "description": {"type": "string", "description": "密钥描述（可选）"},
        },
        "required": ["name", "private_key"],
    },
)
def create_ssh_key_tool(args, ctx, db):
    from app.db.models import SshKey
    name = args.get("name", "")
    private_key = args.get("private_key", "")
    passphrase = args.get("passphrase", "")
    if not name or not private_key:
        return {"ok": False, "error": "name and private_key are required"}
    existing = db.query(SshKey).filter(SshKey.name == name).first()
    if existing:
        return {"ok": False, "error": f"SSH key '{name}' already exists"}
    row = SshKey(
        name=name,
        private_key_encrypted=encrypt_secret(private_key),
        passphrase_encrypted=encrypt_secret(passphrase) if passphrase else None,
        description=args.get("description"),
    )
    db.add(row)
    db.commit()
    return {"ok": True, "name": name}


@registry.register(
    name="ops.update_ssh_key",
    description="更新 SSH 密钥。High risk; requires human approval. 中文: 更新SSH密钥/修改密钥.",
    scopes=["server:write"],
    risk="high",
    category="ssh_key_write",
    write=True,
    requires_confirmation=True,
    requires_human_approval=True,
    ai_callable=True,
    ai_auto_callable=False,
    data_sensitivity="sensitive",
    input_schema={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "密钥名称"},
            "private_key": {"type": "string", "description": "新私钥内容（PEM格式）"},
            "passphrase": {"type": "string", "description": "新私钥口令"},
            "description": {"type": "string", "description": "密钥描述"},
        },
        "required": ["name"],
    },
)
def update_ssh_key_tool(args, ctx, db):
    from app.db.models import SshKey
    name = args.get("name", "")
    row = db.query(SshKey).filter(SshKey.name == name).first()
    if not row:
        return {"ok": False, "error": f"SSH key '{name}' not found"}
    if args.get("private_key"):
        row.private_key_encrypted = encrypt_secret(args["private_key"])
    if "passphrase" in args:
        row.passphrase_encrypted = encrypt_secret(args["passphrase"]) if args["passphrase"] else None
    if args.get("description") is not None:
        row.description = args["description"]
    db.commit()
    return {"ok": True, "name": name}


@registry.register(
    name="ops.delete_ssh_key",
    description="删除 SSH 密钥。High risk; requires human approval. 中文: 删除SSH密钥/移除密钥.",
    scopes=["server:write"],
    risk="high",
    category="ssh_key_write",
    write=True,
    requires_confirmation=True,
    requires_human_approval=True,
    ai_callable=True,
    ai_auto_callable=False,
    data_sensitivity="sensitive",
    input_schema={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "密钥名称"},
            "confirm_text": {"type": "string", "description": "确认短语: DELETE <name>"},
        },
        "required": ["name", "confirm_text"],
    },
)
def delete_ssh_key_tool(args, ctx, db):
    from app.db.models import SshKey
    name = args.get("name", "")
    confirm = args.get("confirm_text", "")
    expected = f"DELETE {name}"
    if confirm != expected:
        return {"ok": False, "error": f"confirm_text must be '{expected}'"}
    row = db.query(SshKey).filter(SshKey.name == name).first()
    if not row:
        return {"ok": False, "error": f"SSH key '{name}' not found"}
    db.delete(row)
    db.commit()
    return {"ok": True, "name": name}
