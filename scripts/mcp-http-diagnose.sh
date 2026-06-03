#!/usr/bin/env bash
set -euo pipefail
OPS_BASE_URL="${OPS_BASE_URL:-http://127.0.0.1:8000}"
if [[ -z "${OPS_TOOL_TOKEN:-}" ]]; then
  echo "[ERROR] OPS_TOOL_TOKEN is not set" >&2
  echo "Usage:" >&2
  echo "  export OPS_BASE_URL=http://127.0.0.1:8000" >&2
  echo "  export OPS_TOOL_TOKEN=ops_tool_xxx" >&2
  echo "  scripts/mcp-http-diagnose.sh" >&2
  exit 1
fi

echo "[INFO] OPS_BASE_URL=${OPS_BASE_URL}"
echo "[INFO] OPS_TOOL_TOKEN present: True"

echo "[MCP HTTP initialize]"
curl -sS -X POST "${OPS_BASE_URL}/api/v2/mcp" \
  -H "Authorization: Bearer ${OPS_TOOL_TOKEN}" \
  -H "Accept: application/json, text/event-stream" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"ops-http-diagnose","version":"1.0.0"}}}'
echo

echo "[MCP HTTP tools/list]"
curl -sS -X POST "${OPS_BASE_URL}/api/v2/mcp" \
  -H "Authorization: Bearer ${OPS_TOOL_TOKEN}" \
  -H "Accept: application/json, text/event-stream" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{"cursor":null}}'
echo

echo "[OK] Remote HTTP MCP endpoint works"
