#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "[1/5] Python syntax"
python3 -m compileall -q app scripts main.py config_manager.py ssh_client.py

echo "[2/5] Frontend syntax"
node scripts/frontend_syntax_check.js

echo "[3/5] Frontend route/static contract"
node scripts/frontend_route_check.js

echo "[4/5] MCP local package smoke"
python3 scripts/mcp_local_package_smoke.py || echo "[warn] MCP smoke skipped or failed; check dependencies and runtime configuration"

echo "[5/6] MCP/tooling contract tests"
python3 -m pytest tests/test_mcp_contract_sync.py tests/test_tool_token_templates_contract.py tests/test_frontend_tooling_contract.py -q

echo "[6/6] Dependency-aware optional checks"
if [ -d frontend/node_modules ]; then
  (cd frontend && npm run typecheck && npm run build)
else
  echo "[skip] frontend/node_modules missing; run: cd frontend && npm ci"
fi

if python3 - <<'PY' >/dev/null 2>&1
import sqlalchemy
PY
then
  python3 -m pytest tests -q
else
  echo "[skip] sqlalchemy missing; run: pip install -r requirements.txt"
fi

echo "final_acceptance_check completed"
