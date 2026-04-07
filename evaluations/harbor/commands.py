from __future__ import annotations

import base64
import os
import shlex
import tomllib
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlparse

from just_another_coding_agent.auth import (
    resolve_openai_codex_oauth_credentials_sync,
    resolve_provider_secret,
)
from just_another_coding_agent.oauth_store import OAUTH_FILE_PATH
from just_another_coding_agent.runtime.models import OPENAI_CODEX_MODEL_NAME_BY_ID
from just_another_coding_agent.secret_store import AUTH_FILE_PATH

_COMMON_ENV_KEYS = ("JUST_ANOTHER_CODING_AGENT_THINKING",)
_OPENAI_ENV_KEYS = ("OPENAI_API_KEY", "OPENAI_BASE_URL")
_ANTHROPIC_ENV_KEYS = ("ANTHROPIC_API_KEY",)
_OPENAI_CODEX_OAUTH_ENV_KEYS = (
    "OPENAI_CODEX_OAUTH_ACCESS_TOKEN",
    "OPENAI_CODEX_OAUTH_REFRESH_TOKEN",
    "OPENAI_CODEX_OAUTH_EXPIRES_AT",
    "OPENAI_CODEX_OAUTH_ACCOUNT_ID",
)
_DEFAULT_HARBOR_LOGFIRE_SERVICE_NAME = "jaca-harbor"
_LOCAL_HOSTNAMES = frozenset({"localhost", "127.0.0.1", "::1"})


def _provider_env_keys_for_model(model: str) -> tuple[str, ...]:
    if _is_openai_codex_model(model):
        return _OPENAI_CODEX_OAUTH_ENV_KEYS
    if model.startswith("openai-responses:"):
        return _OPENAI_ENV_KEYS
    if model.startswith("openai:") or model.startswith("openai-chat:"):
        return _OPENAI_ENV_KEYS
    if model.startswith("anthropic:"):
        return _ANTHROPIC_ENV_KEYS
    raise ValueError(f"Unsupported Harbor model provider: {model}")


def _provider_name_for_model(model: str) -> str:
    if model.startswith(("openai-responses:", "openai:", "openai-chat:")):
        return "openai"
    if model.startswith("anthropic:"):
        return "anthropic"
    raise ValueError(f"Unsupported Harbor model provider: {model}")


def build_provider_env(
    *,
    model: str,
    environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    source = os.environ if environ is None else environ
    allowed_keys = (*_provider_env_keys_for_model(model), *_COMMON_ENV_KEYS)
    selected = {key: source[key] for key in allowed_keys if key in source}
    if _is_openai_codex_model(model):
        _inject_openai_codex_oauth_credentials(selected=selected)
    _inject_required_provider_secret(model=model, selected=selected)
    selected["JACA_TRACE_MODE"] = "logfire"
    selected["LOGFIRE_SERVICE_NAME"] = _resolve_logfire_service_name(source)
    selected["LOGFIRE_TOKEN"] = _resolve_logfire_token(source)
    return selected


def harbor_auth_file_uploads(model: str) -> list[tuple[Path, str]]:
    uploads: list[tuple[Path, str]] = []
    if AUTH_FILE_PATH.exists():
        uploads.append((AUTH_FILE_PATH, "/root/.jaca/auth.json"))
    if (_is_openai_codex_model(model) or _is_github_copilot_model(model)) and (
        OAUTH_FILE_PATH.exists()
    ):
        uploads.append((OAUTH_FILE_PATH, "/root/.jaca/oauth.json"))
    return uploads


def _inject_required_provider_secret(*, model: str, selected: dict[str, str]) -> None:
    env_key = _provider_secret_env_key(model)
    if not _harbor_model_requires_secret(model=model, selected=selected):
        return
    if env_key in selected and selected[env_key].strip():
        return
    provider = _provider_name_for_model(model)
    secret = resolve_provider_secret(provider)
    if not secret:
        raise ValueError(
            f"Harbor task model {model} requires {env_key}, but no provider "
            "secret is configured."
        )
    selected[env_key] = secret


def _provider_secret_env_key(model: str) -> str:
    if model.startswith(("openai-responses:", "openai:", "openai-chat:")):
        return "OPENAI_API_KEY"
    if model.startswith("anthropic:"):
        return "ANTHROPIC_API_KEY"
    raise ValueError(f"Unsupported Harbor model provider: {model}")


def _harbor_model_requires_secret(*, model: str, selected: Mapping[str, str]) -> bool:
    if _is_openai_codex_model(model):
        return False
    if model.startswith("anthropic:"):
        return True
    if model.startswith(("openai-responses:", "openai:", "openai-chat:")):
        return not _base_url_is_local(selected.get("OPENAI_BASE_URL"))
    raise ValueError(f"Unsupported Harbor model provider: {model}")


def _base_url_is_local(base_url: str | None) -> bool:
    if not base_url:
        return False
    hostname = (urlparse(base_url).hostname or "").strip().lower()
    return hostname in _LOCAL_HOSTNAMES


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


def _is_openai_codex_model(model: str) -> bool:
    if not model.startswith("openai-responses:"):
        return False
    model_name = model.split(":", 1)[1]
    return model_name in OPENAI_CODEX_MODEL_NAME_BY_ID


def _is_github_copilot_model(model: str) -> bool:
    return (
        model.startswith(("openai-responses:", "openai-chat:", "anthropic:"))
        and model.endswith("-copilot")
    )


def _inject_openai_codex_oauth_credentials(*, selected: dict[str, str]) -> None:
    credentials = resolve_openai_codex_oauth_credentials_sync()
    if credentials is None:
        raise ValueError(
            "Harbor task ChatGPT model requires openai-codex OAuth login, "
            "but no OAuth credentials are configured."
        )
    selected["OPENAI_CODEX_OAUTH_ACCESS_TOKEN"] = credentials.access
    selected["OPENAI_CODEX_OAUTH_REFRESH_TOKEN"] = credentials.refresh
    selected["OPENAI_CODEX_OAUTH_EXPIRES_AT"] = str(credentials.expires)
    selected["OPENAI_CODEX_OAUTH_ACCOUNT_ID"] = credentials.account_id


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
