from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from pydantic_ai import Tool

from just_another_coding_agent.contracts.tools import (
    BashToolInput,
    make_tool_error_result,
)
from just_another_coding_agent.tools._workspace import normalize_workspace_root

BASH_MAX_LINES = 2000
BASH_MAX_BYTES = 50 * 1024


def _append_bash_note(output: str, note: str) -> str:
    if not output:
        return note
    return f"{output.rstrip('\n')}\n\n{note}"


def _format_bash_failure(output: str, failure_message: str) -> str:
    if output:
        return f"{output}\n\n{failure_message}"
    return failure_message


def _truncate_last_bytes(text: str, max_bytes: int) -> str:
    chars: list[str] = []
    bytes_used = 0

    for char in reversed(text):
        char_bytes = len(char.encode("utf-8"))
        if bytes_used + char_bytes > max_bytes:
            break
        chars.append(char)
        bytes_used += char_bytes

    return "".join(reversed(chars))


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

    output_lines = output.splitlines(keepends=True)
    output_bytes = len(output.encode("utf-8"))
    if len(output_lines) <= BASH_MAX_LINES and output_bytes <= BASH_MAX_BYTES:
        return output

    full_output_path = _write_full_output(output)
    tail_lines: list[str] = []
    tail_bytes = 0
    last_line_partial = False
    truncated_by = "lines"

    for line in reversed(output_lines):
        if len(tail_lines) >= BASH_MAX_LINES:
            truncated_by = "lines"
            break

        line_bytes = len(line.encode("utf-8"))
        if not tail_lines and line_bytes > BASH_MAX_BYTES:
            tail_lines.append(_truncate_last_bytes(line, BASH_MAX_BYTES))
            last_line_partial = True
            truncated_by = "bytes"
            break

        if tail_bytes + line_bytes > BASH_MAX_BYTES:
            truncated_by = "bytes"
            break

        tail_lines.append(line)
        tail_bytes += line_bytes

    displayed_lines = list(reversed(tail_lines))
    displayed_output = "".join(displayed_lines)
    end_line = len(output_lines)
    start_line = end_line - len(displayed_lines) + 1

    if last_line_partial:
        note = (
            f"[Showing last {BASH_MAX_BYTES} bytes of line {end_line} "
            f"(line exceeds limit). Full output: {full_output_path}]"
        )
    elif truncated_by == "lines":
        note = (
            f"[Showing lines {start_line}-{end_line} of {len(output_lines)}. "
            f"Full output: {full_output_path}]"
        )
    else:
        note = (
            f"[Showing lines {start_line}-{end_line} of {len(output_lines)} "
            f"({BASH_MAX_BYTES} byte limit). Full output: {full_output_path}]"
        )

    return _append_bash_note(displayed_output, note)


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
        output_bytes = error.output or b""
        output = _truncate_bash_output(output_bytes.decode("utf-8"))
        raise TimeoutError(
            _format_bash_failure(
                output,
                f"Command timed out after {tool_input.timeout} seconds",
            )
        ) from error

    output_bytes = completed.stdout or b""
    output = _truncate_bash_output(output_bytes.decode("utf-8"))

    if completed.returncode != 0:
        raise RuntimeError(
            _format_bash_failure(
                output,
                f"Command exited with code {completed.returncode}",
            )
        )

    return {
        "exit_code": completed.returncode,
        "output": output,
    }


def create_bash_tool(*, workspace_root: Path | str) -> Tool:
    root = normalize_workspace_root(workspace_root)

    def bash(
        command: str,
        timeout: int | None = None,
    ) -> dict[str, int | str] | dict[str, bool | str]:
        """Execute one local bash command in the workspace root.

        Args:
            command: Bash command to execute with `bash -lc`.
            timeout: Optional timeout in seconds before the command is stopped.
        """

        try:
            return execute_bash(
                tool_input=BashToolInput(command=command, timeout=timeout),
                workspace_root=root,
            )
        except (RuntimeError, TimeoutError, OSError, UnicodeError) as error:
            return make_tool_error_result(error)

    return Tool(
        bash,
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


__all__ = ["create_bash_tool", "execute_bash"]
