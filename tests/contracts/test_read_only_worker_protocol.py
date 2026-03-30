from __future__ import annotations

import pytest
from pydantic import ValidationError

from just_another_coding_agent.tools.errors import (
    ToolCommandError,
    ToolEncodingError,
    ToolOperationalError,
    ToolPathError,
)
from just_another_coding_agent.tools.read_only_worker.protocol import (
    READ_ONLY_WORKER_OPERATIONS,
    READ_ONLY_WORKER_PROTOCOL_VERSION,
    CancelWorkerRequest,
    FindCallResult,
    FindWorkerRequest,
    GrepCallResult,
    GrepMatch,
    GrepWorkerRequest,
    HelloWorkerRequest,
    HelloWorkerResponse,
    LsCallResult,
    LsEntry,
    LsWorkerRequest,
    ReadCallResult,
    ReadOnlyWorkerErrorResponse,
    ReadWorkerRequest,
    ShutdownWorkerRequest,
    WorkerRequest,
    WorkerResponse,
    encode_worker_message,
    parse_worker_request_line,
    parse_worker_response_line,
    worker_error_to_exception,
)


def test_read_only_worker_request_round_trip_supports_handshake_and_calls() -> None:
    hello = parse_worker_request_line(
        encode_worker_message(HelloWorkerRequest(request_id="hello-1"))
    )
    assert isinstance(hello, HelloWorkerRequest)
    assert hello.protocol_version == READ_ONLY_WORKER_PROTOCOL_VERSION

    read_request = parse_worker_request_line(
        encode_worker_message(
            ReadWorkerRequest(
                request_id="read-1",
                workspace_root="/workspace",
                path="src/app.py",
                offset=10,
                limit=20,
                max_lines=2000,
                max_bytes=50 * 1024,
            )
        )
    )
    assert isinstance(read_request, ReadWorkerRequest)
    assert read_request.path == "src/app.py"
    assert read_request.offset == 10
    assert read_request.limit == 20

    grep_request = parse_worker_request_line(
        encode_worker_message(
            GrepWorkerRequest(
                request_id="grep-1",
                workspace_root="/workspace",
                pattern="TODO",
                path="src",
                glob="*.py",
                ignore_case=True,
                literal=False,
                limit=100,
                max_bytes=50 * 1024,
                max_line_chars=300,
            )
        )
    )
    assert isinstance(grep_request, GrepWorkerRequest)
    assert grep_request.ignore_case is True
    assert grep_request.glob == "*.py"

    cancel_request = parse_worker_request_line(
        encode_worker_message(
            CancelWorkerRequest(
                request_id="cancel-1",
                target_request_id="grep-1",
            )
        )
    )
    assert isinstance(cancel_request, CancelWorkerRequest)
    assert cancel_request.target_request_id == "grep-1"

    shutdown_request = parse_worker_request_line(
        encode_worker_message(ShutdownWorkerRequest(request_id="shutdown-1"))
    )
    assert isinstance(shutdown_request, ShutdownWorkerRequest)


