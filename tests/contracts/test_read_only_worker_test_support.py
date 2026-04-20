from __future__ import annotations

from tests import read_only_worker_test_support as support


def test_workspace_deps_uses_reference_worker_without_eager_go_build(
    monkeypatch,
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    def _unexpected_go_build():
        raise AssertionError("workspace_deps should not build the Go worker")

    monkeypatch.setattr(
        support,
        "ensure_built_go_read_only_worker",
        _unexpected_go_build,
    )

    deps = support.workspace_deps(workspace_root)

    assert deps.read_only_worker._command == tuple(
        support.reference_read_only_worker_command()
    )
