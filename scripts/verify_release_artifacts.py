#!/usr/bin/env python3

from __future__ import annotations

import argparse
import sys
from pathlib import Path

EXPECTED_WHEEL_TAGS = (
    "py3-none-manylinux_2_17_x86_64",
    "py3-none-win_amd64",
    "py3-none-macosx_10_12_x86_64",
    "py3-none-macosx_11_0_arm64",
)


def _release_versions(dist_dir: Path) -> set[str]:
    versions: set[str] = set()
    for artifact in dist_dir.iterdir():
        name = artifact.name
        if name.endswith(".tar.gz"):
            prefix = "just_another_coding_agent-"
            if name.startswith(prefix):
                versions.add(name.removeprefix(prefix).removesuffix(".tar.gz"))
            continue
        if name.endswith(".whl"):
            prefix = "just_another_coding_agent-"
            suffix = "-py3-none-"
            if name.startswith(prefix) and suffix in name:
                versions.add(name[len(prefix) : name.index(suffix)])
    return versions


def verify_release_artifacts(dist_dir: Path) -> list[str]:
    versions = _release_versions(dist_dir)
    if len(versions) != 1:
        raise RuntimeError(
            "expected exactly one release version in dist/, "
            f"found {sorted(versions) or 'none'}"
        )

    version = next(iter(versions))
    missing: list[str] = []

    sdist_name = f"just_another_coding_agent-{version}.tar.gz"
    if not (dist_dir / sdist_name).is_file():
        missing.append(f"missing sdist: {sdist_name}")

    for wheel_tag in EXPECTED_WHEEL_TAGS:
        wheel_name = f"just_another_coding_agent-{version}-{wheel_tag}.whl"
        if not (dist_dir / wheel_name).is_file():
            missing.append(f"missing wheel: {wheel_name}")

    return missing


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Verify that a release dist directory contains every supported artifact."
        )
    )
    parser.add_argument("dist_dir", nargs="?", default="dist")
    args = parser.parse_args(argv)

    dist_dir = Path(args.dist_dir)
    issues = verify_release_artifacts(dist_dir)
    if issues:
        print(f"{dist_dir}:", file=sys.stderr)
        for issue in issues:
            print(f"  - {issue}", file=sys.stderr)
        return 1

    print(f"{dist_dir}: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
