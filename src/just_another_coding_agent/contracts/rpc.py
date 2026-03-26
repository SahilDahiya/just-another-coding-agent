from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from .run_events import RunEvent

SessionId = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{32}$")]


class _RpcModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SessionCreatePayload(_RpcModel):
    pass


class SessionCreateRequest(_RpcModel):
    id: str
    command: Literal["session.create"]
    payload: SessionCreatePayload


class RunStartPayload(_RpcModel):
    session_id: SessionId
    prompt: str


class RunStartRequest(_RpcModel):
    id: str
    command: Literal["run.start"]
    payload: RunStartPayload


RpcRequest = Annotated[
    SessionCreateRequest | RunStartRequest,
    Field(discriminator="command"),
]


class SessionCreateResponse(_RpcModel):
    session_id: SessionId


class RpcResponseEnvelope(_RpcModel):
    type: Literal["rpc_response"] = "rpc_response"
    id: str
    response: SessionCreateResponse


class RpcEventEnvelope(_RpcModel):
    type: Literal["rpc_event"] = "rpc_event"
    id: str
    event: RunEvent


class RpcErrorEnvelope(_RpcModel):
    type: Literal["rpc_error"] = "rpc_error"
    id: str | None
    error_type: str
    message: str


__all__ = [
    "RpcErrorEnvelope",
    "RpcEventEnvelope",
    "RpcRequest",
    "RpcResponseEnvelope",
    "RunStartPayload",
    "RunStartRequest",
    "SessionId",
    "SessionCreatePayload",
    "SessionCreateRequest",
    "SessionCreateResponse",
]
