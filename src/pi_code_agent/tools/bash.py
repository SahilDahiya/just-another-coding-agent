from __future__ import annotations

import subprocess
from pathlib import Path

from pydantic_ai import Tool

from pi_code_agent.contracts.tools import BashToolInput
from pi_code_agent.tools._workspace import normalize_workspace_root


def execute_bash(
    *,
    tool_input: BashToolInput,
    workspace_root: Path | str,
) -> dict[str, int | str]:
    root = normalize_workspace_root(workspace_root)

    try:
        completed = subprocess.run(
            ["bash", "-lc", tool_input.command],
            check=False,
            cwd=root,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=tool_input.timeout,
        )
    except subprocess.TimeoutExpired as error:
        raise TimeoutError(
            f"Bash command timed out after {tool_input.timeout} seconds"
        ) from error

    output_bytes = completed.stdout or b""
    output = output_bytes.decode("utf-8")
    return {
        "exit_code": completed.returncode,
        "output": output,
    }


def create_bash_tool(*, workspace_root: Path | str) -> Tool:
    root = normalize_workspace_root(workspace_root)

    def bash(command: str, timeout: int | None = None) -> dict[str, int | str]:
        """Run a local bash command and return exit code plus combined output."""

        return execute_bash(
            tool_input=BashToolInput(command=command, timeout=timeout),
            workspace_root=root,
        )

    return Tool(bash, name="bash", strict=True)

__all__ = ["create_bash_tool", "execute_bash"]
