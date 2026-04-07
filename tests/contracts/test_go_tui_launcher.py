from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

import just_another_coding_agent.__main__ as entry
from just_another_coding_agent.__main__ import main
from just_another_coding_agent.go_tui import GO_TUI_BINARY

UPDATE_COMMAND_JSON = json.dumps(
    ["uv", "tool", "upgrade", "just-another-coding-agent"]
)


def test_main_launches_go_tui_for_interactive_mode(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"
    go_binary = tmp_path / ("jaca-go.exe" if os.name == "nt" else "jaca-go")
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        entry,
        "resolve_go_tui_launch",
        lambda: ([str(go_binary)], None),
    )
    monkeypatch.delenv("JACA_MODEL", raising=False)
    monkeypatch.setattr(entry, "load_config", lambda: {})
    monkeypatch.setattr(
        entry,
        "default_backend_command",
        lambda: ["/tmp/fake-python", "-m", "just_another_coding_agent"],
    )
    monkeypatch.setattr(entry, "package_version", lambda: "0.1.4")
    monkeypatch.setattr(
        entry,
        "explicit_update_command_json",
        lambda repo_root=None: UPDATE_COMMAND_JSON,
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
            "--app-version",
            "0.1.4",
            "--model",
            "openai:test-model",
            "--workspace-root",
            str(workspace_root.resolve()),
            "--sessions-root",
            str(sessions_root.resolve()),
            "--update-command-json",
            UPDATE_COMMAND_JSON,
            "--thinking",
            "high",
        ],
        "check": False,
        "cwd": None,
    }


def test_main_resume_launches_go_tui_with_resolved_session(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"
    go_binary = tmp_path / ("jaca-go.exe" if os.name == "nt" else "jaca-go")
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        entry,
        "resolve_go_tui_launch",
        lambda: ([str(go_binary)], None),
    )
    monkeypatch.delenv("JACA_MODEL", raising=False)
    monkeypatch.setattr(entry, "load_config", lambda: {})
    monkeypatch.setattr(
        entry,
        "default_backend_command",
        lambda: ["/tmp/fake-python", "-m", "just_another_coding_agent"],
    )
    monkeypatch.setattr(entry, "package_version", lambda: "0.1.4")
    monkeypatch.setattr(
        entry,
        "explicit_update_command_json",
        lambda repo_root=None: UPDATE_COMMAND_JSON,
    )
    monkeypatch.setattr(
        entry,
        "resolve_session_reference",
        lambda **_: SimpleNamespace(
            session_id="0123456789abcdef0123456789abcdef",
            name="auth-store-cleanup",
            forked_from_session_id=None,
        ),
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
            "resume",
            "Auth",
            "Store",
            "Cleanup",
            "--workspace-root",
            str(workspace_root),
            "--sessions-root",
            str(sessions_root),
        ]
    )

    assert exit_code == 23
    assert captured == {
        "command": [
            str(go_binary),
            "--backend-command-json",
            json.dumps(["/tmp/fake-python", "-m", "just_another_coding_agent"]),
            "--app-version",
            "0.1.4",
            "--model",
                "openai-responses:gpt-5.4",
            "--workspace-root",
            str(workspace_root.resolve()),
            "--sessions-root",
            str(sessions_root.resolve()),
            "--update-command-json",
            UPDATE_COMMAND_JSON,
            "--session-id",
            "0123456789abcdef0123456789abcdef",
            "--session-name",
            "auth-store-cleanup",
        ],
        "check": False,
        "cwd": None,
    }


def test_main_resume_launches_go_tui_with_parent_fork_context(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"
    go_binary = tmp_path / ("jaca-go.exe" if os.name == "nt" else "jaca-go")
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        entry,
        "resolve_go_tui_launch",
        lambda: ([str(go_binary)], None),
    )
    monkeypatch.delenv("JACA_MODEL", raising=False)
    monkeypatch.setattr(entry, "load_config", lambda: {})
    monkeypatch.setattr(
        entry,
        "default_backend_command",
        lambda: ["/tmp/fake-python", "-m", "just_another_coding_agent"],
    )
    monkeypatch.setattr(entry, "package_version", lambda: "0.1.4")
    monkeypatch.setattr(
        entry,
        "explicit_update_command_json",
        lambda repo_root=None: UPDATE_COMMAND_JSON,
    )

    def fake_resolve_session_reference(**kwargs):
        if kwargs["session_ref"] == "auth-store-cleanup-followup":
            return SimpleNamespace(
                session_id="fedcba9876543210fedcba9876543210",
                name="auth-store-cleanup-followup",
                forked_from_session_id="0123456789abcdef0123456789abcdef",
            )
        if kwargs["session_ref"] == "0123456789abcdef0123456789abcdef":
            return SimpleNamespace(
                session_id="0123456789abcdef0123456789abcdef",
                name="auth-store-cleanup",
                forked_from_session_id=None,
            )
        raise AssertionError(f"unexpected session ref: {kwargs['session_ref']}")

    monkeypatch.setattr(
        entry,
        "resolve_session_reference",
        fake_resolve_session_reference,
    )

    def fake_run(command, *, check, cwd=None):
        captured["command"] = command
        captured["check"] = check
        captured["cwd"] = cwd
        return SimpleNamespace(returncode=29)

    monkeypatch.setattr(
        "just_another_coding_agent.__main__.subprocess.run",
        fake_run,
    )

    exit_code = main(
        [
            "resume",
            "auth-store-cleanup-followup",
            "--workspace-root",
            str(workspace_root),
            "--sessions-root",
            str(sessions_root),
        ]
    )

    assert exit_code == 29
    assert captured == {
        "command": [
            str(go_binary),
            "--backend-command-json",
            json.dumps(["/tmp/fake-python", "-m", "just_another_coding_agent"]),
            "--app-version",
            "0.1.4",
            "--model",
                "openai-responses:gpt-5.4",
            "--workspace-root",
            str(workspace_root.resolve()),
            "--sessions-root",
            str(sessions_root.resolve()),
            "--update-command-json",
            UPDATE_COMMAND_JSON,
            "--session-id",
            "fedcba9876543210fedcba9876543210",
            "--session-name",
            "auth-store-cleanup-followup",
            "--forked-from-session-id",
            "0123456789abcdef0123456789abcdef",
            "--forked-from-session-name",
            "auth-store-cleanup",
        ],
        "check": False,
        "cwd": None,
    }


