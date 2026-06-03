#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "[upgrade] running from $ROOT_DIR"

if [ -x scripts/check_env.sh ]; then
  scripts/check_env.sh
fi

if [ -x scripts/backup.sh ]; then
  echo "[upgrade] creating safety backup before upgrade"
  scripts/backup.sh
fi

if [ -f requirements.txt ]; then
  if command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
  else
    PYTHON_BIN="python"
  fi
  echo "[upgrade] installing backend dependencies"
  "$PYTHON_BIN" -m pip install -r requirements.txt
fi

if [ -x scripts/init_db.sh ]; then
  echo "[upgrade] initializing or migrating local database"
  scripts/init_db.sh
fi

if [ -f scripts/smoke_test.py ]; then
  if command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
  else
    PYTHON_BIN="python"
  fi
  echo "[upgrade] smoke test"
  "$PYTHON_BIN" scripts/smoke_test.py || echo "[upgrade] smoke test failed; check application logs before serving users"
fi

echo "[upgrade] done"
