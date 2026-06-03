#!/usr/bin/env python3
"""Runtime smoke test for local or Docker trial runs."""
import argparse
import sys
import time
import urllib.error
import urllib.request


def fetch_json(url: str, timeout: float = 5.0) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")


def main() -> int:
    parser = argparse.ArgumentParser(description="Ops Platform smoke test")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--retries", type=int, default=30)
    parser.add_argument("--interval", type=float, default=1.0)
    args = parser.parse_args()

    for name in ("healthz", "readyz"):
        url = f"{args.base_url.rstrip('/')}/{name}"
        last = ""
        for _ in range(args.retries):
            status, body = fetch_json(url)
            last = body[:300]
            if status == 200 and ("ok" in body or "ready" in body):
                print(f"[OK] {name}: {body[:160]}")
                break
            time.sleep(args.interval)
        else:
            print(f"[FAIL] {name}: {last}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
