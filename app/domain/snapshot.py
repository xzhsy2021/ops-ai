from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def _snapshot_dir() -> str:
    from app.core.config import get_runtime_path
    d = get_runtime_path("SNAPSHOT_DIR", "snapshots")
    os.makedirs(d, exist_ok=True)
    return d


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def capture_asset_snapshot(db=None) -> Dict[str, Any]:
    from app.maintenance.server_assets import list_server_assets
    servers = list_server_assets(db=db)
    snapshot = {
        "captured_at": _now_iso(),
        "total_servers": len(servers),
        "servers": servers,
        "groups": {},
    }
    for s in servers:
        grp = s.get("group", "default")
        if grp not in snapshot["groups"]:
            snapshot["groups"][grp] = {"total": 0, "servers": []}
        snapshot["groups"][grp]["total"] += 1
        snapshot["groups"][grp]["servers"].append(s["name"])
    return snapshot


def save_asset_snapshot(db=None) -> Dict[str, Any]:
    snapshot = capture_asset_snapshot(db=db)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    filepath = os.path.join(_snapshot_dir(), f"asset_snapshot_{ts}.json")
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)
    return {"file": filepath, "captured_at": snapshot["captured_at"], "total_servers": snapshot["total_servers"]}


def list_asset_snapshots(limit: int = 20) -> List[Dict[str, Any]]:
    snapshots: List[Dict[str, Any]] = []
    try:
        for entry in sorted(os.scandir(_snapshot_dir()), key=lambda x: x.name, reverse=True):
            if entry.is_file() and entry.name.startswith("asset_snapshot_") and entry.name.endswith(".json"):
                stat = entry.stat()
                snapshots.append({
                    "file": entry.name,
                    "path": entry.path,
                    "size": stat.st_size,
                    "modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                })
                if len(snapshots) >= limit:
                    break
    except FileNotFoundError:
        pass
    return snapshots


def get_latest_asset_snapshot() -> Optional[Dict[str, Any]]:
    snapshots = list_asset_snapshots(limit=1)
    if not snapshots:
        return None
    try:
        with open(snapshots[0]["path"], "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def diff_asset_snapshots(id1: str, id2: str) -> Dict[str, Any]:
    dir_path = _snapshot_dir()
    path1 = os.path.join(dir_path, id1)
    path2 = os.path.join(dir_path, id2)
    if not os.path.isfile(path1) or not os.path.isfile(path2):
        return {"error": "one or both snapshots not found"}

    with open(path1, "r", encoding="utf-8") as f:
        snap1 = json.load(f)
    with open(path2, "r", encoding="utf-8") as f:
        snap2 = json.load(f)

    names1 = {s["name"] for s in snap1.get("servers", [])}
    names2 = {s["name"] for s in snap2.get("servers", [])}

    return {
        "snapshot1": {"file": id1, "captured_at": snap1.get("captured_at"), "total": snap1.get("total_servers")},
        "snapshot2": {"file": id2, "captured_at": snap2.get("captured_at"), "total": snap2.get("total_servers")},
        "added": sorted(names2 - names1),
        "removed": sorted(names1 - names2),
        "unchanged": sorted(names1 & names2),
    }