#!/bin/bash
set -euo pipefail

ROOT=$(cd "$(dirname "$0")" && pwd)
if [ "${OPS_DOTENV_LOADED:-0}" != "1" ]; then
  export OPS_DOTENV_LOADED=1
  DOTENV_PYTHON="${PYTHON_BIN:-$(command -v python3 || command -v python)}"
  exec "$DOTENV_PYTHON" "$ROOT/scripts/load_dotenv.py" "$ROOT/.env" --run bash "$0" "$@"
fi
VENV="$ROOT/venv"
FRONTEND_DIR="$ROOT/frontend"
BACKEND_PORT="${BACKEND_PORT:-8000}"
BACKEND_HOST="${BACKEND_HOST:-0.0.0.0}"
FRONTEND_PORT="${FRONTEND_PORT:-3000}"
SKIP_INSTALL="${SKIP_INSTALL:-0}"
OPEN_BROWSER="${OPEN_BROWSER:-0}"
PID_FILE_BACKEND="$ROOT/.backend.pid"
PID_FILE_FRONTEND="$ROOT/.frontend.pid"
APP_DATA_DIR="${APP_DATA_DIR:-$ROOT/data}"
UPLOAD_DIR="${UPLOAD_DIR:-$APP_DATA_DIR/uploads}"
KEYS_DIR="${KEYS_DIR:-$APP_DATA_DIR/keys}"
BACKUP_DIR="${BACKUP_DIR:-$APP_DATA_DIR/backups}"
LOG_DIR="${LOG_DIR:-$APP_DATA_DIR/logs}"
REPORT_DIR="${REPORT_DIR:-$APP_DATA_DIR/reports}"
BACKEND_LOG="$LOG_DIR/backend.log"
FRONTEND_LOG="$LOG_DIR/frontend.log"
export APP_DATA_DIR UPLOAD_DIR KEYS_DIR BACKUP_DIR LOG_DIR REPORT_DIR

log() { echo "[$(date '+%H:%M:%S')] $*"; }
fail() { echo "[ERROR] $*" >&2; exit 1; }

is_port_free() {
  local port="$1"
  python - "$port" <<'PY'
import socket, sys
port=int(sys.argv[1])
s=socket.socket(); s.settimeout(0.3)
try:
    used=s.connect_ex(('127.0.0.1', port)) == 0
finally:
    s.close()
sys.exit(1 if used else 0)
PY
}

wait_http() {
  local url="$1"; local name="$2"; local max="${3:-30}"
  for i in $(seq 1 "$max"); do
    if command -v curl >/dev/null 2>&1 && curl -fsS "$url" >/dev/null 2>&1; then
      log "$name ready: $url"
      return 0
    fi
    sleep 1
  done
  return 1
}

cleanup() {
  echo ""
  log "Stopping services..."
  if [ -f "$PID_FILE_FRONTEND" ]; then kill "$(cat "$PID_FILE_FRONTEND")" 2>/dev/null || true; rm -f "$PID_FILE_FRONTEND"; fi
  if [ -f "$PID_FILE_BACKEND" ]; then kill "$(cat "$PID_FILE_BACKEND")" 2>/dev/null || true; rm -f "$PID_FILE_BACKEND"; fi
  log "Stopped"
  exit 0
}
trap cleanup SIGINT SIGTERM

clear_stale_pid() {
  local file="$1"; local name="$2"
  if [ -f "$file" ]; then
    local pid; pid=$(cat "$file" 2>/dev/null || true)
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
      fail "$name already running with pid $pid. Stop it first or remove $file if stale."
    fi
    rm -f "$file"
  fi
}

echo ""
echo "  ========================================"
echo "    Ops Platform v2.1.6"
echo "  ========================================"
echo ""

clear_stale_pid "$PID_FILE_BACKEND" "Backend"
clear_stale_pid "$PID_FILE_FRONTEND" "Frontend"
mkdir -p "$LOG_DIR" "$UPLOAD_DIR" "$KEYS_DIR" "$BACKUP_DIR" "$REPORT_DIR"

