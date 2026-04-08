from __future__ import annotations

import shutil
import uuid
from pathlib import Path

import pytest

from scripts.verify_release_artifacts import verify_release_artifacts


def _temp_dist_dir() -> Path:
    root = Path(".tmp-release-artifacts-tests")
    root.mkdir(exist_ok=True)
    dist_dir = root / uuid.uuid4().hex
    dist_dir.mkdir()
    return dist_dir


def test_verify_release_artifacts_accepts_complete_supported_manifest() -> None:
    dist_path = _temp_dist_dir()
    try:
        version = "0.1.3"
        artifact_names = [
            f"just_another_coding_agent-{version}.tar.gz",
            f"just_another_coding_agent-{version}-py3-none-manylinux_2_17_x86_64.whl",
            f"just_another_coding_agent-{version}-py3-none-win_amd64.whl",
            f"just_another_coding_agent-{version}-py3-none-macosx_10_12_x86_64.whl",
            f"just_another_coding_agent-{version}-py3-none-macosx_11_0_arm64.whl",
        ]

        for artifact_name in artifact_names:
            (dist_path / artifact_name).write_text("artifact", encoding="utf-8")

        assert verify_release_artifacts(dist_path) == []
    finally:
        shutil.rmtree(dist_path)


def test_verify_release_artifacts_rejects_missing_windows_wheel() -> None:
    dist_path = _temp_dist_dir()
    try:
        version = "0.1.3"
        artifact_names = [
            f"just_another_coding_agent-{version}.tar.gz",
            f"just_another_coding_agent-{version}-py3-none-manylinux_2_17_x86_64.whl",
            f"just_another_coding_agent-{version}-py3-none-macosx_10_12_x86_64.whl",
            f"just_another_coding_agent-{version}-py3-none-macosx_11_0_arm64.whl",
        ]

        for artifact_name in artifact_names:
            (dist_path / artifact_name).write_text("artifact", encoding="utf-8")

        assert verify_release_artifacts(dist_path) == [
            f"missing wheel: just_another_coding_agent-{version}-py3-none-win_amd64.whl"
        ]
    finally:
        shutil.rmtree(dist_path)


def test_verify_release_artifacts_rejects_multiple_versions() -> None:
    dist_path = _temp_dist_dir()
    try:
        (dist_path / "just_another_coding_agent-0.1.3.tar.gz").write_text(
            "artifact", encoding="utf-8"
        )
        (
            dist_path / "just_another_coding_agent-0.1.5-py3-none-win_amd64.whl"
        ).write_text("artifact", encoding="utf-8")

        with pytest.raises(RuntimeError, match="expected exactly one release version"):
            verify_release_artifacts(dist_path)
    finally:
        shutil.rmtree(dist_path)