def test_read_only_worker_response_round_trip_is_structured_and_versioned() -> None:
    hello_response = parse_worker_response_line(
        encode_worker_message(HelloWorkerResponse(request_id="hello-1"))
    )
    assert isinstance(hello_response, HelloWorkerResponse)
    assert hello_response.protocol_version == READ_ONLY_WORKER_PROTOCOL_VERSION
    assert hello_response.supported_operations == READ_ONLY_WORKER_OPERATIONS
    assert hello_response.supports_cancel is True

    read_result = parse_worker_response_line(
        encode_worker_message(
            ReadCallResult(
                request_id="read-1",
                window_text="line 10\nline 11\n",
                total_lines=42,
                start_line=10,
                end_line=11,
                truncated=False,
                next_offset=12,
            )
        )
    )
    assert isinstance(read_result, ReadCallResult)
    assert read_result.window_text == "line 10\nline 11\n"
    assert read_result.total_lines == 42
    assert read_result.next_offset == 12

    ls_result = parse_worker_response_line(
        encode_worker_message(
            LsCallResult(
                request_id="ls-1",
                entries=[
                    LsEntry(name=".gitignore", is_dir=False),
                    LsEntry(name="src", is_dir=True),
                ],
                total_entries=2,
                limit_hit=False,
                byte_limit_hit=False,
            )
        )
    )
    assert isinstance(ls_result, LsCallResult)
    assert ls_result.entries[1].is_dir is True

    find_result = parse_worker_response_line(
        encode_worker_message(
            FindCallResult(
                request_id="find-1",
                matches=["src/app.py", "tests/test_app.py"],
                total_matches=2,
                limit_hit=False,
                byte_limit_hit=False,
            )
        )
    )
    assert isinstance(find_result, FindCallResult)
    assert find_result.matches == ["src/app.py", "tests/test_app.py"]

    grep_result = parse_worker_response_line(
        encode_worker_message(
            GrepCallResult(
                request_id="grep-1",
                matches=[
                    GrepMatch(
                        path="src/app.py",
                        line_number=10,
                        text="TODO: refactor",
                        text_truncated=False,
                    )
                ],
                limit_hit=False,
                byte_limit_hit=False,
                truncated_lines=False,
            )
        )
    )
    assert isinstance(grep_result, GrepCallResult)
    assert grep_result.matches[0].line_number == 10


def test_read_only_worker_protocol_rejects_unknown_version() -> None:
    with pytest.raises(ValidationError):
        parse_worker_request_line(
            (
                '{"type":"hello","request_id":"hello-1",'
                '"protocol_version":999,"worker_kind":"read_only"}'
            )
        )

    with pytest.raises(ValidationError):
        parse_worker_response_line(
            (
                '{"type":"hello_ok","request_id":"hello-1",'
                '"protocol_version":999,"worker_kind":"read_only",'
                '"supported_operations":["read","ls","find","grep"],'
                '"supports_cancel":true,"supports_parallel_calls":true}'
            )
        )


def test_read_only_worker_error_mapping_distinguishes_error_classes() -> None:
    path_error = ReadOnlyWorkerErrorResponse(
        request_id="read-1",
        error_code="path_error",
        message="missing file",
    )
    command_error = ReadOnlyWorkerErrorResponse(
        request_id="grep-1",
        error_code="command_error",
        message="rg missing",
    )
    operational_error = ReadOnlyWorkerErrorResponse(
        request_id="read-1",
        error_code="operational_error",
        message="offset too large",
    )
    encoding_error = ReadOnlyWorkerErrorResponse(
        request_id="read-2",
        error_code="encoding_error",
        message="weights.pt is not valid UTF-8 text",
    )
    protocol_error = ReadOnlyWorkerErrorResponse(
        request_id="hello-1",
        error_code="protocol_error",
        message="unsupported version",
    )

    assert isinstance(worker_error_to_exception(path_error), ToolPathError)
    assert isinstance(worker_error_to_exception(command_error), ToolCommandError)
    assert isinstance(worker_error_to_exception(encoding_error), ToolEncodingError)
    assert isinstance(
        worker_error_to_exception(operational_error),
        ToolOperationalError,
    )
    assert isinstance(worker_error_to_exception(protocol_error), RuntimeError)


def test_read_only_worker_unions_accept_all_supported_operations() -> None:
    request: WorkerRequest = FindWorkerRequest(
        request_id="find-1",
        workspace_root="/workspace",
        pattern="*.py",
        path="src",
        limit=1000,
        max_bytes=50 * 1024,
    )
    assert isinstance(request, FindWorkerRequest)

    ls_request: WorkerRequest = LsWorkerRequest(
        request_id="ls-1",
        workspace_root="/workspace",
        path="src",
        limit=500,
        max_bytes=50 * 1024,
    )
    assert isinstance(ls_request, LsWorkerRequest)

    response: WorkerResponse = ReadOnlyWorkerErrorResponse(
        request_id="find-1",
        error_code="cancelled",
        message="request cancelled",
    )
    assert isinstance(response, ReadOnlyWorkerErrorResponse)


def test_tools_errors_imports_without_read_only_worker_cycle() -> None:
    import importlib

    module = importlib.import_module("just_another_coding_agent.tools.errors")

    assert module.ToolCommandError.__name__ == "ToolCommandError"