def test_main_fork_launches_go_tui_with_new_session_and_parent_context(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"
    go_binary = tmp_path / ("jaca-go.exe" if os.name == "nt" else "jaca-go")
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        entry,
        "resolve_go_tui_launch",
        lambda: ([str(go_binary)], None),
    )
    monkeypatch.delenv("JACA_MODEL", raising=False)
    monkeypatch.setattr(entry, "load_config", lambda: {})
    monkeypatch.setattr(
        entry,
        "default_backend_command",
        lambda: ["/tmp/fake-python", "-m", "just_another_coding_agent"],
    )
    monkeypatch.setattr(entry, "package_version", lambda: "0.1.4")
    monkeypatch.setattr(
        entry,
        "explicit_update_command_json",
        lambda repo_root=None: UPDATE_COMMAND_JSON,
    )
    monkeypatch.setattr(
        entry,
        "resolve_session_reference",
        lambda **_: SimpleNamespace(
            session_id="0123456789abcdef0123456789abcdef",
            name="auth-store-cleanup",
            forked_from_session_id=None,
        ),
    )
    monkeypatch.setattr(
        entry,
        "create_fork",
        lambda **_: SimpleNamespace(
            session_id="fedcba9876543210fedcba9876543210",
            name="auth-store-cleanup-followup",
            forked_from_session_id="0123456789abcdef0123456789abcdef",
        ),
    )

    def fake_run(command, *, check, cwd=None):
        captured["command"] = command
        captured["check"] = check
        captured["cwd"] = cwd
        return SimpleNamespace(returncode=31)

    monkeypatch.setattr(
        "just_another_coding_agent.__main__.subprocess.run",
        fake_run,
    )

    exit_code = main(
        [
            "fork",
            "auth-store-cleanup",
            "--name",
            "auth store cleanup followup",
            "--workspace-root",
            str(workspace_root),
            "--sessions-root",
            str(sessions_root),
        ]
    )

    assert exit_code == 31
    assert captured == {
        "command": [
            str(go_binary),
            "--backend-command-json",
            json.dumps(["/tmp/fake-python", "-m", "just_another_coding_agent"]),
            "--app-version",
            "0.1.4",
            "--model",
                "openai-responses:gpt-5.4",
            "--workspace-root",
            str(workspace_root.resolve()),
            "--sessions-root",
            str(sessions_root.resolve()),
            "--update-command-json",
            UPDATE_COMMAND_JSON,
            "--session-id",
            "fedcba9876543210fedcba9876543210",
            "--session-name",
            "auth-store-cleanup-followup",
            "--forked-from-session-id",
            "0123456789abcdef0123456789abcdef",
            "--forked-from-session-name",
            "auth-store-cleanup",
        ],
        "check": False,
        "cwd": None,
    }


def test_main_resume_without_reference_prompts_for_recent_session_selection(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"
    go_binary = tmp_path / ("jaca-go.exe" if os.name == "nt" else "jaca-go")
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        entry,
        "resolve_go_tui_launch",
        lambda: ([str(go_binary)], None),
    )
    monkeypatch.setattr(entry, "load_config", lambda: {})
    monkeypatch.setattr(
        entry,
        "default_backend_command",
        lambda: ["/tmp/fake-python", "-m", "just_another_coding_agent"],
    )
    monkeypatch.setattr(entry, "package_version", lambda: "0.1.4")
    monkeypatch.setattr(
        entry,
        "explicit_update_command_json",
        lambda repo_root=None: UPDATE_COMMAND_JSON,
    )
    monkeypatch.setattr(
        entry,
        "list_workspace_sessions",
        lambda **_: [
            SimpleNamespace(
                session_id="1" * 32,
                name="first-session",
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            ),
            SimpleNamespace(
                session_id="2" * 32,
                name="second-session",
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            ),
        ],
    )
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _: "2")

    def fake_run(command, *, check, cwd=None):
        captured["command"] = command
        return SimpleNamespace(returncode=29)

    monkeypatch.setattr(
        "just_another_coding_agent.__main__.subprocess.run",
        fake_run,
    )

    exit_code = main(
        [
            "resume",
            "--workspace-root",
            str(workspace_root),
            "--sessions-root",
            str(sessions_root),
        ]
    )

    assert exit_code == 29
    assert captured["command"][-2:] == ["--session-name", "second-session"]
    assert captured["command"][-4:-2] == ["--session-id", "2" * 32]
    output = capsys.readouterr().out
    assert "Recent sessions" in output
    assert "1. first-session" in output
    assert "2. second-session" in output


