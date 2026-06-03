#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

APP_DATA_DIR="${APP_DATA_DIR:-$ROOT_DIR/data}"
UPLOAD_DIR="${UPLOAD_DIR:-$APP_DATA_DIR/uploads}"
KEYS_DIR="${KEYS_DIR:-$APP_DATA_DIR/keys}"
BACKUP_DIR="${BACKUP_DIR:-$APP_DATA_DIR/backups}"
LOG_DIR="${LOG_DIR:-$APP_DATA_DIR/logs}"
OPS_DB_PATH="${OPS_DB_PATH:-$APP_DATA_DIR/ops.db}"

status=0
ok() { printf '[OK] %s\n' "$1"; }
warn() { printf '[WARN] %s\n' "$1"; }
fail() { printf '[FAIL] %s\n' "$1"; status=1; }

command -v python3 >/dev/null 2>&1 && ok "python3 available" || fail "python3 not found"

python3 --version | sed 's/^/[OK] /'

for dir in "$APP_DATA_DIR" "$UPLOAD_DIR" "$KEYS_DIR" "$BACKUP_DIR" "$LOG_DIR"; do
  mkdir -p "$dir"
  if [ -d "$dir" ] && [ -w "$dir" ]; then
    ok "writable directory: $dir"
  else
    fail "directory is not writable: $dir"
  fi
done

if [ -n "${OPS_SECRET_KEY:-}" ]; then
  if [ "${#OPS_SECRET_KEY}" -lt 24 ]; then
    warn "OPS_SECRET_KEY is set but short; use at least 32 random chars"
  else
    ok "OPS_SECRET_KEY configured"
  fi
else
  warn "OPS_SECRET_KEY not set; local dev works, but stored credentials may use compatibility mode"
fi

if [ -f requirements.txt ]; then
  ok "requirements.txt found"
else
  fail "requirements.txt missing"
fi

printf 'APP_DATA_DIR=%s\nUPLOAD_DIR=%s\nKEYS_DIR=%s\nBACKUP_DIR=%s\nLOG_DIR=%s\nOPS_DB_PATH=%s\n' \
  "$APP_DATA_DIR" "$UPLOAD_DIR" "$KEYS_DIR" "$BACKUP_DIR" "$LOG_DIR" "$OPS_DB_PATH"

exit "$status"
