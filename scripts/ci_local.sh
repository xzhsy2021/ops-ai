#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "[1/6] environment check"
bash scripts/check_env.sh

echo "[2/6] python compile"
python3 -m compileall -q main.py config_manager.py ssh_client.py app scripts

echo "[3/6] capability/MCP static smoke"
python3 scripts/capability_tools_smoke.py

echo "[4/6] MCP stdio fallback contract"
if [ -f tests/test_mcp_stdio_fallback.py ]; then
  timeout 45s env PYTEST_DISABLE_PLUGIN_AUTOLOAD="${PYTEST_DISABLE_PLUGIN_AUTOLOAD:-1}" python3 -m pytest -q tests/test_mcp_stdio_fallback.py
else
  echo "skip: tests/test_mcp_stdio_fallback.py not found in this package"
fi

echo "[5/6] frontend build/typecheck when dependencies are installed"
timeout 60s bash scripts/frontend_check.sh

echo "[6/6] focused pytest"
rm -rf .pytest_tmp
FOCUSED_TESTS=(
  tests/test_release_hotfix_contract.py
  tests/test_release_reliability_contract.py
  tests/test_dashboard_iteration_contract.py
  tests/test_deploy_refactor_contract.py
  tests/test_mcp_http_contract.py
  tests/test_mcp_local_package_upload.py
  tests/test_secret_store.py
  tests/test_auth_security.py
  tests/test_sftp_api.py
  tests/test_terminal_api.py
  tests/test_sql_query_protection.py
  tests/test_cleanup_start_plan.py
  tests/test_rollback_health.py
  tests/test_release_switch_and_rollback.py
  tests/test_mcp_contract_sync.py
  tests/test_tool_token_templates_contract.py
  tests/test_frontend_tooling_contract.py
)
EXISTING_TESTS=()
for test_file in "${FOCUSED_TESTS[@]}"; do
  if [ -f "$test_file" ]; then
    EXISTING_TESTS+=("$test_file")
  else
    echo "skip: $test_file not found in this package"
  fi
done
if [ "${#EXISTING_TESTS[@]}" -gt 0 ]; then
  timeout 120s env PYTEST_DISABLE_PLUGIN_AUTOLOAD="${PYTEST_DISABLE_PLUGIN_AUTOLOAD:-1}" python3 -m pytest -q "${EXISTING_TESTS[@]}"
else
  echo "skip: no focused pytest files found"
fi

echo "local CI passed"
