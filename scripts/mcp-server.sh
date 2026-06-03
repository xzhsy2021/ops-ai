#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$SCRIPT_DIR/.." && pwd)
cd "$ROOT"
export OPS_BASE_URL="${OPS_BASE_URL:-http://127.0.0.1:8000}"
export OPS_MCP_HTTP_TIMEOUT="${OPS_MCP_HTTP_TIMEOUT:-6}"
export OPS_MCP_ASCII_DESCRIPTIONS="${OPS_MCP_ASCII_DESCRIPTIONS:-1}"
export PYTHONIOENCODING="${PYTHONIOENCODING:-utf-8}"
if [ -x "$ROOT/venv/bin/python" ]; then
  exec "$ROOT/venv/bin/python" -m app.mcp.server
fi
if command -v python3 >/dev/null 2>&1; then
  exec python3 -m app.mcp.server
fi
if command -v python >/dev/null 2>&1; then
  exec python -m app.mcp.server
fi
echo "[ERROR] Python not found. Install Python 3.10+ or run ./start.sh once to create venv." >&2
exit 1
