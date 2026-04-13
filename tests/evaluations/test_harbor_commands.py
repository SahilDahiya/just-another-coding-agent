from evaluations.harbor.commands import (
    DEFAULT_HARBOR_SESSIONS_ROOT,
    build_harbor_exec_command,
    build_provider_env,
    harbor_auth_file_uploads,
    resolve_harbor_sessions_root,
)


def test_build_provider_env_filters_to_openai_provider_env() -> None:
    env = build_provider_env(
        model="openai-responses:gpt-5.3-codex",
        environ={
            "OPENAI_API_KEY": "secret",
            "OPENAI_BASE_URL": "https://example.test/v1",
            "LOGFIRE_TOKEN": "logfire-secret",
            "OLLAMA_BASE_URL": "https://ollama.com/v1",
            "OLLAMA_API_KEY": "ollama-secret",
            "ANTHROPIC_API_KEY": "anthropic-secret",
            "JUST_ANOTHER_CODING_AGENT_THINKING": "high",
            "JACA_SESSION_AUTO_COMPACTION_CONTEXT_WINDOW_UTILIZATION": "0.1",
            "UNRELATED": "ignored",
        },
    )

    assert env == {
        "OPENAI_API_KEY": "secret",
        "OPENAI_BASE_URL": "https://example.test/v1",
        "JUST_ANOTHER_CODING_AGENT_THINKING": "high",
        "JACA_SESSION_AUTO_COMPACTION_CONTEXT_WINDOW_UTILIZATION": "0.1",
        "JACA_TRACE_MODE": "logfire",
        "LOGFIRE_SERVICE_NAME": "jaca-harbor",
        "LOGFIRE_TOKEN": "logfire-secret",
    }


def test_build_provider_env_exports_openai_codex_oauth_credentials(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "evaluations.harbor.commands.resolve_openai_codex_oauth_credentials_sync",
        lambda: type(
            "Creds",
            (),
            {
                "access": "oauth-access",
                "refresh": "oauth-refresh",
                "expires": 1234567890000,
                "account_id": "acct-123",
            },
        )(),
    )

    env = build_provider_env(
        model="openai-responses:gpt-5.4-chatgpt",
        environ={
            "LOGFIRE_TOKEN": "logfire-secret",
            "OPENAI_API_KEY": "secret",
            "OPENAI_BASE_URL": "https://example.test/v1",
            "ANTHROPIC_API_KEY": "anthropic-secret",
            "JUST_ANOTHER_CODING_AGENT_THINKING": "high",
            "UNRELATED": "ignored",
        },
    )

    assert env == {
        "OPENAI_CODEX_OAUTH_ACCESS_TOKEN": "oauth-access",
        "OPENAI_CODEX_OAUTH_REFRESH_TOKEN": "oauth-refresh",
        "OPENAI_CODEX_OAUTH_EXPIRES_AT": "1234567890000",
        "OPENAI_CODEX_OAUTH_ACCOUNT_ID": "acct-123",
        "JUST_ANOTHER_CODING_AGENT_THINKING": "high",
        "JACA_TRACE_MODE": "logfire",
        "LOGFIRE_SERVICE_NAME": "jaca-harbor",
        "LOGFIRE_TOKEN": "logfire-secret",
    }


def test_harbor_auth_file_uploads_include_auth_and_chatgpt_oauth(
    monkeypatch, tmp_path
) -> None:
    auth_file = tmp_path / "auth.json"
    oauth_file = tmp_path / "oauth.json"
    auth_file.write_text('{"OPENAI_API_KEY":"test"}\n', encoding="utf-8")
    oauth_file.write_text('{"openai-codex":{}}\n', encoding="utf-8")
    monkeypatch.setattr(
        "evaluations.harbor.commands.AUTH_FILE_PATH",
        auth_file,
    )
    monkeypatch.setattr(
        "evaluations.harbor.commands.OAUTH_FILE_PATH",
        oauth_file,
    )

    uploads = harbor_auth_file_uploads("openai-responses:gpt-5.4-chatgpt")

    assert uploads == [
        (auth_file, "/root/.jaca/auth.json"),
        (oauth_file, "/root/.jaca/oauth.json"),
    ]


def test_harbor_auth_file_uploads_skip_oauth_for_api_key_model(
    monkeypatch, tmp_path
) -> None:
    auth_file = tmp_path / "auth.json"
    oauth_file = tmp_path / "oauth.json"
    auth_file.write_text('{"OPENAI_API_KEY":"test"}\n', encoding="utf-8")
    oauth_file.write_text('{"openai-codex":{}}\n', encoding="utf-8")
    monkeypatch.setattr(
        "evaluations.harbor.commands.AUTH_FILE_PATH",
        auth_file,
    )
    monkeypatch.setattr(
        "evaluations.harbor.commands.OAUTH_FILE_PATH",
        oauth_file,
    )

    uploads = harbor_auth_file_uploads("openai-responses:gpt-5.4")

    assert uploads == [
        (auth_file, "/root/.jaca/auth.json"),
    ]

def test_build_provider_env_uses_explicit_service_name_override() -> None:
    env = build_provider_env(
        model="openai-responses:gpt-5.4",
        environ={
            "OPENAI_API_KEY": "openai-secret",
            "JUST_ANOTHER_CODING_AGENT_THINKING": "high",
            "JACA_TRACE_MODE": "local",
            "LOGFIRE_SERVICE_NAME": "harbor-task",
            "LOGFIRE_TOKEN": "logfire-secret",
        },
    )

    assert env == {
        "OPENAI_API_KEY": "openai-secret",
        "JUST_ANOTHER_CODING_AGENT_THINKING": "high",
        "JACA_TRACE_MODE": "logfire",
        "LOGFIRE_SERVICE_NAME": "harbor-task",
        "LOGFIRE_TOKEN": "logfire-secret",
    }


