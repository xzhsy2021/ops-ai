from __future__ import annotations

import re
from typing import Any

SENSITIVE_PATTERNS = [
    re.compile(r"(?i)(password|passwd|pwd|secret|token|access[_-]?key|secret[_-]?key|authorization|cookie)\s*[:=]\s*([^\s,;]+)"),
    re.compile(r"(?i)(jdbc:[^\s]+://[^\s:]+:)[^@\s]+(@)"),
]


def mask_sensitive(value: Any, limit: int = 20000) -> str:
    text = str(value if value is not None else "")[:limit]
    for pattern in SENSITIVE_PATTERNS:
        if pattern.groups >= 2:
            text = pattern.sub(
                lambda match: (
                    f"{match.group(1)}=******"
                    if "jdbc:" not in match.group(1).lower()
                    else f"{match.group(1)}******{match.group(2)}"
                ),
                text,
            )
    return text
