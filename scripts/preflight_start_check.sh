#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"
PYTHON_BIN="${PYTHON_BIN:-}"
if [ -z "$PYTHON_BIN" ]; then
  if command -v python3 >/dev/null 2>&1; then PYTHON_BIN=python3; else PYTHON_BIN=python; fi
fi
if [ "${OPS_DOTENV_LOADED:-0}" != "1" ]; then
  export OPS_DOTENV_LOADED=1
  exec "$PYTHON_BIN" "$ROOT_DIR/scripts/load_dotenv.py" "$ROOT_DIR/.env" --run bash "$0" "$@"
fi
exec "$PYTHON_BIN" scripts/preflight_start_check.py "$@"
