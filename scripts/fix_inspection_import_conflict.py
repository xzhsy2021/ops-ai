"""清理巡检服务历史残留目录，避免 app/services/inspection.py 与 app/services/inspection/ 导入冲突。"""
from pathlib import Path
import shutil

root = Path(__file__).resolve().parents[1]
legacy_dir = root / "app" / "services" / "inspection"
if legacy_dir.exists() and legacy_dir.is_dir():
    shutil.rmtree(legacy_dir)
    print(f"removed legacy directory: {legacy_dir}")
else:
    print(f"legacy directory not found: {legacy_dir}")
print("inspection import conflict cleanup completed")
