#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

APP_DATA_DIR_VALUE="${APP_DATA_DIR:-$ROOT_DIR/data}"
MAX_LOG_TAIL_LINES_VALUE="${MAX_LOG_TAIL_LINES:-500}"
MAX_TERMINAL_SESSIONS_VALUE="${MAX_TERMINAL_SESSIONS:-3}"
TASK_IDLE_POLL_SECONDS_VALUE="${TASK_IDLE_POLL_SECONDS:-15}"

echo "[resource] root=$ROOT_DIR"
echo "[resource] APP_DATA_DIR=$APP_DATA_DIR_VALUE"
echo "[resource] MAX_LOG_TAIL_LINES=$MAX_LOG_TAIL_LINES_VALUE"
echo "[resource] MAX_TERMINAL_SESSIONS=$MAX_TERMINAL_SESSIONS_VALUE"
echo "[resource] TASK_IDLE_POLL_SECONDS=$TASK_IDLE_POLL_SECONDS_VALUE"

if [ -d "$APP_DATA_DIR_VALUE" ]; then
  echo "[resource] data directory usage:"
  du -sh "$APP_DATA_DIR_VALUE" 2>/dev/null || true
  for dir in uploads logs backups runtime keys; do
    if [ -e "$APP_DATA_DIR_VALUE/$dir" ]; then
      du -sh "$APP_DATA_DIR_VALUE/$dir" 2>/dev/null || true
    fi
  done
else
  echo "[resource] data directory does not exist yet"
fi

python3 -m py_compile app/services/runtime_resources.py app/services/tool_adapters/runtime_tools.py app/api/system.py
node scripts/frontend_syntax_check.js

echo "[resource] lightweight resource check passed"
