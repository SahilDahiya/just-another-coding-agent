from __future__ import annotations

import json
import os
from contextlib import nullcontext
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import pytest

import just_another_coding_agent.__main__ as entry
from just_another_coding_agent.__main__ import main
from just_another_coding_agent.go_tui import GO_TUI_BINARY, AvailableUpdate


class _TTYBuffer(StringIO):
    def isatty(self) -> bool:
        return True


class _NonTTYBuffer(StringIO):
    def isatty(self) -> bool:
        return False


@pytest.fixture(autouse=True)
def _disable_update_notice(monkeypatch) -> None:
    monkeypatch.setattr(entry, "available_installed_update", lambda **_: None)


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
    monkeypatch.setattr(entry, "package_version", lambda: "0.1.5")

    def fake_run(command, *, check, cwd=None, env=None):
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
            "0.1.5",
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
    monkeypatch.setattr(entry, "package_version", lambda: "0.1.5")
    monkeypatch.setattr(
        entry,
        "resolve_session_reference",
        lambda **_: SimpleNamespace(
            session_id="0123456789abcdef0123456789abcdef",
            name="auth-store-cleanup",
            forked_from_session_id=None,
        ),
    )

    def fake_run(command, *, check, cwd=None, env=None):
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
            "0.1.5",
            "--model",
                "openai-responses:gpt-5.4",
            "--workspace-root",
            str(workspace_root.resolve()),
            "--sessions-root",
            str(sessions_root.resolve()),
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
    monkeypatch.setattr(entry, "package_version", lambda: "0.1.5")

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

    def fake_run(command, *, check, cwd=None, env=None):
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
            "0.1.5",
            "--model",
                "openai-responses:gpt-5.4",
            "--workspace-root",
            str(workspace_root.resolve()),
            "--sessions-root",
            str(sessions_root.resolve()),
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
    monkeypatch.setattr(entry, "package_version", lambda: "0.1.5")
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

    def fake_run(command, *, check, cwd=None, env=None):
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
            "0.1.5",
            "--model",
                "openai-responses:gpt-5.4",
            "--workspace-root",
            str(workspace_root.resolve()),
            "--sessions-root",
            str(sessions_root.resolve()),
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
    monkeypatch.setattr(entry, "package_version", lambda: "0.1.5")
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
    monkeypatch.setattr(entry, "_resume_picker_input_mode", nullcontext)
    keys = iter(["down", "enter"])
    monkeypatch.setattr(entry, "_read_resume_picker_key", lambda: next(keys))

    def fake_run(command, *, check, cwd=None, env=None):
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
    assert "Use up/down arrows, then press Enter to resume." in output
    assert "first-session" in output
    assert "second-session" in output
    assert "2" * 32 not in output


def test_build_resume_selection_options_uses_first_prompt_for_unnamed_sessions(
    tmp_path,
    monkeypatch,
) -> None:
    sessions_root = tmp_path / "sessions"
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    updated_at = datetime(2026, 4, 13, 1, 15, tzinfo=UTC)

    monkeypatch.setattr(
        entry,
        "session_path_for_id",
        lambda **kwargs: (
            Path(kwargs["sessions_root"]) / f'{kwargs["session_id"]}.jsonl'
        ),
    )
    monkeypatch.setattr(
        entry,
        "load_session",
        lambda *, path, workspace_root: SimpleNamespace(
            runs=[
                SimpleNamespace(
                    prompt=(
                        "Investigate auth-store cleanup"
                        if path.stem.startswith("2")
                        else "Follow up on login UX"
                    )
                )
            ]
        ),
    )

    options = entry._build_resume_selection_options(
        sessions_root=sessions_root,
        workspace_root=workspace_root,
        sessions=[
            SimpleNamespace(
                session_id="1" * 32,
                name="saved-name",
                created_at=updated_at,
                updated_at=updated_at,
            ),
            SimpleNamespace(
                session_id="2" * 32,
                name=None,
                created_at=updated_at,
                updated_at=updated_at,
            ),
        ],
    )

    assert options[0].label == "saved-name"
    assert options[0].subtitle == entry._format_resume_timestamp(updated_at)
    assert options[1].label == "Investigate auth-store cleanup"
    assert options[1].subtitle == entry._format_resume_timestamp(updated_at)
    rendered = " ".join(
        filter(
            None,
            [
                options[0].label,
                options[0].subtitle,
                options[1].label,
                options[1].subtitle,
            ],
        )
    )
    assert "1" * 32 not in rendered
    assert "2" * 32 not in rendered