def test_main_uses_saved_default_model_and_trace_mode(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"
    go_binary = tmp_path / ("jaca-go.exe" if os.name == "nt" else "jaca-go")
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        entry,
        "load_config",
        lambda: {
            "default_model": "openai-responses:gpt-5.4",
            "trace_mode": "local",
        },
    )
    monkeypatch.setattr(
        entry,
        "resolve_go_tui_launch",
        lambda: ([str(go_binary)], None),
    )
    monkeypatch.setattr(
        entry,
        "default_backend_command",
        lambda: ["/tmp/fake-python", "-m", "just_another_coding_agent"],
    )
    monkeypatch.setattr(entry, "package_version", lambda: "0.1.4")
    monkeypatch.setattr(
        entry,
        "explicit_update_command_json",
        lambda repo_root=None: UPDATE_COMMAND_JSON,
    )

    def fake_run(command, *, check, cwd=None):
        captured["command"] = command
        captured["trace_mode"] = os.environ.get("JACA_TRACE_MODE")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(
        "just_another_coding_agent.__main__.subprocess.run",
        fake_run,
    )

    exit_code = main(
        [
            "--workspace-root",
            str(workspace_root),
            "--sessions-root",
            str(sessions_root),
        ]
    )

    assert exit_code == 0
    assert captured == {
        "command": [
            str(go_binary),
            "--backend-command-json",
            json.dumps(["/tmp/fake-python", "-m", "just_another_coding_agent"]),
            "--app-version",
            "0.1.4",
            "--model",
            "openai-responses:gpt-5.4",
            "--workspace-root",
            str(workspace_root.resolve()),
            "--sessions-root",
            str(sessions_root.resolve()),
            "--update-command-json",
            UPDATE_COMMAND_JSON,
        ],
        "trace_mode": "local",
    }


def test_main_restores_config_applied_environment_after_return(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"
    go_binary = tmp_path / ("jaca-go.exe" if os.name == "nt" else "jaca-go")
    captured: dict[str, object] = {}

    monkeypatch.delenv("JACA_TRACE_MODE", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.setattr(
        entry,
        "load_config",
        lambda: {
            "default_model": "openai-responses:gpt-5.4",
            "trace_mode": "local",
            "OPENAI_BASE_URL": "https://example.test/v1",
        },
    )
    monkeypatch.setattr(
        entry,
        "resolve_go_tui_launch",
        lambda: ([str(go_binary)], None),
    )
    monkeypatch.setattr(
        entry,
        "default_backend_command",
        lambda: ["/tmp/fake-python", "-m", "just_another_coding_agent"],
    )
    monkeypatch.setattr(entry, "package_version", lambda: "0.1.4")
    monkeypatch.setattr(
        entry,
        "explicit_update_command_json",
        lambda repo_root=None: UPDATE_COMMAND_JSON,
    )

    def fake_run(command, *, check, cwd=None):
        captured["command"] = command
        captured["trace_mode"] = os.environ.get("JACA_TRACE_MODE")
        captured["openai_base_url"] = os.environ.get("OPENAI_BASE_URL")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(
        "just_another_coding_agent.__main__.subprocess.run",
        fake_run,
    )

    exit_code = main(
        [
            "--workspace-root",
            str(workspace_root),
            "--sessions-root",
            str(sessions_root),
        ]
    )

    assert exit_code == 0
    assert captured["trace_mode"] == "local"
    assert captured["openai_base_url"] == "https://example.test/v1"
    assert os.environ.get("JACA_TRACE_MODE") is None
    assert os.environ.get("OPENAI_BASE_URL") is None


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
    (repo_root / "cmd" / "jaca" / "main.go").write_text(
        "package main\n",
        encoding="utf-8",
    )
    (repo_root / "go.mod").write_text("module jaca\n", encoding="utf-8")
    (repo_root / "pyproject.toml").write_text(
        "[build-system]\nrequires=[]\n",
        encoding="utf-8",
    )
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
    monkeypatch.setattr(entry, "package_version", lambda: "0.1.4")
    monkeypatch.setattr(
        entry,
        "explicit_update_command_json",
        lambda repo_root=None: None,
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
            "--app-version",
            "0.1.4",
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
