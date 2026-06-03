#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

"$ROOT_DIR/scripts/check_env.sh"

python3 - <<'PY'
from app.core.config import ensure_runtime_dirs, get_database_path
from config_manager import _init_db, _ensure_defaults
from app.db import init_db
from app.core.auth_v2 import init_default_user
from app.db.base import SessionLocal

ensure_runtime_dirs()
_init_db()
_ensure_defaults()
init_db()

db = SessionLocal()
try:
    init_default_user(db)
finally:
    db.close()
print(f'[OK] database initialized: {get_database_path()}')
PY
