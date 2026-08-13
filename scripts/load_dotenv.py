#!/usr/bin/env python3
"""Parse a simple dotenv file and emit only variables absent from the process."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys


KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def parse_dotenv(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"{path}:{line_number}: expected KEY=VALUE")
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not KEY_PATTERN.fullmatch(key):
            raise ValueError(f"{path}:{line_number}: invalid environment variable name")
        if value[:1] in {"'", '"'}:
            quote = value[0]
            if len(value) < 2 or value[-1] != quote:
                raise ValueError(f"{path}:{line_number}: unmatched quote")
            value = value[1:-1]
        values[key] = value
    return values


def missing_dotenv_values(path: Path, environ: dict[str, str]) -> dict[str, str]:
    return {key: value for key, value in parse_dotenv(path).items() if key not in environ}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--format", choices=("json", "nul"), default="json")
    parser.add_argument("path", type=Path)
    parser.add_argument("--run", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    try:
        values = missing_dotenv_values(args.path, os.environ)
    except (OSError, UnicodeError, ValueError) as exc:
        print(exc, file=sys.stderr)
        return 2
    if args.run is not None:
        if not args.run:
            print("--run requires a command", file=sys.stderr)
            return 2
        child_env = os.environ.copy()
        child_env.update(values)
        return subprocess.run(args.run, env=child_env, check=False).returncode
    if args.format == "json":
        print(json.dumps(values, ensure_ascii=False))
    else:
        for key, value in values.items():
            sys.stdout.buffer.write(key.encode("utf-8") + b"\0")
            sys.stdout.buffer.write(value.encode("utf-8") + b"\0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
