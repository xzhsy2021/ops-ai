"""Small cross-platform helpers for local Windows/macOS/Linux installs."""
from __future__ import annotations

import os
import platform
from typing import Any, Dict


def is_windows() -> bool:
    return os.name == "nt" or platform.system().lower().startswith("win")


def safe_chmod(path: str, mode: int) -> bool:
    """Best-effort chmod that is safe on Windows.

    Windows only supports a limited read-only bit through os.chmod. For local
    small-team installs we should not fail bootstrap or key import because a
    POSIX mode such as 0600 cannot be represented.
    """
    if is_windows():
        return False
    try:
        os.chmod(path, mode)
        return True
    except Exception:
        return False


def normalize_local_path(path: str) -> str:
    return os.path.normcase(os.path.abspath(os.path.expanduser(path)))


def is_path_within(path: str, base: str) -> bool:
    """Return True when path is inside base using platform-aware comparison."""
    try:
        path_n = normalize_local_path(path)
        base_n = normalize_local_path(base)
        common = os.path.commonpath([path_n, base_n])
        return common == base_n
    except Exception:
        return False


def platform_info() -> Dict[str, Any]:
    return {
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "is_windows": is_windows(),
    }
