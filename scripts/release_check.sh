#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "[1/6] Frontend syntax scan"
node scripts/frontend_syntax_check.js

if [ ! -d frontend/node_modules ]; then
  echo "frontend/node_modules missing; installing dependencies with npm ci"
  (cd frontend && npm ci --ignore-scripts --no-audit --no-fund)
fi

echo "[2/6] Frontend production build"
(cd frontend && CI=1 node ./node_modules/vite/bin/vite.js build)

echo "[3/6] Frontend route/static artifact check"
node scripts/frontend_route_check.js

echo "[4/6] Python syntax check"
if [ -d tests ]; then
  python3 -m compileall -q app tests scripts
else
  python3 -m compileall -q app scripts
fi

echo "[5/6] Environment check"
APP_DATA_DIR="${APP_DATA_DIR:-/tmp/ops-release-check-data}" bash scripts/check_env.sh

echo "[6/6] DB optimization script smoke"
DATABASE_PATH="${APP_DATA_DIR:-/tmp/ops-release-check-data}/ops.db" python3 scripts/db_optimize.py --check

echo "release_check passed"
