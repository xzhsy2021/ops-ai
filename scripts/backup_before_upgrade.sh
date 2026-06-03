#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_DATA_DIR="${APP_DATA_DIR:-$ROOT_DIR/data}"
BACKUP_DIR="${BACKUP_DIR:-$APP_DATA_DIR/backups}"
TS="$(date +%Y%m%d_%H%M%S)"
OUT="$BACKUP_DIR/pre_upgrade_$TS.tar.gz"
mkdir -p "$BACKUP_DIR"

if [ ! -d "$APP_DATA_DIR" ]; then
  echo "[WARN] APP_DATA_DIR does not exist: $APP_DATA_DIR"
  mkdir -p "$APP_DATA_DIR"
fi

echo "[backup] APP_DATA_DIR=$APP_DATA_DIR"
echo "[backup] output=$OUT"
tar -czf "$OUT" -C "$(dirname "$APP_DATA_DIR")" "$(basename "$APP_DATA_DIR")"
echo "[OK] pre-upgrade backup created: $OUT"