def test_build_provider_env_reads_logfire_token_from_default_credentials_file(
    monkeypatch, tmp_path
) -> None:
    credentials_dir = tmp_path / ".logfire"
    credentials_dir.mkdir()
    (credentials_dir / "default.toml").write_text(
        '[tokens]\n"https://logfire-us.pydantic.dev" = "logfire-secret"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(tmp_path))

    env = build_provider_env(
        model="openai-responses:gpt-5.4",
        environ={
            "OPENAI_API_KEY": "openai-secret",
            "JUST_ANOTHER_CODING_AGENT_THINKING": "high",
        },
    )

    assert env == {
        "OPENAI_API_KEY": "openai-secret",
        "JUST_ANOTHER_CODING_AGENT_THINKING": "high",
        "JACA_TRACE_MODE": "logfire",
        "LOGFIRE_SERVICE_NAME": "jaca-harbor",
        "LOGFIRE_TOKEN": "logfire-secret",
    }


def test_build_provider_env_requires_logfire_credentials(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("LOGFIRE_TOKEN", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    try:
        build_provider_env(
            model="openai-responses:gpt-5.4",
            environ={
                "OPENAI_API_KEY": "openai-secret",
            },
        )
    except ValueError as error:
        assert str(error) == (
            "Harbor tasks always export traces to Logfire and require host "
            "Logfire credentials. Run `uv run logfire auth` and `uv run "
            "logfire projects use <project>` or set `LOGFIRE_TOKEN`."
        )
    else:  # pragma: no cover - defensive
        raise AssertionError("Expected Harbor run without Logfire credentials to fail")


def test_build_provider_env_requires_openai_codex_oauth_for_chatgpt_model(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "evaluations.harbor.commands.resolve_openai_codex_oauth_credentials_sync",
        lambda: None,
    )
    try:
        build_provider_env(
            model="openai-responses:gpt-5.4-chatgpt",
            environ={
                "JUST_ANOTHER_CODING_AGENT_THINKING": "high",
                "LOGFIRE_TOKEN": "logfire-secret",
            },
        )
    except ValueError as error:
        assert str(error) == (
            "Harbor task ChatGPT model requires openai-codex OAuth login, "
            "but no OAuth credentials are configured."
        )
    else:  # pragma: no cover - defensive
        raise AssertionError("Expected Harbor ChatGPT model without OAuth to fail")


def test_build_provider_env_injects_stored_openai_secret_when_host_env_missing(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "evaluations.harbor.commands.resolve_provider_secret",
        lambda provider: "openai-secret" if provider == "openai" else None,
    )

    env = build_provider_env(
        model="openai-responses:gpt-5.4",
        environ={
            "JUST_ANOTHER_CODING_AGENT_THINKING": "high",
            "LOGFIRE_TOKEN": "logfire-secret",
        },
    )

    assert env["OPENAI_API_KEY"] == "openai-secret"


def test_build_provider_env_rejects_unsupported_model_provider() -> None:
    try:
        build_provider_env(model="test", environ={})
    except ValueError as error:
        assert str(error) == "Unsupported Harbor model provider: test"
    else:  # pragma: no cover - defensive
        raise AssertionError("Expected unsupported Harbor model provider to fail")


def test_build_harbor_exec_command_preserves_exact_model_string() -> None:
    command = build_harbor_exec_command(
        instruction="solve it",
        model="openai-responses:gpt-5.3-codex",
    )

    assert "evaluations.bench.exec_prompt" in command
    assert "openai-responses:gpt-5.3-codex" in command
    assert "printf %s " in command
    assert " base64 -d | " in command
    assert "/installed-agent/just-another-coding-agent/.venv/bin/python -m " in command
    assert f" --sessions-root {DEFAULT_HARBOR_SESSIONS_ROOT} " in command
    assert " -C . -" in command


def test_build_harbor_exec_command_forwards_thinking_when_requested() -> None:
    command = build_harbor_exec_command(
        instruction="solve it",
        model="openai-responses:gpt-5.3-codex",
        thinking="high",
    )

    assert " --thinking high " in command


def test_resolve_harbor_sessions_root_uses_hidden_tmp_default() -> None:
    assert resolve_harbor_sessions_root(environ={}) == DEFAULT_HARBOR_SESSIONS_ROOT


def test_resolve_harbor_sessions_root_uses_absolute_override() -> None:
    assert resolve_harbor_sessions_root(
        environ={"JACA_HARBOR_SESSIONS_ROOT": "/var/tmp/jaca-harbor-sessions"}
    ) == "/var/tmp/jaca-harbor-sessions"


def test_resolve_harbor_sessions_root_accepts_posix_container_path() -> None:
    assert resolve_harbor_sessions_root(
        environ={"JACA_HARBOR_SESSIONS_ROOT": "/tmp/.jaca/harbor-sessions"}
    ) == "/tmp/.jaca/harbor-sessions"


def test_resolve_harbor_sessions_root_rejects_relative_override() -> None:
    try:
        resolve_harbor_sessions_root(
            environ={"JACA_HARBOR_SESSIONS_ROOT": "tmp/jaca-harbor-sessions"}
        )
    except ValueError as error:
        assert str(error) == (
            "JACA_HARBOR_SESSIONS_ROOT must be an absolute path inside the "
            "Harbor task container: tmp/jaca-harbor-sessions"
        )
    else:  # pragma: no cover - defensive
        raise AssertionError("Expected relative Harbor sessions root to fail")
