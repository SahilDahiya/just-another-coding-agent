from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from just_another_coding_agent.tools.errors import (
    ToolCommandError,
    ToolEncodingError,
    ToolOperationalError,
    ToolPathError,
)

READ_ONLY_WORKER_PROTOCOL_VERSION = 1
READ_ONLY_WORKER_KIND = "read_only"
READ_ONLY_WORKER_OPERATIONS = ("read", "ls", "find", "grep")

type WorkerOperation = Literal["read", "ls", "find", "grep"]
type WorkerErrorCode = Literal[
    "command_error",
    "encoding_error",
    "operational_error",
    "path_error",
    "invalid_request",
    "unsupported_operation",
    "protocol_error",
    "cancelled",
]


class _WorkerMessageBase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    request_id: Annotated[str, Field(min_length=1)]


class HelloWorkerRequest(_WorkerMessageBase):
    type: Literal["hello"] = "hello"
    protocol_version: Literal[READ_ONLY_WORKER_PROTOCOL_VERSION] = (
        READ_ONLY_WORKER_PROTOCOL_VERSION
    )
    worker_kind: Literal[READ_ONLY_WORKER_KIND] = READ_ONLY_WORKER_KIND


class _WorkerCallRequestBase(_WorkerMessageBase):
    workspace_root: Annotated[str, Field(min_length=1)]


class ReadWorkerRequest(_WorkerCallRequestBase):
    type: Literal["call_read"] = "call_read"
    path: Annotated[str, Field(min_length=1)]
    offset: Annotated[int | None, Field(ge=1)] = None
    limit: Annotated[int | None, Field(ge=1)] = None
    max_lines: Annotated[int, Field(ge=1)]
    max_bytes: Annotated[int, Field(ge=1)]


class LsWorkerRequest(_WorkerCallRequestBase):
    type: Literal["call_ls"] = "call_ls"
    path: Annotated[str | None, Field(min_length=1)] = None
    limit: Annotated[int, Field(ge=1)]
    max_bytes: Annotated[int, Field(ge=1)]


class FindWorkerRequest(_WorkerCallRequestBase):
    type: Literal["call_find"] = "call_find"
    pattern: Annotated[str, Field(min_length=1)]
    path: Annotated[str | None, Field(min_length=1)] = None
    limit: Annotated[int, Field(ge=1)]
    max_bytes: Annotated[int, Field(ge=1)]


class GrepWorkerRequest(_WorkerCallRequestBase):
    type: Literal["call_grep"] = "call_grep"
    pattern: Annotated[str, Field(min_length=1)]
    path: Annotated[str | None, Field(min_length=1)] = None
    glob: Annotated[str | None, Field(min_length=1)] = None
    ignore_case: bool = False
    literal: bool = False
    limit: Annotated[int, Field(ge=1)]
    max_bytes: Annotated[int, Field(ge=1)]
    max_line_chars: Annotated[int, Field(ge=1)]


class CancelWorkerRequest(_WorkerMessageBase):
    type: Literal["cancel"] = "cancel"
    target_request_id: Annotated[str, Field(min_length=1)]


class ShutdownWorkerRequest(_WorkerMessageBase):
    type: Literal["shutdown"] = "shutdown"


type WorkerRequest = Annotated[
    HelloWorkerRequest
    | ReadWorkerRequest
    | LsWorkerRequest
    | FindWorkerRequest
    | GrepWorkerRequest
    | CancelWorkerRequest
    | ShutdownWorkerRequest,
    Field(discriminator="type"),
]


class HelloWorkerResponse(_WorkerMessageBase):
    type: Literal["hello_ok"] = "hello_ok"
    protocol_version: Literal[READ_ONLY_WORKER_PROTOCOL_VERSION] = (
        READ_ONLY_WORKER_PROTOCOL_VERSION
    )
    worker_kind: Literal[READ_ONLY_WORKER_KIND] = READ_ONLY_WORKER_KIND
    supported_operations: tuple[WorkerOperation, ...] = READ_ONLY_WORKER_OPERATIONS
    supports_cancel: bool = True
    supports_parallel_calls: bool = True


