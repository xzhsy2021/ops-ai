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

# 占位/示例值检测：与 app/core/secrets_policy.py 的 INSECURE_MARKERS 保持一致。
# 历史缺陷：这里只判断"是否设置 + 长度"，于是与仓库 .env.example 逐字节相同的占位
# 密钥（可伪造会话令牌 / 可解密库内凭据）会被报成 OK。
check_secret_key() {
  name="$1"
  value="$2"
  if [ -z "$value" ]; then
    warn "$name not set; local dev works, but stored credentials may use compatibility mode"
    return
  fi
  lower=$(printf '%s' "$value" | tr '[:upper:]' '[:lower:]')
  case "$lower" in
    *change-me*|*change_me*|*changeme*|*please-change*|*please-generate*|*replace-me*|*replace_me*|*your-secret*|*your_secret*|*yoursecret*|*your-key*|*your_key*|*placeholder*|*do-not-use-in-production*|*dev-fallback*)
      fail "$name looks like a public example/placeholder value; rotate it (python scripts/rotate_secrets.py --check)"
      return
      ;;
  esac
  if [ "${#value}" -lt 32 ]; then
    warn "$name is set but short (${#value}); use at least 32 random chars"
  else
    ok "$name configured"
  fi
}

check_secret_key "SESSION_SECRET" "${SESSION_SECRET:-}"
check_secret_key "OPS_SECRET_KEY" "${OPS_SECRET_KEY:-}"
ok "密钥体检（权威判定）：python scripts/rotate_secrets.py --check"

if [ -f requirements.txt ]; then
  ok "requirements.txt found"
else
  fail "requirements.txt missing"
fi

printf 'APP_DATA_DIR=%s\nUPLOAD_DIR=%s\nKEYS_DIR=%s\nBACKUP_DIR=%s\nLOG_DIR=%s\nOPS_DB_PATH=%s\n' \
  "$APP_DATA_DIR" "$UPLOAD_DIR" "$KEYS_DIR" "$BACKUP_DIR" "$LOG_DIR" "$OPS_DB_PATH"

exit "$status"
