from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from pydantic_ai import RunContext, Tool

from just_another_coding_agent.contracts.tools import (
    BashToolInput,
)
from just_another_coding_agent.tools.deps import WorkspaceDeps
from just_another_coding_agent.tools.errors import (
    ToolCommandError,
    ToolEncodingError,
)
from just_another_coding_agent.tools.truncation import (
    append_tool_note,
    truncate_tail_text,
)

BASH_MAX_LINES = 2000
BASH_MAX_BYTES = 50 * 1024


def _format_bash_failure(output: str, failure_message: str) -> str:
    if output:
        return f"{output}\n\n{failure_message}"
    return failure_message


def _write_full_output(output: str) -> str:
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        prefix="just-another-coding-agent-bash-",
        suffix=".log",
        delete=False,
    ) as file_handle:
        file_handle.write(output)
        return file_handle.name


def _truncate_bash_output(output: str) -> str:
    if not output:
        return ""

    window = truncate_tail_text(
        output,
        max_lines=BASH_MAX_LINES,
        max_bytes=BASH_MAX_BYTES,
    )
    if window.truncated_by is None:
        return output

    full_output_path = _write_full_output(output)

    if window.last_line_partial:
        note = (
            f"[Showing last {BASH_MAX_BYTES} bytes of line {window.end_line} "
            f"(line exceeds limit). Full output: {full_output_path}]"
        )
    elif window.truncated_by == "lines":
        note = (
            f"[Showing lines {window.start_line}-{window.end_line} of "
            f"{window.total_lines}. "
            f"Full output: {full_output_path}]"
        )
    else:
        note = (
            f"[Showing lines {window.start_line}-{window.end_line} of "
            f"{window.total_lines} "
            f"({BASH_MAX_BYTES} byte limit). Full output: {full_output_path}]"
        )

    return append_tool_note(window.text, note)


def execute_bash(
    *,
    tool_input: BashToolInput,
    workspace_root: Path | str,
) -> dict[str, int | str]:
    try:
        completed = subprocess.run(
            ["bash", "-lc", tool_input.command],
            check=False,
            cwd=workspace_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=tool_input.timeout,
        )
    except subprocess.TimeoutExpired as error:
        output_bytes = error.output or b""
        try:
            output = _truncate_bash_output(output_bytes.decode("utf-8"))
        except UnicodeError as decode_error:
            raise ToolEncodingError(
                "Command output is not valid UTF-8 text"
            ) from decode_error
        raise ToolCommandError(
            _format_bash_failure(
                output,
                f"Command timed out after {tool_input.timeout} seconds",
            )
        ) from error

    output_bytes = completed.stdout or b""
    try:
        output = _truncate_bash_output(output_bytes.decode("utf-8"))
    except UnicodeError as error:
        raise ToolEncodingError("Command output is not valid UTF-8 text") from error

    if completed.returncode != 0:
        raise ToolCommandError(
            _format_bash_failure(
                output,
                f"Command exited with code {completed.returncode}",
            )
        )

    return {
        "exit_code": completed.returncode,
        "output": output,
    }


def bash(
    ctx: RunContext[WorkspaceDeps],
    command: str,
    timeout: int | None = None,
) -> dict[str, int | str]:
    """Execute one local bash command in the workspace root.

    Args:
        command: Bash command to execute with `bash -lc`.
        timeout: Optional timeout in seconds before the command is stopped.
    """

    return execute_bash(
        tool_input=BashToolInput(command=command, timeout=timeout),
        workspace_root=ctx.deps.workspace_root,
    )


BASH_TOOL = Tool(
    bash,
    takes_ctx=True,
    name="bash",
    description=(
        "Execute a local bash command in the workspace root. Returns "
        "combined stdout and stderr on success. Non-zero exits and "
        "timeouts become error results. Large output is truncated to the "
        "last 2000 lines or 50 KiB, and the full output is saved to a "
        "temp file."
    ),
    docstring_format="google",
    require_parameter_descriptions=True,
    strict=True,
)


__all__ = ["BASH_TOOL", "bash", "execute_bash"]
