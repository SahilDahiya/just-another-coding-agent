#!/usr/bin/env python3

from __future__ import annotations

import argparse
import glob
import sys
import zipfile
from pathlib import Path

UNIX_BINARIES = ("jaca-go", "jaca-read-only-worker")
WINDOWS_BINARIES = ("jaca-go.exe", "jaca-read-only-worker.exe")


def _expected_binaries(wheel_name: str) -> tuple[str, ...]:
    return WINDOWS_BINARIES if "win_" in wheel_name else UNIX_BINARIES


def verify_wheel(path: Path) -> list[str]:
    issues: list[str] = []
    if path.name.endswith("none-any.whl"):
        issues.append("wheel is tagged as pure Python (none-any)")

    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()

    for binary_name in _expected_binaries(path.name):
        if not any(name.endswith(f"/{binary_name}") for name in names):
            issues.append(f"missing bundled binary: {binary_name}")

    return issues


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify that built JACA wheels bundle required Go binaries."
    )
    parser.add_argument("wheels", nargs="+")
    args = parser.parse_args(argv)

    wheel_paths: list[Path] = []
    for pattern in args.wheels:
        matches = sorted(Path(match) for match in glob.glob(pattern))
        if matches:
            wheel_paths.extend(matches)
        else:
            wheel_paths.append(Path(pattern))

    failures = False
    for wheel in wheel_paths:
        issues = verify_wheel(wheel)
        if issues:
            failures = True
            print(f"{wheel}:", file=sys.stderr)
            for issue in issues:
                print(f"  - {issue}", file=sys.stderr)
        else:
            print(f"{wheel}: ok")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
