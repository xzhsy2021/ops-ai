#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DIST="$ROOT/dist"
VERSION="${1:-dev}"
PKG_NAME="ops-platform-$VERSION"
PKG_DIR="$DIST/$PKG_NAME"

rm -rf "$PKG_DIR"
mkdir -p "$PKG_DIR"

copy_path() {
  local src="$1"
  if [ -e "$ROOT/$src" ]; then
    mkdir -p "$PKG_DIR/$(dirname "$src")"
    cp -a "$ROOT/$src" "$PKG_DIR/$src"
  fi
}

copy_path app
copy_path config
copy_path docs
copy_path tools
copy_path scripts
copy_path tests
copy_path frontend/src
copy_path frontend/dist
copy_path frontend/index.html
copy_path frontend/package.json
copy_path frontend/package-lock.json
copy_path frontend/tsconfig.json
copy_path frontend/tsconfig.node.json
copy_path frontend/vite.config.ts
copy_path main.py
copy_path manage_users.py
copy_path ssh_client.py
copy_path config_manager.py
copy_path requirements.txt
copy_path pytest.ini
copy_path start.sh
copy_path start_prod.sh
copy_path start_dev.sh
copy_path start_diag.sh
copy_path start.bat
copy_path start_single_process.bat
copy_path start_single_process.ps1
copy_path .env.example
copy_path .env.local.example
copy_path .env.docker.example
copy_path .env.windows.example
copy_path dev_ops.md
copy_path UPGRADE.md

find "$PKG_DIR" -type d \( \
  -name "__pycache__" -o \
  -name ".pytest_cache" -o \
  -name ".pytest_tmp" -o \
  -name "node_modules" -o \
  -name "venv" -o \
  -name ".venv" -o \
  -name "keys" \
\) -prune -exec rm -rf {} +

find "$PKG_DIR" -type f \( \
  -name "*.pyc" -o \
  -name "*.log" -o \
  -name "ops.db" -o \
  -name "ops.db-shm" -o \
  -name "ops.db-wal" -o \
  -name ".env" -o \
  -name "*.pem" -o \
  -name "*.key" \
\) -delete

PYTHON_BIN="${PYTHON_BIN:-$(command -v python3 || command -v python)}"
"$PYTHON_BIN" "$ROOT/scripts/create_package_zip.py" \
  --root "$ROOT" \
  --package-dir "$PKG_DIR" \
  --output "$DIST/$PKG_NAME.zip"

echo "Package created: $DIST/$PKG_NAME.zip"
