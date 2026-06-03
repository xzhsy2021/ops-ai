from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ToolContext:
    """Runtime identity and policy context for MCP-like tool calls."""

    username: str = ""
    user_id: str = ""
    role: str = "readonly"
    is_admin: bool = False
    can_deploy: bool = False
    auth_type: str = "session"
    token_id: str = ""
    token_name: str = ""
    token_owner: str = ""
    scopes: List[str] = field(default_factory=list)
    allow_write: bool = False
    allow_prod: bool = False
    client_name: str = ""
    ip_address: str = ""
    user_agent: str = ""

    def has_scope(self, scope: str) -> bool:
        if self.is_admin and self.auth_type == "session":
            return True
        if "*" in self.scopes:
            return True
        if scope in self.scopes:
            return True
        prefix = scope.split(":", 1)[0]
        return f"{prefix}:*" in self.scopes

    def to_audit_dict(self) -> Dict[str, Any]:
        return {
            "username": self.username,
            "role": self.role,
            "is_admin": self.is_admin,
            "auth_type": self.auth_type,
            "token_id": self.token_id,
            "token_name": self.token_name,
            "token_owner": self.token_owner,
            "scopes": self.scopes,
            "allow_write": self.allow_write,
            "allow_prod": self.allow_prod,
            "client_name": self.client_name,
            "ip_address": self.ip_address,
            "user_agent": self.user_agent,
        }
