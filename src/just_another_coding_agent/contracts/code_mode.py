from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

CODE_MODE_TOOL_NAMES = ("exec", "wait")

CodeModeToolName = Literal["exec", "wait"]
CodeModeCellState = Literal[
    "running",
    "yielded",
    "completed",
    "failed",
    "terminated",
]
CodeModeOutputChannel = Literal["stdout", "stderr", "result"]

_TERMINAL_STATES: frozenset[CodeModeCellState] = frozenset(
    {"completed", "failed", "terminated"}
)


class _CodeModeContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class CodeModeExecRequest(_CodeModeContractModel):
    source: str = Field(min_length=1)
    yield_time_ms: int | None = Field(default=None, ge=0)
    max_output_tokens: int | None = Field(default=None, ge=1)
    timeout_ms: int | None = Field(default=None, gt=0)


class CodeModeWaitRequest(_CodeModeContractModel):
    cell_id: str = Field(min_length=1)
    yield_time_ms: int | None = Field(default=None, ge=0)
    terminate: bool = False


class CodeModeOutputChunk(_CodeModeContractModel):
    channel: CodeModeOutputChannel
    text: str
    truncated: bool = False


class CodeModeError(_CodeModeContractModel):
    error_type: str = Field(min_length=1)
    message: str = Field(min_length=1)
    tool_name: str | None = None
    tool_call_id: str | None = None


class CodeModeCellResult(_CodeModeContractModel):
    cell_id: str = Field(min_length=1)
    state: CodeModeCellState
    output: tuple[CodeModeOutputChunk, ...] = ()
    error: CodeModeError | None = None
    elapsed_ms: int | None = Field(default=None, ge=0)
    output_truncated: bool = False

    @model_validator(mode="after")
    def _validate_error_state_relationship(self) -> "CodeModeCellResult":
        if self.state == "failed":
            if self.error is None:
                raise ValueError("failed Code Mode cell results require an error")
            return self
        if self.error is not None:
            raise ValueError("only failed Code Mode cell results may carry an error")
        return self


def is_code_mode_terminal_state(state: CodeModeCellState) -> bool:
    return state in _TERMINAL_STATES


__all__ = [
    "CODE_MODE_TOOL_NAMES",
    "CodeModeCellResult",
    "CodeModeCellState",
    "CodeModeError",
    "CodeModeExecRequest",
    "CodeModeOutputChannel",
    "CodeModeOutputChunk",
    "CodeModeToolName",
    "CodeModeWaitRequest",
    "is_code_mode_terminal_state",
]
