from __future__ import annotations

import argparse
import fnmatch
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


ROOT_FILE_PATTERNS = (
    "capabilities_*.txt",
    "debug_out.txt",
    "inspect_out*.txt",
    "inspect_report_*.md",
    "passwd_debug.txt",
    "raw_out.txt",
    "token_debug*.txt",
    "_inspect_crypto_*.log",
    "_verify_*.log",
)

SCRIPT_FILE_PATTERNS = (
    "_*.json",
)


@dataclass(frozen=True)
class StaleArtifact:
    path: Path
    reason: str


def _is_inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _match_files(directory: Path, patterns: Iterable[str], reason: str) -> list[StaleArtifact]:
    if not directory.exists() or not directory.is_dir():
        return []
    items: list[StaleArtifact] = []
    for child in directory.iterdir():
        if not child.is_file():
            continue
        if any(fnmatch.fnmatch(child.name, pattern) for pattern in patterns):
            items.append(StaleArtifact(path=child, reason=reason))
    return items


def collect_stale_artifacts(root: str | Path) -> list[StaleArtifact]:
    base = Path(root).resolve()
    candidates: list[StaleArtifact] = []
    candidates.extend(_match_files(base, ROOT_FILE_PATTERNS, "root inspection/debug artifact"))
    candidates.extend(_match_files(base / "scripts", SCRIPT_FILE_PATTERNS, "one-shot script output"))

    logs_dir = base / "logs"
    if logs_dir.exists() and logs_dir.is_dir():
        for child in logs_dir.iterdir():
            if child.is_file() and child.suffix == ".log":
                candidates.append(StaleArtifact(path=child, reason="local service log"))

    safe_candidates = [item for item in candidates if _is_inside(item.path, base)]
    safe_candidates.sort(key=lambda item: item.path.relative_to(base).as_posix())
    return safe_candidates


def cleanup_stale_artifacts(root: str | Path, *, apply: bool = False) -> dict[str, list[str]]:
    base = Path(root).resolve()
    candidates = collect_stale_artifacts(base)
    deleted: list[str] = []

    if apply:
        for item in candidates:
            if not _is_inside(item.path, base):
                continue
            relative = item.path.relative_to(base).as_posix()
            item.path.unlink(missing_ok=True)
            deleted.append(relative)

        logs_dir = base / "logs"
        if logs_dir.exists() and logs_dir.is_dir() and not any(logs_dir.iterdir()):
            logs_dir.rmdir()

    return {
        "candidates": [item.path.relative_to(base).as_posix() for item in candidates],
        "deleted": deleted,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Clean local one-shot inspection/debug artifacts.")
    parser.add_argument("--root", default=".", help="Project root. Defaults to current directory.")
    parser.add_argument("--apply", action="store_true", help="Delete matched artifacts. Default is dry-run.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON output.")
    args = parser.parse_args()

    result = cleanup_stale_artifacts(args.root, apply=args.apply)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        action = "deleted" if args.apply else "would delete"
        for item in result["deleted" if args.apply else "candidates"]:
            print(f"{action}: {item}")
        if not result["candidates"]:
            print("No stale artifacts found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
