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
    channel_bindings: List[Dict[str, str]] = field(default_factory=list)
    approver_identities: List[Dict[str, str]] = field(default_factory=list)
    # qclaw Element room binding: when the underlying token has a non-empty
    # list of allowed room IDs, MCP routing/approval tools must reject calls
    # coming from a room not on the list. Empty list = no binding.
    bound_room_ids: List[str] = field(default_factory=list)
    # qclaw Element approver whitelist (from the token). When non-empty,
    # only these Matrix user IDs may consume approval short codes created
    # through this credential. Empty list = no token-level restriction.
    approver_matrix_ids: List[str] = field(default_factory=list)
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
            "channel_bindings": self.channel_bindings,
            "approver_identities": self.approver_identities,
            # Include room binding so downstream audit logs can correlate a
            # qclaw MCP call with the room restriction in force at call time.
            "bound_room_ids": self.bound_room_ids,
            "approver_matrix_ids": self.approver_matrix_ids,
            "client_name": self.client_name,
            "ip_address": self.ip_address,
            "user_agent": self.user_agent,
        }
