#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [ "${1:-}" = "" ]; then
  echo "Usage: scripts/restore.sh /path/to/ops_runtime_backup_YYYYmmdd_HHMMSS.tar.gz"
  exit 1
fi

backup_file="$1"
if [ ! -f "$backup_file" ]; then
  echo "[FAIL] backup file not found: $backup_file"
  exit 1
fi

APP_DATA_DIR="${APP_DATA_DIR:-$ROOT_DIR/data}"
SAFETY_BACKUP_DIR="${BACKUP_DIR:-$APP_DATA_DIR/backups}"
mkdir -p "$SAFETY_BACKUP_DIR"
safety_file="$SAFETY_BACKUP_DIR/ops_runtime_before_restore_$(date +%Y%m%d_%H%M%S).tar.gz"

if [ "${OPS_RESTORE_CONFIRM:-}" != "YES" ]; then
  echo "This will restore files into: $ROOT_DIR"
  echo "Set OPS_RESTORE_CONFIRM=YES to confirm. A safety backup will be created first."
  exit 2
fi

echo "[INFO] creating safety backup: $safety_file"
tar -czf "$safety_file" -C "$ROOT_DIR" --exclude='data/backups/ops_runtime_before_restore_*.tar.gz' data config .env.example requirements.txt 2>/dev/null || true

echo "[INFO] restoring from: $backup_file"
tar -xzf "$backup_file" -C "$ROOT_DIR"
echo '[OK] restore completed. Restart the application to apply database/config changes.'
