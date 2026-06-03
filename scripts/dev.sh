#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

"$ROOT_DIR/scripts/check_env.sh"
"$ROOT_DIR/scripts/init_db.sh"

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"
export ENV="${ENV:-development}"
export SERVE_FRONTEND="${SERVE_FRONTEND:-auto}"

printf '[OK] starting backend at http://%s:%s\n' "$HOST" "$PORT"
exec python3 -m uvicorn main:app --host "$HOST" --port "$PORT" --reload
