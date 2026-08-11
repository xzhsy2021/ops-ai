#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

if [ "${OPS_DOTENV_LOADED:-0}" != "1" ]; then
  export OPS_DOTENV_LOADED=1
  exec python3 "$ROOT_DIR/scripts/load_dotenv.py" "$ROOT_DIR/.env" --run "$0" "$@"
fi

APP_DATA_DIR="${APP_DATA_DIR:-$ROOT_DIR/data}"
export APP_DATA_DIR
export UPLOAD_DIR="${UPLOAD_DIR:-$APP_DATA_DIR/uploads}"
export KEYS_DIR="${KEYS_DIR:-$APP_DATA_DIR/keys}"
export BACKUP_DIR="${BACKUP_DIR:-$APP_DATA_DIR/backups}"
export LOG_DIR="${LOG_DIR:-$APP_DATA_DIR/logs}"
export RUNTIME_DIR="${RUNTIME_DIR:-$APP_DATA_DIR/runtime}"
export REPORT_DIR="${REPORT_DIR:-$APP_DATA_DIR/reports}"
export SERVE_FRONTEND="${SERVE_FRONTEND:-true}"
export ENV="${ENV:-local}"
export HOST="${HOST:-0.0.0.0}"
export PORT="${PORT:-8000}"
export MAX_TERMINAL_SESSIONS="${MAX_TERMINAL_SESSIONS:-3}"
export MAX_LOG_TAIL_LINES="${MAX_LOG_TAIL_LINES:-500}"
export TASK_IDLE_POLL_SECONDS="${TASK_IDLE_POLL_SECONDS:-15}"

mkdir -p "$UPLOAD_DIR" "$KEYS_DIR" "$BACKUP_DIR" "$LOG_DIR" "$RUNTIME_DIR" "$REPORT_DIR"

if [ ! -d frontend/dist ]; then
  if command -v npm >/dev/null 2>&1; then
    echo "[single] frontend/dist not found, building frontend"
    (cd frontend && npm install --no-audit --no-fund && CI=1 npm run build)
  else
    echo "[single] frontend/dist not found and npm is unavailable" >&2
    exit 1
  fi
fi

echo "[single] running startup preflight"
python3 scripts/preflight_start_check.py --host "$HOST" --port "$PORT" --require-dist || exit 1

echo "[single] running local upgrade/data check"
python3 scripts/migrate_check.py --app-data-dir "$APP_DATA_DIR" || exit 1

echo "[single] starting OPS on http://$HOST:$PORT with backend-served frontend"
exec python3 -m uvicorn main:app --host "$HOST" --port "$PORT"