def test_main_resume_without_reference_uses_first_prompt_as_session_name(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"
    go_binary = tmp_path / ("jaca-go.exe" if os.name == "nt" else "jaca-go")
    captured: dict[str, object] = {}
    updated_at = datetime.now(UTC)

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
    monkeypatch.setattr(entry, "package_version", lambda: "0.1.5")
    monkeypatch.setattr(
        entry,
        "list_workspace_sessions",
        lambda **_: [
            SimpleNamespace(
                session_id="1" * 32,
                name=None,
                created_at=updated_at,
                updated_at=updated_at,
            )
        ],
    )
    monkeypatch.setattr(
        entry,
        "session_path_for_id",
        lambda **kwargs: (
            Path(kwargs["sessions_root"]) / f'{kwargs["session_id"]}.jsonl'
        ),
    )
    monkeypatch.setattr(
        entry,
        "load_session",
        lambda *, path, workspace_root: SimpleNamespace(
            runs=[SimpleNamespace(prompt="Investigate auth-store cleanup")]
        ),
    )
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr(entry, "_resume_picker_input_mode", nullcontext)
    keys = iter(["enter"])
    monkeypatch.setattr(entry, "_read_resume_picker_key", lambda: next(keys))

    def fake_run(command, *, check, cwd=None, env=None):
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
    assert captured["command"][-2:] == [
        "--session-name",
        "Investigate auth-store cleanup",
    ]


@pytest.mark.parametrize(
    ("sequence", "want"),
    [
        ("[A", "up"),
        ("[B", "down"),
        ("OA", "up"),
        ("OB", "down"),
        ("[1;5A", "up"),
        ("[1;5B", "down"),
        ("[C", "other"),
        ("", "other"),
    ],
)
def test_decode_resume_picker_escape_sequence(sequence: str, want: str) -> None:
    assert entry._decode_resume_picker_escape_sequence(sequence) == want


@pytest.mark.skipif(os.name == "nt", reason="POSIX key reader only")
@pytest.mark.parametrize(
    ("chars", "want"),
    [
        (["\x1b", "[", "A"], "up"),
        (["\x1b", "[", "B"], "down"),
        (["\x1b", "O", "A"], "up"),
        (["\x1b", "O", "B"], "down"),
        (["j"], "down"),
        (["k"], "up"),
    ],
)
def test_read_resume_picker_key_posix_handles_arrows(monkeypatch, chars, want) -> None:
    class _FakeStdin:
        def __init__(self, values: list[str]) -> None:
            self._values = iter(values)

        def read(self, n: int = 1) -> str:
            return next(self._values)

    monkeypatch.setattr(entry.sys, "stdin", _FakeStdin(chars))

    assert entry._read_resume_picker_key_posix() == want


@pytest.mark.skipif(os.name == "nt", reason="POSIX terminal mode only")
def test_resume_picker_input_mode_uses_cbreak(monkeypatch) -> None:
    import termios
    import tty

    calls: list[tuple[object, ...]] = []

    monkeypatch.setattr("sys.stdin.fileno", lambda: 7)
    monkeypatch.setattr(termios, "tcgetattr", lambda fd: ["original", fd])
    monkeypatch.setattr(
        termios,
        "tcsetattr",
        lambda fd, when, attrs: calls.append(("restore", fd, when, attrs)),
    )
    monkeypatch.setattr(tty, "setcbreak", lambda fd: calls.append(("cbreak", fd)))
    monkeypatch.setattr(tty, "setraw", lambda fd: calls.append(("raw", fd)))

    with entry._resume_picker_input_mode():
        pass

    assert ("cbreak", 7) in calls
    assert not any(call[0] == "raw" for call in calls)
    assert (
        "restore",
        7,
        termios.TCSADRAIN,
        ["original", 7],
    ) in calls


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
    monkeypatch.setattr(entry, "package_version", lambda: "0.1.5")

    def fake_run(command, *, check, cwd=None, env=None):
        captured["command"] = command
        captured["trace_mode"] = env.get("JACA_TRACE_MODE") if env is not None else None
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
            "0.1.5",
            "--model",
            "openai-responses:gpt-5.4",
            "--workspace-root",
            str(workspace_root.resolve()),
            "--sessions-root",
            str(sessions_root.resolve()),
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
    monkeypatch.setattr(entry, "package_version", lambda: "0.1.5")

    def fake_run(command, *, check, cwd=None, env=None):
        captured["command"] = command
        captured["trace_mode"] = env.get("JACA_TRACE_MODE") if env is not None else None
        captured["openai_base_url"] = (
            env.get("OPENAI_BASE_URL") if env is not None else None
        )
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
                "Installed Go TUI binary is missing. Restore it explicitly with "
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
    monkeypatch.setattr(entry, "package_version", lambda: "0.1.5")

    def fake_run(command, *, check, cwd=None, env=None):
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
            "0.1.5",
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


def test_run_tui_passes_available_update_to_go_tui(
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
    monkeypatch.setattr(entry, "package_version", lambda: "0.1.5")
    monkeypatch.setattr(
        entry,
        "available_installed_update",
        lambda **_: AvailableUpdate(
            current_version="0.1.5",
            latest_version="0.1.6",
            command=("uv", "tool", "upgrade", "just-another-coding-agent"),
        ),
    )
    monkeypatch.setattr(
        entry,
        "default_backend_command",
        lambda: ["/tmp/fake-python", "-m", "just_another_coding_agent"],
    )
    monkeypatch.setattr(
        entry,
        "refresh_cached_release_version_in_background",
        lambda *, repo_root: captured.setdefault("refresh_repo_root", repo_root),
    )

    def fake_run(command, *, check, cwd=None, env=None):
        captured["command"] = command
        captured["check"] = check
        captured["cwd"] = cwd
        return SimpleNamespace(returncode=19)

    monkeypatch.setattr(
        "just_another_coding_agent.__main__.subprocess.run",
        fake_run,
    )

    exit_code = entry._run_tui(
        model="openai:test-model",
        workspace_root=workspace_root.resolve(),
        sessions_root=sessions_root.resolve(),
        thinking=None,
    )

    assert exit_code == 19
    assert captured["refresh_repo_root"] is None
    assert captured["command"] == [
        str(go_binary),
        "--backend-command-json",
        json.dumps(["/tmp/fake-python", "-m", "just_another_coding_agent"]),
        "--app-version",
        "0.1.5",
        "--available-update-version",
        "0.1.6",
        "--available-update-command-json",
        json.dumps(["uv", "tool", "upgrade", "just-another-coding-agent"]),
        "--model",
        "openai:test-model",
        "--workspace-root",
        str(workspace_root.resolve()),
        "--sessions-root",
        str(sessions_root.resolve()),
    ]

def test_run_tui_omits_available_update_flags_when_none_are_available(
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
    monkeypatch.setattr(entry, "package_version", lambda: "0.1.5")
    monkeypatch.setattr(entry, "available_installed_update", lambda **_: None)
    monkeypatch.setattr(
        entry,
        "default_backend_command",
        lambda: ["/tmp/fake-python", "-m", "just_another_coding_agent"],
    )
    monkeypatch.setattr(
        entry,
        "refresh_cached_release_version_in_background",
        lambda *, repo_root: captured.setdefault("refresh_repo_root", repo_root),
    )

    def fake_run(command, *, check, cwd=None, env=None):
        captured["command"] = command
        captured["check"] = check
        captured["cwd"] = cwd
        return SimpleNamespace(returncode=7)

    monkeypatch.setattr(
        "just_another_coding_agent.__main__.subprocess.run",
        fake_run,
    )

    exit_code = entry._run_tui(
        model="openai:test-model",
        workspace_root=workspace_root.resolve(),
        sessions_root=sessions_root.resolve(),
        thinking=None,
    )

    assert exit_code == 7
    assert captured["refresh_repo_root"] is None
    assert captured["command"] == [
        str(go_binary),
        "--backend-command-json",
        json.dumps(["/tmp/fake-python", "-m", "just_another_coding_agent"]),
        "--app-version",
        "0.1.5",
        "--model",
        "openai:test-model",
        "--workspace-root",
        str(workspace_root.resolve()),
        "--sessions-root",
        str(sessions_root.resolve()),
    ]
    assert captured["command"] == [
        str(go_binary),
        "--backend-command-json",
        json.dumps(["/tmp/fake-python", "-m", "just_another_coding_agent"]),
        "--app-version",
        "0.1.5",
        "--model",
        "openai:test-model",
        "--workspace-root",
        str(workspace_root.resolve()),
        "--sessions-root",
        str(sessions_root.resolve()),
    ]


def test_run_tui_bootstraps_windows_search_tools_before_launch(
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
    monkeypatch.setattr(entry, "package_version", lambda: "0.1.5")
    monkeypatch.setattr(
        entry,
        "default_backend_command",
        lambda: ["/tmp/fake-python", "-m", "just_another_coding_agent"],
    )
    monkeypatch.setattr(
        entry,
        "bootstrap_windows_search_tools",
        lambda *, writer: captured.setdefault("writer", writer),
    )

    def fake_run(command, *, check, cwd=None, env=None):
        captured["command"] = command
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(
        "just_another_coding_agent.__main__.subprocess.run",
        fake_run,
    )

    output_stream = _TTYBuffer()
    exit_code = entry._run_tui(
        model="openai:test-model",
        workspace_root=workspace_root.resolve(),
        sessions_root=sessions_root.resolve(),
        thinking=None,
        input_stream=_TTYBuffer("\n"),
        output_stream=output_stream,
    )

    assert exit_code == 0
    assert captured["writer"] is output_stream
    assert captured["command"][0] == str(go_binary)


def test_run_tui_reports_explicit_windows_policy_block_for_installed_binary(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"
    go_binary = tmp_path / "jaca-go.exe"

    monkeypatch.setattr(
        entry,
        "resolve_go_tui_launch",
        lambda: ([str(go_binary)], None),
    )
    monkeypatch.setattr(entry, "package_version", lambda: "0.1.5")
    monkeypatch.setattr(
        entry,
        "default_backend_command",
        lambda: ["/tmp/fake-python", "-m", "just_another_coding_agent"],
    )
    monkeypatch.setattr(entry, "find_go_tui_repo_root", lambda: None)

    def fake_run(command, *, check, cwd=None, env=None):
        del command, check, cwd, env
        error = OSError("Application Control policy has blocked this file")
        error.winerror = 216
        raise error

    monkeypatch.setattr(
        "just_another_coding_agent.__main__.subprocess.run",
        fake_run,
    )

    with pytest.raises(
        RuntimeError,
        match="Windows blocked the JACA Go TUI executable before launch",
    ) as excinfo:
        entry._run_tui(
            model="openai:test-model",
            workspace_root=workspace_root.resolve(),
            sessions_root=sessions_root.resolve(),
            thinking=None,
        )

    message = str(excinfo.value)
    assert str(go_binary) in message
    assert "uv tool" in message


def test_run_tui_reports_repo_go_run_workaround_for_windows_policy_block(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sessions_root = tmp_path / "sessions"
    go_binary = tmp_path / "jaca-go.exe"
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    monkeypatch.setattr(
        entry,
        "resolve_go_tui_launch",
        lambda: ([str(go_binary)], None),
    )
    monkeypatch.setattr(entry, "package_version", lambda: "0.1.5")
    monkeypatch.setattr(
        entry,
        "default_backend_command",
        lambda: ["/tmp/fake-python", "-m", "just_another_coding_agent"],
    )
    monkeypatch.setattr(entry, "find_go_tui_repo_root", lambda: repo_root)
    monkeypatch.setattr(entry.shutil, "which", lambda name: "C:/Go/bin/go.exe" if name == "go" else None)

    def fake_run(command, *, check, cwd=None, env=None):
        del command, check, cwd, env
        error = OSError("This version of %1 is not compatible with the version of Windows you're running")
        error.winerror = 216
        raise error

    monkeypatch.setattr(
        "just_another_coding_agent.__main__.subprocess.run",
        fake_run,
    )

    with pytest.raises(RuntimeError, match="Repo workaround: JACA_GO_RUN=1 uv run jaca") as excinfo:
        entry._run_tui(
            model="openai:test-model",
            workspace_root=workspace_root.resolve(),
            sessions_root=sessions_root.resolve(),
            thinking=None,
        )

    assert (
        "Release fix: publish a verified Windows wheel whose bundled jaca-go.exe launches after uv tool install."
        in str(excinfo.value)
    )
