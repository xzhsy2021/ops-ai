#!/usr/bin/env python3
"""HTTP smoke checks for a running OPS single-process/dev instance."""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request


def fetch(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "ops-smoke-check/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read(1024 * 1024)
            return resp.status, resp.headers.get("content-type", ""), body
    except urllib.error.HTTPError as exc:
        body = exc.read(4096)
        return exc.code, exc.headers.get("content-type", ""), body


def require(cond: bool, message: str, failures: list[str]) -> None:
    if cond:
        print(f"[OK] {message}")
    else:
        print(f"[FAIL] {message}")
        failures.append(message)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8000", help="OPS base URL")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    failures: list[str] = []

    status, ctype, body = fetch(base + "/healthz")
    require(status == 200 and b"ok" in body.lower(), "/healthz returns ok", failures)

    status, ctype, body = fetch(base + "/login")
    html = body.decode("utf-8", errors="ignore")
    require(status == 200 and "text/html" in ctype.lower(), "/login returns HTML", failures)
    assets = re.findall(r'src="(/assets/[^"]+\.js)"', html)
    assets += re.findall(r'href="(/assets/[^"]+\.css)"', html)
    require(bool(assets), "/login HTML references built /assets files", failures)

    for asset in assets[:4]:
        status, ctype, body = fetch(base + asset)
        if asset.endswith(".js"):
            ok = status == 200 and ("javascript" in ctype.lower() or "text/plain" not in ctype.lower()) and b"<!DOCTYPE html" not in body[:200]
        else:
            ok = status == 200 and "text/css" in ctype.lower() and b"<!DOCTYPE html" not in body[:200]
        require(ok, f"{asset} returns static asset MIME ({ctype or '-'})", failures)

    status, ctype, body = fetch(base + "/api/v2/system/health")
    require(status in {200, 401}, "/api/v2/system/health is routed to API (200 or auth 401)", failures)

    status, ctype, body = fetch(base + "/api/v2/system/diagnostics/export")
    require(status in {200, 401}, "/api/v2/system/diagnostics/export is routed to API (200 or auth 401)", failures)

    status, ctype, body = fetch(base + "/api/v2/system/diagnostics/report")
    require(status in {200, 401}, "/api/v2/system/diagnostics/report is routed to API (200 or auth 401)", failures)

    status, ctype, body = fetch(base + "/api/v2/system/recent-errors")
    require(status in {200, 401}, "/api/v2/system/recent-errors is routed to API (200 or auth 401)", failures)

    status, ctype, body = fetch(base + "/api/v2/system/ai-diagnostics")
    require(status in {200, 401}, "/api/v2/system/ai-diagnostics is routed to API (200 or auth 401)", failures)

    status, ctype, body = fetch(base + "/api/v2/tools/risk-policy")
    require(status in {200, 401}, "/api/v2/tools/risk-policy is routed to API (200 or auth 401)", failures)

    status, ctype, body = fetch(base + "/api/v2/tasks?kind=tool")
    require(status in {200, 401}, "/api/v2/tasks?kind=tool is routed to API (200 or auth 401)", failures)

    status, ctype, body = fetch(base + "/api/v2/audit/operation-chains")
    require(status in {200, 401, 403}, "/api/v2/audit/operation-chains is routed to API (200/auth/forbidden)", failures)

    status, ctype, body = fetch(base + "/api/v2/audit/operation-chain?job_id=not-found")
    require(status in {200, 401, 403}, "/api/v2/audit/operation-chain is routed to API (200/auth/forbidden)", failures)


    status, ctype, body = fetch(base + "/api/v2/reports/summary")
    require(status in {200, 401}, "/api/v2/reports/summary is routed to API (200 or auth 401)", failures)

    status, ctype, body = fetch(base + "/api/v2/reports/types")
    require(status in {200, 401}, "/api/v2/reports/types is routed to API (200 or auth 401)", failures)

    status, ctype, body = fetch(base + "/api/v2/db/tables")
    require(status in {200, 401}, "/api/v2/db/tables is routed to API (200 or auth 401)", failures)

    status, ctype, body = fetch(base + "/api/v2/db/exports")
    require(status in {200, 401}, "/api/v2/db/exports is routed to API (200 or auth 401)", failures)

    status, ctype, body = fetch(base + "/api/v2/system/build-info")
    require(status in {200, 401}, "/api/v2/system/build-info is routed to API (200 or auth 401)", failures)

    status, ctype, body = fetch(base + "/api/v2/deploy/worker/status")
    require(status in {200, 401}, "/api/v2/deploy/worker/status is routed to API (200 or auth 401)", failures)

    status, ctype, body = fetch(base + "/api/v2/deploy/tool-plans")
    require(status in {200, 401}, "/api/v2/deploy/tool-plans is routed to API (200 or auth 401)", failures)

    status, ctype, body = fetch(base + "/api/v2/deploy/deployments/not-found/rollback-readiness")
    require(status in {401, 404}, "/api/v2/deploy/deployments/{id}/rollback-readiness is routed and not SPA fallback", failures)

    status, ctype, body = fetch(base + "/api/v2/admin/backups")
    require(status in {200, 401}, "/api/v2/admin/backups is routed to API (200 or auth 401)", failures)

    status, ctype, body = fetch(base + "/api/v2/admin/backups/not-a-backup.db/verify")
    require(status in {401, 404, 405}, "/api/v2/admin/backups/{file}/verify is routed and not SPA fallback", failures)

    status, ctype, body = fetch(base + "/api/v2/admin/restore-db")
    require(status in {401, 405}, "/api/v2/admin/restore-db is routed and not SPA fallback", failures)

    status, ctype, body = fetch(base + "/api/v2/mcp")
    require(status in {405, 401, 200}, "/api/v2/mcp is routed and not SPA fallback", failures)

    page_routes = [
        "/", "/dashboard", "/login", "/deploy", "/system", "/system/status",
        "/system/diagnostics", "/servers", "/apps", "/tasks", "/task-center",
        "/maintenance", "/files", "/pipelines", "/audit", "/reports", "/database", "/tools",
    ]
    for route in page_routes:
        status, ctype, body = fetch(base + route)
        require(
            status in {200, 307, 401}
            and b"Frontend not built" not in body
            and b'{"detail":"Not Found"}' not in body
            and (status != 200 or "text/html" in ctype.lower()),
            f"{route} route returns SPA shell and not FastAPI 404",
            failures,
        )

    if assets:
        js_status, js_ctype, js_body = fetch(base + assets[0])
        require(
            js_status == 200 and b"__OPS_FRONTEND_READY__" in js_body,
            "built JS contains frontend ready marker",
            failures,
        )

    if failures:
        print("\nSmoke check failed:")
        for item in failures:
            print(f"- {item}")
        return 1
    print("\nsmoke_check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
