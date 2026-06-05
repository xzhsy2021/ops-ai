"""
对账脚本:对比 SQLAlchemy 资源表 与 config_kv 资产键,定位漂移点。

典型用途:
  # 1) 跑一次看现状(纯读,不写)
  python -m app.maintenance.inventory_reconcile

  # 2) 保存 JSON 报告
  python -m app.maintenance.inventory_reconcile --save-json

  # 3) 漂移即非零退出(CI 守护)
  python -m app.maintenance.inventory_reconcile --fail-on-drift

关注点:
  - DB 有 / config_kv 没有  →  可能是 UI 新建未回写(本次报告的根因场景)
  - config_kv 有 / DB 没有  →  可能是 sync 漏跑
  - 字段不一致              →  host / port / user 漂移
  - 命名分歧                →  UI "crypto" vs config_kv "crypto-trader"
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(message)s",
)
log = logging.getLogger("inventory_reconcile")


# ---------- 取数层 ----------

def _load_db(db) -> Dict[str, List[Dict[str, Any]]]:
    from app.db.models import Server, ServerGroup, Service, Environment
    servers = [
        {
            "name": s.name, "host": s.host, "port": s.port,
            "user": s.user, "key": s.key, "status": s.status,
            "jump_host": s.jump_host,
        }
        for s in db.query(Server).all()
    ]
    server_groups = [
        {
            "name": g.name, "display_name": g.display_name,
            "server_names": list(g.server_names or []),
            "tags": list(g.tags or []),
        }
        for g in db.query(ServerGroup).all()
    ]
    services = [
        {
            "name": svc.name, "system_name": svc.system_name,
            "display_name": svc.display_name,
            "servers": list(svc.servers or []),
        }
        for svc in db.query(Service).all()
    ]
    environments = [
        {"name": e.name, "variables": dict(e.variables or {})}
        for e in db.query(Environment).all()
    ]
    return {
        "servers": servers,
        "server_groups": server_groups,
        "services": services,
        "environments": environments,
    }


def _load_kv() -> Dict[str, Any]:
    """从 config_kv 解析出与 DB 同构的视图。"""
    from app.config.repository import load_config
    cfg = load_config() or {}

    # servers: config_kv["servers"] 与 config_kv["jump_hosts"] 都会落进 DB
    servers_raw: list = []
    for key in ("servers", "jump_hosts"):
        bucket = cfg.get(key) or []
        if isinstance(bucket, list):
            for s in bucket:
                if isinstance(s, dict):
                    servers_raw.append(s)
    servers = servers_raw

    # systems 是 dict;system_name -> {servers, services, ...}
    systems = cfg.get("systems") or {}
    if not isinstance(systems, dict):
        systems = {}

    # 派生 server_groups:每个 system 一条
    kv_groups: List[Dict[str, Any]] = []
    for sys_name, sys_data in systems.items():
        if not isinstance(sys_data, dict):
            continue
        kv_groups.append({
            "name": sys_name,
            "display_name": sys_data.get("display_name"),
            "server_names": list(sys_data.get("servers") or []),
            "tags": list(sys_data.get("tags") or []),
        })

    # 派生 services:从每个 system.services[*]
    kv_services: List[Dict[str, Any]] = []
    for sys_name, sys_data in systems.items():
        if not isinstance(sys_data, dict):
            continue
        for svc in (sys_data.get("services") or []):
            if not isinstance(svc, dict):
                continue
            kv_services.append({
                "name": svc.get("name") or "",
                "system_name": sys_name,
                "display_name": svc.get("display_name"),
                "servers": list(svc.get("servers") or []),
            })

    # environments:config_kv["environments"] 可能是 list 或 dict
    envs_raw = cfg.get("environments") or {}
    if isinstance(envs_raw, dict):
        kv_envs = [
            {"name": k, "variables": dict(v) if isinstance(v, dict) else {}}
            for k, v in envs_raw.items()
        ]
    elif isinstance(envs_raw, list):
        kv_envs = [
            e if isinstance(e, dict) else {"name": str(e), "variables": {}}
            for e in envs_raw
        ]
    else:
        kv_envs = []

    return {
        "servers": [
            {
                "name": s.get("name") or s.get("host") or "",
                "host": s.get("host") or "",
                "port": s.get("port") or 22,
                "user": s.get("username") or s.get("user") or "root",
                "key": s.get("key"),
                "status": s.get("status") or "online",
            }
            for s in servers if isinstance(s, dict)
        ],
        "server_groups": kv_groups,
        "services": kv_services,
        "environments": kv_envs,
    }


# ---------- 对比层 ----------

def _by_name(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {r.get("name"): r for r in rows if r.get("name")}


def _compare_servers(
    db_rows: List[Dict[str, Any]],
    kv_rows: List[Dict[str, Any]],
) -> Dict[str, Any]:
    db_map = _by_name(db_rows)
    kv_map = _by_name(kv_rows)
    db_names, kv_names = set(db_map), set(kv_map)
    only_in_db = sorted(db_names - kv_names)
    only_in_kv = sorted(kv_names - db_names)
    diff: List[Dict[str, Any]] = []
    for n in sorted(db_names & kv_names):
        a, b = db_map[n], kv_map[n]
        fields_diff = []
        for f in ("host", "port", "user", "status"):
            va, vb = a.get(f), b.get(f)
            if str(va or "") != str(vb or ""):
                fields_diff.append({"field": f, "db": va, "kv": vb})
        if fields_diff:
            diff.append({"name": n, "fields": fields_diff})
    return {
        "counts": {"db": len(db_rows), "kv": len(kv_rows)},
        "only_in_db": [{"name": n, "host": db_map[n].get("host")} for n in only_in_db],
        "only_in_kv": [{"name": n, "host": kv_map[n].get("host")} for n in only_in_kv],
        "field_mismatch": diff,
        "drift": bool(only_in_db or only_in_kv or diff),
    }


def _compare_groups(
    db_rows: List[Dict[str, Any]],
    kv_rows: List[Dict[str, Any]],
) -> Dict[str, Any]:
    db_map = _by_name(db_rows)
    kv_map = _by_name(kv_rows)
    db_names, kv_names = set(db_map), set(kv_map)
    only_in_db = sorted(db_names - kv_names)
    only_in_kv = sorted(kv_names - db_names)
    diff: List[Dict[str, Any]] = []
    for n in sorted(db_names & kv_names):
        a, b = db_map[n], kv_map[n]
        if (a.get("display_name") or "") != (b.get("display_name") or ""):
            diff.append({
                "name": n,
                "field": "display_name",
                "db": a.get("display_name"),
                "kv": b.get("display_name"),
            })
        if set(a.get("server_names") or []) != set(b.get("server_names") or []):
            diff.append({
                "name": n,
                "field": "server_names",
                "db": sorted(a.get("server_names") or []),
                "kv": sorted(b.get("server_names") or []),
            })

    # 命名分歧:DB 名字 与 config_kv 系统名 存在 fuzzy 匹配
    name_aliases: List[Dict[str, str]] = []
    db_n_list = sorted(db_names)
    for kn in sorted(kv_names):
        for dn in db_n_list:
            if kn == dn:
                continue
            if kn in dn or dn in kn:
                name_aliases.append({"kv_name": kn, "db_name": dn, "note": "name overlap"})

    return {
        "counts": {"db": len(db_rows), "kv": len(kv_rows)},
        "only_in_db": [{"name": n, "display_name": db_map[n].get("display_name"),
                        "server_count": len(db_map[n].get("server_names") or [])}
                       for n in only_in_db],
        "only_in_kv": [{"name": n, "display_name": kv_map[n].get("display_name"),
                        "server_count": len(kv_map[n].get("server_names") or [])}
                       for n in only_in_kv],
        "field_mismatch": diff,
        "name_aliases": name_aliases,
        "drift": bool(only_in_db or only_in_kv or diff or name_aliases),
    }


def _compare_services(
    db_rows: List[Dict[str, Any]],
    kv_rows: List[Dict[str, Any]],
) -> Dict[str, Any]:
    def _key(r: Dict[str, Any]) -> Tuple[str, str]:
        return (r.get("system_name") or "", r.get("name") or "")
    db_map = {_key(r): r for r in db_rows}
    kv_map = {_key(r): r for r in kv_rows}
    db_keys, kv_keys = set(db_map), set(kv_map)
    only_in_db = sorted(db_keys - kv_keys)
    only_in_kv = sorted(kv_keys - db_keys)
    return {
        "counts": {"db": len(db_rows), "kv": len(kv_rows)},
        "only_in_db": [{"system": k[0], "name": k[1]} for k in only_in_db],
        "only_in_kv": [{"system": k[0], "name": k[1]} for k in only_in_kv],
        "drift": bool(only_in_db or only_in_kv),
    }


def _compare_envs(
    db_rows: List[Dict[str, Any]],
    kv_rows: List[Dict[str, Any]],
) -> Dict[str, Any]:
    db_map = _by_name(db_rows)
    kv_map = _by_name(kv_rows)
    db_names, kv_names = set(db_map), set(kv_map)
    only_in_db = sorted(db_names - kv_names)
    only_in_kv = sorted(kv_names - db_names)
    return {
        "counts": {"db": len(db_rows), "kv": len(kv_rows)},
        "only_in_db": only_in_db,
        "only_in_kv": only_in_kv,
        "drift": bool(only_in_db or only_in_kv),
    }


# ---------- 主流程 ----------

def main() -> int:
    p = argparse.ArgumentParser(description="对账 DB vs config_kv 资产数据")
    p.add_argument("--save-json", action="store_true", help="保存 JSON 报告到 data/reports/")
    p.add_argument("--fail-on-drift", action="store_true", help="发现漂移时退出码非零")
    args = p.parse_args()

    from app.db import SessionLocal

    with SessionLocal() as db:
        db_view = _load_db(db)
        kv_view = _load_kv()

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "servers":       _compare_servers(db_view["servers"], kv_view["servers"]),
        "server_groups": _compare_groups(db_view["server_groups"], kv_view["server_groups"]),
        "services":      _compare_services(db_view["services"], kv_view["services"]),
        "environments":  _compare_envs(db_view["environments"], kv_view["environments"]),
    }

    any_drift = any(
        report[k]["drift"]
        for k in ("servers", "server_groups", "services", "environments")
    )

    # 人类可读 summary
    log.info("=" * 60)
    log.info("对账结果概览")
    log.info("=" * 60)
    for k in ("servers", "server_groups", "services", "environments"):
        sub = report[k]
        log.info(
            "[%s] db=%d, kv=%d, only_in_db=%d, only_in_kv=%d, drift=%s",
            k,
            sub["counts"]["db"],
            sub["counts"]["kv"],
            len(sub.get("only_in_db", [])),
            len(sub.get("only_in_kv", [])),
            sub["drift"],
        )
    log.info("整体漂移: %s", "是" if any_drift else "否")

    # 详细差异 dump
    if report["servers"]["drift"]:
        log.info("-" * 60)
        log.info("SERVERS 详细差异:")
        for s in report["servers"]["only_in_db"]:
            log.info("  +DB 独有: %s (host=%s)", s["name"], s.get("host"))
        for s in report["servers"]["only_in_kv"]:
            log.info("  -KV 独有: %s (host=%s)", s["name"], s.get("host"))
        for s in report["servers"]["field_mismatch"]:
            log.info("  ≠ %s: %s", s["name"], s["fields"])

    if report["server_groups"]["drift"]:
        log.info("-" * 60)
        log.info("SERVER_GROUPS 详细差异:")
        for g in report["server_groups"]["only_in_db"]:
            log.info("  +DB 独有: %s (display=%s, server_count=%d)",
                     g["name"], g.get("display_name"), g["server_count"])
        for g in report["server_groups"]["only_in_kv"]:
            log.info("  -KV 独有: %s (display=%s, server_count=%d)",
                     g["name"], g.get("display_name"), g["server_count"])
        for g in report["server_groups"]["name_aliases"]:
            log.info("  ~命名分歧: kv=%s vs db=%s", g["kv_name"], g["db_name"])
        for d in report["server_groups"]["field_mismatch"]:
            log.info("  ≠ %s.%s: db=%s, kv=%s", d["name"], d["field"], d["db"], d["kv"])

    # JSON 报告
    if args.save_json:
        from app.services.runtime_resources import get_app_data_dir
        out_dir = os.path.join(get_app_data_dir(), "reports")
        os.makedirs(out_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = os.path.join(out_dir, f"inventory_reconcile_{ts}.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        log.info("JSON 报告已写入: %s", out_path)

    # 退出码
    if any_drift and args.fail_on_drift:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
