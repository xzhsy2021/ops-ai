#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import shutil
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_DATA_DIR = Path(os.getenv("APP_DATA_DIR", ROOT / "data")).resolve()
SENSITIVE_PATTERNS = ["keys", "venv", ".venv", "node_modules", "frontend/node_modules", ".pytest_cache", ".pytest_tmp", "__pycache__"]
SENSITIVE_FILES = ["ops.db", "ops.db-shm", "ops.db-wal", "ops.log", ".env", ".initial_admin_password"]
RUNTIME_FILES = ["initial_admin_password"]
RUNTIME_DIRS = ["uploads", "keys", "backups", "logs", "runtime"]


def status(level: str, message: str) -> None:
    print(f"[{level}] {message}")


def check_python() -> bool:
    ok = (3, 10) <= sys.version_info[:2] <= (3, 14)
    status("OK" if ok else "WARN", f"Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")
    return ok


def check_node() -> bool:
    node = shutil.which("node")
    if not node:
        status("WARN", "node not found; frontend dev/build commands will fail")
        return False
    status("OK", f"node found: {node}")
    return True


def check_port(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        in_use = sock.connect_ex(("127.0.0.1", port)) == 0
    status("WARN" if in_use else "OK", f"port {port} {'is in use' if in_use else 'is free'}")
    return not in_use


def check_imports(quick: bool) -> bool:
    if quick:
        return True
    ok = True
    for mod in ["fastapi", "sqlalchemy", "paramiko", "pymysql"]:
        try:
            __import__(mod)
            status("OK", f"python module installed: {mod}")
        except Exception:
            ok = False
            status("FAIL", f"python module missing: {mod}")
    return ok


def check_runtime_hygiene(strict: bool) -> bool:
    ok = True
    status("OK", f"APP_DATA_DIR={APP_DATA_DIR}")
    if APP_DATA_DIR == ROOT:
        ok = False
        status("FAIL", "APP_DATA_DIR points at project root")
    for rel in RUNTIME_DIRS:
        runtime_path = APP_DATA_DIR / rel
        status("OK" if runtime_path.exists() else "WARN", f"runtime directory {'exists' if runtime_path.exists() else 'missing'}: {runtime_path}")
    for rel in SENSITIVE_FILES:
        if (ROOT / rel).exists():
            ok = False if strict else ok
            status("WARN", f"runtime file exists in project root: {rel}")
    for rel in RUNTIME_FILES:
        p = APP_DATA_DIR / rel
        if p.exists():
            status("OK", f"runtime file located under APP_DATA_DIR: {p}")
    for rel in SENSITIVE_PATTERNS:
        if (ROOT / rel).resolve() == (APP_DATA_DIR / rel).resolve():
            continue
        if (ROOT / rel).exists():
            level = "FAIL" if strict and rel == "keys" else "WARN"
            if level == "FAIL":
                ok = False
            status(level, f"runtime/sensitive directory present: {rel}")
    return ok


def check_dist_hygiene() -> bool:
    dist = ROOT / "dist"
    if not dist.exists():
        status("OK", "dist directory does not exist")
        return True
    bad = []
    for p in dist.rglob("*"):
        rel = str(p.relative_to(dist)).replace(os.sep, "/")
        if any(part in {"keys", "venv", ".venv", "node_modules", "__pycache__", ".pytest_cache", ".pytest_tmp"} for part in rel.split("/")):
            bad.append(rel)
        if p.name in SENSITIVE_FILES or p.suffix in {".pem", ".key", ".log", ".pyc"}:
            bad.append(rel)
    if bad:
        status("FAIL", "dist contains forbidden files: " + ", ".join(bad[:10]))
        return False
    status("OK", "dist hygiene check passed")
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", action="store_true")
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()
    checks = [
        check_python(),
        check_node(),
        check_port(int(os.getenv("BACKEND_PORT", "8000"))),
        check_port(int(os.getenv("FRONTEND_PORT", "3000"))),
        check_imports(args.quick),
        check_runtime_hygiene(args.strict),
        check_dist_hygiene(),
    ]
    if os.getenv("ENV") == "production" and not os.getenv("SESSION_SECRET"):
        status("FAIL", "SESSION_SECRET is required in production")
        checks.append(False)
    if not os.getenv("OPS_SECRET_KEY"):
        status("WARN", "OPS_SECRET_KEY is not configured; encrypted secret storage is disabled")
    return 0 if all(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
