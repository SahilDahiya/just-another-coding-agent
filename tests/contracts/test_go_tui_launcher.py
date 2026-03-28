from __future__ import annotations

import json
import os
from types import SimpleNamespace

import pytest

import just_another_coding_agent.__main__ as entry
from just_another_coding_agent.__main__ import main


def test_main_launches_go_tui_for_interactive_mode(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"
    go_binary = tmp_path / ("jaca-go.exe" if os.name == "nt" else "jaca-go")
    captured: dict[str, object] = {}

    monkeypatch.setattr(entry, "_resolve_go_tui_binary", lambda: go_binary)
    monkeypatch.setattr(entry.sys, "executable", "/tmp/fake-python")

    def fake_run(command, *, check):
        captured["command"] = command
        captured["check"] = check
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
    }


def test_main_fails_fast_when_go_binary_is_missing(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"

    missing = tmp_path / "missing" / "jaca-go"
    monkeypatch.setattr(entry, "GO_TUI_BINARY", missing.name)
    monkeypatch.setattr(entry.sysconfig, "get_path", lambda key: str(missing.parent))

    with pytest.raises(
        RuntimeError,
        match="Installed Go TUI binary is missing",
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