class ReadCallResult(_WorkerMessageBase):
    type: Literal["read_result"] = "read_result"
    window_text: str
    total_lines: Annotated[int, Field(ge=0)]
    start_line: Annotated[int, Field(ge=1)]
    end_line: Annotated[int, Field(ge=1)]
    truncated: bool = False
    next_offset: Annotated[int | None, Field(ge=1)] = None
    first_line_exceeds_max_bytes: bool = False


class LsEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: Annotated[str, Field(min_length=1)]
    is_dir: bool


class LsCallResult(_WorkerMessageBase):
    type: Literal["ls_result"] = "ls_result"
    entries: list[LsEntry]
    total_entries: Annotated[int, Field(ge=0)]
    limit_hit: bool
    byte_limit_hit: bool


class FindCallResult(_WorkerMessageBase):
    type: Literal["find_result"] = "find_result"
    matches: list[str]
    total_matches: Annotated[int, Field(ge=0)]
    limit_hit: bool
    byte_limit_hit: bool


class GrepMatch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: Annotated[str, Field(min_length=1)]
    line_number: Annotated[int, Field(ge=1)]
    text: str
    text_truncated: bool = False


class GrepCallResult(_WorkerMessageBase):
    type: Literal["grep_result"] = "grep_result"
    matches: list[GrepMatch]
    limit_hit: bool
    byte_limit_hit: bool
    truncated_lines: bool


class ReadOnlyWorkerErrorResponse(_WorkerMessageBase):
    type: Literal["error"] = "error"
    error_code: WorkerErrorCode
    message: str


type WorkerResponse = Annotated[
    HelloWorkerResponse
    | ReadCallResult
    | LsCallResult
    | FindCallResult
    | GrepCallResult
    | ReadOnlyWorkerErrorResponse,
    Field(discriminator="type"),
]

_WORKER_REQUEST_ADAPTER = TypeAdapter(WorkerRequest)
_WORKER_RESPONSE_ADAPTER = TypeAdapter(WorkerResponse)


def encode_worker_message(message: BaseModel) -> str:
    return message.model_dump_json()


def parse_worker_request_line(line: str) -> WorkerRequest:
    return _WORKER_REQUEST_ADAPTER.validate_json(line)


def parse_worker_response_line(line: str) -> WorkerResponse:
    return _WORKER_RESPONSE_ADAPTER.validate_json(line)


def worker_error_to_exception(error: ReadOnlyWorkerErrorResponse) -> Exception:
    if error.error_code == "command_error":
        return ToolCommandError(error.message)
    if error.error_code == "encoding_error":
        return ToolEncodingError(error.message)
    if error.error_code == "operational_error":
        return ToolOperationalError(error.message)
    if error.error_code == "path_error":
        return ToolPathError(error.message)
    return RuntimeError(
        "Read-only worker failure "
        f"({error.error_code}) for request {error.request_id}: {error.message}"
    )


__all__ = [
    "CancelWorkerRequest",
    "FindCallResult",
    "FindWorkerRequest",
    "GrepCallResult",
    "GrepMatch",
    "GrepWorkerRequest",
    "HelloWorkerRequest",
    "HelloWorkerResponse",
    "LsCallResult",
    "LsEntry",
    "LsWorkerRequest",
    "READ_ONLY_WORKER_KIND",
    "READ_ONLY_WORKER_OPERATIONS",
    "READ_ONLY_WORKER_PROTOCOL_VERSION",
    "ReadCallResult",
    "ReadOnlyWorkerErrorResponse",
    "ReadWorkerRequest",
    "ShutdownWorkerRequest",
    "WorkerErrorCode",
    "WorkerOperation",
    "WorkerRequest",
    "WorkerResponse",
    "encode_worker_message",
    "parse_worker_request_line",
    "parse_worker_response_line",
    "worker_error_to_exception",
]
