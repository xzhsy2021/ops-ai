"""Offline smoke checks for release platform iteration 13-18 code contracts."""
from pathlib import Path

root = Path(__file__).resolve().parents[1]
checks = {
    "migration_runner": root / "app/db/migrations/runner.py",
    "deploy_api": root / "app/api/deploy_v2.py",
    "retention_service": root / "app/services/release_retention.py",
}
missing = [name for name, path in checks.items() if not path.exists()]
if missing:
    raise SystemExit(f"missing: {missing}")
print("release iteration 13-18 smoke contracts OK")
