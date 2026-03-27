from just_another_coding_agent_adapters.harbor.commands import (
    build_harbor_exec_command,
    build_provider_env,
)


def test_build_provider_env_passes_supported_provider_env() -> None:
    env = build_provider_env(
        {
            "OPENAI_API_KEY": "secret",
            "OPENAI_BASE_URL": "https://example.test/v1",
            "OLLAMA_BASE_URL": "https://ollama.com/v1",
            "OLLAMA_API_KEY": "ollama-secret",
            "JUST_ANOTHER_CODING_AGENT_THINKING": "high",
            "UNRELATED": "ignored",
        }
    )

    assert env == {
        "OPENAI_API_KEY": "secret",
        "OPENAI_BASE_URL": "https://example.test/v1",
        "OLLAMA_BASE_URL": "https://ollama.com/v1",
        "OLLAMA_API_KEY": "ollama-secret",
        "JUST_ANOTHER_CODING_AGENT_THINKING": "high",
    }


def test_build_harbor_exec_command_preserves_exact_model_string() -> None:
    command = build_harbor_exec_command(
        instruction="solve it",
        model="openai-responses:gpt-5.3-codex",
    )

    assert "just_another_coding_agent_adapters.bench.exec_prompt" in command
    assert "openai-responses:gpt-5.3-codex" in command
    assert "printf %s " in command
    assert " base64 -d | " in command
    assert "/installed-agent/just-another-coding-agent/.venv/bin/python -m " in command
    assert " --sessions-root /tmp/just-another-coding-agent-sessions " in command
    assert " -C . -" in command


def test_build_harbor_exec_command_forwards_thinking_when_requested() -> None:
    command = build_harbor_exec_command(
        instruction="solve it",
        model="openai-responses:gpt-5.3-codex",
        thinking="high",
    )

    assert " --thinking high " in command
