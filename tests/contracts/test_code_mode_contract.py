from __future__ import annotations

import pytest
from pydantic import ValidationError

from just_another_coding_agent.contracts import code_mode
from just_another_coding_agent.contracts.run_events import (
    CodeModeActivityDetails,
    ToolActivity,
)


def test_code_mode_contract_exports_expected_types() -> None:
    assert set(code_mode.__all__) == {
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
    }


def test_code_mode_exec_request_is_strict_and_explicit() -> None:
    request = code_mode.CodeModeExecRequest(
        source="await tools.read(path='README.md')",
        yield_time_ms=250,
        max_output_tokens=1200,
        timeout_ms=10_000,
    )

    assert request.source == "await tools.read(path='README.md')"
    assert request.yield_time_ms == 250
    assert request.max_output_tokens == 1200
    assert request.timeout_ms == 10_000

    with pytest.raises(ValidationError):
        code_mode.CodeModeExecRequest(source="")

    with pytest.raises(ValidationError):
        code_mode.CodeModeExecRequest(source="print('ok')", extra=True)

    with pytest.raises(ValidationError):
        code_mode.CodeModeExecRequest(source="print('ok')", yield_time_ms=-1)


def test_code_mode_wait_request_defaults_and_validation() -> None:
    request = code_mode.CodeModeWaitRequest(cell_id="cell-1")

    assert request.cell_id == "cell-1"
    assert request.yield_time_ms is None
    assert request.max_output_tokens is None
    assert request.terminate is False

    with pytest.raises(ValidationError):
        code_mode.CodeModeWaitRequest(cell_id="")

    with pytest.raises(ValidationError):
        code_mode.CodeModeWaitRequest(cell_id="cell-1", max_output_tokens=0)


def test_code_mode_cell_result_validates_error_state_relationship() -> None:
    failed = code_mode.CodeModeCellResult(
        cell_id="cell-1",
        state="failed",
        error=code_mode.CodeModeError(
            error_type="CodeModeRuntimeError",
            message="execution failed",
        ),
    )

    assert failed.state == "failed"
    assert failed.error is not None

    with pytest.raises(ValidationError):
        code_mode.CodeModeCellResult(cell_id="cell-1", state="failed")

    with pytest.raises(ValidationError):
        code_mode.CodeModeCellResult(
            cell_id="cell-1",
            state="completed",
            error=code_mode.CodeModeError(
                error_type="UnexpectedError",
                message="should not be attached",
            ),
        )


def test_code_mode_cell_result_carries_output_and_terminal_status() -> None:
    result = code_mode.CodeModeCellResult(
        cell_id="cell-1",
        state="completed",
        output=(
            code_mode.CodeModeOutputChunk(
                channel="stdout",
                text="loaded 5 jobs",
            ),
            code_mode.CodeModeOutputChunk(
                channel="result",
                text='{"rows": 5}',
                truncated=False,
            ),
        ),
        elapsed_ms=42,
        output_truncated=False,
    )

    assert result.model_dump(mode="json") == {
        "cell_id": "cell-1",
        "state": "completed",
        "output": [
            {
                "channel": "stdout",
                "text": "loaded 5 jobs",
                "truncated": False,
            },
            {
                "channel": "result",
                "text": '{"rows": 5}',
                "truncated": False,
            },
        ],
        "error": None,
        "elapsed_ms": 42,
        "output_truncated": False,
    }
    assert code_mode.is_code_mode_terminal_state(result.state)
    assert not code_mode.is_code_mode_terminal_state("yielded")


def test_code_mode_activity_details_are_typed_for_exec_updates() -> None:
    activity = ToolActivity(
        title="exec code cell",
        summary="read succeeded",
        details=CodeModeActivityDetails(
            cell_id="cell-1",
            nested_tool="read",
            nested_status="succeeded",
            title="read note.txt",
            elapsed_ms=12,
        ),
    )

    assert activity.model_dump(mode="json")["details"] == {
        "kind": "code_mode",
        "cell_id": "cell-1",
        "nested_tool": "read",
        "nested_status": "succeeded",
        "title": "read note.txt",
        "elapsed_ms": 12,
        "error_type": None,
        "message": None,
    }
