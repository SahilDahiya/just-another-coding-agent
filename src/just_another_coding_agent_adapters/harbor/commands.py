from __future__ import annotations

import base64
import os
import shlex
from collections.abc import Mapping

_PROVIDER_ENV_KEYS = (
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OLLAMA_API_KEY",
    "OLLAMA_BASE_URL",
    "JUST_ANOTHER_CODING_AGENT_THINKING",
)


def build_provider_env(
    environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    source = os.environ if environ is None else environ
    return {key: source[key] for key in _PROVIDER_ENV_KEYS if key in source}


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
        f"--thinking {shlex.quote(thinking)} "
        if thinking is not None
        else ""
    )
    return (
        f"printf %s {shlex.quote(prompt_b64)} | base64 -d | "
        f"{shlex.quote(python_executable)} -m "
        "just_another_coding_agent_adapters.bench.exec_prompt "
        f"--model {shlex.quote(model)} "
        f"{thinking_arg}"
        f"--sessions-root {shlex.quote(sessions_root)} "
        f"-C {shlex.quote(workspace_root)} - "
        "2>&1 | stdbuf -oL tee /logs/agent/just-another-coding-agent.txt"
    )
