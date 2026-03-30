from __future__ import annotations

import base64
import os
import shlex
from collections.abc import Mapping

_COMMON_ENV_KEYS = ("JUST_ANOTHER_CODING_AGENT_THINKING",)
_OPENAI_ENV_KEYS = ("OPENAI_API_KEY", "OPENAI_BASE_URL")
_OLLAMA_ENV_KEYS = ("OLLAMA_API_KEY", "OLLAMA_BASE_URL")
_ANTHROPIC_ENV_KEYS = ("ANTHROPIC_API_KEY",)
_DEFAULT_OLLAMA_BASE_URL = "https://ollama.com/v1"


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
    return selected


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
