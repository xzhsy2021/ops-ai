#!/usr/bin/env python3
"""Create a ZIP while preserving executable modes recorded in Git."""
import argparse
from datetime import datetime
from pathlib import Path
import stat
import subprocess
import zipfile


def _tracked_modes(root: Path) -> dict[str, str]:
    output = subprocess.check_output(
        ["git", "-C", str(root), "ls-files", "--stage", "-z"]
    )
    modes: dict[str, str] = {}
    for record in output.split(b"\0"):
        if not record:
            continue
        metadata, path = record.split(b"\t", 1)
        modes[path.decode("utf-8")] = metadata.split(b" ", 1)[0].decode("ascii")
    return modes


def create_package_zip(root: Path, package_dir: Path, output: Path) -> None:
    tracked_modes = _tracked_modes(root)
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(item for item in package_dir.rglob("*") if item.is_file()):
            relative = path.relative_to(package_dir).as_posix()
            permissions = 0o755 if tracked_modes.get(relative) == "100755" else 0o644
            modified = datetime.fromtimestamp(path.stat().st_mtime)
            info = zipfile.ZipInfo(
                f"{package_dir.name}/{relative}",
                date_time=(modified.year, modified.month, modified.day,
                           modified.hour, modified.minute, modified.second),
            )
            info.create_system = 3
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (stat.S_IFREG | permissions) << 16
            bundle.writestr(info, path.read_bytes())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--package-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    create_package_zip(args.root.resolve(), args.package_dir.resolve(), args.output.resolve())


if __name__ == "__main__":
    main()
