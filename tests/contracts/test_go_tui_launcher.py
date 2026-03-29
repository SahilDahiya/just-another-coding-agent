from __future__ import annotations

import json
import os
from types import SimpleNamespace

import pytest

import just_another_coding_agent.__main__ as entry
from just_another_coding_agent.__main__ import main
from just_another_coding_agent.go_tui import GO_TUI_BINARY


def test_main_launches_go_tui_for_interactive_mode(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"
    go_binary = tmp_path / ("jaca-go.exe" if os.name == "nt" else "jaca-go")
    captured: dict[str, object] = {}

    monkeypatch.setattr(entry, "resolve_go_tui_launch", lambda: ([str(go_binary)], None))
    monkeypatch.setattr(
        entry,
        "default_backend_command",
        lambda: ["/tmp/fake-python", "-m", "just_another_coding_agent"],
    )

    def fake_run(command, *, check, cwd=None):
        captured["command"] = command
        captured["check"] = check
        captured["cwd"] = cwd
        return SimpleNamespace(returncode=17)

    monkeypatch.setattr(
        "just_another_coding_agent.__main__.subprocess.run",
        fake_run,
    )

    exit_code = main(
        [
            "--model",
            "openai:test-model",
            "--workspace-root",
            str(workspace_root),
            "--sessions-root",
            str(sessions_root),
            "--thinking",
            "high",
            ]
        )

    assert exit_code == 17
    assert captured == {
        "command": [
            str(go_binary),
            "--backend-command-json",
            json.dumps(["/tmp/fake-python", "-m", "just_another_coding_agent"]),
            "--model",
            "openai:test-model",
            "--workspace-root",
            str(workspace_root.resolve()),
            "--sessions-root",
            str(sessions_root.resolve()),
            "--thinking",
            "high",
        ],
        "check": False,
        "cwd": None,
    }


def test_main_fails_fast_when_go_binary_is_missing(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"

    missing = tmp_path / "missing" / GO_TUI_BINARY
    monkeypatch.setattr(
        entry,
        "resolve_go_tui_launch",
        lambda: (_ for _ in ()).throw(
            RuntimeError(
                "Installed Go TUI binary is missing. Build it explicitly with "
                "`JACA_BUILD_TUI=1 uv sync --reinstall-package "
                f"just-another-coding-agent --extra dev --extra test`: {missing}"
            )
        ),
    )

    with pytest.raises(
        RuntimeError,
        match=(
            "JACA_BUILD_TUI=1 uv sync --reinstall-package "
            "just-another-coding-agent --extra dev --extra test"
        ),
    ):
        main(
            [
                "--model",
                "openai:test-model",
                "--workspace-root",
                str(workspace_root),
                "--sessions-root",
                str(sessions_root),
            ]
        )


def test_main_launches_repo_local_go_tui_when_installed_binary_is_missing(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"
    repo_root = tmp_path / "repo"
    (repo_root / "cmd" / "jaca").mkdir(parents=True)
    (repo_root / "cmd" / "jaca" / "main.go").write_text("package main\n", encoding="utf-8")
    (repo_root / "go.mod").write_text("module jaca\n", encoding="utf-8")
    (repo_root / "pyproject.toml").write_text("[build-system]\nrequires=[]\n", encoding="utf-8")
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        entry,
        "resolve_go_tui_launch",
        lambda: (["go", "run", "./cmd/jaca"], repo_root),
    )
    monkeypatch.setattr(
        entry,
        "default_backend_command",
        lambda: ["/tmp/fake-python", "-m", "just_another_coding_agent"],
    )

    def fake_run(command, *, check, cwd=None):
        captured["command"] = command
        captured["check"] = check
        captured["cwd"] = cwd
        return SimpleNamespace(returncode=23)

    monkeypatch.setattr(
        "just_another_coding_agent.__main__.subprocess.run",
        fake_run,
    )

    exit_code = main(
        [
            "--model",
            "openai:test-model",
            "--workspace-root",
            str(workspace_root),
            "--sessions-root",
            str(sessions_root),
        ]
    )

    assert exit_code == 23
    assert captured == {
        "command": [
            "go",
            "run",
            "./cmd/jaca",
            "--backend-command-json",
            json.dumps(["/tmp/fake-python", "-m", "just_another_coding_agent"]),
            "--model",
            "openai:test-model",
            "--workspace-root",
            str(workspace_root.resolve()),
            "--sessions-root",
            str(sessions_root.resolve()),
        ],
        "check": False,
        "cwd": str(repo_root),
    }
