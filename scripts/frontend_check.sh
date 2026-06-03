#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -d frontend ]; then
  echo "[frontend] frontend directory not found, skip"
  exit 0
fi

node scripts/frontend_syntax_check.js

if [ ! -d frontend/node_modules ]; then
  echo "[frontend] node_modules not found, skip build/typecheck"
  echo "[frontend] run: cd frontend && npm ci && npm run build"
  exit 0
fi

cd frontend
npm run build
