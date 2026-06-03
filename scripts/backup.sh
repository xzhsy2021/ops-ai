#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

APP_DATA_DIR="${APP_DATA_DIR:-$ROOT_DIR/data}"
BACKUP_DIR="${BACKUP_DIR:-$APP_DATA_DIR/backups}"
mkdir -p "$BACKUP_DIR"

timestamp="$(date +%Y%m%d_%H%M%S)"
backup_file="$BACKUP_DIR/ops_runtime_backup_$timestamp.tar.gz"
tmp_dir="$(mktemp -d)"

cleanup() { rm -rf "$tmp_dir"; }
trap cleanup EXIT

cat > "$tmp_dir/OPS_BACKUP_MANIFEST.txt" <<EOF
created_at=$(date '+%Y-%m-%d %H:%M:%S')
root_dir=$ROOT_DIR
app_data_dir=$APP_DATA_DIR
note=.env is intentionally excluded to avoid leaking local secrets.
EOF

stage="$tmp_dir/stage"
mkdir -p "$stage"

if [ -d "$APP_DATA_DIR" ]; then
  mkdir -p "$stage/data"
  (cd "$APP_DATA_DIR" && tar --exclude='backups/ops_runtime_backup_*.tar.gz' -cf - .) | (cd "$stage/data" && tar -xf -)
fi
[ -d "$ROOT_DIR/config" ] && cp -a "$ROOT_DIR/config" "$stage/config"
[ -f "$ROOT_DIR/.env.example" ] && cp "$ROOT_DIR/.env.example" "$stage/.env.example"
[ -f "$ROOT_DIR/requirements.txt" ] && cp "$ROOT_DIR/requirements.txt" "$stage/requirements.txt"
cp "$tmp_dir/OPS_BACKUP_MANIFEST.txt" "$stage/OPS_BACKUP_MANIFEST.txt"

if [ -z "$(find "$stage" -mindepth 1 -maxdepth 1 -print -quit)" ]; then
  echo '[FAIL] nothing to backup'
  exit 1
fi

tar -czf "$backup_file" -C "$stage" .
printf '[OK] runtime backup created: %s\n' "$backup_file"