log "[1/8] Checking Python..."
if command -v python3 >/dev/null 2>&1; then PYTHON="python3"; elif command -v python >/dev/null 2>&1; then PYTHON="python"; else fail "Python not found"; fi
PY_VER=$($PYTHON -c "import sys;print(f'{sys.version_info.major}.{sys.version_info.minor}')")
log "Python $PY_VER found"

log "[2/8] Checking ports..."
is_port_free "$BACKEND_PORT" || fail "Backend port $BACKEND_PORT is in use. Try: BACKEND_PORT=8001 ./start.sh"
is_port_free "$FRONTEND_PORT" || fail "Frontend port $FRONTEND_PORT is in use. Try: FRONTEND_PORT=3001 ./start.sh"

log "[3/8] Checking virtual environment..."
if [ ! -f "$VENV/bin/python" ]; then "$PYTHON" -m venv "$VENV"; fi
# shellcheck disable=SC1091
source "$VENV/bin/activate"

log "[4/8] Checking backend dependencies..."
if [ "$SKIP_INSTALL" != "1" ]; then
  python -c "import fastapi,uvicorn,paramiko,pydantic,sqlalchemy,yaml,pymysql" 2>/dev/null || pip install -r "$ROOT/requirements.txt"
fi

log "[5/8] Checking frontend dependencies..."
if ! command -v node >/dev/null 2>&1; then
  fail "Node.js not found. Install Node.js 18+ first."
fi
if ! command -v npm >/dev/null 2>&1; then
  fail "npm not found. Install Node.js 18+ first."
fi
if [ ! -f "$FRONTEND_DIR/package.json" ]; then
  fail "frontend/package.json not found"
fi
if [ "$SKIP_INSTALL" != "1" ]; then
  if [ -f "$FRONTEND_DIR/scripts/ensure-frontend-deps.cjs" ]; then
    node "$FRONTEND_DIR/scripts/ensure-frontend-deps.cjs"
  else
    log "Dependency helper not found, running npm install directly..."
    (cd "$FRONTEND_DIR" && npm install --no-audit --no-fund)
  fi
else
  log "SKIP_INSTALL=1, skipped frontend dependency installation"
fi

log "[6/8] Initializing database..."
cd "$ROOT"
python -c "from app.db import init_db; init_db()" 2>/dev/null || true
python -c "from config_manager import _init_db; _init_db()" 2>/dev/null || true

log "[7/8] Running startup preflight..."
python "$ROOT/scripts/preflight_start_check.py" --host "$BACKEND_HOST" --port "$BACKEND_PORT" || exit 1

log "[7/8] Running quick doctor..."
python "$ROOT/scripts/doctor.py" --quick || true

log "[8/8] Starting services..."
: > "$BACKEND_LOG"
: > "$FRONTEND_LOG"
python -m uvicorn main:app --host "$BACKEND_HOST" --port "$BACKEND_PORT" >>"$BACKEND_LOG" 2>&1 &
echo $! > "$PID_FILE_BACKEND"
(cd "$FRONTEND_DIR" && npm run dev -- --host 0.0.0.0 --port "$FRONTEND_PORT" >>"$FRONTEND_LOG" 2>&1) &
echo $! > "$PID_FILE_FRONTEND"

if ! wait_http "http://127.0.0.1:$BACKEND_PORT/healthz" "Backend" 30; then
  echo ""
  echo "Backend failed to become ready. Last log lines:"
  tail -60 "$BACKEND_LOG" || true
  cleanup
fi

wait_http "http://127.0.0.1:$FRONTEND_PORT" "Frontend" 30 || log "Frontend is still starting; check $FRONTEND_LOG"

if [ "$OPEN_BROWSER" = "1" ]; then
  if command -v xdg-open >/dev/null 2>&1; then xdg-open "http://localhost:$FRONTEND_PORT" >/dev/null 2>&1 || true; fi
fi

echo ""
echo "  ========================================"
echo "    Ops Platform v2.1.6 Running"
echo "    Frontend: http://localhost:$FRONTEND_PORT"
echo "    Backend:  http://localhost:$BACKEND_PORT"
echo "    Health:   http://localhost:$BACKEND_PORT/healthz"
echo "    Data:     $APP_DATA_DIR"
echo "    Logs:     $LOG_DIR"
echo "  ========================================"
echo "    Press Ctrl+C to stop all services"
echo ""

wait
