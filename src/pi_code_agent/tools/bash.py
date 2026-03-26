from __future__ import annotations

import subprocess

from pydantic_ai import Tool

from pi_code_agent.contracts.tools import BashToolInput


def execute_bash(tool_input: BashToolInput) -> dict[str, int | str]:
    try:
        completed = subprocess.run(
            ["bash", "-lc", tool_input.command],
            check=False,
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


def bash(command: str, timeout: int | None = None) -> dict[str, int | str]:
    """Run a local bash command and return exit code plus combined output."""

    return execute_bash(BashToolInput(command=command, timeout=timeout))


BASH_TOOL = Tool(bash, name="bash", strict=True)

__all__ = ["BASH_TOOL", "bash", "execute_bash"]
