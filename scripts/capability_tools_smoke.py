#!/usr/bin/env python3
"""Static smoke checks for OPS Capability Server integration."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_FILES = [
    "app/api/tools.py",
    "app/services/tool_registry.py",
    "app/services/tool_policy.py",
    "app/services/tool_token.py",
    "app/services/tool_adapters/deploy_tools.py",
    "app/services/tool_adapters/file_tools.py",
    "app/services/tool_adapters/server_tools.py",
    "app/services/tool_adapters/capability_tools.py",
    "app/services/package_retention.py",
    "frontend/src/pages/ToolAccessPage.tsx",
]

REQUIRED_TOOL_NAMES = [
    "ops.list_systems",
    "ops.describe_capabilities",
    "ops.list_services",
    "ops.get_service_config",
    "ops.list_servers",
    "ops.list_packages",
    "ops.inspect_local_package",
    "ops.upload_package",
    "ops.prepare_release_from_local_package",
    "ops.get_package_retention_preview",
    "ops.cleanup_packages",
    "ops.protect_package",
    "ops.create_deploy_plan",
    "ops.run_precheck",
    "ops.execute_deploy_plan",
    "ops.create_rollback_plan",
    "ops.execute_rollback_plan",
    "ops.get_deployment_report",
    "ops.get_deployment_tasks",
    "ops.get_deployment_logs",
    "ops.create_config_change_plan",
    "ops.check_disk",
    "ops.run_health_check",
]


def main() -> int:
    missing = [f for f in REQUIRED_FILES if not (ROOT / f).exists()]
    if missing:
        raise SystemExit(f"Missing files: {missing}")
    for py in [p for p in REQUIRED_FILES if p.endswith('.py')]:
        ast.parse((ROOT / py).read_text(encoding='utf-8'))
    combined = "\n".join((ROOT / f).read_text(encoding='utf-8', errors='ignore') for f in REQUIRED_FILES)
    not_found = [name for name in REQUIRED_TOOL_NAMES if name not in combined]
    if not_found:
        raise SystemExit(f"Missing tool names: {not_found}")
    policy_text = (ROOT / "app/services/tool_policy.py").read_text(encoding='utf-8')
    bootstrap_text = (ROOT / "app/db/bootstrap.py").read_text(encoding='utf-8')
    if "DEFAULT_CAPABILITY_SETTINGS" not in policy_text or "ensure_domain_defaults" not in bootstrap_text:
        raise SystemExit("Missing capability settings database bootstrap")
    api_text = (ROOT / "app/api/tools.py").read_text(encoding='utf-8')
    for token in ["/capabilities", "capability_version", "include_disabled", "format"]:
        if token not in api_text:
            raise SystemExit(f"Missing capability discovery token: {token}")
    print("Capability tools smoke check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
