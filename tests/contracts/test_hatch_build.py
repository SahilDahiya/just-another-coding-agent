from __future__ import annotations

from pathlib import Path

from just_another_coding_agent import go_binaries


def test_build_go_binary_uses_explicit_prebuilt_worker(
    monkeypatch, tmp_path: Path
) -> None:
    prebuilt = tmp_path / "prebuilt-worker"
    prebuilt.write_text("worker-binary", encoding="utf-8")
    monkeypatch.setenv("JACA_PREBUILT_READ_ONLY_WORKER", str(prebuilt))

    def _unexpected_run(*args, **kwargs):  # pragma: no cover - should not execute
        raise AssertionError(
            "go build should not run when a prebuilt worker is supplied"
        )

    monkeypatch.setattr(go_binaries.subprocess, "run", _unexpected_run)

    output = go_binaries.build_go_binary(
        project_root=tmp_path,
        build_dir=tmp_path / "build",
        output_name="jaca-read-only-worker",
        package_path="./cmd/jaca-read-only-worker",
        prebuilt_env_var="JACA_PREBUILT_READ_ONLY_WORKER",
        failure_label="read-only worker binary",
    )

    assert output.read_text(encoding="utf-8") == "worker-binary"
