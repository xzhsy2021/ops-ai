#!/usr/bin/env bash
# 开发模式启动脚本
# 默认：同端口模式（后端 8000 端口同时服务前端静态文件）
# 分离模式：DEV_MODE=separated ./scripts/dev.sh（后端 8000 + 前端 3000）
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

if [ "${OPS_DOTENV_LOADED:-0}" != "1" ]; then
  export OPS_DOTENV_LOADED=1
  exec python3 "$ROOT_DIR/scripts/load_dotenv.py" "$ROOT_DIR/.env" --run bash "$0" "$@"
fi

DEV_MODE="${DEV_MODE:-single}"

if [ "$DEV_MODE" = "separated" ]; then
  # 前后端分离模式（开发热更新）
  bash "$ROOT_DIR/scripts/check_env.sh"
  bash "$ROOT_DIR/scripts/init_db.sh"

  HOST="${HOST:-127.0.0.1}"
  PORT="${PORT:-8000}"
  export ENV="${ENV:-development}"
  export SERVE_FRONTEND="false"

  printf '[OK] starting backend at http://%s:%s (API only)\n' "$HOST" "$PORT"
  printf '[OK] frontend dev server: http://localhost:3000\n'
  python3 -m uvicorn main:app --host "$HOST" --port "$PORT" --reload &
  BACKEND_PID=$!

  cd frontend
  if [ ! -d node_modules ]; then
    npm install --no-audit --no-fund
  fi
  npm run dev &
  FRONTEND_PID=$!

  trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null || true" EXIT
  wait
else
  # 同端口模式（构建前端 dist + 后端服务）
  exec "$ROOT_DIR/scripts/start_single_process.sh"
fi
