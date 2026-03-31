from __future__ import annotations

import base64
import os
import shlex
import tomllib
from collections.abc import Mapping
from pathlib import Path

_COMMON_ENV_KEYS = ("JUST_ANOTHER_CODING_AGENT_THINKING",)
_OPENAI_ENV_KEYS = ("OPENAI_API_KEY", "OPENAI_BASE_URL")
_OLLAMA_ENV_KEYS = ("OLLAMA_API_KEY", "OLLAMA_BASE_URL")
_ANTHROPIC_ENV_KEYS = ("ANTHROPIC_API_KEY",)
_DEFAULT_OLLAMA_BASE_URL = "https://ollama.com/v1"
_DEFAULT_HARBOR_LOGFIRE_SERVICE_NAME = "jaca-harbor"


def _provider_env_keys_for_model(model: str) -> tuple[str, ...]:
    if model.startswith("openai-responses:"):
        return _OPENAI_ENV_KEYS
    if model.startswith("openai:") or model.startswith("openai-chat:"):
        return _OPENAI_ENV_KEYS
    if model.startswith("ollama:"):
        return _OLLAMA_ENV_KEYS
    if model.startswith("anthropic:"):
        return _ANTHROPIC_ENV_KEYS
    raise ValueError(f"Unsupported Harbor model provider: {model}")


def build_provider_env(
    *,
    model: str,
    environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    source = os.environ if environ is None else environ
    allowed_keys = (*_provider_env_keys_for_model(model), *_COMMON_ENV_KEYS)
    selected = {key: source[key] for key in allowed_keys if key in source}
    if model.startswith("ollama:") and "OLLAMA_BASE_URL" not in selected:
        selected["OLLAMA_BASE_URL"] = _DEFAULT_OLLAMA_BASE_URL
    selected["JACA_TRACE_MODE"] = "logfire"
    selected["LOGFIRE_SERVICE_NAME"] = _resolve_logfire_service_name(source)
    selected["LOGFIRE_TOKEN"] = _resolve_logfire_token(source)
    return selected


def _resolve_logfire_service_name(source: Mapping[str, str]) -> str:
    value = source.get("LOGFIRE_SERVICE_NAME", "").strip()
    if value:
        return value
    return _DEFAULT_HARBOR_LOGFIRE_SERVICE_NAME


def _resolve_logfire_token(source: Mapping[str, str]) -> str:
    explicit = source.get("LOGFIRE_TOKEN", "").strip()
    if explicit:
        return explicit

    config_path = _resolve_home_dir(source) / ".logfire" / "default.toml"
    if not config_path.exists():
        raise ValueError(
            "Harbor tasks always export traces to Logfire and require host "
            "Logfire credentials. Run `uv run logfire auth` and `uv run "
            "logfire projects use <project>` or set `LOGFIRE_TOKEN`."
        )

    try:
        with config_path.open("rb") as handle:
            config = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ValueError(
            f"Invalid Logfire credentials file: {config_path}"
        ) from error

    tokens = config.get("tokens")
    if not isinstance(tokens, dict):
        raise ValueError(
            "Harbor tasks always export traces to Logfire and require host "
            "Logfire credentials. Run `uv run logfire auth` and `uv run "
            "logfire projects use <project>` or set `LOGFIRE_TOKEN`."
        )

    for value in tokens.values():
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            token = value.get("token")
            if isinstance(token, str) and token.strip():
                return token.strip()

    raise ValueError(
        "Harbor tasks always export traces to Logfire and require host "
        "Logfire credentials. Run `uv run logfire auth` and `uv run "
        "logfire projects use <project>` or set `LOGFIRE_TOKEN`."
    )


def _resolve_home_dir(source: Mapping[str, str]) -> Path:
    for key in ("HOME", "USERPROFILE"):
        value = source.get(key, "").strip()
        if value:
            return Path(value)
        env_value = os.environ.get(key, "").strip()
        if env_value:
            return Path(env_value)
    return Path.home()


def build_harbor_exec_command(
    *,
    instruction: str,
    model: str,
    thinking: str | None = None,
    workspace_root: str = ".",
    sessions_root: str = "/tmp/just-another-coding-agent-sessions",
) -> str:
    prompt_b64 = base64.b64encode(instruction.encode("utf-8")).decode("ascii")
    python_executable = "/installed-agent/just-another-coding-agent/.venv/bin/python"
    thinking_arg = (
        f"--thinking {shlex.quote(thinking)} " if thinking is not None else ""
    )
    return (
        f"printf %s {shlex.quote(prompt_b64)} | base64 -d | "
        f"{shlex.quote(python_executable)} -m "
        "evaluations.bench.exec_prompt "
        f"--model {shlex.quote(model)} "
        f"{thinking_arg}"
        f"--sessions-root {shlex.quote(sessions_root)} "
        f"-C {shlex.quote(workspace_root)} - "
        "2>&1 | stdbuf -oL tee /logs/agent/just-another-coding-agent.txt"
    )
