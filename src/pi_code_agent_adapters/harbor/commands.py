from __future__ import annotations

import base64
import os
import shlex
from collections.abc import Mapping

_PROVIDER_ENV_KEYS = (
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
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
    workspace_root: str = ".",
    sessions_root: str = "/tmp/pi-code-agent-sessions",
) -> str:
    prompt_b64 = base64.b64encode(instruction.encode("utf-8")).decode("ascii")
    return (
        f"printf %s {shlex.quote(prompt_b64)} | base64 -d | "
        "python3 -m pi_code_agent_adapters.bench.exec_prompt "
        f"--model {shlex.quote(model)} "
        f"--sessions-root {shlex.quote(sessions_root)} "
        f"-C {shlex.quote(workspace_root)} - "
        "2>&1 | stdbuf -oL tee /logs/agent/pi-code-agent.txt"
    )
